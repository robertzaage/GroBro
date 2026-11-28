"""Support bundle: one anonymized file with what is needed to analyze a device in an issue.

Only Modbus register frames are kept, never the datalogger config (Wi-Fi, IP addresses,
broker settings). Serials of devices, inverters and battery packs are masked in all values
and frames, keeping their first characters so the device family stays recognizable.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from threading import Lock

from grobro import settings, version
from grobro.model.growatt_registers import (
    DETECTED_DEVICES,
    INVERTER_SERIALS,
    get_device_type_name,
)

LOG = logging.getLogger(__name__)

FRAMES_PER_DEVICE = 12
KEEP_PREFIX = 4

# Options without secrets, hosts or serials
SAFE_SETTINGS = (
    "LOG_LEVEL", "HA_BASE_TOPIC", "PUBLISH_SENSORS_RETAINED", "DEVICE_TIMEOUT", "AVAILABILITY_SENSOR",
    "MAX_SLOTS", "MAX_BAT", "FILTER_DATA_GLITCHES", "KEEP_BATTERY_POSITION", "KEEP_LAST_VALUES",
    "AUTO_TIME_SYNC", "TIME_SYNC_HOURS", "GROWATT_CLOUD_ENABLED", "GROWATT_CLOUD_CONFIG_FILTER", "DUMP_MESSAGES",
)


class FrameRecorder:
    """The last register frames of every device, kept in memory for the support bundle."""

    def __init__(self, maxlen: int = FRAMES_PER_DEVICE):
        self._lock = Lock()
        self._maxlen = maxlen
        self._frames: dict[str, deque] = {}

    def record(self, device_id: str, frame: bytes):
        with self._lock:
            frames = self._frames.setdefault(device_id, deque(maxlen=self._maxlen))
            frames.append((datetime.now(timezone.utc).isoformat(timespec="seconds"), bytes(frame)))

    def frames(self) -> dict[str, list[tuple[str, bytes]]]:
        with self._lock:
            return {device_id: list(frames) for device_id, frames in self._frames.items()}


class Anonymizer:
    """Replaces serials by masked values of the same length, so frames keep their layout."""

    def __init__(self):
        self._masks: dict[str, str] = {}

    def add(self, serial) -> None:
        if not isinstance(serial, str):
            return
        serial = serial.strip("\x00 ")
        if len(serial) <= KEEP_PREFIX or serial in self._masks or not serial.isalnum():
            return
        number = str(len(self._masks) + 1)
        filler = max(0, len(serial) - KEEP_PREFIX - len(number))
        self._masks[serial] = (serial[:KEEP_PREFIX] + "X" * filler + number)[: len(serial)]

    def text(self, value: str) -> str:
        # Longest first, a serial can contain another one
        for serial in sorted(self._masks, key=len, reverse=True):
            value = value.replace(serial, self._masks[serial])
        return value

    def frame(self, frame: bytes) -> bytes:
        for serial in sorted(self._masks, key=len, reverse=True):
            frame = frame.replace(serial.encode("ascii"), self._masks[serial].encode("ascii"))
        return frame


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _string_values(state: dict) -> list[str]:
    return [value for value in state.values() if isinstance(value, str)]


def build_bundle(stats, last_states: dict[str, str], holding_values: dict[tuple[str, str], str]) -> dict:
    """Collect the bundle. last_states and holding_values are the HA client's caches."""
    frames = stats.frames.frames()
    devices = stats.devices()
    device_ids = sorted(set(devices) | set(frames))

    states: dict[str, dict] = {}
    for device_id in device_ids:
        raw = last_states.get(f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state")
        try:
            state = json.loads(raw) if raw else {}
        except ValueError:
            state = {}
        # Pieces of serials, the combined serials are kept and masked
        states[device_id] = {key: value for key, value in state.items() if "_ser_part_" not in key and not key.startswith("ser_part_")}

    anonymizer = Anonymizer()
    for device_id in device_ids:
        anonymizer.add(device_id)
        anonymizer.add(INVERTER_SERIALS.get(device_id))
        for _, frame in frames.get(device_id, []):
            # Device serial and the inverter serial in the metadata of register frames
            anonymizer.add(frame[8:38].split(b"\x00")[0].decode("ascii", errors="ignore"))
            anonymizer.add(frame[38:68].split(b"\x00")[0].decode("ascii", errors="ignore"))
        for value in _string_values(states[device_id]):
            # Battery serials and similar identifiers
            if any(c.isdigit() for c in value):
                anonymizer.add(value)

    bundle_devices = {}
    for device_id in device_ids:
        info = devices.get(device_id, {})
        inverter_serial = INVERTER_SERIALS.get(device_id)
        holding = {name: value for (dev, name), value in sorted(holding_values.items()) if dev == device_id}
        bundle_devices[anonymizer.text(device_id)] = {
            "type": get_device_type_name(device_id),
            "detected_by_inverter": device_id in DETECTED_DEVICES,
            "inverter_serial": anonymizer.text(inverter_serial) if inverter_serial else None,
            "messages": info.get("messages", 0),
            "decode_errors": info.get("decode_errors", 0),
            "last_seen": _iso(info.get("last_seen")),
            "state": json.loads(anonymizer.text(json.dumps(states[device_id]))),
            "holding_registers": json.loads(anonymizer.text(json.dumps(holding))),
            "frames": [
                {"time": ts, "hex": anonymizer.frame(frame).hex()}
                for ts, frame in frames.get(device_id, [])
            ],
        }

    return {
        "grobro_version": version.VERSION,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {name: getattr(settings, name, None) for name in SAFE_SETTINGS},
        "messages": stats.messages,
        "decode_errors": stats.decode_errors,
        "devices": bundle_devices,
    }


def write_bundle(bundle: dict) -> str | None:
    """Write the bundle to CONFIG_DIR/support and return the file path."""
    directory = os.path.join(settings.CONFIG_DIR, "support")
    name = f"grobro-support-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path = os.path.join(directory, name)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            json.dump(bundle, f, indent=2, sort_keys=True, default=str)
    except OSError as e:
        LOG.error("Writing the support bundle failed: %s", e)
        return None
    LOG.warning("Support bundle written to %s, check it before attaching it to an issue", path)
    return path
