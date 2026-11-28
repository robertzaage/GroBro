"""
Client for the grobro mqtt side, handling messages from and to the Growatt devices.
Forwarding to the Growatt Cloud lives in forward.py.
"""

import os
import re
import struct
import logging
from typing import Callable


import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage

from grobro import model, settings
from grobro.grobro import parser
from grobro.grobro.builder import append_crc
from grobro.grobro.builder import scramble
from grobro.grobro.forward import MQTT_PROP_FORWARD_HA, CloudForwardMixin
from grobro.model.modbus_function import GrowattModbusFunctionMultiple, GrowattModbusFunctionSingle
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.modbus_message import GrowattModbusMessage
from grobro.model.mqtt_config import MQTTConfig
from grobro.stats import BridgeStats
from grobro.model.growatt_registers import HomeAssistantHoldingRegisterInput
from grobro.model.growatt_registers import HomeAssistantHoldingRegisterValue
from grobro.model.growatt_registers import HomeAssistantInputRegister
from grobro.model.growatt_registers import (
    DEVICE_TYPE_CODE_REGISTERS,
    GENERIC_DATALOGGER_PREFIXES,
    GrowattRegisterPosition,
    KNOWN_NEO_REGISTERS,
    detect_device_by_inverter_serial,
    detect_device_by_type_code,
    remember_inverter_serial,
    get_device_type_name,
    get_known_registers,
)


_DEVICE_ID_RE = re.compile(r"[^A-Za-z0-9]")


def _extract_device_id(topic: str) -> str:
    """Extract the device serial from the last segment of an MQTT topic.

    Growatt device serials are alphanumeric (A-Z, 0-9). Some dongles
    (e.g. ShineWiFi-X2 / XH family) include stray trailing bytes in the
    SUBSCRIBE topic, such as `s/33/ZGQ0F5601J?\\x18`. Strip everything
    that isn't a valid serial character.
    """
    return _DEVICE_ID_RE.sub("", topic.split("/")[-1])

def _normalize_neo_output_power_limit(value: float | int) -> float | int:
    # NEO encodes its 100% output-power limit as raw value 1 in read responses.
    return 100 if value == 1 else value




LOG = logging.getLogger(__name__)

