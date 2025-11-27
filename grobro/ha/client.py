from __future__ import annotations

import os
import ssl
import json
import logging
from threading import Timer
from typing import Callable, Optional

import paho.mqtt.client as mqtt

import grobro.model as model
from grobro.model.growatt_registers import (
    HomeAssistantInputRegister,
    HomeAssistantHoldingRegisterInput,
    GroBroRegisters,
    KNOWN_NEO_REGISTERS,
    KNOWN_NOAH_REGISTERS,
    KNOWN_NEXA_REGISTERS,
    KNOWN_SPF_REGISTERS
)
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.modbus_function import (
    GrowattModbusFunctionSingle,
    GrowattModbusFunctionMultiple,
)

HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", 0))
AVAILABILITY_SENSOR = os.getenv("AVAILABILITY_SENSOR", "False").lower() == "true"
PUBLISH_SENSORS_RETAINED = os.getenv("PUBLISH_SENSORS_RETAINED", "False").lower() == "true"
MAX_SLOTS = int(os.getenv("MAX_SLOTS", "1"))
LOG = logging.getLogger(__name__)


# ------------------- Helpfunctions -------------------

def get_known_registers(device_id: str) -> Optional[GroBroRegisters]:
    """Ermittle passende Register-Sammlung anhand device_id-Präfix."""
    if device_id.startswith("QMN"):
        return KNOWN_NEO_REGISTERS
    if device_id.startswith("0PVP"):
        return KNOWN_NOAH_REGISTERS
    if device_id.startswith("0HVR"):
        return KNOWN_NEXA_REGISTERS
    if device_id.startswith("HAQ"):
        return KNOWN_SPF_REGISTERS
    return None


def get_device_type_name(device_id: str) -> str:
    """Ermittle Klartext-Typname anhand der device_id."""
    if device_id.startswith("QMN"):
        return "NEO"
    if device_id.startswith("0PVP"):
        return "NOAH"
    if device_id.startswith("0HVR"):
        return "NEXA"
    if device_id.startswith("HAQ"):
        return "SPF"
    return "UNKNOWN"


def map_enum_value(reg, value):
    """Wandelt ENUM-INT_MAP-Werte in Klartext um (falls vorhanden)."""
    try:
        data = getattr(reg.growatt, "data", None)
        if not data or getattr(data, "data_type", None) != "ENUM":
            return value
        enum_opts = getattr(data, "enum_options", None)
        if not enum_opts or getattr(enum_opts, "enum_type", None) != "INT_MAP":
            return value
        return enum_opts.values.get(str(value), enum_opts.values.get(value, str(value)))
    except Exception as e:
        LOG.warning("Enum mapping failed for %s=%s: %s", reg, value, e)
        return value


def make_modbus_command(device_id: str, func: GrowattModbusFunction, register_no: int, value: Optional[int] = None) -> GrowattModbusFunctionSingle:
    """Erzeugt einen GrowattModbusFunctionSingle-Befehl."""
    return GrowattModbusFunctionSingle(
        device_id=device_id,
        function=func,
        register=register_no,
        value=value if value is not None else register_no,
    )


# ------------------- Client-Class -------------------

