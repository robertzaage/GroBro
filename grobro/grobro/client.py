from grobro.model.neo_messages import NeoOutputPowerLimit

"""
Client for the grobro mqtt side, handling messages from/to
* growatt cloud
* growatt devices
"""

import os
import struct
import logging
import ssl
from typing import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage
from grobro.model.neo_messages import NeoMessageTypes

from grobro import model
from grobro.grobro import parser
from grobro.grobro.builder import scramble
from grobro.grobro.builder import append_crc

LOG = logging.getLogger(__name__)
HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")

# Updated growatt cloud forwarding config
GROWATT_CLOUD = os.getenv("GROWATT_CLOUD", "false")
if GROWATT_CLOUD.lower() == "true":
    GROWATT_CLOUD_ENABLED = True
    GROWATT_CLOUD_FILTER = set()
elif GROWATT_CLOUD:
    GROWATT_CLOUD_ENABLED = True
    GROWATT_CLOUD_FILTER = set(map(str.strip, GROWATT_CLOUD.split(",")))
else:
    GROWATT_CLOUD_ENABLED = False
    GROWATT_CLOUD_FILTER = set()

DUMP_MESSAGES = os.getenv("DUMP_MESSAGES", "false").lower() == "true"
DUMP_DIR = os.getenv("DUMP_DIR", "/dump")