class Client(CloudForwardMixin):
    on_config: Callable[[str, model.DeviceConfig], None]
    on_input_register: Callable[HomeAssistantInputRegister, None]
    on_holding_register_input: Callable[HomeAssistantHoldingRegisterInput, None]

    _client: mqtt.Client
    _forward_mqtt_config: model.MQTTConfig

    def __init__(self, grobro_mqtt: MQTTConfig, forward_mqtt: MQTTConfig, stats: BridgeStats | None = None):
        self._forward_clients = {}
        self._stats = stats or BridgeStats()
        LOG.info(
            f"Connecting to GroBro broker at '{grobro_mqtt.host}:{grobro_mqtt.port}'"
        )
        client_id_suffix = settings.MQTT_CLIENT_SUFFIX
        client_id = f"grobro-grobro{('-' + client_id_suffix) if client_id_suffix else ''}"
        self._type_code_requested: set[str] = set()

        self._client = mqtt.Client(
            client_id=client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv5,
        )

        if grobro_mqtt.username and grobro_mqtt.password:
            self._client.username_pw_set(grobro_mqtt.username, grobro_mqtt.password)
        grobro_mqtt.configure_tls(self._client)
        self._client.connect(grobro_mqtt.host, grobro_mqtt.port, 60)
        self._client.on_message = self._on_message
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._forward_mqtt_config = forward_mqtt
        self._ptq_for_raq: dict[str, str] = {}

    def start(self):
        LOG.info("GroBro: Start")
        self._client.loop_start()

    def stop(self):
        LOG.info("GroBro: Stop")
        self._client.loop_stop()
        self._client.disconnect()
        for key, client in self._forward_clients.items():
            client.loop_stop()
            client.disconnect()

    def send_command(self, cmd: GrowattModbusFunctionSingle | GrowattModbusFunctionMultiple):
        scrambled = scramble(cmd.build_grobro())
        final_payload = append_crc(scrambled)

        topic = f"s/33/{cmd.device_id}"
        LOG.debug("Send command: %s: %s: %s", type(cmd).__name__, topic, cmd)

        result = self._client.publish(
            topic,
            final_payload,
            properties=MQTT_PROP_FORWARD_HA,
        )
        status = result[0]
        if status != 0:
            LOG.warning("Sending failed: %s", result)

    def send_config_read_message(self, device_id: str, register_no: int):

        HEADER = b"\x00\x01\x00\x07"
        MSG_TYPE = 0x0119

        dev = device_id.encode("ascii").ljust(16, b"\x00")
        body = (
            struct.pack(">H", 1)
            + struct.pack(">H", register_no)
        )
        payload = b"\x00" * 14 + body
        msg_len = len(payload) + 2 + 16

        msg = (
            HEADER
            + struct.pack(">H", msg_len)
            + struct.pack(">H", MSG_TYPE)
            + dev
            + payload
        )

        final_payload = append_crc(scramble(msg))
        topic = f"s/33/{device_id}"

        LOG.info(f"Sending config read to {device_id} register={register_no}")
        self._client.publish(topic, final_payload, properties=MQTT_PROP_FORWARD_HA)


    def send_config_message(self, device_id: str, register_no: int, value: str):

        def build_config_message(device_id: str, register_no: int, value: str):
            HEADER = b"\x00\x01\x00\x07"
            MSG_TYPE = 0x0118
            dev = device_id.encode("ascii").ljust(16, b"\x00")
            value_bytes = value.encode("ascii")

            tlv = (
                struct.pack(">H", 1)
                + struct.pack(">H", len(value_bytes) + 4)
                + struct.pack(">H", register_no)
                + struct.pack(">H", len(value_bytes))
                + value_bytes
            )
            payload = b"\x00" * 14 + tlv
            msg_len = len(payload) + 2 + 16

            msg = (
                HEADER
                + struct.pack(">H", msg_len)
                + struct.pack(">H", MSG_TYPE)
                + dev
                + payload
            )
            return msg

        raw = build_config_message(device_id, register_no, value)
        scrambled = scramble(raw)
        final_payload = append_crc(scrambled)

        topic = f"s/33/{device_id}"
        LOG.info(f"Sending config message to {device_id} register={register_no} value={value}")

        self._client.publish(
            topic,
            final_payload,
            properties=MQTT_PROP_FORWARD_HA,
        )

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        LOG.info(f"Connected to GroBro MQTT server with result code {reason_code}")
        self._stats.set_source_connected(not getattr(reason_code, "is_failure", False))
        self._client.subscribe("c/#")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        LOG.warning("Disconnected from GroBro MQTT server: %s", reason_code)
        self._stats.set_source_connected(False)

    def _on_message(self, client, userdata, msg: MQTTMessage):
        # check for forwarded messages and ignore them
        forwarded_for = get_property(msg, "forwarded-for")
        if forwarded_for and forwarded_for in ["ha", "growatt"]:
            LOG.debug("Message forwarded from %s. Skipping...", forwarded_for)
            return

        file = get_property(msg, "file")
        LOG.debug("Received message (%s): %s: %s", file, msg.topic, msg.payload)
        if settings.DUMP_MESSAGES:
            dump_message_binary(msg.topic, msg.payload)
        device_id = None
        try:
            device_id = _extract_device_id(msg.topic)
            self._stats.message_received(device_id)
            self._forward_to_cloud(msg, device_id)

            unscrambled = parser.unscramble(msg.payload)
            LOG.debug("Received: %s %s", msg.topic, unscrambled.hex(" "))

            # Read msg_type from both possible offsets
            msg_type_4 = struct.unpack_from(">H", unscrambled, 4)[0]
            msg_type = struct.unpack_from(">H", unscrambled, 6)[0]

            # Config TLV: NEO=340,341 / NOAH=387 at offset 4; ShineWeLink=0x0129 at offset 6
            if msg_type_4 in (340, 341, 387) or msg_type == 0x0129:
                self._handle_config(device_id, unscrambled, msg_type_4, msg_type)
            elif msg_type == 281:
                self._handle_config_read_response(unscrambled)
            elif msg_type == 280:
                cfg = parser.parse_config_ack(unscrambled)
                LOG.info("Received config write response for %s reg=%s accepted", cfg["device_id"], cfg["register_no"])
            elif msg_type == 0x6F64:
                self._handle_smart_meter(unscrambled)
            elif self._handle_noah_config(device_id, unscrambled):
                pass
            elif self._handle_neo_preset_response(device_id, unscrambled):
                pass
            elif not self._handle_modbus(device_id, unscrambled):
                # NOAH: msg type 37 is the response when setting a register was successful,
                # msg type 831 looks like published holding registers. Both aren't handled yet.
                LOG.debug("Unknown msg_type %s: %s", msg_type, unscrambled.hex())
        except Exception as e:
            self._stats.decode_error(device_id)
            LOG.error(f"Processing message: {e}")

    def _handle_config(self, device_id: str, unscrambled: bytes, msg_type_4: int, msg_type: int):
        """Datalogger config (TLV)."""
        config_offset = parser.find_config_offset(unscrambled)
        config = parser.parse_config_type(unscrambled, config_offset)
        if not config:
            return
        self.on_config(device_id, config)
        LOG.info("Received config message for %s", device_id)
        # Extract PTQ inverter serial from ShineWeLink dongle config
        if msg_type == 0x0129 and len(unscrambled) >= 68:
            ptq_serial = unscrambled[38:68].rstrip(b"\x00").decode("ascii", errors="replace").strip()
            if ptq_serial.startswith("PTQ"):
                self._ptq_for_raq[device_id] = ptq_serial
                ptq_config = model.DeviceConfig(serial_number=ptq_serial)
                ptq_config.device_type = "55"
                if getattr(config, 'model_id', None):
                    ptq_config.model_id = config.model_id
                if getattr(config, 'sw_version', None):
                    ptq_config.sw_version = config.sw_version
                self.on_config(ptq_serial, ptq_config)
                LOG.info("Registered PTQ inverter %s behind %s", ptq_serial, device_id)

    def _handle_config_read_response(self, unscrambled: bytes):
        """Config read response (msg type 281)."""
        cfg = parser.parse_config_message(unscrambled)
        LOG.info(
            "Received config read response for %s reg=%s value=%s",
            cfg["device_id"],
            cfg["register_no"],
            cfg["value"],
        )

        value = cfg["value"]

        # Cast value for HA number entities (INT config registers)
        try:
            known_registers = get_known_registers(cfg["device_id"])
            if known_registers:
                for reg in known_registers.config_registers.values():
                    if reg.growatt.register_no == cfg["register_no"]:
                        if reg.growatt.data.data_type == "INT":
                            value = int(value)
                        break
        except Exception as e:
            LOG.debug(
                "Failed to cast config value for %s reg=%s: %s",
                cfg["device_id"],
                cfg["register_no"],
                e,
            )

        if self.on_config_read_response:
            self.on_config_read_response(cfg["device_id"], cfg["register_no"], value)

    def _handle_smart_meter(self, unscrambled: bytes):
        """Smart meter readings (EcoTracker, Shelly etc.) reported by NOAH and NEXA (msg type 0x6F64)."""
        smart_meter = parser.parse_noah_6f64(unscrambled)
        LOG.debug("Smart Meter data for %s: %s", smart_meter["device_id"], smart_meter["data"])
        topic = f"{settings.HA_BASE_TOPIC}/sensor/grobro/{smart_meter['device_id']}/smart_meter/state"
        self._client.publish(topic, smart_meter["data"], retain=settings.PUBLISH_SENSORS_RETAINED)

    def _handle_noah_config(self, device_id: str, unscrambled: bytes) -> bool:
        """NOAH and NEXA config (msg type 0xFE19)."""
        noah_msg = parser.parse_noah_message(unscrambled)
        if not noah_msg or noah_msg.get("message_type") != 0xFE19:
            return False
        if not device_id.startswith(("0PVP", "0HVR")):
            return False
        config = noah_msg.get("config")
        if not (config and config.serial_number):
            return False
        LOG.info("Received config for %s (sw_version=%s)", config.serial_number, config.sw_version or "?")
        self.on_config(device_id, config)
        return True

    def _handle_neo_preset_response(self, device_id: str, unscrambled: bytes) -> bool:
        """NEO preset-single responses carry the confirmed register value after
        a one-byte status field, unlike standard Modbus register blocks."""
        preset_response = GrowattModbusFunctionSingle.parse_response_grobro(unscrambled)
        if not (preset_response and device_id.startswith("QMN")):
            return False
        state = HomeAssistantHoldingRegisterInput(device_id=device_id)
        for name, register in KNOWN_NEO_REGISTERS.holding_registers.items():
            if register.growatt.position.register_no != preset_response.register_no:
                continue
            value = register.growatt.data.parse(preset_response.value.to_bytes(2, "big"))
            if name == "output_power_limit":
                value = _normalize_neo_output_power_limit(value)
            if value is not None:
                state.payload.append(
                    HomeAssistantHoldingRegisterValue(
                        name=name,
                        value=value,
                        register=register.homeassistant,
                    )
                )
            break
        if state.payload:
            self.on_holding_register_input(state)
        return True

    def _handle_modbus(self, device_id: str, unscrambled: bytes) -> bool:
        """Modbus register blocks (input and holding registers)."""
        modbus_message = GrowattModbusMessage.parse_grobro(unscrambled)
        LOG.debug("Received Modbus response: %s", modbus_message)
        if not modbus_message:
            return False

        # The LoRa inverter behind a ShineWeLink datalogger reports under the datalogger serial
        modbus_device_id = self._ptq_for_raq.get(device_id) or device_id
        self._stats.frames.record(modbus_device_id, unscrambled)
        known_registers = self._identify_device(modbus_device_id, modbus_message)
        if not known_registers:
            LOG.info("Modbus message from unknown device type: %s", device_id)
            self._request_device_type_code(modbus_device_id)
            return True

        if modbus_message.function in (
            GrowattModbusFunction.READ_SINGLE_REGISTER,
            GrowattModbusFunction.READ_HOLDING_REGISTER,
        ):
            self._publish_holding_registers(modbus_device_id, known_registers, modbus_message)

        if modbus_message.function == GrowattModbusFunction.READ_INPUT_REGISTER:
            self._publish_input_registers(device_id, modbus_device_id, known_registers, modbus_message)
        return True

    def _identify_device(self, modbus_device_id: str, modbus_message: GrowattModbusMessage):
        """Register map of a device. Unknown and generic dataloggers are identified by
        the serial of their inverter, then by its device type code."""
        known_registers = get_known_registers(modbus_device_id)
        unknown_or_generic = not known_registers or modbus_device_id.startswith(GENERIC_DATALOGGER_PREFIXES)
        if modbus_message.metadata and unknown_or_generic:
            detected = detect_device_by_inverter_serial(modbus_device_id, modbus_message.metadata.device_sn)
            if detected:
                known_registers = detected
                LOG.info(
                    "Detected %s inverter %s behind datalogger %s",
                    get_device_type_name(modbus_device_id),
                    modbus_message.metadata.device_sn,
                    modbus_device_id,
                )
        if modbus_message.metadata:
            remember_inverter_serial(modbus_device_id, modbus_message.metadata.device_sn)
        if modbus_message.function in (
            GrowattModbusFunction.READ_SINGLE_REGISTER,
            GrowattModbusFunction.READ_HOLDING_REGISTER,
        ) and (not known_registers or modbus_device_id.startswith(GENERIC_DATALOGGER_PREFIXES)):
            detected = detect_device_by_type_code(modbus_device_id, _device_type_code(modbus_message))
            if detected:
                known_registers = detected
                LOG.info(
                    "Detected %s by device type code behind datalogger %s",
                    get_device_type_name(modbus_device_id),
                    modbus_device_id,
                )
        return known_registers

    def _publish_holding_registers(self, modbus_device_id: str, known_registers, modbus_message: GrowattModbusMessage):
        state = HomeAssistantHoldingRegisterInput(device_id=modbus_device_id)
        for name, register in known_registers.holding_registers.items():
            data_raw = modbus_message.get_data(register.growatt.position)
            if data_raw is None:
                continue
            value = register.growatt.data.parse(data_raw)
            if value is None:
                continue
            if modbus_device_id.startswith("QMN") and name == "output_power_limit":
                value = _normalize_neo_output_power_limit(value)
            if register.homeassistant.type == "switch":
                value = "ON" if value == 1 else "OFF"
            state.payload.append(
                HomeAssistantHoldingRegisterValue(
                    name=name,
                    value=value,
                    register=register.homeassistant,
                )
            )
        self.on_holding_register_input(state)

    def _publish_input_registers(self, device_id: str, modbus_device_id: str, known_registers, modbus_message: GrowattModbusMessage):
        state = HomeAssistantInputRegister(device_id=modbus_device_id)
        for name, register in known_registers.input_registers.items():
            data_raw = modbus_message.get_data(register.growatt.position)
            value = register.growatt.data.parse(data_raw)
            # NEO inverters sometimes send broken messages at night with an incredibly high
            # wattage, which spoils the HA statistics. Nobody runs a balcony plant with more
            # than a million watts peak, so such messages are dropped.
            if name == "Ppv" and value > 1000000:
                LOG.debug("Dropping bad payload: %s", device_id)
                return
            state.payload[name] = value
        self.on_input_register(state)

    def _request_device_type_code(self, device_id: str):
        """Ask an unknown device once for its device type code (holding register 43)."""
        if device_id in self._type_code_requested:
            return
        self._type_code_requested.add(device_id)
        LOG.info("Requesting device type code of unknown device %s", device_id)
        self.send_command(
            GrowattModbusFunctionSingle(
                device_id=device_id,
                function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                register=DEVICE_TYPE_CODE_REGISTERS[0],
                value=DEVICE_TYPE_CODE_REGISTERS[0],
            )
        )


