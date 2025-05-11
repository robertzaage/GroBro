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

from grobro.grobro import parser
from grobro.model import DeviceAlias, DeviceConfig, MQTTConfig

LOG = logging.getLogger(__name__)
HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
ACTIVATE_COMMUNICATION_GROWATT_SERVER = (
    os.getenv("ACTIVATE_COMMUNICATION_GROWATT_SERVER", "").lower() == "true"
)
DUMP_MESSAGES = os.getenv("DUMP_MESSAGES", "false").lower() == "true"
DUMP_DIR = os.getenv("DUMP_DIR", "/dump")
REGISTER_FILTER_ENV = os.getenv("REGISTER_FILTER", "")
REGISTER_FILTER: dict[str, DeviceAlias] = {}
for entry in REGISTER_FILTER_ENV.split(","):
    if ":" in entry:
        serial, alias = entry.split(":", 1)
        REGISTER_FILTER[serial] = DeviceAlias(alias)

# fmt: off
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
# fmt: on
ALIAS_TO_REGISTERS = {
    DeviceAlias.NEO600: NEO_SP2_REGISTERS,
    DeviceAlias.NEO800: NEO_SP2_REGISTERS,
    DeviceAlias.NEO1000: NEO_SP2_REGISTERS,
    DeviceAlias.NOAH: NOAH_REGISTERS,
}

# property to flag messages forwarded from growatt cloud
MQTT_PROP_FORWARD_GROWATT = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_GROWATT.UserProperty = [("forwarded-for", "growatt")]
# property to flag messages forwarded from ha
MQTT_PROP_FORWARD_HA = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_HA.UserProperty = [("forwarded-for", "ha")]


class Client:
    on_config: Callable[[DeviceConfig], None]
    on_state: Callable[[str, dict], None]

    _client: mqtt.Client
    _forward_mqtt_config: MQTTConfig
    _forward_clients = {}

    def __init__(
        self,
        grobro_mqtt: MQTTConfig,
        forward_mqtt: MQTTConfig,
    ):
        LOG.info(f"connecting to HA mqtt '{grobro_mqtt.host}:{grobro_mqtt.port}'")
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
        LOG.debug("grobro: start")
        self._client.loop_start()

    def stop(self):
        LOG.debug("grobro: stop")
        self._client.loop_stop()
        self._client.disconnect()
        for key, client in self._forward_clients.items():
            client.loop_stop()
            client.disconnect()

    def __on_message(self, client, userdata, msg: MQTTMessage):
        # check for forwarded messages and ignore them
        props = msg.properties.json().get("UserProperty", [])
        for key, value in props:
            if key == "forwarded-for" and value in ["ha", "growatt"]:
                LOG.debug("message forwarded from %s. skip.", value)
                return

        LOG.debug(f"received: {msg.topic} {msg.payload}")
        if DUMP_MESSAGES:
            dump_message_binary(msg.topic, msg.payload)
        try:
            if ACTIVATE_COMMUNICATION_GROWATT_SERVER:
                forward_client = self.__connect_to_growatt_server(
                    msg.topic.split("/")[-1]
                )
                forward_client.publish(
                    msg.topic,
                    payload=msg.payload,
                    qos=msg.qos,
                    retain=msg.retain,
                )
            unscrambled = parser.unscramble(msg.payload)
            msg_type = struct.unpack_from(">H", unscrambled, 4)[0]
            unscrambled = parser.unscramble(msg.payload)
            msg_type = struct.unpack_from(">H", unscrambled, 4)[0]

            # NOAH=387 NEO=340
            if msg_type in (387, 340):
                # Config message
                config_offset = parser.find_config_offset(unscrambled)
                config = parser.parse_config_type(unscrambled, config_offset)
                self.on_config(device_id=device_id, config=config)
            # NOAH=323 NEO=577
            elif msg_type in (323, 577):
                # Modbus message
                temp_descriptions = parser.load_modbus_input_register_file(
                    "growatt_inverter_registers.json"
                )
                parsed = parser.parse_modbus_type(unscrambled, temp_descriptions)
                device_id = parsed.get("device_id")
                alias = REGISTER_FILTER.get(device_id)
                regfile = "growatt_inverter_registers.json"
                if alias and alias.strip().upper() == "NOAH":
                    regfile = "growatt_noah_registers.json"
                modbus_input_register_descriptions = (
                    parser.load_modbus_input_register_file(regfile)
                )

                # Rebuild HA metadata lookup for this register set
                parsed = parser.parse_modbus_type(
                    unscrambled, modbus_input_register_descriptions
                )
                device_id = parsed.get("device_id")
                all_registers = parsed.get("modbus1", {}).get(
                    "registers", []
                ) + parsed.get("modbus2", {}).get("registers", [])
                self.__publish_state(device_id, all_registers)
                LOG.info(
                    f"Published state for {device_id} with {len(all_registers)} registers"
                )
        except Exception as e:
            LOG.error(f"processing message: {e}")

    def __publish_state(self, device_id, registers):
        alias = REGISTER_FILTER.get(device_id)
        allowed_registers = ALIAS_TO_REGISTERS.get(alias)
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
            if ACTIVATE_COMMUNICATION_GROWATT_SERVER:
                # We need to publish the messages from Growatt on the Topic
                # s/33/{deviceid}. Growatt sends them on Topic s/{deviceid}
                LOG.debug(
                    "msg from Growatt for client %s",
                    msg.topic.split("/")[-1],
                )
                self._client.publish(
                    msg.topic.split("/")[0] + "/33/" + msg.topic.split("/")[-1],
                    payload=msg.payload,
                    qos=msg.qos,
                    retain=msg.retain,
                    properties=MQTT_PROP_FORWARD_GROWATT,
                )
        except Exception as e:
            LOG.error(f"forwarding message: {e}")

    # Setup Growatt Server MQTT for forwarding messages
    def __connect_to_growatt_server(self, client_id):
        if f"forward_client_{client_id}" not in self._forward_clients:
            LOG.info(
                "connect to Forwarding Server at %s:%s, subscribed to '+/%s'",
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


# Ensure that the dump directory exists (not sure if needed, but for safety)
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
