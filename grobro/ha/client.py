import paho.mqtt.client as mqtt
import os
import json
import logging

HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
LOG = logging.getLogger(__name__)


class Client:
    client: mqtt.Client
    config_cache = {}

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

    def set_config(self, device_id, config):
        self.config_cache[device_id] = config
        pass

    def publish_discovery(self, device_id, variable, ha):
        topic = f"{HA_BASE_TOPIC}/sensor/grobro/{device_id}_{variable}/config"
        device_info = {
            "identifiers": [device_id],
            "name": f"Growatt {device_id}",
            "manufacturer": "Growatt",
            "serial_number": device_id,
        }
        # Find matching config
        config = self.config_cache.get(device_id)

        # Fallback: try loading from file
        if not config:
            config_path = f"config_{device_id}.json"
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r") as f:
                        config = json.load(f)
                        self.config_cache[device_id] = config
                        LOG.info(
                            f"Loaded cached config for {device_id} from file (fallback)"
                        )
                except Exception:
                    config = {}
        if isinstance(config, dict):
            device_type_map = {
                "55": "NEO-series",
                "72": "NEXA-series",
                "61": "NOAH-series",
            }
            known_model_id = device_type_map.get(config.get("device_type"))
            if known_model_id:
                device_info["model"] = known_model_id
            elif config.get("model_id"):
                device_info["model"] = config["model_id"]
            if config.get("sw_version"):
                device_info["sw_version"] = config["sw_version"]
            if config.get("hw_version"):
                device_info["hw_version"] = config["hw_version"]
            if config.get("mac_address"):
                device_info["connections"] = [["mac", config["mac_address"]]]
        payload = {
            "name": ha.get("name", variable),
            "state_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/state",
            "value_template": f"{{{{ value_json['{variable}'] }}}}",
            "unique_id": f"grobro_{device_id}_{variable}",
            "object_id": f"{device_id}_{variable}",
            "device": device_info,
        }
        for key in ["device_class", "state_class", "unit_of_measurement", "icon"]:
            if key in ha:
                payload[key] = ha[key]
        self.client.publish(topic, json.dumps(payload), retain=True)

    def publish_state(self, device_id, state):
        topic = f"{HA_BASE_TOPIC}/grobro/{device_id}/state"
        self.client.publish(topic, json.dumps(state), retain=False)
