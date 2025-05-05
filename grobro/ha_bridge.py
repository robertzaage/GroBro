# Home Assistant extension for GroBro to act as a MQTT bridge between source and target MQTT brokers
# Reads Growatt MQTT packets, decodes them, maps registers and republishes values for Home Assistant auto-discovery

import os
from dataclasses import asdict
import json
import struct
import ssl
import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage
import threading
import logging
from .grobro import unscramble, parse_modbus_type, load_modbus_input_register_file, parse_config_type, find_config_offset # TODO avoid relative import with more refactoring
import grobro.ha as ha
import time
from threading import Timer

Forwarding_Clients = {}
device_timers = {}

# Configuration from environment variables
SOURCE_MQTT_HOST = os.getenv("SOURCE_MQTT_HOST", "localhost")
SOURCE_MQTT_PORT = int(os.getenv("SOURCE_MQTT_PORT", 1883))
SOURCE_MQTT_USER = os.getenv("SOURCE_MQTT_USER")
SOURCE_MQTT_PASS = os.getenv("SOURCE_MQTT_PASS")
SOURCE_MQTT_TLS = os.getenv("SOURCE_MQTT_TLS", "false").lower() == "true"

TARGET_MQTT_HOST = os.getenv("TARGET_MQTT_HOST", SOURCE_MQTT_HOST)
TARGET_MQTT_PORT = int(os.getenv("TARGET_MQTT_PORT", SOURCE_MQTT_PORT))
TARGET_MQTT_USER = os.getenv("TARGET_MQTT_USER", SOURCE_MQTT_USER)
TARGET_MQTT_PASS = os.getenv("TARGET_MQTT_PASS", SOURCE_MQTT_PASS)
TARGET_MQTT_TLS = os.getenv("TARGET_MQTT_TLS", "false").lower() == "true"
FORWARD_MQTT_HOST = os.getenv("FORWARD_MQTT_HOST", "mqtt.growatt.com")
FORWARD_MQTT_PORT = int( os.getenv("FORWARD_MQTT_PORT", 7006))
ACTIVATE_COMMUNICATION_GROWATT_SERVER = os.getenv("ACTIVATE_COMMUNICATION_GROWATT_SERVER", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR").upper()

DUMP_MESSAGES = os.getenv("DUMP_MESSAGES", "false").lower() == "true"
DUMP_DIR = os.getenv("DUMP_DIR", "/dump")
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", 0))

# Setup Logger
try:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
except Exception as e:
    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    print(f"Failed to setup Logger {e} USING DEFAULT LOG Level(Error)")
LOG = logging.getLogger(__name__)


ha_client = ha.Client(TARGET_MQTT_HOST, TARGET_MQTT_PORT, TARGET_MQTT_TLS, TARGET_MQTT_USER, TARGET_MQTT_PASS)

# Ensure that the dump directory exists (not sure if needed, but for safety)
if DUMP_MESSAGES and not os.path.exists(DUMP_DIR):
    os.makedirs(DUMP_DIR, exist_ok=True)
    LOG.info(f"Dump directory created: {DUMP_DIR}")

# Register filter configuration
NEO_SP2_REGISTERS = [
    3001, 3003, 3004, 3005, 3007, 3008, 3009,
    3023, 3025, 3026, 3027, 3028, 3038, 3047,
    3049, 3051, 3053, 3055, 3057, 3059, 3061,
    3087, 3093, 3094, 3095, 3096, 3098, 3100,
    3101, 3115
]

# TODO: Add additional registers based on battery count
NOAH_REGISTERS = [
     2,   7,   8,  10,  11,  12,  13,
    21,  23,  25,  27,  29,  72,  74,
    76,  78,  90,  91,  92,  93,  94,
    95,  96,  97,  99, 100, 101, 102,
   109,  65,  53,  41
]

REGISTER_FILTER_ENV = os.getenv("REGISTER_FILTER", "")
device_filter_alias_map = {}
for entry in REGISTER_FILTER_ENV.split(","):
    if ":" in entry:
        serial, alias = entry.split(":", 1)
        device_filter_alias_map[serial] = alias

alias_to_registers = {
    "NEO600": NEO_SP2_REGISTERS,
    "NEO800": NEO_SP2_REGISTERS,
    "NEO1000": NEO_SP2_REGISTERS,
    "NOAH": NOAH_REGISTERS
}

def parse_ascii(value):
    try:
        return bytes.fromhex(hex(value)[2:].zfill(4)).decode('ascii').strip()
    except Exception:
        return str(value)

def apply_conversion(register):
    unit = register.get("unit")
    if unit == "s":
        register["value"] = parse_ascii(register["value"])
    elif isinstance(register.get("value"), (int, float)) and register.get("multiplier"):
        register["value"] *= register["multiplier"]

def publish_state(device_id, registers):
    alias = device_filter_alias_map.get(device_id)
    allowed_registers = alias_to_registers.get(alias)
    if allowed_registers:
        registers = [
            reg for reg in registers
            if "register_no" in reg and reg["register_no"] in allowed_registers
        ]
    for reg in registers:
        apply_conversion(reg)
    payload = {
        reg["name"]: round(reg["value"], 3) if isinstance(reg["value"], float) else reg["value"]
        for reg in registers
    }
    LOG.info(f"Device {device_id} matched {len(registers)} registers after filtering.")
    if DEVICE_TIMEOUT > 0:
        reset_device_timer(device_id)
    ha_client.publish_state(device_id, payload)
    ha_client.publish_availability(device_id, "online")

def dump_message_binary(topic, payload):
    try:
        # Build path following topic structure
        topic_parts = topic.strip("/").split("/")
        dir_path = os.path.join(DUMP_DIR, *topic_parts)
        os.makedirs(dir_path, exist_ok=True)

        # Write each message to a new file with timestamp
        import time
        timestamp = int(time.time() * 1000)
        file_path = os.path.join(dir_path, f"{timestamp}.bin")

        with open(file_path, "wb") as f:
            f.write(payload)
    except Exception as e:
        LOG.error(f"Failed to dump message for topic {topic}: {e}")

def on_message(client, userdata, msg: MQTTMessage):
    LOG.debug(f"received: {msg.topic} {msg.payload}")
    if DUMP_MESSAGES:
        dump_message_binary(msg.topic, msg.payload)
    try:
        if ACTIVATE_COMMUNICATION_GROWATT_SERVER:
            Forwarding_Client = connect_to_growatt_server(msg.topic.split("/")[-1])
            Forwarding_Client.publish(msg.topic, payload=msg.payload, qos=msg.qos, retain=msg.retain)
        unscrambled = unscramble(msg.payload)
        msg_type = struct.unpack_from('>H', unscrambled, 4)[0]
        unscrambled = unscramble(msg.payload)
        msg_type = struct.unpack_from('>H', unscrambled, 4)[0]

        # NOAH=387 NEO=340
        if msg_type in (387, 340):
            # Config message
            config_offset = find_config_offset(unscrambled)
            config = parse_config_type(unscrambled, config_offset)
            ha_client.set_config(device_id=device_id, config=config)
        # NOAH=323 NEO=577
        elif msg_type in (323, 577):
            # Modbus message
            temp_descriptions = load_modbus_input_register_file("growatt_inverter_registers.json")
            parsed = parse_modbus_type(unscrambled, temp_descriptions)
            device_id = parsed.get("device_id")
            alias = device_filter_alias_map.get(device_id)
            regfile = "growatt_inverter_registers.json"
            if alias and alias.strip().upper() == "NOAH":
                regfile = "growatt_noah_registers.json"
            modbus_input_register_descriptions = load_modbus_input_register_file(regfile)

            # Rebuild HA metadata lookup for this register set
            ha_lookup = {
                reg["variable_name"]: reg.get("ha")
                for reg in modbus_input_register_descriptions if reg.get("ha")
            }
            parsed = parse_modbus_type(unscrambled, modbus_input_register_descriptions)
            device_id = parsed.get("device_id")
            all_registers = parsed.get("modbus1", {}).get("registers", []) + parsed.get("modbus2", {}).get("registers", [])
            alias = device_filter_alias_map.get(device_id)
            allowed_registers = alias_to_registers.get(alias)
            if allowed_registers:
                all_registers = [
                    reg for reg in all_registers
                    if isinstance(reg.get("register_no"), int) and reg["register_no"] in allowed_registers
                ]
            publish_state(device_id, all_registers)
            for reg in all_registers:
                ha = ha_lookup.get(reg['name'], {})
                ha_client.publish_discovery(device_id, reg['name'], ha)
            LOG.info(f"Published state for {device_id} with {len(all_registers)} registers")
    except Exception as e:
        LOG.error(f"Error processing message: {e}")
def on_message_forward_client(client, userdata, msg: MQTTMessage):
    if DUMP_MESSAGES:
        dump_message_binary(msg.topic, msg.payload)
    try:
        if ACTIVATE_COMMUNICATION_GROWATT_SERVER:
            # We need to publish the messages from Growatt on the Topic s/33/{deviceid}. Growatt sends them on Topic s/{deviceid}
            LOG.debug("msg from Growatt")
            source_client.publish(msg.topic.split("/")[0] + "/33/" + msg.topic.split("/")[-1], payload=msg.payload, qos=msg.qos, retain=msg.retain)
    except Exception as e:
        LOG.error(f"Error processing message: {e}")

# Setup Growatt Server MQTT for forwarding messages
def connect_to_growatt_server(client_id):
    if f"forward_client_{client_id}" not in Forwarding_Clients:
        Forwarding_Clients[f"forward_client_{client_id}"] = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        Forwarding_Clients[f"forward_client_{client_id}"].tls_set(cert_reqs=ssl.CERT_NONE)
        Forwarding_Clients[f"forward_client_{client_id}"].tls_insecure_set(True)
        Forwarding_Clients[f"forward_client_{client_id}"].on_message = on_message_forward_client
        Forwarding_Clients[f"forward_client_{client_id}"].connect(FORWARD_MQTT_HOST, FORWARD_MQTT_PORT, 60)
        Forwarding_Clients[f"forward_client_{client_id}"].subscribe("#")
        LOG.info(f"Connected to Forwarding Server at {FORWARD_MQTT_HOST}:{FORWARD_MQTT_PORT} with ClientId{client_id}, listening on 's/#'")
        forward_thread = threading.Thread(target=Forwarding_Clients[f"forward_client_{client_id}"].loop_forever)
        forward_thread.start()
    return Forwarding_Clients[f"forward_client_{client_id}"]

# Set the device state to unavailable in Home Assistant.
def set_device_unavailable(device_id):
    LOG.warning(f"Device {device_id} timed out. Setting to unavailable.")
    ha_client.publish_availability(device_id, "offline")
    
# Reset the timeout timer for a device.
def reset_device_timer(device_id):
    if device_id in device_timers:
        device_timers[device_id].cancel()  # Cancel the existing timer
    
    timer = Timer(DEVICE_TIMEOUT, set_device_unavailable, args=[device_id])  # Pass function reference and arguments
    device_timers[device_id] = timer
    timer.start()

# Setup source MQTT client for subscribing
source_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="grobro-source")
if SOURCE_MQTT_USER and SOURCE_MQTT_PASS:
    source_client.username_pw_set(SOURCE_MQTT_USER, SOURCE_MQTT_PASS)
if SOURCE_MQTT_TLS:
    source_client.tls_set(cert_reqs=ssl.CERT_NONE)
    source_client.tls_insecure_set(True)
source_client.on_message = on_message
source_client.connect(SOURCE_MQTT_HOST, SOURCE_MQTT_PORT, 60)
source_client.subscribe("c/#")
LOG.info(f"Connected to source MQTT at {SOURCE_MQTT_HOST}:{SOURCE_MQTT_PORT}, listening on 'c/#'")
source_thread = threading.Thread(target=source_client.loop_forever)
source_thread.start()
