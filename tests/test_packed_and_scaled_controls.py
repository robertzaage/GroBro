import struct
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.grobro.builder import scramble
from grobro.grobro.client import Client
from grobro.ha.client import Client as HaClient
from grobro.model.growatt_registers import KNOWN_MOD_REGISTERS, KNOWN_XH2_REGISTERS
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.mqtt_config import MQTTConfig

XH2_ID = "ZGQ0000TEST0001"
# Slot begin register: bit 15 enabled, bits 13-14 mode, bits 0-12 start time
SLOT1_BEGIN = 0x8000 | (1 << 13) | (5 * 256 + 30)


def _msg(topic, payload=b""):
    msg = MagicMock(spec=MQTTMessage)
    msg.topic = topic
    msg.payload = payload
    msg.qos = 0
    msg.retain = False
    msg.properties.json.return_value = {"UserProperty": []}
    return msg


@pytest.mark.parametrize("registers", [KNOWN_XH2_REGISTERS, KNOWN_MOD_REGISTERS])
def test_packed_slot_fields_are_decoded(registers):
    raw = struct.pack(">H", SLOT1_BEGIN)
    holding = registers.holding_registers
    assert holding["slot1_enabled"].growatt.data.parse(raw) == 1
    assert holding["slot1_mode"].growatt.data.parse(raw) == "Battery First"
    assert holding["slot1_start_time"].growatt.data.parse(raw) == "05:30"
    assert holding["slot5_start_time"].growatt.position.register_no == 3050


def test_single_register_response_publishes_all_slot_fields():
    with patch("grobro.grobro.client.mqtt.Client"):
        client = Client(MQTTConfig(host="localhost", port=1883), MQTTConfig(host="localhost", port=1883))
    client.on_holding_register_input = MagicMock()
    response = struct.pack(">HHHBB30sHHH", 1, 7, 38, 1, 5, XH2_ID.encode().ljust(30, b"\x00"), 3038, 3038, SLOT1_BEGIN)
    client._client.on_message(None, None, _msg(f"c/33/{XH2_ID}", scramble(response + b"\x00\x00")))

    values = {v.name: v.value for v in client.on_holding_register_input.call_args.args[0].payload}
    assert values["slot1_enabled"] == "ON"
    assert values["slot1_mode"] == "Battery First"
    assert values["slot1_start_time"] == "05:30"


@pytest.fixture
def ha_client():
    with patch("grobro.ha.client.mqtt.Client"), patch("grobro.ha.client.os.listdir", return_value=[]):
        client = HaClient(MQTTConfig(host="localhost", port=1883))
    client.on_command = MagicMock()
    return client


def _known_slot1(ha_client):
    ha_client._holding_values.update({
        (XH2_ID, "slot1_enabled"): "ON",
        (XH2_ID, "slot1_mode"): "Battery First",
        (XH2_ID, "slot1_start_time"): "05:30",
    })


@pytest.mark.parametrize(
    "platform, name, payload, expected",
    [
        ("select", "slot1_mode", b"Grid First", 0x8000 | (2 << 13) | (5 * 256 + 30)),
        ("switch", "slot1_enabled", b"OFF", (1 << 13) | (5 * 256 + 30)),
        ("time", "slot1_start_time", b"22:15", 0x8000 | (1 << 13) | (22 * 256 + 15)),
    ],
)
def test_packed_write_keeps_other_fields(ha_client, platform, name, payload, expected):
    _known_slot1(ha_client)
    ha_client._client.on_message(None, None, _msg(f"homeassistant/{platform}/grobro/{XH2_ID}/{name}/set", payload))
    command = ha_client.on_command.call_args_list[0].args[0]
    assert command.function == GrowattModbusFunction.PRESET_SINGLE_REGISTER
    assert command.register_no == 3038
    assert command.value == expected


def test_packed_write_with_unknown_fields_reads_first(ha_client):
    ha_client._client.on_message(None, None, _msg(f"homeassistant/select/grobro/{XH2_ID}/slot1_mode/set", b"Grid First"))
    command = ha_client.on_command.call_args_list[0].args[0]
    assert ha_client.on_command.call_count == 1
    assert command.function == GrowattModbusFunction.READ_SINGLE_REGISTER
    assert command.register_no == 3038


def test_published_holding_values_are_cached(ha_client):
    from grobro.model.growatt_registers import HomeAssistantHoldingRegisterInput, HomeAssistantHoldingRegisterValue

    reg = KNOWN_XH2_REGISTERS.holding_registers["slot1_mode"].homeassistant
    ha_client.publish_holding_register_input(HomeAssistantHoldingRegisterInput(
        device_id=XH2_ID,
        payload=[HomeAssistantHoldingRegisterValue(name="slot1_mode", value="Grid First", register=reg)],
    ))
    assert ha_client._holding_values[(XH2_ID, "slot1_mode")] == "Grid First"


@pytest.mark.parametrize(
    "name, payload, register_no, value",
    [
        ("export_limit", b"-12.5", 123, (-125) & 0xFFFF),
        ("export_limit", b"80", 123, 800),
        ("peak_shaving_import_limit", b"7.5", 3307, 75),
        ("charge_stop_soc", b"90.0", 3048, 90),
    ],
)
def test_scaled_number_writes(ha_client, name, payload, register_no, value):
    ha_client._client.on_message(None, None, _msg(f"homeassistant/number/grobro/{XH2_ID}/{name}/set", payload))
    command = ha_client.on_command.call_args_list[0].args[0]
    assert command.register_no == register_no
    assert command.value == value


def test_signed_export_limit_is_read_back():
    reg = KNOWN_XH2_REGISTERS.holding_registers["export_limit"]
    assert reg.growatt.data.parse(struct.pack(">H", (-125) & 0xFFFF)) == pytest.approx(-12.5)
