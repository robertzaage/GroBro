"""Processing of input and holding register values into Home Assistant states."""

from __future__ import annotations

import json
import logging
from typing import Optional

from grobro import settings
from grobro.model.growatt_codes import fault_text, warning_texts
from grobro.model.growatt_registers import (
    HomeAssistantHoldingRegisterInput,
    HomeAssistantInputRegister,
    get_known_registers,
)
from grobro.ha.firmware import _COMBINED_FIRMWARE_PREFIXES, compose_combined_firmware

LOG = logging.getLogger(__name__)


# Highest detected battery count per device (persisted with the value cache)
_MAX_BAT_CACHE: dict[str, int] = {}
# Battery serials per position, for KEEP_BATTERY_POSITION
_LAST_BAT_SERIALS: dict[str, dict[int, str]] = {}


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


def _mark_absent_temperatures(payload: dict, known_registers) -> set[str]:
    """Absent temperature sensors report a raw 0 K, which decodes to -273.1 °C.

    They are published as unknown and never filled in from the value cache.
    """
    absent_keys: set[str] = set()
    if not known_registers:
        return absent_keys
    for key, value in list(payload.items()):
        reg = known_registers.input_registers.get(key)
        if not reg:
            continue
        if getattr(reg.homeassistant, "device_class", None) != "temperature":
            continue
        if isinstance(value, (int, float)) and value == -273.1:
            payload[key] = None
            absent_keys.add(key)
    return absent_keys


def _drop_batteries_above(payload: dict, max_bat: int):
    for key in list(payload.keys()):
        bat_num = _get_bat_number(key)
        if bat_num is not None and bat_num > max_bat:
            del payload[key]


def _combine_battery_serials(payload: dict):
    """Combine the serial parts of battery 2-4 into single values."""
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


def _check_battery_positions(device_id: str, payload: dict):
    """KEEP_BATTERY_POSITION: warn when a battery moved to another position."""
    current_serials: dict[int, str] = {}
    for bat_num in range(2, 5):
        key = f"bat{bat_num}_serial"
        if key in payload and payload[key]:
            current_serials[bat_num] = str(payload[key])
    prev_serials = _LAST_BAT_SERIALS.get(device_id, {})
    if prev_serials and current_serials:
        for pos, serial in current_serials.items():
            for prev_pos, prev_serial in prev_serials.items():
                if serial == prev_serial and pos != prev_pos:
                    LOG.warning(
                        "Battery %s moved from Bat%d to Bat%d for device %s — inverter re-enumeration detected",
                        serial, prev_pos, pos, device_id,
                    )
    _LAST_BAT_SERIALS[device_id] = current_serials


class StateMixin:
    """Publishes device values as Home Assistant states."""

    def publish_input_register(self, state: HomeAssistantInputRegister):
        LOG.debug("HA: publish: %s", state)
        device_id = state.device_id
        state = self._add_combined_firmware(state)
        prev_max_bat = _MAX_BAT_CACHE.get(device_id)
        effective_max_bat = _resolve_max_bat(device_id, state.payload)
        if _MAX_BAT_CACHE.get(device_id) != prev_max_bat:
            self._value_cache_dirty.add(device_id)
        self._detect_neo_pv_count(device_id, state.payload)
        # discovery + availability
        self._publish_device_discovery(device_id, effective_max_bat)
        self._publish_availability(device_id, True)
        if settings.DEVICE_TIMEOUT > 0:
            self._reset_device_timer(device_id)

        payload = dict(state.payload)
        known_registers = get_known_registers(device_id)
        absent_keys = _mark_absent_temperatures(payload, known_registers)
        _drop_batteries_above(payload, effective_max_bat)
        # ENUM mapping, must come after the replacements above
        if known_registers:
            for key, value in list(payload.items()):
                reg = known_registers.input_registers.get(key)
                if reg:
                    payload[key] = map_enum_value(reg, value)

        status_summary = _status_summary(payload, known_registers)
        if status_summary:
            payload["Status_Summary"] = status_summary

        if settings.FILTER_DATA_GLITCHES and known_registers:
            self._filter_energy_glitches(device_id, payload, known_registers)
        _combine_battery_serials(payload)
        if settings.KEEP_BATTERY_POSITION:
            _check_battery_positions(device_id, payload)

        if settings.KEEP_LAST_VALUES:
            self._fill_in_last_values(device_id, known_registers, state.payload, payload, absent_keys, effective_max_bat)
        self._maybe_save_value_cache(device_id)

        topic = f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/state"
        state_str = json.dumps(payload, separators=(",", ":"))
        # Also used when Home Assistant restarts, see _republish_after_ha_restart
        self._last_states[topic] = state_str
        self._client.publish(topic, state_str, retain=settings.PUBLISH_SENSORS_RETAINED)

    def _filter_energy_glitches(self, device_id: str, payload: dict, known_registers):
        """Prevent glitches on total_increasing sensors (energy counters): a value never decreases."""
        for key, value in list(payload.items()):
            reg = known_registers.input_registers.get(key)
            if not reg:
                continue
            if getattr(reg.homeassistant, "state_class", None) == "total_increasing":
                device_key = (device_id, key)
                last_value = self._last_energy_values.get(device_key)
                if isinstance(value, (int, float)):
                    if last_value is not None and value < last_value:
                        payload[key] = last_value
                        LOG.debug("Suppressed decrease for %s/%s: %.1f -> %.1f", device_id, key, last_value, value)
                    else:
                        self._last_energy_values[device_key] = value

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

    def publish_holding_register_input(self, ha_input: HomeAssistantHoldingRegisterInput):
        try:
            LOG.debug("HA: publish: %s", ha_input)
            for value in ha_input.payload:
                topic = f"{settings.HA_BASE_TOPIC}/{value.register_def.type}/grobro/{ha_input.device_id}/{value.name}/get"
                self._holding_values[(ha_input.device_id, value.name)] = str(value.value)
                self._client.publish(topic, value.value, retain=True)
        except Exception as e:
            LOG.error(f"HA: publish msg: {e}")

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