# Ensure that the dump directory exists
if settings.DUMP_MESSAGES and not os.path.exists(settings.DUMP_DIR):
    os.makedirs(settings.DUMP_DIR, exist_ok=True)
    LOG.info(f"Dump directory created: {settings.DUMP_DIR}")


def dump_message_binary(topic, payload):
    try:
        # Build path following topic structure
        topic_parts = topic.strip("/").split("/")
        dir_path = os.path.join(settings.DUMP_DIR, *topic_parts)
        os.makedirs(dir_path, exist_ok=True)

        # Write each message to a new file with timestamp
        import time

        timestamp = int(time.time() * 1000)
        file_path = os.path.join(dir_path, f"{timestamp}.bin")

        with open(file_path, "wb") as f:
            f.write(payload)
    except Exception as e:
        LOG.error(f"Failed to dump message for topic {topic}: {e}")


def _device_type_code(modbus_message: GrowattModbusMessage):
    """Return the device type code if the message contains register 43 or 30000."""
    for register_no in DEVICE_TYPE_CODE_REGISTERS:
        data = modbus_message.get_data(GrowattRegisterPosition(register_no=register_no, size=2))
        if data:
            return int.from_bytes(data, "big")
    return None


def get_property(msg, prop) -> str:
    props = msg.properties.json().get("UserProperty", [])
    for key, value in props:
        if key == prop:
            return value
    return None
