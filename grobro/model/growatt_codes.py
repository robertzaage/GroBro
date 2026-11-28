"""Readable texts for Growatt fault and warning codes, used by the combined status sensor.

Only tables from the official Growatt protocol documents are listed here:
- SPH/SPA: inverter fault code (input register 105), Modbus RTU Protocol II V1.20 (&*1)
- SPF: fault code (input register 40) and warning bits (41/42), OffGrid Modbus Protocol V0.26 (&*1, &*8)

The fault main code of NEO, TL-XH and MOD inverters (input register 3105) has no table in
the protocol documents, so its raw code is shown.
"""

from typing import Optional

from grobro.model.growatt_registers import (
    GroBroRegisters,
    KNOWN_SPF_REGISTERS,
    KNOWN_SPH_REGISTERS,
)

# Inverter fault code &*1, values 1-23 are shown as "Error 100" to "Error 122" on the inverter
LEGACY_FAULT_TEXTS: dict[int, str] = {
    **{code: f"Error {99 + code}" for code in range(1, 24)},
    24: "Auto test failed",
    25: "No AC connection",
    26: "PV isolation low",
    27: "Residual current high",
    28: "Output DC current high",
    29: "PV voltage high",
    30: "AC voltage out of range",
    31: "AC frequency out of range",
    32: "Module hot",
}

SPF_FAULT_TEXTS: dict[int, str] = {
    1: "Fan lock",
    2: "Over temperature",
    3: "Battery voltage high",
    4: "Battery voltage low",
    5: "Output short circuit",
    6: "Output voltage high",
    7: "Overload",
    8: "Bus voltage high",
    9: "Bus soft start failed",
    11: "Main relay failed",
    51: "Over current",
    52: "Bus voltage low",
    53: "Inverter soft start failed",
    56: "IGBT over current",
    58: "Output voltage low",
    60: "Negative power",
    61: "PV voltage high",
    62: "Internal communication error",
    80: "CAN communication failed",
    81: "Host lost",
}

SPF_WARNING_BITS: dict[int, str] = {
    0: "Fan lock",
    1: "Battery overcharged",
    2: "Battery voltage low",
    3: "Overload",
    4: "Output power derating",
    5: "Solar stopped, battery low",
    6: "Solar stopped, PV voltage high",
    7: "Solar stopped, overload",
    8: "Parallel grid input different",
    9: "Parallel grid phase error",
    10: "Parallel output phase loss",
    11: "Over temperature",
    12: "Buck current high",
    13: "Battery disconnected",
    14: "BMS communication error",
    15: "PV power insufficient",
}

SPF_WARNING_BITS_HIGH: dict[int, str] = {
    0: "No battery, parallel disabled",
    1: "Parallel version different",
    3: "Parallel capacity different",
    4: "Host lost",
    5: "BMS cell over voltage",
    6: "BMS total over voltage",
    7: "BMS discharge over current",
    8: "BMS charge over current",
    9: "BMS over temperature",
    10: "Battery voltage inconsistent",
}

# Per register map: (fault register, fault texts), [(warning register, bit texts), ...]
_CODE_TABLES: list[tuple[GroBroRegisters, Optional[tuple[str, dict[int, str]]], list[tuple[str, dict[int, str]]]]] = [
    (KNOWN_SPH_REGISTERS, ("Fault_code", LEGACY_FAULT_TEXTS), []),
    (
        KNOWN_SPF_REGISTERS,
        ("Fault_code", SPF_FAULT_TEXTS),
        [("Warning_code", SPF_WARNING_BITS), ("Fault_value", SPF_WARNING_BITS_HIGH)],
    ),
]


def _tables(known_registers: Optional[GroBroRegisters]):
    for registers, fault, warnings in _CODE_TABLES:
        if registers is known_registers:
            return fault, warnings
    return None, []


def fault_text(known_registers: Optional[GroBroRegisters], code: int) -> str:
    """Readable text of a fault code, e.g. "PV isolation low (26)", or "Code 26" if unknown."""
    fault, _ = _tables(known_registers)
    text = fault[1].get(code) if fault else None
    return f"{text} ({code})" if text else f"Code {code}"


def warning_texts(known_registers: Optional[GroBroRegisters], payload: dict) -> list[str]:
    """Readable texts of all active warning bits in a payload."""
    _, warnings = _tables(known_registers)
    texts = []
    for key, bits in warnings:
        value = payload.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        value = int(value)
        texts.extend(text for bit, text in bits.items() if value & (1 << bit))
    return texts
