from grobro.model.growatt_registers import HomeAssistantInputRegister
from grobro.model.growatt_registers import KNOWN_NEO_REGISTERS, KNOWN_NOAH_REGISTERS, KNOWN_NEXA_REGISTERS
import os
import struct
import ssl
import json
import logging
import grobro.model as model
import importlib.resources as resources
from threading import Timer
from typing import Callable

import paho.mqtt.client as mqtt
from grobro.model.growatt_registers import HomeAssistantHoldingRegisterInput
from grobro.model.growatt_registers import GroBroRegisters
from typing import Optional
from grobro.model.modbus_function import GrowattModbusFunctionSingle
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.modbus_function import GrowattModbusFunctionMultiple

HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", 0))
MAX_SLOTS = int(os.getenv("MAX_SLOTS", "1"))
LOG = logging.getLogger(__name__)


class Client:
    on_command: Optional[Callable[GrowattModbusFunctionSingle, None]]

    _client: mqtt.Client
    _config_cache: dict[str, model.DeviceConfig] = {}
    _discovery_cache: list[str] = []
    _device_timers: dict[str, Timer] = {}

    def __init__(
        self,
        mqtt_config: model.MQTTConfig,
    ):
        # Setup target MQTT client for publishing
        LOG.info(f"Connecting to HA broker at '{mqtt_config.host}:{mqtt_config.port}'")
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="grobro-ha"
        )
        if mqtt_config.username and mqtt_config.password:
            self._client.username_pw_set(mqtt_config.username, mqtt_config.password)
        if mqtt_config.use_tls:
            self._client.tls_set(cert_reqs=ssl.CERT_NONE)
            self._client.tls_insecure_set(True)
        self._client.connect(mqtt_config.host, mqtt_config.port, 60)

        for cmd_type in ["number", "button", "switch"]:
            for action in ["set", "read"]:
                topic = f"{HA_BASE_TOPIC}/{cmd_type}/grobro/+/+/{action}"
                self._client.subscribe(topic)
        self._client.on_message = self.__on_message

        for fname in os.listdir("."):
            if fname.startswith("config_") and fname.endswith(".json"):
                config = model.DeviceConfig.from_file(fname)
                if config:
                    self._config_cache[config.device_id] = config

        self._discovery_payload_cache: dict[str, str] = {}

    def start(self):
        self._client.loop_start()

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()

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

    def publish_input_register(self, state: HomeAssistantInputRegister):
        LOG.debug("HA: publish: %s", state)
        # publish discovery
        self.__publish_device_discovery(state.device_id)
        # update availability
        self.__publish_availability(state.device_id, True)
        if DEVICE_TIMEOUT > 0:
            self.__reset_device_timer(state.device_id)

        # --- NEU: ENUM INT_MAP → Klartext mappen ---------------------------------
        payload = dict(state.payload)  # shallow copy
        # passende Register-Definitionen ermitteln (anhand device_id-Präfix)
        known_registers: Optional[GroBroRegisters] = None
        try:
            if state.device_id.startswith("QMN"):
                known_registers = KNOWN_NEO_REGISTERS
            elif state.device_id.startswith("0PVP"):
                known_registers = KNOWN_NOAH_REGISTERS
            elif state.device_id.startswith("0HVR"):
                known_registers = KNOWN_NEXA_REGISTERS
        except Exception:
            known_registers = None

        if known_registers:
            for key, value in list(payload.items()):
                try:
                    reg = known_registers.input_registers.get(key)
                    if not reg:
                        continue

                    # helper, um sowohl Dict- als auch Objektzugriffe zu unterstützen
                    def _get(obj, name, default=None):
                        try:
                            return getattr(obj, name)
                        except Exception:
                            try:
                                return obj.get(name, default)  # type: ignore[attr-defined]
                            except Exception:
                                return default

                    data = _get(reg.growatt, "data")
                    data_type = _get(data, "data_type")
                    enum_options = _get(data, "enum_options")
                    enum_type = _get(enum_options, "enum_type")
                    values_map = _get(enum_options, "values", {})

                    if data_type == "ENUM" and enum_type == "INT_MAP" and values_map:
                        # keys sind in der Map Strings; fallback auf Originalwert
                        mapped = values_map.get(str(value), values_map.get(value, str(value)))
                        payload[key] = mapped
                except Exception as e:
                    LOG.warning("HA: enum mapping failed for %s=%s: %s", key, value, e)
        # -------------------------------------------------------------------------

        # update state
        topic = f"{HA_BASE_TOPIC}/grobro/{state.device_id}/state"
        self._client.publish(topic, json.dumps(payload), retain=False)

    def publish_holding_register_input(
        self, ha_input: HomeAssistantHoldingRegisterInput
    ):
        try:
            LOG.debug("HA: publish: %s", ha_input)
            for value in ha_input.payload:
                topic = f"{HA_BASE_TOPIC}/{value.register.type}/grobro/{ha_input.device_id}/{value.name}/get"
                self._client.publish(topic, value.value, retain=False)
        except Exception as e:
            LOG.error(f"HA: publish msg: {e}")

    def __on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        parts = msg.topic.removeprefix(f"{HA_BASE_TOPIC}/").split("/")
        cmd_type, device_id, cmd_name, action = None, None, None, None
        if len(parts) == 5 and parts[0] in ["number"]:
            cmd_type, _, device_id, cmd_name, action = parts
        if len(parts) == 5 and parts[0] in ["button"]:
            cmd_type, _, device_id, cmd_name, action = parts
        if len(parts) == 5 and parts[0] in ["switch"]:
            cmd_type, _, device_id, cmd_name, action = parts

        LOG.debug(
            "Received %s %s command %s for device %s",
            cmd_type,
            action,
            cmd_name,
            device_id,
        )

        known_registers: Optional[GroBroRegisters] = None
        if device_id.startswith("QMN"):
            known_registers = KNOWN_NEO_REGISTERS
        elif device_id.startswith("0PVP"):
            known_registers = KNOWN_NOAH_REGISTERS
        elif device_id.startswith("0HVR"):
            known_registers = KNOWN_NEXA_REGISTERS
        if not known_registers:
            LOG.info("Unknown device type: %s", device_id)
            return

        if cmd_type == "button" and cmd_name == "read_all":
            for name, register in known_registers.holding_registers.items():
                if name.startswith("slot"):
                    try:
                        slot_num = int(name[4])
                        if slot_num > MAX_SLOTS:
                            continue
                    except ValueError:
                        continue
                pos = register.growatt.position
                self.on_command(
                    GrowattModbusFunctionSingle(
                        device_id=device_id,
                        function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                        register=pos.register_no,
                        value=pos.register_no,
                    )
                )
            return
        if cmd_type == "button" and action == "read":
            pos = known_registers.holding_registers[cmd_name].growatt.position
            self.on_command(
                GrowattModbusFunctionSingle(
                    device_id=device_id,
                    function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                    register=pos.register_no,
                    value=pos.register_no,
                )
            )

        if (cmd_type == "number" or cmd_type == "switch") and action == "set":

            if cmd_type == "switch":
                parsed_value = 1 if msg.payload.decode().upper() == "ON" else 0
            elif "_start_time" in cmd_name or "_end_time" in cmd_name:
                hour = int(msg.payload.decode()) // 100
                minute = int(msg.payload.decode()) % 100
                parsed_value = (hour * 256) + minute
            else:
                parsed_value = int(msg.payload.decode())

            pos = known_registers.holding_registers[cmd_name].growatt.position
            LOG.debug("Setting %s register %s to value %s", cmd_name, pos.register_no, parsed_value)
            self.on_command(
                GrowattModbusFunctionSingle(
                    device_id=device_id,
                    function=GrowattModbusFunction.PRESET_SINGLE_REGISTER,
                    register=pos.register_no,
                    value=parsed_value,
                )
            )
            # Todo , if we can parse msg_type 37 , read is no longer needed
            LOG.debug("Triggering read-after-write for Command %s register %s", cmd_name, pos.register_no)
            self.on_command(
                GrowattModbusFunctionSingle(
                    device_id=device_id,
                    function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                    register=pos.register_no,
                    value=pos.register_no,  # Wie Sie bestätigt haben, muss hier die Registernummer stehen
                )
            )

    # Reset the timeout timer for a device.
    def __reset_device_timer(self, device_id):
        def set_device_unavailable(device_id):
            LOG.warning("Device %s timed out. Mark it as unavailable.", device_id)
            self.__publish_availability(device_id, False)

        if device_id in self._device_timers:
            self._device_timers[device_id].cancel()  # Cancel the existing timer

        timer = Timer(
            DEVICE_TIMEOUT,
            set_device_unavailable,
            args=[device_id],
        )  # Pass function reference and arguments
        self._device_timers[device_id] = timer
        timer.start()

    def __publish_availability(self, device_id, online: bool):
        LOG.debug("Set device %s availability: %s", device_id, online)
        self._client.publish(
            f"{HA_BASE_TOPIC}/grobro/{device_id}/availability",
            "online" if online else "offline",
            retain=False,
        )

    def __publish_device_discovery(self, device_id):
        known_registers: Optional[GroBroRegisters] = None
        if device_id.startswith("QMN"):
            known_registers = KNOWN_NEO_REGISTERS
        elif device_id.startswith("0PVP"):
            known_registers = KNOWN_NOAH_REGISTERS
        elif device_id.startswith("0HVR"):
            known_registers = KNOWN_NEXA_REGISTERS
        if not known_registers:
            LOG.info("Unable to publish unknown device type: %s", device_id)
            return

        self.__migrate_entity_discovery(device_id, known_registers)

        topic = f"{HA_BASE_TOPIC}/device/{device_id}/config"

        # prepare discovery payload
        payload = {
            "dev": self.__device_info_from_config(device_id),
            "avty_t": f"{HA_BASE_TOPIC}/grobro/{device_id}/availability",
            "o": {
                "name": "grobro",
                "url": "https://github.com/robertzaage/GroBro",
            },
            "cmps": {},
        }

        for cmd_name, cmd in known_registers.holding_registers.items():
            if not cmd.homeassistant.publish:
                continue

            if cmd_name.startswith("slot"):
                try:
                    slot_num = int(cmd_name[4])
                    if slot_num > MAX_SLOTS:
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

        payload["cmps"][f"grobro_{device_id}_cmd_read_all"] = {
            "command_topic": f"{HA_BASE_TOPIC}/button/grobro/{device_id}/read_all/read",
            "platform": "button",
            "unique_id": f"grobro_{device_id}_cmd_read_all",
            "name": "Read All Values",
        }

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
                "object_id": f"{device_id}_{state_name}",
                "device_class": state.homeassistant.device_class,
                "state_class": state.homeassistant.state_class,
                "unit_of_measurement": state.homeassistant.unit_of_measurement,
                "icon": state.homeassistant.icon,
            }

        payload_str = json.dumps(payload, sort_keys=True)

        if self._discovery_payload_cache.get(device_id) == payload_str:
            LOG.debug("Discovery unchanged for %s, skipping", device_id)
            self._discovery_cache.append(device_id)
            return

        LOG.info("Publishing updated discovery for %s", device_id)
        self._client.publish(topic, "", retain=True)
        self._client.publish(topic, payload_str, retain=True)
        self._discovery_payload_cache[device_id] = payload_str
        self._discovery_cache.append(device_id)

    def __migrate_entity_discovery(self, device_id, knwon_registers: GroBroRegisters):
        old_entities = [
            ("set_wirk", "number"),
        ]
        for e_name, e_type in old_entities:
            self._client.publish(
                f"{HA_BASE_TOPIC}/{e_type}/grobro/{device_id}_{e_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )
        for cmd_name, cmd in knwon_registers.holding_registers.items():
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
        for state_name, state in knwon_registers.input_registers.items():
            self._client.publish(
                f"{HA_BASE_TOPIC}/sensor/grobro/{device_id}_{state_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )

    def __device_info_from_config(self, device_id):
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

        device_info = {
            "identifiers": [device_id],
            "name": f"Growatt {device_id}",
            "manufacturer": "Growatt",
            "serial_number": device_id,
        }
        known_model_id = {
            "55": "NEO-series",
            "72": "NEXA-series",
            "61": "NOAH-series",
        }.get(config.device_type)

        if known_model_id:
            device_info["model"] = known_model_id
        elif config.model_id:
            device_info["model"] = config.model_id
        if config.sw_version:
            device_info["sw_version"] = config.sw_version
        if config.hw_version:
            device_info["hw_version"] = config.hw_version
        if config.mac_address:
            device_info["connections"] = [["mac", config.mac_address]]

        return device_info
