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
    print(f"Failed to setup logger {e} USING DEFAULT LOG Level(Error)")
LOG = logging.getLogger(__name__)

# Configuration from environment variables
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
        LOG.info("Signal received, shutting down...")
        self._running = False

    @property
    def caught(self) -> bool:
        """
        Wether the signal was caught.
        """
        return self._running


if __name__ == "__main__":
    ha_client = ha.Client(HA_MQTT_CONFIG)
    grobro_client = grobro.Client(GROBRO_MQTT_CONFIG, FORWARD_MQTT_CONFIG)

    # setup com: grobro -> ha
    grobro_client.on_input_register = ha_client.publish_input_register
    grobro_client.on_holding_register_input = ha_client.publish_holding_register_input
    grobro_client.on_config = ha_client.set_config

    # setup com: ha -> grobro
    ha_client.on_command = grobro_client.send_command
    ha_client.on_config_read = grobro_client.send_config_read_message
    ha_client.on_config_command = lambda dev, reg, val: grobro_client.send_config_message(dev, reg, val)

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
        LOG.info("Stopped both clients. Exiting...")
