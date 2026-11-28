"""Optional forwarding between the devices and the Growatt Cloud (GROWATT_CLOUD)."""

import logging
import ssl
import struct

import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage

from grobro import settings
from grobro.grobro import parser

LOG = logging.getLogger(__name__)

# Property to flag messages forwarded from growatt cloud
MQTT_PROP_FORWARD_GROWATT = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_GROWATT.UserProperty = [("forwarded-for", "growatt")]

# Property to flag messages forwarded from ha
MQTT_PROP_FORWARD_HA = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_HA.UserProperty = [("forwarded-for", "ha")]

# Config write messages and their acknowledgements
CONFIG_WRITE_MESSAGE_TYPES = (0x0118, 0x0110)


def _forwarded(device_id: str) -> bool:
    return settings.GROWATT_CLOUD_ENABLED and (
        settings.GROWATT_CLOUD == "true" or device_id in settings.GROWATT_CLOUD_FILTER
    )


class CloudForwardMixin:
    """Forwards device messages to the Growatt Cloud and its answers back to the devices."""

    def _forward_to_cloud(self, msg: MQTTMessage, device_id: str):
        """Forward a device message to the Growatt Cloud, if enabled for this device."""
        if not _forwarded(device_id):
            return
        try:
            if settings.GROWATT_CLOUD_CONFIG_FILTER:
                try:
                    unscrambled = parser.unscramble(msg.payload)
                    msg_type = struct.unpack_from(">H", unscrambled, 6)[0]
                    if msg_type in CONFIG_WRITE_MESSAGE_TYPES:
                        LOG.warning("Blocked config write from Growatt Cloud for %s", device_id)
                        return
                except Exception as e:
                    LOG.error("Failed to inspect message before forwarding: %s", e)
                    return

            forward_client = self._connect_to_growatt_server(device_id)
            forward_client.publish(
                msg.topic,
                payload=msg.payload,
                qos=msg.qos,
                retain=msg.retain,
            )
        except Exception as e:
            LOG.error(f"Forwarding to Growatt Cloud failed: {e}")

    def _on_message_forward_client(self, client, userdata, msg: MQTTMessage):
        from grobro.grobro.client import _extract_device_id, dump_message_binary

        LOG.debug("Received Growatt forward message: %s: %s", msg.topic, msg.payload)
        if settings.DUMP_MESSAGES:
            dump_message_binary(msg.topic, msg.payload)
        try:
            device_id = _extract_device_id(msg.topic)

            unscrambled = parser.unscramble(msg.payload)
            LOG.debug("Received Growatt forward: %s %s", msg.topic, unscrambled.hex(" "))

            if not settings.GROWATT_CLOUD_ENABLED:
                return
            if settings.GROWATT_CLOUD != "true" and device_id not in settings.GROWATT_CLOUD_FILTER:
                LOG.debug(
                    "Dropping Growatt message for device %s not in GROWATT_CLOUD filter",
                    device_id,
                )
                return
            LOG.debug("Forwarding message from Growatt for client %s", device_id)
            # We need to publish the messages from Growatt on the Topic
            # s/33/{deviceid}. Growatt sends them on Topic s/{deviceid}
            self._client.publish(
                msg.topic.split("/")[0] + "/33/" + device_id,
                payload=msg.payload,
                qos=msg.qos,
                retain=msg.retain,
                properties=MQTT_PROP_FORWARD_GROWATT,
            )
        except Exception as e:
            LOG.error(f"Forwarding message: {e}")

    def _connect_to_growatt_server(self, client_id):
        """Connect to the Growatt Cloud broker as the device, one connection per device."""
        if f"forward_client_{client_id}" not in self._forward_clients:
            LOG.info(
                "Connecting to Growatt broker at '%s:%s', subscribed to '+/%s'",
                self._forward_mqtt_config.host,
                self._forward_mqtt_config.port,
                client_id,
            )
            client = mqtt.Client(
                client_id=client_id,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
            client.on_message = self._on_message_forward_client
            client.connect(
                self._forward_mqtt_config.host,
                self._forward_mqtt_config.port,
                60,
            )
            client.subscribe(f"+/{client_id}")
            client.loop_start()
            self._forward_clients[f"forward_client_{client_id}"] = client
        return self._forward_clients[f"forward_client_{client_id}"]
