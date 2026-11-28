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


@pytest.mark.parametrize(
    "key, register_no, raw, expected, unit",
    [
        ("register_110", 110, 30100, 1.0, "A"),
        ("register_111", 111, 31000, 100.0, "W"),
        ("register_112", 112, 520, 52.0, "V"),
        ("register_114", 114, 4500, 450.0, "V"),
        ("onGridPower", 116, 29000, -100.0, "W"),
        ("register_117", 117, 29900, -1.0, "A"),
    ],
)
def test_nexa_grid_and_inverter_scaling(key, register_no, raw, expected, unit):
    reg = KNOWN_NEXA_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.homeassistant.unit_of_measurement == unit
    assert reg.growatt.data.parse(struct.pack("!H", raw)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "key, register_no", [("eacToday", 71), ("eacMonth", 73), ("eacYear", 75), ("eacTotal", 77)]
)
def test_nexa_energy_counters_are_32_bit(key, register_no):
    reg = KNOWN_NEXA_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.growatt.position.size == 4
    assert reg.growatt.data.parse(struct.pack("!HH", 1, 5)) == pytest.approx(6554.1)


@pytest.mark.parametrize(
    "key, register_no, raw, expected",
    [
        ("meterConnected", 6, 1, 1),
        ("offGridModeActive", 18, 1, 1),
        ("meterPower", 19, 30250, 250.0),
        ("batteryDischargeEnergyToday", 79, 12, 1.2),
        ("batteryChargeEnergyToday", 80, 34, 3.4),
        ("acOutputEnergyToday", 82, 56, 5.6),
        ("offGridVoltage", 109, 2301, 230.1),
        ("inverterTemp", 113, 2931, 20.0),
        ("batteryCurrent", 118, 30025, 2.5),
    ],
)
def test_nexa_additional_sensors(key, register_no, raw, expected):
    reg = KNOWN_NEXA_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.homeassistant.publish
    assert reg.growatt.data.parse(struct.pack("!H", raw)) == pytest.approx(expected)


def test_nexa_work_mode_2_is_smart_mode():
    reg = KNOWN_NEXA_REGISTERS.input_registers["workMode"]
    assert reg.growatt.data.parse(struct.pack("!H", 2)) == "Smart Mode"


def test_nexa_default_mode_select():
    holding = KNOWN_NEXA_REGISTERS.holding_registers
    assert "smart_mode" not in holding
    reg = holding["default_mode"]
    assert reg.growatt.position.register_no == 321
    assert reg.homeassistant.type == "select"
    assert reg.homeassistant.options == {"0": "Load First", "1": "Battery First", "2": "Smart Mode"}


@pytest.mark.parametrize(
    "key, register_no, platform",
    [("ac_couple", 303, "switch"), ("always_on", 304, "switch"), ("export_limitation", 323, "switch"), ("export_limitation_power", 324, "number")],
)
def test_nexa_additional_controls(key, register_no, platform):
    reg = KNOWN_NEXA_REGISTERS.holding_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.homeassistant.type == platform


@pytest.mark.parametrize("key, register_no", [("internalFaults", 2), ("gridFaults", 3), ("outputFaults", 4)])
def test_nexa_fault_words_are_diagnostic(key, register_no):
    reg = KNOWN_NEXA_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.homeassistant.entity_category == "diagnostic"


@pytest.mark.parametrize(
    "key, register_no",
    [
        ("batteryFaults", 15),
        ("battery1Alarm", 31),
        ("battery1Protection", 32),
        ("battery2Alarm", 43),
        ("battery2Protection", 44),
        ("battery3Alarm", 55),
        ("battery3Protection", 56),
        ("battery4Alarm", 67),
        ("battery4Protection", 68),
        ("smartPlugConnected", 88),
        ("smartPlugPower", 89),
    ],
)
def test_nexa_alarm_and_smart_plug_registers(key, register_no):
    reg = KNOWN_NEXA_REGISTERS.input_registers[key]
    assert reg.growatt.position.register_no == register_no
    assert reg.growatt.data.parse(struct.pack("!H", 0x0021)) == 0x0021


DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@pytest.mark.parametrize("slot", range(1, 10))
def test_nexa_slot_weekdays(slot):
    """One weekday mask per slot from register 342, Monday in the lowest bit."""
    for bit, day in enumerate(DAYS):
        reg = KNOWN_NEXA_REGISTERS.holding_registers[f"slot{slot}_{day}"]
        assert reg.growatt.position.register_no == 342 + 2 * (slot - 1)
        assert reg.homeassistant.type == "switch"
        assert reg.growatt.data.parse(struct.pack("!H", 1 << bit)) == 1
        assert reg.growatt.data.parse(struct.pack("!H", 0x7F & ~(1 << bit))) == 0


def test_nexa_weekday_write_keeps_other_days():
    from unittest.mock import MagicMock, patch

    from paho.mqtt.client import MQTTMessage

    from grobro.ha.client import Client
    from grobro.model.mqtt_config import MQTTConfig

    device_id = "0HVR0000TEST0001"
    with patch("grobro.ha.client.mqtt.Client"), patch("grobro.ha.client.os.listdir", return_value=[]):
        client = Client(MQTTConfig(host="localhost", port=1883))
    client.on_command = MagicMock()
    # Weekdays known from the last read: Monday to Friday
    for bit, day in enumerate(DAYS):
        client._holding_values[(device_id, f"slot1_{day}")] = "ON" if bit < 5 else "OFF"

    msg = MagicMock(spec=MQTTMessage)
    msg.topic = f"homeassistant/switch/grobro/{device_id}/slot1_saturday/set"
    msg.payload = b"ON"
    client._client.on_message(None, None, msg)

    write = client.on_command.call_args_list[0].args[0]
    assert write.register_no == 342
    assert write.value == 0b0111111
    # read back once
    assert client.on_command.call_args_list[1].args[0].register_no == 342
