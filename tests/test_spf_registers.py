import struct

import pytest

from grobro.model.growatt_registers import KNOWN_SPF_REGISTERS


@pytest.mark.parametrize(
    "key, register_no",
    [
        ("AC_Charger_VA", 15),
        ("AC_In_VA", 38),
        ("AC_Discharge_VA", 71),
        ("Battery_Discharge_Watts", 73),
        ("Battery_Discharge_VA", 75),
    ],
)
def test_spf_power_values_are_32_bit(key, register_no):
    reg = KNOWN_SPF_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.growatt.position.size == 4
    # High word 0, low word 12345 -> 1234.5, previously only the high word was read
    assert reg.growatt.data.parse(struct.pack("!HH", 0, 12345)) == pytest.approx(1234.5)


@pytest.mark.parametrize(
    "key, register_no, size, raw, expected",
    [
        ("Export_Today", 45, 2, struct.pack("!H", 12), 1.2),
        ("Export_Total", 46, 4, struct.pack("!I", 1234), 123.4),
        ("PV2_Energy_Today", 52, 4, struct.pack("!I", 55), 5.5),
        ("Battery_Charge_Total", 100, 4, struct.pack("!I", 999), 99.9),
        ("Generator_Voltage", 97, 2, struct.pack("!H", 2301), 230.1),
        ("AC_Input_Power", 36, 4, struct.pack("!i", -5000), -500.0),
    ],
)
def test_spf_registers_from_protocol_v026(key, register_no, size, raw, expected):
    reg = KNOWN_SPF_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.growatt.position.size == size
    assert reg.growatt.data.parse(raw) == pytest.approx(expected)
