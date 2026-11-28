import struct

import pytest

from grobro.model.growatt_registers import KNOWN_NEXA_REGISTERS, KNOWN_NOAH_REGISTERS


@pytest.mark.parametrize("slot", range(1, 10))
def test_nexa_slot_power_allows_1000w(slot):
    reg = KNOWN_NEXA_REGISTERS.holding_registers[f"slot{slot}_power"]
    assert reg.homeassistant.max == 1000


def test_nexa_default_power_allows_1000w():
    assert KNOWN_NEXA_REGISTERS.holding_registers["default_power"].homeassistant.max == 1000


def test_noah_slot_power_stays_at_800w():
    assert KNOWN_NOAH_REGISTERS.holding_registers["slot1_power"].homeassistant.max == 800


@pytest.mark.parametrize(
    "key, register_no, raw, expected",
    [
        ("battery1Temp", 30, 2821, 9.0),
        ("battery2Temp", 42, 2781, 5.0),
        ("battery3Temp", 54, 2821, 9.0),
        ("battery4Temp", 66, 2821, 9.0),
        ("pv1Temp", 94, 2827, 9.6),
        ("pv2Temp", 97, 2827, 9.6),
        ("systemTemp", 98, 2835, 10.4),
        ("pv3Temp", 105, 2845, 11.4),
        ("pv4Temp", 108, 2845, 11.4),
    ],
)
def test_nexa_temperatures_are_reported_in_tenth_kelvin(key, register_no, raw, expected):
    reg = KNOWN_NEXA_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.homeassistant.device_class == "temperature"
    assert reg.growatt.data.parse(struct.pack("!H", raw)) == pytest.approx(expected)


def test_nexa_absent_temperature_decodes_to_zero_kelvin():
    reg = KNOWN_NEXA_REGISTERS.input_registers["battery2Temp"]
    assert reg.growatt.data.parse(struct.pack("!H", 0)) == -273.1


@pytest.mark.parametrize("raw, expected", [(0, "Off"), (1, "1 On"), (2, "2 On"), (3, "1&2 On")])
def test_nexa_heater_is_pack_bitmask_in_register_14(raw, expected):
    reg = KNOWN_NEXA_REGISTERS.input_registers["heater"]
    assert reg.growatt.position.register_no == 14
    assert reg.growatt.data.parse(struct.pack("!H", raw)) == expected


def test_nexa_power_plus_switch():
    reg = KNOWN_NEXA_REGISTERS.holding_registers["power_plus"]
    assert reg.growatt.position.register_no == 325
    assert reg.homeassistant.type == "switch"
