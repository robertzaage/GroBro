"""Firmware version of NOAH and NEXA devices, composed like the ShinePhone app shows it."""

from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)


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
