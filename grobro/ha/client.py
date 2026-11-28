"""MQTT connection to Home Assistant.

The client itself only holds the connection, availability and the persisted device
configs. The other parts live in mixins:
- discovery.py: Home Assistant device discovery
- state.py: input and holding register values as states
- value_cache.py: KEEP_LAST_VALUES
- commands.py: commands from Home Assistant, config reads and the time sync
"""

from __future__ import annotations

import logging
import os
from collections import deque
from threading import Lock, Timer
from typing import Callable, Optional

import paho.mqtt.client as mqtt

import grobro.model as model
from grobro import settings
from grobro.ha.commands import COMMAND_PLATFORMS, CommandsMixin
from grobro.ha.discovery import DiscoveryMixin
from grobro.ha.state import StateMixin
from grobro.ha.value_cache import ValueCacheMixin
from grobro.model.modbus_function import GrowattModbusFunctionSingle

LOG = logging.getLogger(__name__)


class Client(DiscoveryMixin, StateMixin, ValueCacheMixin, CommandsMixin):
    on_command: Optional[Callable[[GrowattModbusFunctionSingle], None]] = None
    on_config_command: Optional[Callable[[str, int, str], None]] = None
    on_config_read: Optional[Callable[[str, int], None]] = None
    on_config_read_response: Callable[[str, int], None] | None = None

    _client: mqtt.Client

    def __init__(self, mqtt_config: model.MQTTConfig):
        self._config_cache: dict[str, model.DeviceConfig] = {}
        self._discovery_cache: list[str] = []
        self._device_timers: dict[str, Timer] = {}
        self._last_energy_values: dict[tuple[str, str], float] = {}
        # Config read sequencing
        self._config_read_queues: dict[str, deque[int]] = {}
        self._config_read_inflight: dict[str, int] = {}
        self._config_read_timers: dict[str, Timer] = {}
        self._config_read_lock = Lock()

        # Setup target MQTT client for publishing
        LOG.info(f"Connecting to HA broker at '{mqtt_config.host}:{mqtt_config.port}'")

        client_id_suffix = settings.MQTT_CLIENT_SUFFIX
        client_id = f"grobro-ha{('-' + client_id_suffix) if client_id_suffix else ''}"

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id
        )

        if mqtt_config.username and mqtt_config.password:
            self._client.username_pw_set(mqtt_config.username, mqtt_config.password)
        mqtt_config.configure_tls(self._client)
        self._client.will_set(settings.BRIDGE_AVAILABILITY_TOPIC, "offline", retain=True)
        self._last_states: dict[str, str] = {}
        self._time_sync_timer: Optional[Timer] = None
        if settings.AUTO_TIME_SYNC:
            self._schedule_time_sync()
        self._client.connect(mqtt_config.host, mqtt_config.port, 60)

        # Subscriptions are (re)established in _on_connect so they survive
        # broker restarts and reconnects (clean session drops them).
        self._client.on_message = self._on_message
        self._client.on_connect = self._on_connect

        self._discovery_payload_cache: dict[str, str] = {}
        # Firmware versions of NOAH and NEXA devices, see compose_combined_firmware
        self._combined_firmware_versions: dict[str, str] = {}
        # Last published holding register values, needed to update packed registers
        self._holding_values: dict[tuple[str, str], str] = {}
        self._neo_pv_count: dict[str, int] = {}

        # Values to publish for keys missing from a payload, per device,
        # persisted to disk so they survive both GroBro and HA restarts.
        self._value_cache: dict[str, dict] = {}
        self._value_miss_counts: dict[str, dict[str, int]] = {}
        self._value_cache_dirty: set[str] = set()
        self._value_cache_saved_at: dict[str, float] = {}

        try:
            os.makedirs(settings.CONFIG_DIR, exist_ok=True)
        except OSError as e:
            LOG.warning("Cannot create config dir %s, persistence disabled: %s", settings.CONFIG_DIR, e)

        # Load from cached files
        cached_files = os.listdir(settings.CONFIG_DIR) if os.path.isdir(settings.CONFIG_DIR) else []
        for fname in cached_files:
            if fname.startswith("config_") and fname.endswith(".json"):
                config = model.DeviceConfig.from_file(os.path.join(settings.CONFIG_DIR, fname))
                if config:
                    self._config_cache[config.device_id] = config
        self._load_value_cache(cached_files)

    # ------------------- Lifecycle -------------------

    def start(self):
        self._client.loop_start()

    def stop(self):
        for device_id in list(self._value_cache_dirty):
            self._save_value_cache(device_id)
        self._client.loop_stop()
        self._client.disconnect()

    # ------------------- Config Handling -------------------

    def set_config(self, device_id: str, config: model.DeviceConfig):
        config_path = os.path.join(settings.CONFIG_DIR, f"config_{device_id}.json")
        existing_config = model.DeviceConfig.from_file(config_path)
        if existing_config is None or existing_config != config:
            LOG.info(f"Saving updated config for {device_id}")
            self._save_config(config, config_path)
        else:
            LOG.debug(f"No config change for {device_id}")
        self._config_cache[device_id] = config

        if device_id in self._discovery_cache:
            self._discovery_cache.remove(device_id)
        self._publish_device_discovery(device_id)

    def _save_config(self, config: model.DeviceConfig, path: str):
        try:
            config.to_file(path)
        except OSError as e:
            LOG.warning("Failed to persist config %s: %s", path, e)

    # ------------------- MQTT Callbacks -------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        LOG.info(f"Connected to HA MQTT server with result code {reason_code}")
        self._client.publish(settings.BRIDGE_AVAILABILITY_TOPIC, "online", retain=True)
        self._client.subscribe(settings.HA_STATUS_TOPIC)
        self._subscribe_commands()
        self._republish_cached_values()

    def _subscribe_commands(self):
        for cmd_type in COMMAND_PLATFORMS:
            for action in ["set", "read"]:
                topic = f"{settings.HA_BASE_TOPIC}/{cmd_type}/grobro/+/+/{action}"
                self._client.subscribe(topic)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        if msg.topic == settings.HA_STATUS_TOPIC:
            if msg.payload.decode(errors="ignore").strip() == "online":
                self._republish_after_ha_restart()
            return
        self._handle_command(msg)

    def _republish_after_ha_restart(self):
        """Home Assistant restarted: send discovery and the last states again."""
        LOG.info("Home Assistant came online, republishing discovery and states")
        for device_id in list(self._discovery_cache):
            # Republish without clearing, so Home Assistant keeps the device
            self._discovery_payload_cache.pop(device_id, None)
            self._publish_device_discovery(device_id)
        for topic, state_str in list(self._last_states.items()):
            self._client.publish(topic, state_str, retain=settings.PUBLISH_SENSORS_RETAINED)

    # ------------------- Availability -------------------

    def _reset_device_timer(self, device_id: str):
        def set_device_unavailable(d_id: str):
            LOG.warning("Device %s timed out. Mark it as unavailable.", d_id)
            self._publish_offline_values(d_id)
            self._publish_availability(d_id, False)

        if device_id in self._device_timers:
            self._device_timers[device_id].cancel()

        timer = Timer(settings.DEVICE_TIMEOUT, set_device_unavailable, args=[device_id])
        timer.daemon = True
        self._device_timers[device_id] = timer
        timer.start()

    def _publish_availability(self, device_id: str, online: bool):
        LOG.debug("Set device %s availability: %s", device_id, online)
        if (not online and not settings.AVAILABILITY_SENSOR) or online:
            self._client.publish(
                f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/availability",
                "online" if online else "offline",
                retain=True,
            )
        if settings.AVAILABILITY_SENSOR:
            self._client.publish(
                f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/online",
                "ON" if online else "OFF",
                retain=settings.PUBLISH_SENSORS_RETAINED,
            )
