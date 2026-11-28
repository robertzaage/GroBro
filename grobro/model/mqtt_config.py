import logging
from typing import Optional
import os
import ssl

import paho.mqtt.client as mqtt
from pydantic import BaseModel

from grobro import settings

LOG = logging.getLogger(__name__)

class MQTTConfig(BaseModel):
    host: str
    port: int
    use_tls: bool = False
    username: Optional[str] = None
    password: Optional[str] = None
    tls_verify: bool = False
    tls_ca_file: Optional[str] = None

    def configure_tls(self, client: mqtt.Client) -> None:
        """Configure transport security without silently falling back to insecure TLS."""
        if self.tls_verify and not self.use_tls:
            raise ValueError("MQTT TLS verification requires TLS to be enabled")
        if self.tls_ca_file and not self.tls_verify:
            raise ValueError("MQTT CA file requires TLS verification to be enabled")
        if not self.use_tls:
            return
        if self.tls_verify:
            context = ssl.create_default_context(cafile=self.tls_ca_file)
            client.tls_set_context(context)
            client.tls_insecure_set(False)
        else:
            # Preserve the historical behavior unless verification is explicitly enabled.
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)

    @staticmethod
    def from_env(prefix: str, defaults: "MQTTConfig") -> "MQTTConfig":
        host = os.getenv(f"{prefix}_MQTT_HOST", defaults.host)
        port = int(os.getenv(f"{prefix}_MQTT_PORT", defaults.port))
        use_tls = os.getenv(f"{prefix}_MQTT_TLS", str(defaults.use_tls)).lower() == "true"
        username = os.getenv(f"{prefix}_MQTT_USER", defaults.username)
        password = os.getenv(f"{prefix}_MQTT_PASS", defaults.password)
        # Verification settings are broker-specific: TARGET must not inherit SOURCE's CA.
        tls_verify = False
        tls_ca_file = None
        if prefix in ("SOURCE", "TARGET"):
            verify_value = os.getenv(f"{prefix}_MQTT_TLS_VERIFY", "false").strip().lower()
            if verify_value not in ("true", "false"):
                raise ValueError(f"{prefix}_MQTT_TLS_VERIFY must be true or false")
            tls_verify = verify_value == "true"
            tls_ca_file = os.getenv(f"{prefix}_MQTT_CA_FILE") or None
            # Relative paths point into the persisted config dir, which is
            # the only folder mapped into the add-on (its addon_config).
            if tls_ca_file and not os.path.isabs(tls_ca_file):
                tls_ca_file = os.path.join(settings.CONFIG_DIR, tls_ca_file)

        LOG.info(f"Loading MQTT configuration from environment (prefix: {prefix})")
        LOG.info(f"MQTT Host: {username if username else 'anonymous'}{':***' if password else ''}@{host}:{port} (TLS: {use_tls})")

        return MQTTConfig(
            host=host,
            port=port,
            use_tls=use_tls,
            username=username,
            password=password,
            tls_verify=tls_verify,
            tls_ca_file=tls_ca_file,
        )
