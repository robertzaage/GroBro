"""KEEP_LAST_VALUES: last known sensor values, persisted across restarts."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from grobro import settings
from grobro.model.growatt_registers import (
    get_known_registers,
)

from grobro.ha.state import _MAX_BAT_CACHE, _detect_bat_count, _get_bat_number, _has_bat_info

LOG = logging.getLogger(__name__)


# Offline handling of sensor values (KEEP_LAST_VALUES): a value missing from a
# payload keeps its last known reading for brief gaps. Once it has been missing
# from OFFLINE_MISS_THRESHOLD consecutive payloads (or the device timed out),
# live readings (power, current, voltage, frequency) drop to 0 while
# state-like values (SoC, temperature, status, serials) keep their last known
# value. Live readings are never restored after a restart, so the history
# shows a gap instead of a dip to 0.
OFFLINE_MISS_THRESHOLD = 3


_ZERO_WHEN_OFFLINE_DEVICE_CLASSES = {"power", "apparent_power", "reactive_power", "current", "voltage", "frequency"}


_ZERO_WHEN_OFFLINE_UNITS = {"W", "kW", "VA", "var", "A", "mA", "ma", "V", "Hz"}


# Minimum seconds between two writes of a device's value cache to disk
# (values change on nearly every message; avoid wearing out SD cards).
VALUE_CACHE_SAVE_INTERVAL = 300


def _offline_behavior(known_registers, key: str) -> Optional[str]:
    """How a sensor value behaves while missing: "zero", "hold" or None (not cached).

    total_increasing/total counters (kWh energy meters, used by the HA Energy
    dashboard) are not cached at all: HA already handles gaps in those
    correctly, and a cached value could get reinstated after a legitimate
    reset (e.g. daily "_today" counters at midnight), which combined with
    settings.FILTER_DATA_GLITCHES could suppress the correct lower value as a glitch.
    """
    reg = known_registers.input_registers.get(key) if known_registers else None
    if not reg:
        return "hold"
    ha = reg.homeassistant
    if ha.offline:
        return ha.offline
    if ha.state_class in ("total_increasing", "total"):
        return None
    if ha.device_class in _ZERO_WHEN_OFFLINE_DEVICE_CLASSES or ha.unit_of_measurement in _ZERO_WHEN_OFFLINE_UNITS:
        return "zero"
    return "hold"


class ValueCacheMixin:
    """Fills in values missing from a payload and persists them."""

    def _load_value_cache(self, cached_files: list[str]):
        """Load the persisted battery counts and, with KEEP_LAST_VALUES, the last known values."""
        for fname in cached_files:
            if fname.startswith("state_cache_") and fname.endswith(".json"):
                device_id = fname[len("state_cache_"):-len(".json")]
                try:
                    with open(os.path.join(settings.CONFIG_DIR, fname), "r") as f:
                        data = json.load(f)
                    cached = data["values"]
                except Exception as e:
                    LOG.warning("Failed to load state cache %s: %s", fname, e)
                    continue
                if isinstance(data.get("max_bat"), int):
                    _MAX_BAT_CACHE[device_id] = data["max_bat"]
                if not settings.KEEP_LAST_VALUES:
                    continue
                # Live readings are not restored: they were only valid at the
                # time they were saved, and restoring them (or 0) would show
                # up as a wrong value or a dip in the history.
                known_registers = get_known_registers(device_id)
                self._value_cache[device_id] = {
                    key: value for key, value in cached.items()
                    if _offline_behavior(known_registers, key) == "hold"
                }
                LOG.info("Loaded cached sensor values for %s from disk", device_id)

    def _republish_cached_values(self):
        """Republish the last known state-like values after GroBro or the broker connection restarts.

        Entities don't have to wait for the next device message (or stay "unknown").
        Live readings are left out, they'd be outdated by now.
        """
        for device_id, device_value_cache in list(self._value_cache.items()):
            known_registers = get_known_registers(device_id)
            cached_payload = {
                key: value for key, value in device_value_cache.items()
                if _offline_behavior(known_registers, key) == "hold"
            }
            if not cached_payload:
                continue
            topic = f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state"
            self._client.publish(
                topic,
                json.dumps(cached_payload, separators=(",", ":")),
                retain=settings.PUBLISH_SENSORS_RETAINED,
            )
            LOG.info("Republished cached values for %s after (re)connect", device_id)

    def _fill_in_last_values(self, device_id: str, known_registers, raw_payload: dict, payload: dict, absent_keys: set[str], effective_max_bat: int):
        """Keep HA entities from dropping to "unknown" when a value is missing
        from the payload (absent or None, e.g. a disconnected battery, or after
        a GroBro/HA restart), see OFFLINE_MISS_THRESHOLD."""
        # Batteries beyond the currently reported count (e.g. disconnected
        # ones) still show up as 0 / empty registers: treat those as missing
        # so their SoC and serial keep the last known value. Their
        # temperatures are absent sensors and become unknown.
        if _has_bat_info(raw_payload):
            reported_bat_cnt = _detect_bat_count(raw_payload)
            for key in list(payload):
                bat_num = _get_bat_number(key)
                if bat_num is not None and bat_num > reported_bat_cnt:
                    payload[key] = None
                    reg = known_registers.input_registers.get(key) if known_registers else None
                    if reg and reg.homeassistant.device_class == "temperature":
                        absent_keys.add(key)

        device_value_cache = self._value_cache.setdefault(device_id, {})
        miss_counts = self._value_miss_counts.setdefault(device_id, {})
        for key in set(payload) | set(device_value_cache):
            behavior = _offline_behavior(known_registers, key)
            if behavior is None:
                continue
            bat_num = _get_bat_number(key)
            if bat_num is not None and bat_num > effective_max_bat:
                continue
            if key in absent_keys:
                miss_counts.pop(key, None)
                if device_value_cache.pop(key, None) is not None:
                    self._value_cache_dirty.add(device_id)
                payload[key] = None
                continue
            value = payload.get(key)
            if value is not None:
                miss_counts.pop(key, None)
                if device_value_cache.get(key) != value:
                    device_value_cache[key] = value
                    self._value_cache_dirty.add(device_id)
                continue
            if key not in device_value_cache:
                continue
            miss_counts[key] = miss_counts.get(key, 0) + 1
            if behavior == "zero" and miss_counts[key] >= OFFLINE_MISS_THRESHOLD and device_value_cache[key] != 0:
                device_value_cache[key] = 0
                self._value_cache_dirty.add(device_id)
            payload[key] = device_value_cache[key]

    def _maybe_save_value_cache(self, device_id: str):
        if device_id not in self._value_cache_dirty:
            return
        saved_at = self._value_cache_saved_at.get(device_id)
        if saved_at is None or time.monotonic() - saved_at >= VALUE_CACHE_SAVE_INTERVAL:
            self._save_value_cache(device_id)

    def _save_value_cache(self, device_id: str):
        path = os.path.join(settings.CONFIG_DIR, f"state_cache_{device_id}.json")
        tmp_path = f"{path}.tmp"
        self._value_cache_saved_at[device_id] = time.monotonic()
        try:
            with open(tmp_path, "w") as f:
                json.dump({"values": self._value_cache.get(device_id, {}), "max_bat": _MAX_BAT_CACHE.get(device_id)}, f)
            os.replace(tmp_path, path)
            self._value_cache_dirty.discard(device_id)
        except Exception as e:
            LOG.warning("Failed to persist state cache for %s: %s", device_id, e)

    def _publish_offline_values(self, device_id: str):
        """Drop live readings of a timed-out device to 0 and publish the result."""
        device_value_cache = self._value_cache.get(device_id)
        if not device_value_cache:
            return
        known_registers = get_known_registers(device_id)
        for key, value in list(device_value_cache.items()):
            if value != 0 and _offline_behavior(known_registers, key) == "zero":
                device_value_cache[key] = 0
                self._value_cache_dirty.add(device_id)
        topic = f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state"
        state_str = json.dumps(device_value_cache, separators=(",", ":"))
        # Also used when Home Assistant restarts, see _republish_after_ha_restart
        self._last_states[topic] = state_str
        self._client.publish(topic, state_str, retain=settings.PUBLISH_SENSORS_RETAINED)
        self._maybe_save_value_cache(device_id)
