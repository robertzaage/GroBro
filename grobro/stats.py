"""Runtime statistics of the bridge, shown on the GroBro device in Home Assistant."""

from __future__ import annotations

import time
from threading import Lock


class BridgeStats:
    """Counts what the device side receives. Shared by both clients, so it is thread safe."""

    def __init__(self):
        self._lock = Lock()
        self.started_at = time.time()
        self.source_connected = False
        self.messages = 0
        self.decode_errors = 0
        self._devices: dict[str, dict] = {}

    def message_received(self, device_id: str):
        with self._lock:
            self.messages += 1
            device = self._devices.setdefault(device_id, {"messages": 0})
            device["messages"] += 1
            device["last_seen"] = time.time()

    def decode_error(self, device_id: str | None = None):
        with self._lock:
            self.decode_errors += 1
            if device_id:
                device = self._devices.setdefault(device_id, {"messages": 0})
                device["decode_errors"] = device.get("decode_errors", 0) + 1

    def set_source_connected(self, connected: bool):
        with self._lock:
            self.source_connected = connected

    def devices(self) -> dict[str, dict]:
        with self._lock:
            return {device_id: dict(info) for device_id, info in self._devices.items()}
