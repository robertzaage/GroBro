#!/usr/bin/env python3
"""
One-shot script to enable HA publish for fields useful in a typical
MIN-XH2 deployment (grid+load+battery+core diagnostics) and to clean up
some confusingly-named entities.

Run from repo root: python scripts/enable_xh2_entities.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent.parent / "grobro/model/growatt_xh2_registers.json"

# Fields to enable + optional rename. Value None = keep existing name.
ENABLE: dict[str, str | None] = {
    # ── Grid power & energy (live + cumulative)
    "Ptouser_total":     "Grid import power",
    "Ptogrid_total":     "Grid export power",
    "Ptoload_total":     "Load power",
    "Etouser_today":     "Grid import today",
    "Etouser_total":     "Lifetime grid import",
    "Etogrid_today":     "Grid export today",
    "Etogrid_total":     "Lifetime grid export",
    "Eload_today":       "Load consumption today",
    "Eload_total":       "Lifetime load consumption",

    # ── Battery state (read-only, will populate when battery installed)
    "Vbat":              "Battery voltage",
    "Ibat":              "Battery current",
    "SOC":               "Battery SOC",
    "Pchr":              "Battery charge power",
    "Pdischr":           "Battery discharge power",
    "Echr_today":        "Battery charged today",
    "Echr_total":        "Lifetime battery charged",
    "Edischr_today":     "Battery discharged today",
    "Edischr_total":     "Lifetime battery discharged",
    "Eacchr_today":      "AC charge today",
    "Eacchr_total":      "Lifetime AC charge",

    # ── BMS (battery management system) – useful when battery installed
    "BMS_Status":        "BMS status",
    "BMS_SOC":           "BMS SOC",
    "BMS_BatteryVolt":   "BMS battery voltage",
    "BMS_BatteryCurr":   "BMS battery current",
    "BMS_BatteryTemp":   "BMS battery temperature",
    "BMS_SOH":           "BMS state of health",
    "BMS_CycleCnt":      "BMS cycle count",

    # ── BDC / Battery DC Coupler
    "BDC_OnOffState":    "Battery DC coupler state",

    # ── Inverter status / diagnostics
    "Inverter_Status":   None,        # keep "Inverter run state ..."
    "Fault_code":        "Inverter fault code",
    "Warning_code":      "Inverter warning code",

    # ── Bus / DC link (helpful for diagnosing inverter health)
    "N_Bus_Voltage":     "N bus voltage",

    # ── Total work time (uptime)
    "Time_total":        "Total work time",
}


def main() -> int:
    raw = json.loads(JSON_PATH.read_text())
    input_regs = raw.setdefault("input_registers", {})

    enabled, missing = [], []
    for name, new_label in ENABLE.items():
        reg = input_regs.get(name)
        if not reg:
            missing.append(name)
            continue
        ha = reg.setdefault("homeassistant", {})
        ha["publish"] = True
        if new_label:
            ha["name"] = new_label
        enabled.append(name)

    JSON_PATH.write_text(json.dumps(raw, indent=2) + "\n")

    print(f"Enabled {len(enabled)} fields in {JSON_PATH.name}")
    for n in enabled:
        print(f"  ✓ {n}")
    if missing:
        print(f"\n⚠ Not found in JSON (skipped): {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
