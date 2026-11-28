"""Home Assistant MQTT device discovery."""

from __future__ import annotations

import json
import logging
import math
import os

import grobro.model as model
from grobro import settings, version
from grobro.ha.bridge import bridge_id
from grobro.ha.commands import supports_meter_reading
from grobro.model.growatt_registers import (
    DETECTED_DEVICES,
    GENERIC_DATALOGGER_PREFIXES,
    INVERTER_SERIALS,
    GroBroRegisters,
    get_device_type_name,
    get_known_registers,
)
from grobro.ha.firmware import _COMBINED_FIRMWARE_PREFIXES
from grobro.ha.state import _bat_count_known, _get_bat_number, _has_status_summary, _resolve_max_bat

LOG = logging.getLogger(__name__)


def display_precision(data) -> int | None:
    """Decimals a scaled value really has, e.g. 1 for a value in 0.1 steps."""
    if data.data_type not in ("FLOAT", "SIGNED_FLOAT"):
        return None
    multiplier = data.mult if data.mult is not None else (data.float_options.multiplier if data.float_options else 1)
    if not multiplier or multiplier >= 1:
        return 0
    return max(0, round(-math.log10(multiplier)))


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


class DiscoveryMixin:
    """Publishes the Home Assistant discovery of a device."""

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
            "o": {"name": "grobro", "sw": version.VERSION, "url": "https://github.com/robertzaage/GroBro"},
            "cmps": {},
        }

        payload["cmps"].update(self._command_components(device_id, known_registers))
        payload["cmps"].update(self._sensor_components(device_id, known_registers, effective_max_bat, remove_extra_bats))
        self._publish_discovery_payload(device_id, topic, payload)

    def _command_components(self, device_id: str, known_registers: GroBroRegisters) -> dict:
        """Controls: holding and config registers, datalogger restart, time sync and read all."""
        cmps: dict = {}
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

            # Home Assistant expects a list of options for selects, not a dict
            if platform == "select":
                options = ha_data.get("options")
                if isinstance(options, dict):
                    ha_data["options"] = list(options.values())

            cmps[unique_id] = {
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
        cmps[restart_uid] = {
            "platform": "button",
            "name": "Restart Datalogger",
            "command_topic": f"{settings.HA_BASE_TOPIC}/config/grobro/{device_id}/32/set",
            "payload_press": "1",
            "icon": "mdi:restart",
            "unique_id": restart_uid
        }

        # Config command: Sync Time (register 31 / Value "%Y-%m-%d %H:%M:%S")
        time_sync_uid = f"grobro_{device_id}_sync_time"
        cmps[time_sync_uid] = {
            "platform": "button",
            "name": "Sync Time",
            "icon": "mdi:clock-outline",
            "command_topic": f"{settings.HA_BASE_TOPIC}/config/grobro/{device_id}/31/set",
            "unique_id": time_sync_uid
        }

        # Read-All Button
        cmps[f"grobro_{device_id}_cmd_read_all"] = {
            "command_topic": f"{settings.HA_BASE_TOPIC}/button/grobro/{device_id}/read_all/read",
            "platform": "button",
            "unique_id": f"grobro_{device_id}_cmd_read_all",
            "name": "Read All Values",
        }

        # Smart meter reading supplied by Home Assistant (NOAH, NEXA), see commands.py
        if supports_meter_reading(known_registers):
            meter_uid = f"grobro_{device_id}_cmd_meter_reading"
            cmps[meter_uid] = {
                "platform": "number",
                "name": "Smart Meter Reading",
                "unique_id": meter_uid,
                "command_topic": f"{settings.HA_BASE_TOPIC}/number/grobro/{device_id}/meter_reading/set",
                "min": -10000,
                "max": 10000,
                "step": 1,
                "mode": "box",
                "unit_of_measurement": "W",
                "device_class": "power",
                "icon": "mdi:meter-electric-outline",
            }
            clear_uid = f"grobro_{device_id}_cmd_meter_reading_clear"
            cmps[clear_uid] = {
                "platform": "button",
                "name": "Clear Smart Meter Reading",
                "unique_id": clear_uid,
                "command_topic": f"{settings.HA_BASE_TOPIC}/button/grobro/{device_id}/meter_reading/set",
                "icon": "mdi:meter-electric-outline",
            }
        return cmps

    def _sensor_components(self, device_id: str, known_registers: GroBroRegisters, effective_max_bat: int, remove_extra_bats: bool) -> dict:
        """Sensors: input registers and the combined entities."""
        cmps: dict = {}
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
                    cmps[unique_id] = {"platform": "sensor"}
                continue
            cmps[unique_id] = {
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
            precision = display_precision(state.growatt.data)
            if precision is not None and state.homeassistant.unit_of_measurement:
                cmps[unique_id]["suggested_display_precision"] = precision
            if state.homeassistant.entity_category:
                cmps[unique_id]["entity_category"] = state.homeassistant.entity_category

        # Combined status text (status, derating reason and fault code)
        if _has_status_summary(known_registers):
            summary_uid = f"grobro_{device_id}_Status_Summary"
            cmps[summary_uid] = {
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
                        cmps[uid] = {"platform": "sensor"}
                    continue
                cmps[uid] = {
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

            cmps[firmware_unique_id] = {
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
        cmps[serial_unique_id] = {
            "platform": "sensor",
            "name": "Device SN",
            "entity_category": "diagnostic",
            "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/serial",
            "unique_id": serial_unique_id,
            "icon": "mdi:identifier",
        }
        
        # Device Type Entity
        type_unique_id = f"grobro_{device_id}_type"
        cmps[type_unique_id] = {
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
            cmps[online_unique_id] = {
                "platform": "binary_sensor",
                "name": "Online",
                "entity_category": "diagnostic",
                "state_topic": f"{settings.HA_BASE_TOPIC}/grobro/{device_id}/online",
                "device_class": "connectivity",
                "unique_id": online_unique_id,
            }
        return cmps

    def _publish_discovery_payload(self, device_id: str, topic: str, payload: dict):
        """Publish the discovery if it changed and refresh the serial and type topics."""
        combined_firmware = self._combined_firmware_versions.get(device_id)
        if combined_firmware and device_id.startswith(_COMBINED_FIRMWARE_PREFIXES):
            payload["dev"]["sw_version"] = combined_firmware

        payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        old_payload_str = self._discovery_payload_cache.get(device_id)
        if old_payload_str == payload_str:
            LOG.debug("Discovery unchanged for %s, skipping", device_id)
            if device_id not in self._discovery_cache:
                self._discovery_cache.append(device_id)
            # Still refresh the serial and type topics
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
            "via_device": bridge_id(),
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
