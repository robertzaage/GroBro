from pathlib import Path
import pytest
import struct

from grobro.grobro import parser
from grobro.model.modbus_message import (
    GrowattModbusMessage,
    GrowattModbusFunction,
    GrowattModbusBlock,
    GrowattMetadata,
)
from grobro.grobro.builder import append_crc, scramble
from datetime import datetime
from grobro.model.modbus_function import GrowattModbusFunctionSingle

TEST_DEVICE_ID = "QMN000ABC1D2E3FG"
DATA_DIR = Path(__file__).parent / "data"

ALL_BIN_FILES = [
    "NeoInputRegister.bin",
    "NeoOutputPowerLimit.bin",
    "NeoReadOutputPowerLimit.bin",
    "NeoSetDateTime.bin",
    "NeoSetInterval.bin",
    "NeoSetMQTTHost.bin",
    "NeoSetMQTTPort.bin",
    "NeoSetOTAUpdate.bin",
    "NeoSetOutputPowerLimit.bin",
]


def test_all_binary_files_exist():
    for fname in ALL_BIN_FILES:
        path = DATA_DIR / fname
        assert path.exists(), f"Missing binary file: {path}"
        data = path.read_bytes()
        assert len(data) > 0, f"Empty binary file: {path}"


@pytest.mark.parametrize("file_name", ALL_BIN_FILES)
def test_unscramble(file_name):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    assert len(unscrambled) == len(data)


CONFIG_BIN_FILES = [
    "NeoSetDateTime.bin",
    "NeoSetInterval.bin",
    "NeoSetMQTTHost.bin",
    "NeoSetMQTTPort.bin",
    "NeoSetOTAUpdate.bin",
]


@pytest.mark.parametrize(
    ("file_name", "exp_register", "exp_value_contains"),
    [
        ("NeoSetDateTime.bin", 5888, "2025-04-25"),
        ("NeoSetInterval.bin", 1280, "1"),
        ("NeoSetMQTTHost.bin", 4352, "mqtt.zaage.it"),
        ("NeoSetMQTTPort.bin", 2048, "8883"),
        ("NeoSetOTAUpdate.bin", 15872, "cdn.growatt.com"),
    ],
)
def test_config_messages(file_name, exp_register, exp_value_contains):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_config_message(unscrambled)
    assert result["device_id"] == "QMN000BZP2N1T1KY"
    assert result["config_type"] == 1
    assert result["register_no"] == exp_register
    assert exp_value_contains in result["value"]


@pytest.mark.parametrize(
    ("want_msg", "file_name"),
    [
        (
            GrowattModbusMessage(
                unknown=1,
                device_id=TEST_DEVICE_ID,
                function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                register_blocks=[
                    GrowattModbusBlock(
                        start=3,
                        end=3,
                        values=struct.pack(">H", 42),
                    ),
                ],
            ),
            "NeoOutputPowerLimit.bin",
        ),
        (
            GrowattModbusFunctionSingle(
                device_id=TEST_DEVICE_ID,
                function=GrowattModbusFunction.PRESET_SINGLE_REGISTER,
                register=3,
                value=42,
            ),
            "NeoSetOutputPowerLimit.bin",
        ),
        (
            GrowattModbusFunctionSingle(
                device_id=TEST_DEVICE_ID,
                function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                register=3,
                value=3,
            ),
            "NeoReadOutputPowerLimit.bin",
        ),
        (
            GrowattModbusMessage(
                unknown=106,
                device_id=TEST_DEVICE_ID,
                metadata=GrowattMetadata(
                    device_sn=TEST_DEVICE_ID[-10:],
                    timestamp=datetime(2025, 5, 2, 14, 23, 11, 2000),
                ),
                function=GrowattModbusFunction.READ_INPUT_REGISTER,
                register_blocks=[
                    GrowattModbusBlock(
                        start=3000,
                        end=3124,
                        values=b"\x00\x01\x00\x00\x04\xa7\x014\x00\x13\x00\x00\x02T\x01:\x00\x12\x00\x00\x02S\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x04\x03\x13\x88\t/\x00\x04\x00\x00\x04\x11\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\t/\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00V\x11\xe8\x00\x00\x00\x01\x00\x00\x07\x13\x00\x00\x07\x9d\x00\x00\x00\x01\x00\x00\x03\xbf\x00\x00\x00\x01\x00\x00\x03\xde\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00\x00\x0f\xec\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x8e\x01\x8e\x01\x8e\x01\x8e\x00\x00\x11\x80\x00\x00M\xd7\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00A\x00\x00\x00\x00\x00\x00\x00\x00P&\x00\x00\x00\x00\x00\x00\x00\x00",
                    ),
                    GrowattModbusBlock(
                        start=3125,
                        end=3249,
                        values=b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\t\x157\x00\x00/\xe4\x00\x01\x00\x00",
                    ),
                ],
            ),
            "NeoInputRegister.bin",
        ),
    ],
)
def test_double(want_msg, file_name):
    fixture_path = DATA_DIR / file_name
    with open(fixture_path, "rb") as f:
        want_raw = parser.unscramble(f.read())
        got_msg = type(want_msg).parse_grobro(want_raw)
        assert got_msg == want_msg
    got_raw = got_msg.build_grobro()
    assert got_raw == want_raw[0:-2]


