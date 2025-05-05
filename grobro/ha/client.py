import paho.mqtt.client as mqtt
import os
import json
import logging
import grobro.model as model

HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
LOG = logging.getLogger(__name__)


class Client:
    client: mqtt.Client
    config_cache: dict[str, model.DeviceConfig] = {}
    discovery_cache: dict[str, list[str]] = {}

    def __init__(
        self, host: str, port: str, tls: bool, user: str | None, password: str | None
    ):
        # Setup target MQTT client for publishing
        LOG.info(f"connecting to HA mqtt '{host}:{port}'")
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="grobro-ha"
        )
        if user and password:
            self.client.username_pw_set(user, password)
        if tls:
            self.client.tls_set(cert_reqs=ssl.CERT_NONE)
            self.client.tls_insecure_set(True)
        self.client.connect(host, port, 60)
        self.client.loop_start()

        for fname in os.listdir("."):
            if fname.startswith("config_") and fname.endswith(".json"):
                config = model.DeviceConfig.from_file(fname)
                if config:
                    self.config_cache[config.device_id] = config

    def set_config(self, config: model.DeviceConfig):
        device_id = config.serial_number
        config_path = f"config_{config.device_id}.json"
        existing_config = model.DeviceConfig.from_file(config_path)
        if existing_config is None or existing_config != config:
            LOG.info(f"save updated config for {config.device_id}")
            config.to_file(config_path)
        else:
            LOG.debug(f"no config change for {config.device_id}")
        self.config_cache[config.device_id] = config

    def publish_discovery(self, device_id: str, variable: str, ha: model.DeviceState):
        if ha.name in self.discovery_cache.get(device_id, []):
            return # already published

        topic = f"{HA_BASE_TOPIC}/sensor/grobro/{device_id}_{variable}/config"
        # Find matching config
        config = self.config_cache.get(device_id)
        config_path = f"config_{device_id}.json"
        # Fallback: try loading from file
        if not config:
            config = model.DeviceConfig.from_file(config_path)
            self.config_cache[device_id] = config
            LOG.info(f"Loaded cached config for {device_id} from file (fallback)")
        # Fallback 2: save minimal config if it was neither in cache nor on disk
        if not config:
            config = model.DeviceConfig(serial_number=device_id)
            config.to_file(config_path)
            self.config_cache[device_id] = config
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
            "value_template": f"{{{{ value_json['{variable}'] }}}}",
            "unique_id": f"grobro_{device_id}_{variable}",
            "object_id": f"{device_id}_{variable}",
            "device": device_info,
        }
        for key in ["device_class", "state_class", "unit_of_measurement", "icon"]:
            if key in ha:
                payload[key] = ha[key]
        self.client.publish(topic, json.dumps(payload), retain=True)
        if device_id not in self.discovery_cache:
            self.discovery_cache[device_id] = []
        self.discovery_cache[device_id].append(ha.name) 

    def publish_state(self, device_id, state):
        topic = f"{HA_BASE_TOPIC}/grobro/{device_id}/state"
        self.client.publish(topic, json.dumps(state), retain=False)
    def publish_availability(self, device_id, state):
        topic = f"{HA_BASE_TOPIC}/grobro/{device_id}/availability"
        self.client.publish(topic, state, retain=False)
