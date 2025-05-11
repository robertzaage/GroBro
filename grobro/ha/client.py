import paho.mqtt.client as mqtt
import os
import json
import logging
import grobro.model as model
import importlib.resources as resources
from threading import Timer

HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", 0))
LOG = logging.getLogger(__name__)


class Client:
    _client: mqtt.Client
    _aliases: dict[str, model.DeviceAlias]

    _config_cache: dict[str, model.DeviceConfig] = {}
    _discovery_cache: dict[str, list[str]] = {}
    _device_timers: dict[str, Timer] = {}
    # device_type -> variable_name -> DeviceState
    _known_states: dict[str, dict[str, model.DeviceState]] = {}

    def __init__(
        self,
        mqtt_config: model.MQTTConfig,
        aliases: dict[str, model.DeviceAlias],
    ):
        self._aliases = aliases

        # load possible device states
        for device_type in ["inverter", "noah"]:
            self._known_states[device_type] = {}
            file = resources.files(__package__).joinpath(
                f"growatt_{device_type}_states.json"
            )
            with file.open("r") as f:
                for variable_name, state in json.load(f).items():
                    if " " in variable_name:
                        LOG.warning("sttate '%s' contains illegal whitespace")
                    self._known_states[device_type][variable_name] = model.DeviceState(
                        variable_name=variable_name, **state
                    )

        # Setup target MQTT client for publishing
        LOG.info(f"connecting to HA mqtt '{mqtt_config.host}:{mqtt_config.port}'")
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="grobro-ha"
        )
        if mqtt_config.username and mqtt_config.password:
            self._client.username_pw_set(mqtt_config.username, mqtt_config.password)
        if mqtt_config.use_tls:
            self._client.tls_set(cert_reqs=ssl.CERT_NONE)
            self._client.tls_insecure_set(True)
        self._client.connect(mqtt_config.host, mqtt_config.port, 60)

        for fname in os.listdir("."):
            if fname.startswith("config_") and fname.endswith(".json"):
                config = model.DeviceConfig.from_file(fname)
                if config:
                    self._config_cache[config.device_id] = config

    def start(self):
        self._client.subscribe("c/#")
        self._client.loop_start()

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()

    def set_config(self, config: model.DeviceConfig):
        device_id = config.serial_number
        config_path = f"config_{config.device_id}.json"
        existing_config = model.DeviceConfig.from_file(config_path)
        if existing_config is None or existing_config != config:
            LOG.info(f"save updated config for {config.device_id}")
            config.to_file(config_path)
        else:
            LOG.debug(f"no config change for {config.device_id}")
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
            LOG.info(f"saved minimal config for unknown device: {config}")

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

    def publish_state(self, device_id, state):
        try:
            # update state
            topic = f"{HA_BASE_TOPIC}/grobro/{device_id}/state"
            self._client.publish(topic, json.dumps(state), retain=False)

            if DEVICE_TIMEOUT > 0:
                self.__reset_device_timer(device_id)
            # update availability
            self.__publish_availability(device_id, True)

            device_type = "inverter"
            if "noah" == self._aliases.get(device_id, "").lower():
                device_type = "noah"
            state_lookup = self._known_states[device_type]

            for variable_name in state.keys():
                state = state_lookup[variable_name]
                self.publish_discovery(device_id, state)
        except Exception as e:
            LOG.error(f"ha: publish state: {e}")

    # Reset the timeout timer for a device.
    def __reset_device_timer(self, device_id):
        def set_device_unavailable(device_id):
            LOG.warning("Device %s timed out. Setting to unavailable.", device_id)
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
