import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.grobro.builder import scramble
from grobro.grobro.client import Client
from grobro.grobro.parser import unscramble
from grobro.model.growatt_registers import KNOWN_MOD_REGISTERS
from grobro.model.mqtt_config import MQTTConfig

DATA_DIR = Path(__file__).parent / "model" / "data"

# NeoReadInputRegisters.bin carries the 3000-3124 and 3125-3249 blocks used by MOD as well.
# Register 3000 starts after the 38 byte header, 37 byte metadata and 4 byte block header,
# register 3125 after the 125 registers of the first block and another block header.
BLOCK_STARTS = {3000: 79, 3125: 79 + 125 * 2 + 4}


def _offset(register_no):
    start = 3125 if register_no >= 3125 else 3000
    return BLOCK_STARTS[start] + (register_no - start) * 2


@pytest.fixture
def mod_payload():
    frame = bytearray(unscramble((DATA_DIR / "NeoReadInputRegisters.bin").read_bytes()))
    values = {
        3001: struct.pack(">I", 123456),  # Ppv 12345.6 W, above the 16 bit limit
        3023: struct.pack(">i", -5000),  # Pac -500.0 W
        3025: struct.pack(">H", 5001),  # Fac 50.01 Hz
        3026: struct.pack(">H", 2301),  # Vac1 230.1 V
        3027: struct.pack(">h", -15),  # Iac1 -1.5 A
        3034: struct.pack(">H", 2355),  # Vac3 235.5 V
        3051: struct.pack(">I", 98765),  # Eac_total 9876.5 kWh
        3083: struct.pack(">I", 123),  # Epv_today 12.3 kWh
        3093: struct.pack(">H", 412),  # Temp1 41.2 °C
        3170: struct.pack(">h", -25),  # Ibat -2.5 A
        3171: struct.pack(">H", 87),  # SOC 87 %
    }
    for register_no, raw in values.items():
        offset = _offset(register_no)
        frame[offset : offset + len(raw)] = raw

    with patch("grobro.grobro.client.mqtt.Client"):
        client = Client(MQTTConfig(host="localhost", port=1883), MQTTConfig(host="localhost", port=1883))
    client.on_input_register = MagicMock()
    msg = MagicMock(spec=MQTTMessage)
    msg.topic = "c/33/VWQ0000TEST0001"
    msg.payload = scramble(bytes(frame))
    msg.qos = 0
    msg.retain = False
    msg.properties.json.return_value = {"UserProperty": []}
    client._client.on_message(None, None, msg)
    return client.on_input_register.call_args.args[0].payload


@pytest.mark.parametrize(
    "key, expected",
    [
        ("Ppv", 12345.6),
        ("Pac", -500.0),
        ("Fac", 50.01),
        ("Vac1", 230.1),
        ("Iac1", -1.5),
        ("Vac3", 235.5),
        ("Eac_total", 9876.5),
        ("Epv_today", 12.3),
        ("Temp1", 41.2),
        ("Ibat", -2.5),
        ("SOC", 87),
    ],
)
def test_mod_decodes_standard_3000_layout(mod_payload, key, expected):
    assert mod_payload[key] == pytest.approx(expected)


def test_mod_publishes_all_three_phases():
    for key in ("Vac1", "Vac2", "Vac3", "Iac1", "Iac2", "Iac3", "Pac1", "Pac2", "Pac3"):
        assert KNOWN_MOD_REGISTERS.input_registers[key].homeassistant.publish, key


def test_mod_exposes_inverter_controls():
    holding = KNOWN_MOD_REGISTERS.holding_registers
    assert holding["inverter_power"].growatt.position.register_no == 0
    assert holding["output_power_limit"].growatt.position.register_no == 3
