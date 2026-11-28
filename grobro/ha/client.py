from __future__ import annotations

import os
import time
import json
import logging
from threading import Timer
from typing import Callable, Optional
from collections import deque
from threading import Lock

import paho.mqtt.client as mqtt

import grobro.model as model
from grobro import settings
from grobro.model.growatt_codes import fault_text, warning_texts
from grobro.model.growatt_registers import (
    HomeAssistantInputRegister,
    HomeAssistantHoldingRegisterInput,
    GroBroRegisters,
    get_device_type_name,
    DETECTED_DEVICES,
    INVERTER_SERIALS,
    GENERIC_DATALOGGER_PREFIXES,
    get_known_registers,
)
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.modbus_function import (
    GrowattModbusFunctionSingle,
)

LOG = logging.getLogger(__name__)

_MAX_BAT_CACHE: dict[str, int] = {}
_LAST_BAT_SERIALS: dict[str, dict[int, str]] = {}

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

# ------------------- Helpfunctions -------------------

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


def _detect_bat_count(payload: dict) -> int:
    bat_cnt = payload.get("bat_cnt")
    if isinstance(bat_cnt, int) and bat_cnt >= 1:
        return bat_cnt
    count = 1
    any_found = False
    for bat_num in range(2, 5):
        key = f"bat{bat_num}_ser_part_1"
        val = payload.get(key)
        if val is not None and str(val).strip():
            count += 1
            any_found = True
    if not any_found:
        return 4
    return count


def _can_detect_bat_count(device_id: str) -> bool:
    known_registers = get_known_registers(device_id)
    if not known_registers:
        return False
    return any(
        name == "bat_cnt" or (name.startswith("bat") and name.endswith("_ser_part_1"))
        for name in known_registers.input_registers
    )


def _has_bat_info(payload: dict) -> bool:
    bat_cnt = payload.get("bat_cnt")
    if isinstance(bat_cnt, int) and bat_cnt >= 1:
        return True
    return any(str(payload.get(f"bat{n}_ser_part_1") or "").strip() for n in range(2, 5))


def _resolve_max_bat(device_id: str, payload: dict | None = None) -> int:
    if isinstance(settings.MAX_BAT, int):
        return settings.MAX_BAT
    if payload is not None and _has_bat_info(payload):
        # The count only ever grows (and is persisted with the value cache):
        # a battery that stops reporting, e.g. because it was disconnected,
        # keeps its entities and last known values. Set MAX_BAT to drop it.
        c = max(_detect_bat_count(payload), _MAX_BAT_CACHE.get(device_id, 0))
        _MAX_BAT_CACHE[device_id] = c
        return c
    if device_id in _MAX_BAT_CACHE:
        return _MAX_BAT_CACHE[device_id]
    if payload is not None or not _can_detect_bat_count(device_id):
        return 4
    # No payload seen yet and no persisted count: assume a single battery.
    # Entities of further batteries are added once a payload reports them,
    # which (unlike removing entities again) leaves no orphans behind in HA.
    return 1


def _bat_count_known(device_id: str) -> bool:
    return isinstance(settings.MAX_BAT, int) or device_id in _MAX_BAT_CACHE

def map_enum_value(reg, value):
    """Wandelt ENUM-INT_MAP-Werte in Klartext um (falls vorhanden)."""
    try:
        data = getattr(reg.growatt, "data", None)
        if not data or getattr(data, "data_type", None) != "ENUM":
            return value
        enum_opts = getattr(data, "enum_options", None)
        if not enum_opts or getattr(enum_opts, "enum_type", None) != "INT_MAP":
            return value
        return enum_opts.values.get(str(value), enum_opts.values.get(value, str(value)))
    except Exception as e:
        LOG.warning("Enum mapping failed for %s=%s: %s", reg, value, e)
        return value


def make_modbus_command(device_id: str, func: GrowattModbusFunction, register_no: int, value: Optional[int] = None) -> GrowattModbusFunctionSingle:
    """Erzeugt einen GrowattModbusFunctionSingle-Befehl."""
    return GrowattModbusFunctionSingle(
        device_id=device_id,
        function=func,
        register=register_no,
        value=value if value is not None else register_no,
    )


def _to_register_value(reg, cmd_type: str, raw_value: str) -> int:
    """Convert a Home Assistant value into the raw 16 bit register value of a holding register."""
    if cmd_type == "switch":
        return 1 if raw_value.upper() == "ON" else 0
    if cmd_type == "time":
        hour, minute = map(int, raw_value.split(":")[:2])
        return (hour * 256) + minute
    if cmd_type == "select":
        options = getattr(reg.homeassistant, "options", None)
        if options:
            reverse_options = {value: int(key) for key, value in options.items()}
            if raw_value not in reverse_options:
                raise ValueError(f"unknown option {raw_value}")
            return reverse_options[raw_value]
        return int(raw_value)

    data = reg.growatt.data
    value = float(raw_value)
    if data.float_options and data.data_type in ("FLOAT", "SIGNED_FLOAT"):
        value = (value - data.float_options.delta) / data.float_options.multiplier
    elif data.mult:
        value = value / data.mult
    raw = int(round(value))
    # Negative values are written as two's complement
    return raw & 0xFFFF if raw < 0 else raw


