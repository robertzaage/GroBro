"""The GroBro device in Home Assistant: version, broker connection and the devices it sees."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from threading import Timer

from grobro import settings, version
from grobro.model.growatt_registers import INVERTER_SERIALS, get_device_type_name

LOG = logging.getLogger(__name__)

BRIDGE_STATE_INTERVAL = 60


def bridge_id() -> str:
    """Identifier of the GroBro device, unique per instance (MQTT_CLIENT_SUFFIX)."""
    suffix = settings.MQTT_CLIENT_SUFFIX
    return f"grobro_bridge{('_' + suffix) if suffix else ''}"


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class BridgeMixin:
    """Publishes the GroBro device and refreshes its state every minute."""

    def _bridge_topic(self, name: str) -> str:
        return f"{settings.HA_BASE_TOPIC}/grobro/{bridge_id()}/{name}"

    def _bridge_device_info(self) -> dict:
        return {
            "identifiers": [bridge_id()],
            "name": "GroBro",
            "manufacturer": "GroBro",
            "model": "Growatt MQTT Bridge",
            "sw_version": version.VERSION,
        }

    def _publish_bridge_discovery(self):
        uid = bridge_id()
        state_topic = self._bridge_topic("state")
        components = {
            f"{uid}_version": {
                "platform": "sensor",
                "name": "Version",
                "value_template": "{{ value_json.version }}",
                "entity_category": "diagnostic",
                "icon": "mdi:tag-outline",
            },
            f"{uid}_source_connected": {
                "platform": "binary_sensor",
                "name": "Device Broker",
                "value_template": "{{ value_json.source_connected }}",
                "device_class": "connectivity",
                "entity_category": "diagnostic",
            },
            f"{uid}_devices": {
                "platform": "sensor",
                "name": "Devices",
                "value_template": "{{ value_json.devices }}",
                "json_attributes_topic": self._bridge_topic("devices"),
                "state_class": "measurement",
                "icon": "mdi:solar-power-variant",
            },
            f"{uid}_messages": {
                "platform": "sensor",
                "name": "Messages Received",
                "value_template": "{{ value_json.messages }}",
                "state_class": "total_increasing",
                "entity_category": "diagnostic",
                "icon": "mdi:message-processing-outline",
            },
            f"{uid}_decode_errors": {
                "platform": "sensor",
                "name": "Decode Errors",
                "value_template": "{{ value_json.decode_errors }}",
                "state_class": "total_increasing",
                "entity_category": "diagnostic",
                "icon": "mdi:alert-circle-outline",
            },
            f"{uid}_started": {
                "platform": "sensor",
                "name": "Started",
                "value_template": "{{ value_json.started }}",
                "device_class": "timestamp",
                "entity_category": "diagnostic",
            },
        }
        for component_id, component in components.items():
            component["unique_id"] = component_id
            component["state_topic"] = state_topic
        payload = {
            "dev": self._bridge_device_info(),
            "avty": [{"t": settings.BRIDGE_AVAILABILITY_TOPIC}],
            "o": {"name": "grobro", "sw": version.VERSION, "url": "https://github.com/robertzaage/GroBro"},
            "cmps": components,
        }
        self._client.publish(
            f"{settings.HA_BASE_TOPIC}/device/{uid}/config",
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            retain=True,
        )

    def _publish_bridge_state(self):
        stats = self._stats
        devices = stats.devices()
        state = {
            "version": version.VERSION,
            "source_connected": "ON" if stats.source_connected else "OFF",
            "devices": len(devices),
            "messages": stats.messages,
            "decode_errors": stats.decode_errors,
            "started": _iso(stats.started_at),
        }
        attributes = {}
        for device_id, info in sorted(devices.items()):
            entry = {
                "type": get_device_type_name(device_id),
                "last_seen": _iso(info.get("last_seen")),
                "messages": info.get("messages", 0),
                "decode_errors": info.get("decode_errors", 0),
            }
            if device_id in INVERTER_SERIALS:
                entry["inverter_serial"] = INVERTER_SERIALS[device_id]
            attributes[device_id] = entry
        self._client.publish(self._bridge_topic("state"), json.dumps(state, sort_keys=True), retain=True)
        self._client.publish(self._bridge_topic("devices"), json.dumps(attributes, sort_keys=True), retain=True)

    def _schedule_bridge_state(self):
        def refresh():
            try:
                self._publish_bridge_state()
            except Exception as e:  # never let the refresh timer die
                LOG.error("Publishing the bridge state failed: %s", e)
            self._schedule_bridge_state()

        timer = Timer(BRIDGE_STATE_INTERVAL, refresh)
        timer.daemon = True
        self._bridge_timer = timer
        timer.start()