class Client:
    on_command: Optional[Callable[[GrowattModbusFunctionSingle], None]]

    _client: mqtt.Client
    _config_cache: dict[str, model.DeviceConfig] = {}
    _discovery_cache: list[str] = []
    _device_timers: dict[str, Timer] = {}

    def __init__(self, mqtt_config: model.MQTTConfig):
        # Setup target MQTT client for publishing
        LOG.info(f"Connecting to HA broker at '{mqtt_config.host}:{mqtt_config.port}'")
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="grobro-ha")
        if mqtt_config.username and mqtt_config.password:
            self._client.username_pw_set(mqtt_config.username, mqtt_config.password)
        if mqtt_config.use_tls:
            self._client.tls_set(cert_reqs=ssl.CERT_NONE)
            self._client.tls_insecure_set(True)
        self._client.connect(mqtt_config.host, mqtt_config.port, 60)

        # Subscriptions
        for cmd_type in ["number", "button", "switch"]:
            for action in ["set", "read"]:
                topic = f"{HA_BASE_TOPIC}/{cmd_type}/grobro/+/+/{action}"
                self._client.subscribe(topic)
        self._client.on_message = self.__on_message
        self._client.on_connect = self.__on_connect

        # Configs laden (Cache aus Dateien)
        for fname in os.listdir("."):
            if fname.startswith("config_") and fname.endswith(".json"):
                config = model.DeviceConfig.from_file(fname)
                if config:
                    self._config_cache[config.device_id] = config

        self._discovery_payload_cache: dict[str, str] = {}

    # ------------------- Lifecycle -------------------

    def start(self):
        self._client.loop_start()

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()

    # ------------------- Config Handling -------------------

    def set_config(self, config: model.DeviceConfig):
        device_id = config.serial_number
        config_path = f"config_{config.device_id}.json"
        existing_config = model.DeviceConfig.from_file(config_path)
        if existing_config is None or existing_config != config:
            LOG.info(f"Saving updated config for {config.device_id}")
            config.to_file(config_path)
        else:
            LOG.debug(f"No config change for {config.device_id}")
        self._config_cache[config.device_id] = config

        if device_id in self._discovery_cache:
            self._discovery_cache.remove(device_id)
        self.__publish_device_discovery(device_id)

    # ------------------- Publishing -------------------

    def publish_input_register(self, state: HomeAssistantInputRegister):
        LOG.debug("HA: publish: %s", state)
        # discovery + availability
        self.__publish_device_discovery(state.device_id)
        self.__publish_availability(state.device_id, True)
        if DEVICE_TIMEOUT > 0:
            self.__reset_device_timer(state.device_id)

        # ENUM Mapping
        payload = dict(state.payload)
        known_registers = get_known_registers(state.device_id)

        # Replace invalid battery temperatures (-273) with None
        if known_registers:
            for key, value in list(payload.items()):
                reg = known_registers.input_registers.get(key)
                if not reg:
                    continue

                # Identify batX_temp sensors
                name = key  # key == "bat1_temp", "bat2_temp", etc.
                if name.startswith("bat") and name.endswith("_temp"):
                    if isinstance(value, (int, float)) and value == -273.1:
                        payload[key] = None

        # ENUM Mapping (must come AFTER our replacement!)
        if known_registers:
            for key, value in list(payload.items()):
                reg = known_registers.input_registers.get(key)
                if reg:
                    payload[key] = map_enum_value(reg, value)

        # State publish
        topic = f"{HA_BASE_TOPIC}/grobro/{state.device_id}/state"
        self._client.publish(topic, json.dumps(payload, separators=(",", ":")), retain=PUBLISH_SENSORS_RETAINED)


    def publish_holding_register_input(self, ha_input: HomeAssistantHoldingRegisterInput):
        try:
            LOG.debug("HA: publish: %s", ha_input)
            for value in ha_input.payload:
                topic = f"{HA_BASE_TOPIC}/{value.register_def.type}/grobro/{ha_input.device_id}/{value.name}/get"
                self._client.publish(topic, value.value, retain=PUBLISH_SENSORS_RETAINED)
        except Exception as e:
            LOG.error(f"HA: publish msg: {e}")

    # ------------------- MQTT Callback -------------------

    def __on_connect(self, client, userdata, flags, reason_code, properties):
        LOG.debug(f"Connected to HA MQTT server with result code {reason_code}")

    def __on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        parts = msg.topic.removeprefix(f"{HA_BASE_TOPIC}/").split("/")
        if len(parts) != 5 or parts[0] not in {"number", "button", "switch"}:
            return
        cmd_type, _, device_id, cmd_name, action = parts

        LOG.debug("Received %s %s command %s for device %s", cmd_type, action, cmd_name, device_id)

        known_registers = get_known_registers(device_id)
        if not known_registers:
            LOG.info("Unknown device type: %s", device_id)
            return

        # Buttons
        if cmd_type == "button":
            if cmd_name == "read_all":
                for name, register in known_registers.holding_registers.items():
                    if name.startswith("slot"):
                        try:
                            if int(name[4]) > MAX_SLOTS:
                                continue
                        except ValueError:
                            continue
                    pos = register.growatt.position
                    self.on_command(make_modbus_command(
                        device_id, GrowattModbusFunction.READ_SINGLE_REGISTER, pos.register_no
                    ))
                return

            if action == "read":
                pos = known_registers.holding_registers[cmd_name].growatt.position
                self.on_command(make_modbus_command(
                    device_id, GrowattModbusFunction.READ_SINGLE_REGISTER, pos.register_no
                ))
                return

        # Number / Switch
        if cmd_type in {"number", "switch"} and action == "set":
            raw_value = msg.payload.decode()
            if cmd_type == "switch":
                parsed_value = 1 if raw_value.upper() == "ON" else 0
            elif "_start_time" in cmd_name or "_end_time" in cmd_name:
                hour, minute = divmod(int(raw_value), 100)
                parsed_value = (hour * 256) + minute
            else:
                parsed_value = int(raw_value)

            pos = known_registers.holding_registers[cmd_name].growatt.position
            LOG.debug("Setting %s register %s to value %s", cmd_name, pos.register_no, parsed_value)

            # write
            self.on_command(make_modbus_command(
                device_id, GrowattModbusFunction.PRESET_SINGLE_REGISTER, pos.register_no, parsed_value
            ))
            # read-after-write
            LOG.debug("Triggering read-after-write for Command %s register %s", cmd_name, pos.register_no)
            self.on_command(make_modbus_command(
                device_id, GrowattModbusFunction.READ_SINGLE_REGISTER, pos.register_no
            ))

    # ------------------- Internals -------------------

    def __reset_device_timer(self, device_id: str):
        def set_device_unavailable(d_id: str):
            LOG.warning("Device %s timed out. Mark it as unavailable.", d_id)
            self.__publish_availability(d_id, False)

        if device_id in self._device_timers:
            self._device_timers[device_id].cancel()

        timer = Timer(DEVICE_TIMEOUT, set_device_unavailable, args=[device_id])
        self._device_timers[device_id] = timer
        timer.start()

    def __publish_availability(self, device_id: str, online: bool):
        LOG.debug("Set device %s availability: %s", device_id, online)
        if (not online and not AVAILABILITY_SENSOR) or online:
            self._client.publish(
                f"{HA_BASE_TOPIC}/grobro/{device_id}/availability",
                "online" if online else "offline",
                retain=True,
            )
        if (AVAILABILITY_SENSOR):
            self._client.publish(
                f"{HA_BASE_TOPIC}/grobro/{device_id}/online",
                "ON" if online else "OFF",
                retain=PUBLISH_SENSORS_RETAINED,
            )

    def __publish_device_discovery(self, device_id: str):
        known_registers = get_known_registers(device_id)
        if not known_registers:
            LOG.info("Unable to publish unknown device type: %s", device_id)
            return

        self.__migrate_entity_discovery(device_id, known_registers)

        topic = f"{HA_BASE_TOPIC}/device/{device_id}/config"

        # prepare discovery payload
        payload: dict = {
            "dev": self.__device_info_from_config(device_id),
            "avty_t": f"{HA_BASE_TOPIC}/grobro/{device_id}/availability",
            "o": {"name": "grobro", "url": "https://github.com/robertzaage/GroBro"},
            "cmps": {},
        }

        # Commands
        for cmd_name, cmd in known_registers.holding_registers.items():
            if not cmd.homeassistant.publish:
                continue

            if cmd_name.startswith("slot"):
                try:
                    if int(cmd_name[4]) > MAX_SLOTS:
                        continue
                except ValueError:
                    continue

            unique_id = f"grobro_{device_id}_cmd_{cmd_name}"
            cmd_type = cmd.homeassistant.type
            payload["cmps"][unique_id] = {
                "command_topic": f"{HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}/{cmd_name}/set",
                "state_topic": f"{HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}/{cmd_name}/get",
                "platform": cmd_type,
                "unique_id": unique_id,
                **cmd.homeassistant.dict(exclude_none=True),
            }

        # Read-All Button
        payload["cmps"][f"grobro_{device_id}_cmd_read_all"] = {
            "command_topic": f"{HA_BASE_TOPIC}/button/grobro/{device_id}/read_all/read",
            "platform": "button",
            "unique_id": f"grobro_{device_id}_cmd_read_all",
            "name": "Read All Values",
        }

        # States
        for state_name, state in known_registers.input_registers.items():
            if not state.homeassistant.publish:
                continue
            unique_id = f"grobro_{device_id}_{state_name}"
            payload["cmps"][unique_id] = {
                "platform": "sensor",
                "name": state.homeassistant.name,
                "state_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/state",
                "value_template": f"{{{{ value_json['{state_name}'] }}}}",
                "unique_id": unique_id,
                "device_class": state.homeassistant.device_class,
                "state_class": state.homeassistant.state_class,
                "unit_of_measurement": state.homeassistant.unit_of_measurement,
                "icon": state.homeassistant.icon,
            }

        # Serial Number Entity
        serial_unique_id = f"grobro_{device_id}_serial"
        payload["cmps"][serial_unique_id] = {
            "platform": "sensor",
            "name": "Device SN",
            "state_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/serial",
            "unique_id": serial_unique_id,
            "icon": "mdi:identifier",
        }

        # Device Type Entity
        type_unique_id = f"grobro_{device_id}_type"
        payload["cmps"][type_unique_id] = {
            "platform": "sensor",
            "name": "Device Type",
            "state_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/type",
            "unique_id": type_unique_id,
            "icon": "mdi:chip",
        }

        # Online Entity
        if DEVICE_TIMEOUT > 0 and AVAILABILITY_SENSOR:
            online_unique_id = f"grobro_{device_id}_online"
            payload["cmps"][online_unique_id] = {
                "platform": "binary_sensor",
                "name": "Online",
                "state_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/online",
                "device_class": "connectivity",
                "unique_id": online_unique_id,
            }

        payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        if self._discovery_payload_cache.get(device_id) == payload_str:
            LOG.debug("Discovery unchanged for %s, skipping", device_id)
            if device_id not in self._discovery_cache:
                self._discovery_cache.append(device_id)
            # trotzdem States aktualisieren
            self._client.publish(f"{HA_BASE_TOPIC}/grobro/{device_id}/serial", device_id, retain=True)
            self._client.publish(f"{HA_BASE_TOPIC}/grobro/{device_id}/type", get_device_type_name(device_id), retain=True)
            return

        LOG.info("Publishing updated discovery for %s", device_id)
        self._client.publish(topic, "", retain=True)  # force HA to refresh
        self._client.publish(topic, payload_str, retain=True)
        self._discovery_payload_cache[device_id] = payload_str
        if device_id not in self._discovery_cache:
            self._discovery_cache.append(device_id)

        self._client.publish(f"{HA_BASE_TOPIC}/grobro/{device_id}/serial", device_id, retain=True)
        self._client.publish(f"{HA_BASE_TOPIC}/grobro/{device_id}/type", get_device_type_name(device_id), retain=True)

    def __migrate_entity_discovery(self, device_id: str, known_registers: GroBroRegisters):
        old_entities = [("set_wirk", "number")]
        for e_name, e_type in old_entities:
            self._client.publish(
                f"{HA_BASE_TOPIC}/{e_type}/grobro/{device_id}_{e_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )
        for cmd_name, cmd in known_registers.holding_registers.items():
            cmd_type = cmd.homeassistant.type
            self._client.publish(
                f"{HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}_{cmd_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )
            self._client.publish(
                f"{HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}_{cmd_name}_read/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )
        for state_name in known_registers.input_registers:
            self._client.publish(
                f"{HA_BASE_TOPIC}/sensor/grobro/{device_id}_{state_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )

    def __device_info_from_config(self, device_id: str):
        # Find matching config
        config = self._config_cache.get(device_id)
        config_path = f"config_{device_id}.json"

        # Fallback: try loading from file
        if not config:
            config = model.DeviceConfig.from_file(config_path)
            self._config_cache[device_id] = config
            LOG.info(f"Loaded cached config for {device_id} from file (fallback)")

        # Fallback 2: save minimal config if it was neither in cache nor on disk
        if not config:
            config = model.DeviceConfig(serial_number=device_id)
            config.to_file(config_path)
            self._config_cache[device_id] = config
            LOG.info(f"Saved minimal config for new device: {config}")

        # Device Info for HA
        device_info: dict = {
            "identifiers": [device_id],
            "name": f"Growatt {device_id}",
            "manufacturer": "Growatt",
            "serial_number": device_id,
        }

        type_name = get_device_type_name(device_id)

        known_model_id = {
            "55": "NEO-series",
            "72": "NEXA-series",
            "61": "NOAH-series",
        }.get(getattr(config, "device_type", None))

        if known_model_id:
            device_info["model"] = known_model_id
        else:
            device_info["model"] = f"{type_name}-series"

        if getattr(config, "model_id", None):
            device_info["model"] += f" ({config.model_id})"
        if getattr(config, "sw_version", None):
            device_info["sw_version"] = config.sw_version
        if getattr(config, "hw_version", None):
            device_info["hw_version"] = config.hw_version
        if getattr(config, "mac_address", None):
            device_info["connections"] = [["mac", config.mac_address]]

        return device_info
