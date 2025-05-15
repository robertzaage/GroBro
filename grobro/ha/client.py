from grobro.model.neo_messages import NeoOutputPowerLimit
import os
import ssl
import json
import logging
import grobro.model as model
import importlib.resources as resources
from threading import Timer
from typing import Callable

import paho.mqtt.client as mqtt
from grobro.model.neo_command import NeoReadOutputPowerLimit
from grobro.model.neo_command import NeoSetOutputPowerLimit
from grobro.model.noah_command import NoahSmartPower
from grobro.model.neo_command import NeoCommandTypes
from grobro.model.noah_command import NoahCommandTypes

HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", 0))
LOG = logging.getLogger(__name__)


class Client:
    on_command: None | Callable[model.Command, None]

    _client: mqtt.Client

    _config_cache: dict[str, model.DeviceConfig] = {}
    _discovery_cache: dict[str, list[str]] = {}
    _device_timers: dict[str, Timer] = {}
    # device_type -> variable_name -> DeviceState
    _known_states: dict[str, dict[str, model.DeviceState]] = {}
    # device_type -> variable_name -> Command
    _known_commands: dict[str, dict[str, dict]] = {}

    def __init__(
        self,
        mqtt_config: model.MQTTConfig,
    ):

        # Load possible device states
        for device_type in ["neo", "noah"]:
            self._known_states[device_type] = {}
            states_file = resources.files(__package__).joinpath(
                f"growatt_{device_type}_states.json"
            )
            with states_file.open("r") as f:
                for variable_name, state in json.load(f).items():
                    if " " in variable_name:
                        LOG.warning("State '%s' contains illegal whitespace")
                    self._known_states[device_type][variable_name] = model.DeviceState(
                        variable_name=variable_name, **state
                    )
            self._known_commands[device_type] = {}
            cmd_file = resources.files(__package__).joinpath(
                f"growatt_{device_type}_commands.json"
            )
            with cmd_file.open("r") as f:
                for variable_name, cmd in json.load(f).items():
                    self._known_commands[device_type][variable_name] = cmd
        # Setup target MQTT client for publishing
        LOG.info(f"Connecting to MQTT broker at '{mqtt_config.host}:{mqtt_config.port}'")
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="grobro-ha"
        )
        if mqtt_config.username and mqtt_config.password:
            self._client.username_pw_set(mqtt_config.username, mqtt_config.password)
        if mqtt_config.use_tls:
            self._client.tls_set(cert_reqs=ssl.CERT_NONE)
            self._client.tls_insecure_set(True)
        self._client.connect(mqtt_config.host, mqtt_config.port, 60)

        for cmd_type in ["number", "button"]:
            topic = f"{HA_BASE_TOPIC}/{cmd_type}/grobro/+/+/set"
            self._client.subscribe(topic)
        self._client.on_message = self.__on_message

        for fname in os.listdir("."):
            if fname.startswith("config_") and fname.endswith(".json"):
                config = model.DeviceConfig.from_file(fname)
                if config:
                    self._config_cache[config.device_id] = config

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

    def publish_discovery(self, device_id: str, ha: model.DeviceState):
        if ha.variable_name in self._discovery_cache.get(device_id, []):
            return  # already published

        topic = f"{HA_BASE_TOPIC}/sensor/grobro/{device_id}_{ha.variable_name}/config"
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
        payload = {
            "name": ha.name,
            "state_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/state",
            "availability_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/availability",
            "value_template": f"{{{{ value_json['{ha.variable_name}'] }}}}",
            "unique_id": f"grobro_{device_id}_{ha.variable_name}",
            "object_id": f"{device_id}_{ha.variable_name}",
            "device": device_info,
            "device_class": ha.device_class,
            "state_class": ha.state_class,
            "unit_of_measurement": ha.unit_of_measurement,
            "icon": ha.icon,
        }
        self._client.publish(topic, json.dumps(payload), retain=True)
        if device_id not in self._discovery_cache:
            self._discovery_cache[device_id] = []
        self._discovery_cache[device_id].append(ha.variable_name)

    def publish_message(self, msg):
        try:
            LOG.debug("ha: publish: %s", msg)
            if isinstance(msg, NeoOutputPowerLimit):
                msg_type = NeoCommandTypes.OUTPUT_POWER_LIMIT
                topic = f"{HA_BASE_TOPIC}/{msg_type.ha_type}/grobro/{msg.device_id}/{msg_type.ha_name}/get"
                LOG.debug(
                    "forward message: %s to %s: %s", type(msg).__name__, topic, msg
                )
                self._client.publish(topic, msg.value, retain=False)
        except Exception as e:
            LOG.error(f"ha: publish msg: {e}")

    def publish_state(self, device_id, state):
        try:
            # Update state
            topic = f"{HA_BASE_TOPIC}/grobro/{device_id}/state"
            self._client.publish(topic, json.dumps(state), retain=False)

            if DEVICE_TIMEOUT > 0:
                self.__reset_device_timer(device_id)
            # Update availability
            self.__publish_availability(device_id, True)

            if device_id.startswith("QMN"):
                device_type = "neo"
            if device_id.startswith("0PVP"):
                device_type = "noah"
            state_lookup = self._known_states[device_type]

            for variable_name in state.keys():
                state = state_lookup[variable_name]
                self.publish_discovery(device_id, state)

            self.__publish_commands(device_id, device_type)
        except Exception as e:
            LOG.error(f"Publish device state: {e}")

    def __on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        parts = msg.topic.removeprefix(f"{HA_BASE_TOPIC}/").split("/")

        cmd_type, device_id, cmd_name = None, None, None
        if len(parts) == 5 and parts[0] in ["number"]:
            cmd_type, _, device_id, cmd_name, _ = parts
        if len(parts) == 5 and parts[0] in ["button"]:
            cmd_type, _, device_id, cmd_name, _ = parts

        LOG.debug(
            "received %s command %s for device %s",
            cmd_type,
            cmd_name,
            device_id,
        )

        cmd = None
        for noah_cmd_type in NoahCommandTypes:
            if noah_cmd_type.matches(cmd_name, cmd_type):
                cmd = noah_cmd_type.parse_ha(device_id, msg.payload)
                break
        for neo_cmd_type in NeoCommandTypes:
            if neo_cmd_type.matches(cmd_name, cmd_type):
                cmd = neo_cmd_type.model.parse_ha(device_id, msg.payload)
                break

        if cmd and self.on_command:
            self.on_command(cmd)
        else:
            LOG.warning(
                "received unknown command %s: %s",
                msg.topic,
                msg.payload,
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
        self._client.publish(
            f"{HA_BASE_TOPIC}/grobro/{device_id}/availability",
            "online" if online else "offline",
            retain=False,
        )

    def __publish_commands(self, device_id, device_type):
        for cmd_name, cmd in self._known_commands[device_type].items():
            if cmd_name in self._discovery_cache.get(device_id, []):
                continue  # already published
            cmd_type = cmd["type"]
            cmd_full = {
                "command_topic": f"{HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}/{cmd_name}/set",
                "state_topic": f"{HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}/{cmd_name}/get",
                "unique_id": f"grobro_{device_id}_cmd_{cmd_name}",
                "object_id": f"{device_id}_cmd_{cmd_name}",
                "device": {
                    "identifiers": [device_id],
                },
                **cmd,
            }
            LOG.debug("announce command %s: %s", cmd_name, cmd_full)
            self._client.publish(
                f"{HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}_{cmd_name}/config",
                json.dumps(cmd_full),
                retain=True,
            )
            if device_id not in self._discovery_cache:
                self._discovery_cache[device_id] = []
            self._discovery_cache[device_id].append(cmd_name)