def _has_status_summary(known_registers) -> bool:
    regs = known_registers.input_registers
    has_status = "Inverter_Status" in regs or "System_Status" in regs
    return has_status and ("Derating_Mode" in regs or "Fault_code" in regs)


def _status_summary(payload: dict, known_registers=None) -> Optional[str]:
    """Combine status, derating reason, fault code and warnings into one readable text."""
    status = payload.get("Inverter_Status", payload.get("System_Status"))
    if not isinstance(status, str) or status == "unknown":
        return None
    parts = [status.removesuffix("Status") or status]
    derating = payload.get("Derating_Mode")
    if isinstance(derating, str) and derating not in ("No derating", "unknown"):
        parts.append(derating)
    fault = payload.get("Fault_code")
    if isinstance(fault, (int, float)) and fault > 0:
        parts.append(fault_text(known_registers, int(fault)))
    warnings = warning_texts(known_registers, payload)
    if warnings:
        parts.append(", ".join(warnings))
    return " – ".join(parts)


def _get_bat_number(name: str) -> Optional[int]:
    if name.startswith("battery"):
        rest = name[7:]
        digits = ""
        for c in rest:
            if c.isdigit():
                digits += c
            else:
                break
        if digits:
            return int(digits)
    elif name.startswith("bat"):
        rest = name[3:]
        if rest.startswith("_"):
            rest = rest[1:]
        digits = ""
        for c in rest:
            if c.isdigit():
                digits += c
            else:
                break
        if digits:
            return int(digits)
    return None


def iter_command_registers(known_registers: GroBroRegisters):
    # Modbus holding registers
    for name, reg in known_registers.holding_registers.items():
        yield {
            "name": name,
            "ha": reg.homeassistant,
            "topic_root": reg.homeassistant.type,
            "cmd_id": name,
            "state_id": name,
            "is_config": False,
        }

    # Config registers
    for name, reg in known_registers.config_registers.items():
        yield {
            "name": name,
            "ha": reg.homeassistant,
            "topic_root": "config",
            "cmd_id": str(reg.growatt.register_no),
            "state_id": str(reg.growatt.register_no),
            "is_config": True,
        }

# ------------------- Client-Class -------------------

