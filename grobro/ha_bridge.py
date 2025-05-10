"""
Home Assistant extension for GroBro to act as a MQTT bridge
between source and target MQTT brokers.
Reads Growatt MQTT packets, decodes them, maps registers
and republishes values for Home Assistant auto-discovery.
"""

import os
import signal
import logging
import time

from grobro import model, ha, grobro

# Setup Logger
LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR").upper()
try:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
# pylint: disable-next=broad-exception-caught
except Exception as e:
    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    print(f"Failed to setup Logger {e} USING DEFAULT LOG Level(Error)")
LOG = logging.getLogger(__name__)

# Configuration from environment variables
REGISTER_FILTER_ENV = os.getenv("REGISTER_FILTER", "")
REGISTER_FILTER: dict[str, model.DeviceAlias] = {}
for entry in REGISTER_FILTER_ENV.split(","):
    if ":" in entry:
        serial, alias = entry.split(":", 1)
        REGISTER_FILTER[serial] = model.DeviceAlias(alias)

GROBRO_MQTT_CONFIG = model.MQTTConfig.from_env(
    prefix="SOURCE",
    defaults=model.MQTTConfig(host="localhost", port=1883),
)
HA_MQTT_CONFIG = model.MQTTConfig.from_env(
    prefix="TARGET",
    defaults=GROBRO_MQTT_CONFIG,
)
FORWARD_MQTT_CONFIG = model.MQTTConfig.from_env(
    prefix="FORWARD",
    defaults=model.MQTTConfig(host="mqtt.growatt.com", port=7006),
)

RUNNING = True


# pylint: disable-next=too-few-public-methods
class SignalHandler:
    """
    Catches SIGINT and SIGTERM in order to trigger
    graceful shutdown.
    """

    _caught: bool

    def __init__(self):
        self._running = True
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, _, __):
        """
        Handles signal by setting RUNNING to false.
        """
        LOG.info("signal received, shutting down...")
        self._running = False

    @property
    def caught(self) -> bool:
        """
        Wether the signal was caught.
        """
        return self._running


if __name__ == "__main__":
    ha_client = ha.Client(HA_MQTT_CONFIG, REGISTER_FILTER)
    grobro_client = grobro.Client(GROBRO_MQTT_CONFIG, FORWARD_MQTT_CONFIG)

    grobro_client.on_state = ha_client.publish_state
    grobro_client.on_config = ha_client.set_config

    RUNNING = True
    signal_handler = SignalHandler()

    # Assume client1 and client2 have .start() and .stop()
    ha_client.start()
    grobro_client.start()

    try:
        while signal_handler.caught:
            time.sleep(0.1)
    finally:
        ha_client.stop()
        grobro_client.stop()
        LOG.info("stopped both clients. Exiting.")
