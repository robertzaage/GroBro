"""Smart meter readings supplied by Home Assistant to NOAH and NEXA (holding registers 310-312)."""

import json
import struct
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.grobro.builder import scramble
from grobro.ha.client import Client
from grobro.ha.commands import make_meter_reading_command, supports_meter_reading
from grobro.model.growatt_registers import (
    KNOWN_NEO_REGISTERS,
    KNOWN_NEXA_REGISTERS,
    KNOWN_NOAH_REGISTERS,
)
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.mqtt_config import MQTTConfig

DATA_DIR = __file__[: __file__.rfind("/")] + "/model/data"
NEXA_ID = "0HVR0000TEST0001"
NOAH_ID = "0PVP0000TEST0001"


def _msg(topic, payload=b""):
    msg = MagicMock(spec=MQTTMessage)
    msg.topic = topic
    msg.payload = payload
    return msg


@pytest.fixture
def client():
    with patch("grobro.ha.client.mqtt.Client"), patch("grobro.ha.client.os.listdir", return_value=[]):
        c = Client(MQTTConfig(host="localhost", port=1883))
    c.on_command = MagicMock()
    return c


def _values(cmd):
    return struct.unpack(">3H", cmd.values)


def test_supported_families():
    assert supports_meter_reading(KNOWN_NEXA_REGISTERS)
    assert supports_meter_reading(KNOWN_NOAH_REGISTERS)
    assert not supports_meter_reading(KNOWN_NEO_REGISTERS)


@pytest.mark.parametrize(
    "watts, expected",
    [
        (250, (0, 250, 1)),  # import in 311
        (-400, (400, 0, 1)),  # export in 310
        (0, (0, 0, 1)),  # a balanced grid is a valid reading
        (None, (0, 0, 0)),  # withdraws the reading
    ],
)
def test_meter_reading_registers(watts, expected):
    cmd = make_meter_reading_command(NEXA_ID, watts)
    assert cmd.function == GrowattModbusFunction.PRESET_MULTIPLE_REGISTER
    assert (cmd.start, cmd.end) == (310, 312)
    assert _values(cmd) == expected


def test_frame_matches_the_growatt_cloud():
    """The captured cloud write of a 63 W import, apart from the CRC (the serial was anonymized)."""
    captured = open(f"{DATA_DIR}/NoahPresetMultiple_310-312.bin", "rb").read()
    ours = scramble(make_meter_reading_command(NOAH_ID, 63).build_grobro())
    assert ours == captured[:-2]


@pytest.mark.parametrize("device_id", [NEXA_ID, NOAH_ID])
def test_number_command_sends_reading(client, device_id):
    client._client.on_message(None, None, _msg(f"homeassistant/number/grobro/{device_id}/meter_reading/set", b"-123.6"))
    cmd = client.on_command.call_args.args[0]
    assert cmd.device_id == device_id
    assert _values(cmd) == (124, 0, 1)
    # A reading has no read-back, it is reported in the input registers
    assert client.on_command.call_count == 1


def test_clear_button_withdraws_reading(client):
    client._client.on_message(None, None, _msg(f"homeassistant/button/grobro/{NEXA_ID}/meter_reading/set", b"PRESS"))
    assert _values(client.on_command.call_args.args[0]) == (0, 0, 0)


@pytest.mark.parametrize("payload", [b"abc", b"nan", b"inf", b"70000", b""])
def test_invalid_reading_is_rejected(client, payload):
    client._client.on_message(None, None, _msg(f"homeassistant/number/grobro/{NEXA_ID}/meter_reading/set", payload))
    client.on_command.assert_not_called()


def test_unsupported_device_is_rejected(client):
    client._client.on_message(None, None, _msg("homeassistant/number/grobro/QMN0000000000/meter_reading/set", b"100"))
    client.on_command.assert_not_called()


def _discovery(client, device_id):
    client._publish_device_discovery(device_id)
    for c in client._client.publish.call_args_list:
        if c.args[0] == f"homeassistant/device/{device_id}/config":
            return json.loads(c.args[1])
    raise AssertionError("no discovery published")


@pytest.mark.parametrize("device_id", [NEXA_ID, NOAH_ID])
def test_discovery_has_meter_controls(client, device_id):
    cmps = _discovery(client, device_id)["cmps"]
    number = cmps[f"grobro_{device_id}_cmd_meter_reading"]
    assert number["platform"] == "number"
    assert number["command_topic"] == f"homeassistant/number/grobro/{device_id}/meter_reading/set"
    assert number["min"] == -10000 and number["max"] == 10000
    assert "state_topic" not in number
    button = cmps[f"grobro_{device_id}_cmd_meter_reading_clear"]
    assert button["command_topic"] == f"homeassistant/button/grobro/{device_id}/meter_reading/set"


def test_discovery_of_other_devices_has_no_meter_controls(client):
    cmps = _discovery(client, "QMN0000000000")["cmps"]
    assert not any("meter_reading" in uid for uid in cmps)