if __name__ == "__main__":
    msgs = [
        (
            "NeoReadOutputPowerLimit.bin",
            GrowattModbusFunctionSingle(
                device_id=TEST_DEVICE_ID,
                function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                register=3,
                value=3,
            ),
        ),
        (
            "NeoSetOutputPowerLimit.bin",
            GrowattModbusFunctionSingle(
                device_id=TEST_DEVICE_ID,
                function=GrowattModbusFunction.PRESET_SINGLE_REGISTER,
                register=3,
                value=42,
            ),
        ),
        (
            "NeoOutputPowerLimit.bin",
            GrowattModbusMessage(
                unknown=1,
                device_id=TEST_DEVICE_ID,
                function=GrowattModbusFunction.READ_SINGLE_REGISTER,
                register_blocks=[
                    GrowattModbusBlock(
                        start=3,
                        end=3,
                        values=struct.pack(">H", 42),
                    ),
                ],
            ),
        ),
        (
            "NeoInputRegister.bin",
            GrowattModbusMessage(
                unknown=106,
                device_id=TEST_DEVICE_ID,
                metadata=GrowattMetadata(
                    device_sn=TEST_DEVICE_ID[-10:],
                    timestamp=datetime(2025, 5, 2, 14, 23, 11, 2000),
                ),
                function=GrowattModbusFunction.READ_INPUT_REGISTER,
                register_blocks=[
                    GrowattModbusBlock(
                        start=3000,
                        end=3124,
                        values=b"\x00\x01\x00\x00\x04\xa7\x014\x00\x13\x00\x00\x02T\x01:\x00\x12\x00\x00\x02S\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x04\x03\x13\x88\t/\x00\x04\x00\x00\x04\x11\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\t/\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00V\x11\xe8\x00\x00\x00\x01\x00\x00\x07\x13\x00\x00\x07\x9d\x00\x00\x00\x01\x00\x00\x03\xbf\x00\x00\x00\x01\x00\x00\x03\xde\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00\x00\x0f\xec\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x8e\x01\x8e\x01\x8e\x01\x8e\x00\x00\x11\x80\x00\x00M\xd7\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00A\x00\x00\x00\x00\x00\x00\x00\x00P&\x00\x00\x00\x00\x00\x00\x00\x00",
                    ),
                    GrowattModbusBlock(
                        start=3125,
                        end=3249,
                        values=b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\t\x157\x00\x00/\xe4\x00\x01\x00\x00",
                    ),
                ],
            ),
        ),
    ]
    for fname, msg in msgs:
        with open(DATA_DIR / fname, "wb") as f:
            msg_raw = msg.build_grobro()
            f.write(append_crc(scramble(msg_raw)))