# fmt: off
# Register filter configuration
NEO_REGISTERS = [
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
# fmt: on

# Property to flag messages forwarded from growatt cloud
MQTT_PROP_FORWARD_GROWATT = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_GROWATT.UserProperty = [("forwarded-for", "growatt")]

# Property to flag messages forwarded from ha
MQTT_PROP_FORWARD_HA = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_HA.UserProperty = [("forwarded-for", "ha")]


class Client:
    on_config: Callable[[model.DeviceConfig], None]
    on_state: Callable[[str, dict], None]
    on_message: Callable[any, None]

    _client: mqtt.Client
    _forward_mqtt_config: model.MQTTConfig
    _forward_clients = {}

    def __init__(self, grobro_mqtt: MQTTConfig, forward_mqtt: MQTTConfig):
        LOG.info(f"Connecting to GroBro broker at '{grobro_mqtt.host}:{grobro_mqtt.port}'")
        self._client = mqtt.Client(
            client_id="grobro-grobro",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv5,
        )
        if grobro_mqtt.username and grobro_mqtt.password:
            self._client.username_pw_set(grobro_mqtt.username, grobro_mqtt.password)
        if grobro_mqtt.use_tls:
            self._client.tls_set(cert_reqs=ssl.CERT_NONE)
            self._client.tls_insecure_set(True)
        self._client.connect(grobro_mqtt.host, grobro_mqtt.port, 60)
        self._client.on_message = self.__on_message
        self._client.subscribe("c/#")
        self._forward_mqtt_config = forward_mqtt

    def start(self):
        LOG.debug("GroBro: Start")
        self._client.loop_start()

    def stop(self):
        LOG.debug("GroBro: Stop")
        self._client.loop_stop()
        self._client.disconnect()
        for key, client in self._forward_clients.items():
            client.loop_stop()
            client.disconnect()

    def send_command(self, cmd: model.Command):
        LOG.debug("send command: %s: %s", type(cmd).__name__, cmd)
        scrambled = scramble(cmd.build_grobro())
        final_payload = append_crc(scrambled)

        topic = f"s/33/{cmd.device_id}"

        result = self._client.publish(
            topic,
            final_payload,
            properties=MQTT_PROP_FORWARD_HA,
        )
        status = result[0]
        if status != 0:
            LOG.warning("sent failed: %s", result)

    def __on_message(self, client, userdata, msg: MQTTMessage):
        # check for forwarded messages and ignore them
        props = msg.properties.json().get("UserProperty", [])
        for key, value in props:
            if key == "forwarded-for" and value in ["ha", "growatt"]:
                LOG.debug("Message forwarded from %s. Skipping...", value)
                return

        LOG.debug(f"Received message: {msg.topic} {msg.payload}")
        if DUMP_MESSAGES:
            dump_message_binary(msg.topic, msg.payload)
        try:
            device_id = msg.topic.split("/")[-1]
            if GROWATT_CLOUD_ENABLED:
                if GROWATT_CLOUD == "true" or device_id in GROWATT_CLOUD_FILTER:
                    forward_client = self.__connect_to_growatt_server(device_id)
                    forward_client.publish(
                        msg.topic,
                        payload=msg.payload,
                        qos=msg.qos,
                        retain=msg.retain,
                    )

            unscrambled = parser.unscramble(msg.payload)
            msg_type = struct.unpack_from(">H", unscrambled, 4)[0]

            # NOAH=387 NEO=340
            if msg_type in (387, 340):
                # Config message
                config_offset = parser.find_config_offset(unscrambled)
                config = parser.parse_config_type(unscrambled, config_offset)
                self.on_config(device_id=device_id, config=config)
                return
            # NOAH=323 NEO=577
            elif msg_type in (323, 577):
                # Modbus message
                if device_id.startswith("QMN"):
                    regfile = "growatt_inverter_registers.json"
                elif device_id.startswith("0PVP"):
                    regfile = "growatt_noah_registers.json"

                modbus_input_register_descriptions = (
                    parser.load_modbus_input_register_file(regfile)
                )

                parsed = parser.parse_modbus_type(
                    unscrambled, modbus_input_register_descriptions
                )

                all_registers = parsed.get("modbus1", {}).get("registers", []) + \
                                parsed.get("modbus2", {}).get("registers", [])
                
                self.__publish_state(device_id, all_registers)
                LOG.info(
                    f"Published state for {device_id} with {len(all_registers)} registers"
                )
                return

            for neo_msg_type in NeoMessageTypes:
                if neo_msg_type.grobro_type == msg_type:
                    LOG.debug("got message: %s", neo_msg_type.name)
                    self.on_message(neo_msg_type.model.parse_grobro(unscrambled))
                    return

            LOG.debug("unknown msg_type %s: %s", msg_type, unscrambled.hex())
        except Exception as e:
            LOG.error(f"Processing message: {e}")

    def __publish_state(self, device_id, registers):
        if device_id.startswith("QMN"):
            allowed_registers = NEO_REGISTERS
        elif device_id.startswith("0PVP"):
            allowed_registers = NOAH_REGISTERS
        if allowed_registers:
            registers = [
                reg
                for reg in registers
                if "register_no" in reg and reg["register_no"] in allowed_registers
            ]
        for reg in registers:
            apply_conversion(reg)
        payload = {
            reg["name"]: (
                round(reg["value"], 3)
                if isinstance(reg["value"], float)
                else reg["value"]
            )
            for reg in registers
        }
        LOG.info(
            f"Device {device_id} matched {len(registers)} registers after filtering."
        )
        self.on_state(device_id, payload)

    def __on_message_forward_client(self, client, userdata, msg: MQTTMessage):
        if DUMP_MESSAGES:
            dump_message_binary(msg.topic, msg.payload)
        try:
            device_id = msg.topic.split("/")[-1]
            if not GROWATT_CLOUD_ENABLED:
                return
            if GROWATT_CLOUD != "true" and device_id not in GROWATT_CLOUD_FILTER:
                LOG.debug("Dropping Growatt message for device %s not in GROWATT_CLOUD filter", device_id)
                return
            LOG.debug("Forwarding message from Growatt for client %s", device_id)
            # We need to publish the messages from Growatt on the Topic
            # s/33/{deviceid}. Growatt sends them on Topic s/{deviceid}
            self._client.publish(
                msg.topic.split("/")[0] + "/33/" + device_id,
                payload=msg.payload,
                qos=msg.qos,
                retain=msg.retain,
                properties=MQTT_PROP_FORWARD_GROWATT,
            )
        except Exception as e:
            LOG.error(f"Forwarding message: {e}")

    # Setup Growatt MQTT broker for forwarding messages
    def __connect_to_growatt_server(self, client_id):
        if f"forward_client_{client_id}" not in self._forward_clients:
            LOG.info(
                "Connecting to Growatt broker at '%s:%s', subscribed to '+/%s'",
                self._forward_mqtt_config.host,
                self._forward_mqtt_config.port,
                client_id,
            )
            client = mqtt.Client(
                client_id=client_id,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
            client.on_message = self.__on_message_forward_client
            client.connect(
                self._forward_mqtt_config.host,
                self._forward_mqtt_config.port,
                60,
            )
            client.subscribe(f"+/{client_id}")
            client.loop_start()
            self._forward_clients[f"forward_client_{client_id}"] = client
        return self._forward_clients[f"forward_client_{client_id}"]


# Ensure that the dump directory exists
if DUMP_MESSAGES and not os.path.exists(DUMP_DIR):
    os.makedirs(DUMP_DIR, exist_ok=True)
    LOG.info(f"Dump directory created: {DUMP_DIR}")


def parse_ascii(value):
    try:
        return bytes.fromhex(hex(value)[2:].zfill(4)).decode("ascii").strip()
    except Exception:
        return str(value)


def apply_conversion(register):
    unit = register.get("unit")
    if unit == "s":
        register["value"] = parse_ascii(register["value"])
    elif isinstance(register.get("value"), (int, float)) and register.get("multiplier"):
        register["value"] *= register["multiplier"]


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
