from dataclasses import dataclass, asdict
import logging
import json
from typing import Optional
import os
from pydantic import BaseModel

LOG = logging.getLogger(__name__)


class MQTTConfig(BaseModel):
    host: str
    port: int
    use_tls: bool = False
    username: Optional[str] = None
    password: Optional[str] = None

    @staticmethod
    def from_env(prefix: str, defaults: "MQTTConfig") -> "MQTTConfig":
        return MQTTConfig(
            host=os.getenv(f"{prefix}_MQTT_HOST", defaults.host),
            port=int(os.getenv(f"{prefix}_MQTT_PORT", defaults.port)),
            use_tls=os.getenv(f"{prefix}_MQTT_TLS", str(defaults.use_tls)).lower()
            == "true",
            username=os.getenv(f"{prefix}_MQTT_USER", defaults.username),
            password=os.getenv(f"{prefix}_MQTT_PASS", defaults.password),
        )