class Client:
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

    # ------------------- Publishing -------------------

    def publish_input_register(self, state: HomeAssistantInputRegister):
        LOG.debug("HA: publish: %s", state)
        state = self._add_combined_firmware(state)
        prev_max_bat = _MAX_BAT_CACHE.get(state.device_id)
        effective_max_bat = _resolve_max_bat(state.device_id, state.payload)
        if _MAX_BAT_CACHE.get(state.device_id) != prev_max_bat:
            self._value_cache_dirty.add(state.device_id)
        self._detect_neo_pv_count(state.device_id, state.payload)
        # discovery + availability
        self._publish_device_discovery(state.device_id, effective_max_bat)
        self._publish_availability(state.device_id, True)
        if settings.DEVICE_TIMEOUT > 0:
            self._reset_device_timer(state.device_id)

        # ENUM Mapping
        payload = dict(state.payload)
        known_registers = get_known_registers(state.device_id)

        # Absent temperature sensors report a raw 0 K, which decodes to -273.1 °C.
        # They are published as unknown and never filled in from the cache.
        absent_keys: set[str] = set()
        if known_registers:
            for key, value in list(payload.items()):
                reg = known_registers.input_registers.get(key)
                if not reg:
                    continue
                if getattr(reg.homeassistant, "device_class", None) != "temperature":
                    continue
                if isinstance(value, (int, float)) and value == -273.1:
                    payload[key] = None
                    absent_keys.add(key)

        for key in list(payload.keys()):
            bat_num = _get_bat_number(key)
            if bat_num is not None and bat_num > effective_max_bat:
                del payload[key]

        # ENUM Mapping (must come AFTER our replacement!)
        if known_registers:
            for key, value in list(payload.items()):
                reg = known_registers.input_registers.get(key)
                if reg:
                    payload[key] = map_enum_value(reg, value)

        status_summary = _status_summary(payload, known_registers)
        if status_summary:
            payload["Status_Summary"] = status_summary

        # Prevent glitches on total_increasing sensors (energy counters)
        if settings.FILTER_DATA_GLITCHES and known_registers:
            for key, value in list(payload.items()):
                reg = known_registers.input_registers.get(key)
                if not reg:
                    continue
                if getattr(reg.homeassistant, "state_class", None) == "total_increasing":
                    device_key = (state.device_id, key)
                    last_value = self._last_energy_values.get(device_key)
                    if isinstance(value, (int, float)):
                        if last_value is not None and value < last_value:
                            payload[key] = last_value
                            LOG.debug("Suppressed decrease for %s/%s: %.1f -> %.1f", state.device_id, key, last_value, value)
                        else:
                            self._last_energy_values[device_key] = value

        # Combine battery serial parts into single values
        for bat_num in range(2, 5):
            parts = []
            for i in range(1, 5):
                key = f"bat{bat_num}_ser_part_{i}"
                val = payload.pop(key, None)
                if val is not None:
                    parts.append(str(val))
            combined = "".join(parts).strip()
            if combined:
                payload[f"bat{bat_num}_serial"] = combined
            else:
                payload.pop(f"bat{bat_num}_serial", None)

        # Detect battery position changes when enabled
        if settings.KEEP_BATTERY_POSITION:
            current_serials: dict[int, str] = {}
            for bat_num in range(2, 5):
                key = f"bat{bat_num}_serial"
                if key in payload and payload[key]:
                    current_serials[bat_num] = str(payload[key])
            prev_serials = _LAST_BAT_SERIALS.get(state.device_id, {})
            if prev_serials and current_serials:
                for pos, serial in current_serials.items():
                    for prev_pos, prev_serial in prev_serials.items():
                        if serial == prev_serial and pos != prev_pos:
                            LOG.warning(
                                "Battery %s moved from Bat%d to Bat%d for device %s — inverter re-enumeration detected",
                                serial, prev_pos, pos, state.device_id,
                            )
            _LAST_BAT_SERIALS[state.device_id] = current_serials

        if settings.KEEP_LAST_VALUES:
            self._fill_in_last_values(state.device_id, known_registers, state.payload, payload, absent_keys, effective_max_bat)
        self._maybe_save_value_cache(state.device_id)

        # State publish
        topic = f"{settings.HA_BASE_TOPIC}/grobro/{state.device_id}/state"
        state_str = json.dumps(payload, separators=(",", ":"))
        self._last_states[topic] = state_str
        self._client.publish(topic, state_str, retain=settings.PUBLISH_SENSORS_RETAINED)

    def _add_combined_firmware(self, state: HomeAssistantInputRegister) -> HomeAssistantInputRegister:
        """Add the firmware version of NOAH and NEXA devices, composed like the ShinePhone app shows it."""
        if not state.device_id.startswith(_COMBINED_FIRMWARE_PREFIXES):
            return state
        config = self._config_cache.get(state.device_id)
        datalogger_version = getattr(config, "sw_version", None) if config else None
        firmware_version = compose_combined_firmware(state.payload, datalogger_version)
        if not firmware_version:
            return state
        if self._combined_firmware_versions.get(state.device_id) != firmware_version:
            self._combined_firmware_versions[state.device_id] = firmware_version
            # Republish the discovery with the new version, without clearing it first
            self._discovery_payload_cache.pop(state.device_id, None)
        return HomeAssistantInputRegister(device_id=state.device_id, payload={**state.payload, "fw_version": firmware_version})

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


    def publish_holding_register_input(self, ha_input: HomeAssistantHoldingRegisterInput):
        try:
            LOG.debug("HA: publish: %s", ha_input)
            for value in ha_input.payload:
                topic = f"{settings.HA_BASE_TOPIC}/{value.register_def.type}/grobro/{ha_input.device_id}/{value.name}/get"
                self._holding_values[(ha_input.device_id, value.name)] = str(value.value)
                self._client.publish(topic, value.value, retain=True)
        except Exception as e:
            LOG.error(f"HA: publish msg: {e}")

    # ------------------- MQTT Callback -------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        LOG.info(f"Connected to HA MQTT server with result code {reason_code}")
        self._client.publish(settings.BRIDGE_AVAILABILITY_TOPIC, "online", retain=True)
        self._client.subscribe(settings.HA_STATUS_TOPIC)
        self._subscribe_commands()
        # Immediately republish the last known state-like values for every
        # device we have a cache for, so entities don't wait for the next
        # device poll (or stay "unknown") after GroBro or the broker connection
        # restarts. Live readings are left out, they'd be outdated by now.
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

    def _subscribe_commands(self):
        for cmd_type in ["number", "time", "button", "switch", "select", "config"]:
            for action in ["set", "read"]:
                topic = f"{settings.HA_BASE_TOPIC}/{cmd_type}/grobro/+/+/{action}"
                self._client.subscribe(topic)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        if msg.topic == settings.HA_STATUS_TOPIC:
            if msg.payload.decode(errors="ignore").strip() == "online":
                self._republish_after_ha_restart()
            return
        parts = msg.topic.removeprefix(f"{settings.HA_BASE_TOPIC}/").split("/")
        if len(parts) != 5 or parts[0] not in {"number", "time", "button", "switch", "select", "config"}:
            return
        cmd_type, _, device_id, cmd_name, action = parts

        LOG.debug("Received %s %s command %s for device %s", cmd_type, action, cmd_name, device_id)

        known_registers = get_known_registers(device_id)
        if not known_registers:
            LOG.info("Unknown device type: %s", device_id)
            return

        # Buttons
        if cmd_type == "button":
            if cmd_name == "read_all":
                # Send all modbus reads first
                for name, register in known_registers.holding_registers.items():
                    if name.startswith("slot"):
                        try:
                            if int(name[4]) > settings.MAX_SLOTS:
                                continue
                        except ValueError:
                            continue

                    pos = register.growatt.position
                    self.on_command(
                        make_modbus_command(
                            device_id,
                            GrowattModbusFunction.READ_SINGLE_REGISTER,
                            pos.register_no,
                        )
                    )

                # Queue config reads
                if self.on_config_read:
                    with self._config_read_lock:
                        q = self._config_read_queues.setdefault(device_id, deque())

                        for cfg in known_registers.config_registers.values():
                            q.append(cfg.growatt.register_no)

                    # give the datalogger time to answer modbus reads
                    kickoff = Timer(3.0, self._kickoff_next_config_read, args=(device_id,))
                    kickoff.daemon = True
                    kickoff.start()

                return

            if action == "read":
                reg = known_registers.holding_registers.get(cmd_name)
                if not reg:
                    LOG.error(
                        "Unknown read command %s for device %s",
                        cmd_name,
                        device_id,
                    )
                    return

                pos = reg.growatt.position

                self.on_command(
                    make_modbus_command(
                        device_id,
                        GrowattModbusFunction.READ_SINGLE_REGISTER,
                        pos.register_no,
                    )
                )

                return
                
        # Number / Switch / Time / Select
        if cmd_type in {"number", "switch", "time", "select"} and action == "set":
            raw_value = msg.payload.decode().strip()

            reg = known_registers.holding_registers.get(cmd_name)
            if not reg:
                LOG.error(
                    "Unknown holding register %s for device %s",
                    cmd_name,
                    device_id,
                )
                return

            try:
                parsed_value = _to_register_value(reg, cmd_type, raw_value)
            except ValueError as e:
                LOG.error("Invalid value %s for %s: %s", raw_value, cmd_name, e)
                return

            # Packed registers hold several fields, keep the other fields unchanged
            if reg.growatt.data.bit_width is not None:
                parsed_value = self._pack_register_value(device_id, known_registers, cmd_name, parsed_value)
                if parsed_value is None:
                    return

            pos = reg.growatt.position

            LOG.debug(
                "Setting %s register %s to value %s",
                cmd_name,
                pos.register_no,
                parsed_value,
            )

            self.on_command(
                make_modbus_command(
                    device_id,
                    GrowattModbusFunction.PRESET_SINGLE_REGISTER,
                    pos.register_no,
                    parsed_value,
                )
            )

            LOG.debug(
                "Triggering read-after-write for Command %s register %s",
                cmd_name,
                pos.register_no,
            )

            self.on_command(
                make_modbus_command(
                    device_id,
                    GrowattModbusFunction.READ_SINGLE_REGISTER,
                    pos.register_no,
                )
            )

            return

        # Config
        if cmd_type == "config" and action == "set":
            register_no = int(cmd_name)
            raw_value = msg.payload.decode().strip()

            # Special case: Sync Time (register 31)
            if register_no == 31:
                from datetime import datetime
                value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Normal config registers: use numeric or string value as-is
            else:
                value = raw_value

            if self.on_config_command:
                self.on_config_command(device_id, register_no, value)
            return

    def _schedule_time_sync(self):
        from datetime import datetime, timedelta

        now = datetime.now()
        candidates = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in settings.TIME_SYNC_HOURS]
        candidates += [c + timedelta(days=1) for c in candidates]
        next_sync = min(c for c in candidates if c > now)
        self._time_sync_timer = Timer((next_sync - now).total_seconds(), self.sync_time_of_all_devices, args=(True,))
        self._time_sync_timer.daemon = True
        self._time_sync_timer.start()

    def sync_time_of_all_devices(self, reschedule: bool = False):
        """Write the current time to every known device that supports it (config register 31)."""
        from datetime import datetime

        value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for device_id in list(self._discovery_cache):
            known_registers = get_known_registers(device_id)
            if not known_registers or not self.on_config_command:
                continue
            if any(reg.growatt.register_no == 31 for reg in known_registers.config_registers.values()):
                LOG.info("Synchronizing time of %s", device_id)
                self.on_config_command(device_id, 31, value)
        if reschedule:
            self._schedule_time_sync()

    def _republish_after_ha_restart(self):
        """Home Assistant restarted: send discovery and the last states again."""
        LOG.info("Home Assistant came online, republishing discovery and states")
        for device_id in list(self._discovery_cache):
            # Republish without clearing, so Home Assistant keeps the device
            self._discovery_payload_cache.pop(device_id, None)
            self._publish_device_discovery(device_id)
        for topic, state_str in list(self._last_states.items()):
            self._client.publish(topic, state_str, retain=settings.PUBLISH_SENSORS_RETAINED)

    def _pack_register_value(self, device_id: str, known_registers, cmd_name: str, field_value: int) -> Optional[int]:
        """Combine a new field value with the last known values of the other fields in the same register."""
        reg = known_registers.holding_registers[cmd_name]
        register_no = reg.growatt.position.register_no
        raw = 0
        for name, other in known_registers.holding_registers.items():
            data = other.growatt.data
            if other.growatt.position.register_no != register_no or data.bit_width is None:
                continue
            if name == cmd_name:
                value = field_value
            else:
                current = self._holding_values.get((device_id, name))
                if current is None:
                    LOG.warning(
                        "Unknown value of %s for %s, reading register %s first. Please try again.",
                        name, device_id, register_no,
                    )
                    self.on_command(
                        make_modbus_command(device_id, GrowattModbusFunction.READ_SINGLE_REGISTER, register_no)
                    )
                    return None
                try:
                    value = _to_register_value(other, other.homeassistant.type, current)
                except ValueError:
                    LOG.error(
                        "Invalid value %s of %s for %s, cannot change %s. Please set %s first.",
                        current, name, device_id, cmd_name, name,
                    )
                    return None
            raw |= (value & ((1 << data.bit_width) - 1)) << data.bit_shift
        return raw

    # ------------------- Internals -------------------

    def _save_config(self, config: model.DeviceConfig, path: str):
        try:
            config.to_file(path)
        except OSError as e:
            LOG.warning("Failed to persist config %s: %s", path, e)

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
        if (settings.AVAILABILITY_SENSOR):
            self._client.publish(
                f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/online",
                "ON" if online else "OFF",
                retain=settings.PUBLISH_SENSORS_RETAINED,
            )

    def _detect_neo_pv_count(self, device_id: str, payload: dict) -> None:
        if not (device_id.startswith("QMN") or device_id.startswith("PTQ") or device_id.startswith("VWQ")):
            return
        if device_id in self._neo_pv_count:
            return

        pv = payload.get("Ppv", 0) or 0
        p1 = payload.get("Ppv1", 0) or 0
        p2 = payload.get("Ppv2", 0) or 0
        p3 = payload.get("Ppv3", 0) or 0
        p4 = payload.get("Ppv4", 0) or 0

        if pv > 0:
            if abs(pv - (p1 + p2 + p3 + p4)) < 10 and (p3 > 0 or p4 > 0):
                LOG.info("Detected NEO 4-PV-input inverter: %s (Ppv=%s, sum4=%s)", device_id, pv, p1 + p2 + p3 + p4)
                self._neo_pv_count[device_id] = 4
                return
            if abs(pv - (p1 + p2)) < 10:
                self._neo_pv_count[device_id] = 2
                return

    def _publish_device_discovery(self, device_id: str, effective_max_bat: int | None = None):
        known_registers = get_known_registers(device_id)
        if not known_registers:
            LOG.info("Unable to publish unknown device type: %s", device_id)
            return
        if effective_max_bat is None:
            effective_max_bat = _resolve_max_bat(device_id)
        # Entities of batteries above the known count are explicitly removed
        # (component config with only the platform key), so HA doesn't keep
        # them as orphans. Skipped while the count is only a guess.
        remove_extra_bats = _bat_count_known(device_id)

        self._migrate_entity_discovery(device_id, known_registers)

        topic = f"{settings.HA_BASE_TOPIC}/device/{device_id}/config"

        # prepare discovery payload
        payload: dict = {
            "dev": self._device_info_from_config(device_id),
            "avty": [
                {"t": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/availability"},
                {"t": settings.BRIDGE_AVAILABILITY_TOPIC},
            ],
            "avty_mode": "all",
            "o": {"name": "grobro", "url": "https://github.com/robertzaage/GroBro"},
            "cmps": {},
        }

        # Commands (Modbus + Config)
        for entry in iter_command_registers(known_registers):
            ha = entry["ha"]
            if not ha.publish:
                continue

            # slot filtering applies only to modbus
            if not entry["is_config"] and entry["name"].startswith("slot"):
                try:
                    if int(entry["name"][4]) > settings.MAX_SLOTS:
                        continue
                except ValueError:
                    continue

            unique_id = f"grobro_{device_id}_cmd_{entry['name']}"
            platform = ha.type

            ha_data = ha.model_dump(exclude_none=True)

            # Home Assistant erwartet bei Select eine Liste, kein Dict
            if platform == "select":
                options = ha_data.get("options")
                if isinstance(options, dict):
                    ha_data["options"] = list(options.values())

            payload["cmps"][unique_id] = {
                "platform": platform,
                "name": ha.name,
                "unique_id": unique_id,
                "command_topic": (
                    f"{settings.HA_BASE_TOPIC}/{entry['topic_root']}/grobro/"
                    f"{device_id}/{entry['cmd_id']}/set"
                ),
                "state_topic": (
                    f"{settings.HA_BASE_TOPIC}/{entry['topic_root']}/grobro/"
                    f"{device_id}/{entry['state_id']}/get"
                ),
                **ha_data,
            }
        # Config command: Restart Datalogger (Register 32 / Value 1)
        restart_uid = f"grobro_{device_id}_restart_datalogger"
        payload["cmps"][restart_uid] = {
            "platform": "button",
            "name": "Restart Datalogger",
            "command_topic": f"{settings.HA_BASE_TOPIC}/config/grobro/{device_id}/32/set",
            "payload_press": "1",
            "icon": "mdi:restart",
            "unique_id": restart_uid
        }

        # Config command: Sync Time (register 31 / Value "%Y-%m-%d %H:%M:%S")
        time_sync_uid = f"grobro_{device_id}_sync_time"
        payload["cmps"][time_sync_uid] = {
            "platform": "button",
            "name": "Sync Time",
            "icon": "mdi:clock-outline",
            "command_topic": f"{settings.HA_BASE_TOPIC}/config/grobro/{device_id}/31/set",
            "unique_id": time_sync_uid
        }

        # Read-All Button
        payload["cmps"][f"grobro_{device_id}_cmd_read_all"] = {
            "command_topic": f"{settings.HA_BASE_TOPIC}/button/grobro/{device_id}/read_all/read",
            "platform": "button",
            "unique_id": f"grobro_{device_id}_cmd_read_all",
            "name": "Read All Values",
        }

        # States
        for state_name, state in known_registers.input_registers.items():
            if not state.homeassistant.publish:
                if not (self._neo_pv_count.get(device_id) == 4 and state_name in ("Vpv3", "Ipv3", "Ppv3", "Vpv4", "Ipv4", "Ppv4", "Epv3_today", "Epv3_total")):
                    continue
            if "_ser_part_" in state_name and state_name.startswith("bat"):
                continue
            unique_id = f"grobro_{device_id}_{state_name}"
            bat_num = _get_bat_number(state_name)
            if bat_num is not None and bat_num > effective_max_bat:
                if remove_extra_bats:
                    payload["cmps"][unique_id] = {"platform": "sensor"}
                continue
            payload["cmps"][unique_id] = {
                "platform": "sensor",
                "name": state.homeassistant.name,
                "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state",
                "value_template": f"{{{{ value_json['{state_name}'] }}}}",
                "unique_id": unique_id,
                "device_class": state.homeassistant.device_class,
                "state_class": state.homeassistant.state_class,
                "unit_of_measurement": state.homeassistant.unit_of_measurement,
                "icon": state.homeassistant.icon,
            }
            if state.homeassistant.entity_category:
                payload["cmps"][unique_id]["entity_category"] = state.homeassistant.entity_category

        # Combined status text (status, derating reason and fault code)
        if _has_status_summary(known_registers):
            summary_uid = f"grobro_{device_id}_Status_Summary"
            payload["cmps"][summary_uid] = {
                "platform": "sensor",
                "name": "Status",
                "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state",
                "value_template": "{{ value_json['Status_Summary'] | default('unknown') }}",
                "unique_id": summary_uid,
                "icon": "mdi:information-outline",
            }

        # Combined battery serial entities (NOAH only — has _ser_part_ registers)
        has_bat_ser_parts = any(
            name.startswith("bat") and "_ser_part_" in name
            for name in known_registers.input_registers
        )
        if has_bat_ser_parts:
            for bat_num in range(2, 5):
                combined_name = f"bat{bat_num}_serial"
                uid = f"grobro_{device_id}_{combined_name}"
                if bat_num > effective_max_bat:
                    if remove_extra_bats:
                        payload["cmps"][uid] = {"platform": "sensor"}
                    continue
                payload["cmps"][uid] = {
                    "platform": "sensor",
                    "name": f"Bat{bat_num} Serial",
                    "entity_category": "diagnostic",
                    "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state",
                    "value_template": f"{{{{ value_json['{combined_name}'] }}}}",
                    "unique_id": uid,
                    "icon": "mdi:identifier",
                }
                
        # Combined firmware version (NOAH = 3 parts, NEXA = 4 parts)
        fw_version_parts = sorted(
            name
            for name in known_registers.input_registers
            if name.startswith("fw_version_part_")
        )

        if fw_version_parts:
            combined_name = "fw_version"
            firmware_unique_id = f"grobro_{device_id}_{combined_name}"

            if device_id.startswith(_COMBINED_FIRMWARE_PREFIXES):
                # Composed like the ShinePhone app shows it, see compose_combined_firmware
                value_template = "{{ value_json['fw_version'] }}"
            else:
                value_template = ".".join(
                    f"{{{{ value_json['{part}'] }}}}"
                    for part in fw_version_parts
                )

            payload["cmps"][firmware_unique_id] = {
                "platform": "sensor",
                "name": "Firmware Version",
                "entity_category": "diagnostic",
                "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state",
                "value_template": value_template,
                "unique_id": firmware_unique_id,
                "icon": "mdi:information",
            }
                
        # Serial Number Entity
        serial_unique_id = f"grobro_{device_id}_serial"
        payload["cmps"][serial_unique_id] = {
            "platform": "sensor",
            "name": "Device SN",
            "entity_category": "diagnostic",
            "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/serial",
            "unique_id": serial_unique_id,
            "icon": "mdi:identifier",
        }
        
        # Device Type Entity
        type_unique_id = f"grobro_{device_id}_type"
        payload["cmps"][type_unique_id] = {
            "platform": "sensor",
            "name": "Device Type",
            "entity_category": "diagnostic",
            "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/type",
            "unique_id": type_unique_id,
            "icon": "mdi:chip",
        }

        # Online Entity
        if settings.DEVICE_TIMEOUT > 0 and settings.AVAILABILITY_SENSOR:
            online_unique_id = f"grobro_{device_id}_online"
            payload["cmps"][online_unique_id] = {
                "platform": "binary_sensor",
                "name": "Online",
                "entity_category": "diagnostic",
                "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/online",
                "device_class": "connectivity",
                "unique_id": online_unique_id,
            }

        combined_firmware = self._combined_firmware_versions.get(device_id)
        if combined_firmware and device_id.startswith(_COMBINED_FIRMWARE_PREFIXES):
            payload["dev"]["sw_version"] = combined_firmware

        payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        old_payload_str = self._discovery_payload_cache.get(device_id)
        if old_payload_str == payload_str:
            LOG.debug("Discovery unchanged for %s, skipping", device_id)
            if device_id not in self._discovery_cache:
                self._discovery_cache.append(device_id)
            # trotzdem States aktualisieren
            self._client.publish(f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/serial", device_id, retain=True)
            self._client.publish(f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/type", get_device_type_name(device_id), retain=True)
            return

        LOG.info("Publishing updated discovery for %s", device_id)
        # Only clear the discovery when it changed while running. Clearing it after a restart
        # would make Home Assistant delete and recreate the device and its customizations.
        if old_payload_str is not None:
            self._client.publish(topic, "", retain=True)
        self._client.publish(topic, payload_str, retain=True)
        self._discovery_payload_cache[device_id] = payload_str
        if device_id not in self._discovery_cache:
            self._discovery_cache.append(device_id)

        self._client.publish(f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/serial", device_id, retain=True)
        self._client.publish(f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/type", get_device_type_name(device_id), retain=True)

    def _migrate_entity_discovery(self, device_id: str, known_registers: GroBroRegisters):
        old_entities = [("set_wirk", "number")]
        for e_name, e_type in old_entities:
            self._client.publish(
                f"{settings.HA_BASE_TOPIC}/{e_type}/grobro/{device_id}_{e_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )
        for cmd_name, cmd in known_registers.holding_registers.items():
            cmd_type = cmd.homeassistant.type
            self._client.publish(
                f"{settings.HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}_{cmd_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )
            self._client.publish(
                f"{settings.HA_BASE_TOPIC}/{cmd_type}/grobro/{device_id}_{cmd_name}_read/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )
        for state_name in known_registers.input_registers:
            self._client.publish(
                f"{settings.HA_BASE_TOPIC}/sensor/grobro/{device_id}_{state_name}/config",
                json.dumps({"migrate_discovery": True}),
                retain=True,
            )

    def _device_info_from_config(self, device_id: str):
        # Find matching config
        config = self._config_cache.get(device_id)
        config_path = os.path.join(settings.CONFIG_DIR, f"config_{device_id}.json")

        # Fallback: try loading from file
        if not config:
            config = model.DeviceConfig.from_file(config_path)
            self._config_cache[device_id] = config
            LOG.info(f"Loaded cached config for {device_id} from file (fallback)")

        # Fallback 2: save minimal config if it was neither in cache nor on disk
        if not config:
            config = model.DeviceConfig(serial_number=device_id)
            self._save_config(config, config_path)
            self._config_cache[device_id] = config
            LOG.info(f"Saved minimal config for new device: {config}")

        # Device Info for HA
        device_info: dict = {
            "identifiers": [device_id],
            "name": f"Growatt {device_id}",
            "manufacturer": "Growatt",
            "serial_number": device_id,
        }

        type_name = get_device_type_name(device_id)

        known_model_id = {
            "55": "NEO-series",
            "72": "NEXA-series",
            "61": "NOAH-series",
        }.get(getattr(config, "device_type", None))

        if device_id in DETECTED_DEVICES:
            # Identified by inverter serial or device type code, e.g. "MOD TL3-XH"
            device_info["model"] = type_name
        elif known_model_id:
            device_info["model"] = known_model_id
        else:
            device_info["model"] = f"{type_name}-series"
        # Devices behind a datalogger show the serial of their inverter
        behind_datalogger = device_id in DETECTED_DEVICES or device_id.startswith(GENERIC_DATALOGGER_PREFIXES)
        if behind_datalogger and device_id in INVERTER_SERIALS:
            device_info["serial_number"] = INVERTER_SERIALS[device_id]

        if getattr(config, "model_id", None):
            device_info["model"] += f" ({config.model_id})"
        if getattr(config, "sw_version", None):
            device_info["sw_version"] = config.sw_version
        if getattr(config, "hw_version", None):
            device_info["hw_version"] = config.hw_version
        if getattr(config, "mac_address", None):
            import re
            if re.match(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$", config.mac_address):
                device_info["connections"] = [["mac", config.mac_address]]

        return device_info

    def _kickoff_next_config_read(self, device_id: str):
        with self._config_read_lock:
            # already waiting for a response
            if device_id in self._config_read_inflight:
                return

            q = self._config_read_queues.get(device_id)
            if not q:
                return

            register_no = q.popleft()
            self._config_read_inflight[device_id] = register_no

        if self.on_config_read:
            self.on_config_read(device_id, register_no)

        # start 1 minute timeout
        timer = Timer(
            60,
            self._config_read_timeout,
            args=(device_id, register_no),
        )
        timer.daemon = True
        self._config_read_timers[device_id] = timer
        timer.start()

    def _config_read_timeout(self, device_id: str, register_no: int):
        with self._config_read_lock:
            inflight = self._config_read_inflight.get(device_id)
            if inflight != register_no:
                return

            LOG.warning(
                "Config read timeout for %s register=%s",
                device_id,
                register_no,
            )

            self._config_read_inflight.pop(device_id, None)
            self._config_read_timers.pop(device_id, None)

        # continue with next queued register
        self._kickoff_next_config_read(device_id)

    def handle_config_read_response(self, device_id: str, register_no: int, value: str | int):
        topic = f"{settings.HA_BASE_TOPIC}/config/grobro/{device_id}/{register_no}/get"
        self._client.publish(topic, value, retain=True)

        with self._config_read_lock:
            inflight = self._config_read_inflight.get(device_id)
            if inflight != register_no:
                return

            LOG.debug(
                "Config read completed for %s register=%s",
                device_id,
                register_no,
            )

            timer = self._config_read_timers.pop(device_id, None)
            if timer:
                timer.cancel()

            self._config_read_inflight.pop(device_id, None)

        # continue with next queued read
        self._kickoff_next_config_read(device_id)


# ------------------- Combined NOAH/NEXA firmware -------------------

_COMBINED_FIRMWARE_PREFIXES = ("0PVP", "0HVR")


def _firmware_part_names(payload: dict) -> list[str]:
    """Return firmware part keys in numeric part order."""
    names = [name for name in payload if name.startswith("fw_version_part_")]

    def part_number(name: str) -> int:
        try:
            return int(name.rsplit("_", 1)[1])
        except (TypeError, ValueError):
            return 9999

    return sorted(names, key=part_number)


def _compact_datalogger_version(value: object) -> str | None:
    """Convert a dotted numeric datalogger version to the ShinePhone suffix."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    parts = text.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None

    return "".join(parts)


def compose_combined_firmware(payload: dict, datalogger_version: object) -> str | None:
    """Build a ShinePhone-style firmware version from received values."""
    names = _firmware_part_names(payload)
    if not names:
        return None

    base_parts: list[str] = []
    for name in names:
        value = payload.get(name)
        if value is None or str(value).strip() == "":
            return None
        base_parts.append(str(value).strip())

    base_version = ".".join(base_parts)
    suffix = _compact_datalogger_version(datalogger_version)
    return f"{base_version}.{suffix}" if suffix else base_version

