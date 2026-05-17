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

NEO_TEST_DEVICE_ID = "QMN000ABC1D2E3FG"
NOAH_TEST_DEVICE_ID = "0PVP0000TEST0001"
DATA_DIR = Path(__file__).parent / "data"


def expects_noah_devid(fname):
    """Return True if the .bin file is a NOAH device (starts with Noah)."""
    return fname.startswith("Noah")

ALL_BIN_FILES = [
    # Original hand-crafted files
    "NeoInputRegister.bin",
    "NeoOutputPowerLimit.bin",
    "NeoReadOutputPowerLimit.bin",
    "NeoSetDateTime.bin",
    "NeoSetInterval.bin",
    "NeoSetMQTTHost.bin",
    "NeoSetMQTTPort.bin",
    "NeoSetOTAUpdate.bin",
    "NeoSetOutputPowerLimit.bin",
    # NEO Modbus
    "NeoReadInputRegisters.bin",
    "NeoReadSingleRegister_3.bin",
    # NEO Config
    "NeoConfigReadResponse_337.bin",
    "NeoConfigReadResponse_314.bin",
    "NeoConfigReadResponse_346.bin",
    "NeoConfigReadResponse_315.bin",
    "NeoConfigWriteAck_DataInterval.bin",
    "NeoConfigWriteAck_MQTTPort.bin",
    "NeoConfigWriteAck_MQTTHost.bin",
    "NeoConfigWriteAck_OTA.bin",
    "NeoConfigWriteAck_FromDevice.bin",
    "NeoConfigTLV_340.bin",
    "NeoConfigTLV_341.bin",
    # NOAH Modbus
    "NoahReadInputRegisters_0-124.bin",
    "NoahPresetSingle_OutputLimit.bin",
    "NoahPresetMultiple_ChargeLimit.bin",
    "NoahPresetMultiple_310-312.bin",
    "NoahPresetMultiple_SlotCmd.bin",
    # NOAH Specific types
    "NoahType0103_HoldingRegs.bin",
    "NoahType0110_PresetMResp.bin",
    "NoahType0125_SerialResp.bin",
    "NoahTypeFE18_DateTime.bin",
    "NoahTypeFE19_Config.bin",
    "NoahTypeFE19_DevStatus.bin",
    "NoahTypeFE19_Config2.bin",
    "NoahTypeFE19_Config3.bin",
    "NoahTypeFE19_Msg.bin",
    "NoahTypeFE25_Empty.bin",
    # NOAH Config
    "NoahConfigWriteAck_Firmware1.bin",
    "NoahConfigWriteAck_Firmware2.bin",
    "NoahConfigWriteAck_mDNS1.bin",
    "NoahConfigWriteAck_mDNS2.bin",
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


@pytest.mark.parametrize("file_name", ALL_BIN_FILES)
def test_no_original_serial(file_name):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    text = unscrambled.decode("ascii", errors="ignore")
    assert "0PVP40ZR15ST0066" not in text
    assert "QMN000BZP2N1T1KY" not in text


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
        ("NeoSetMQTTHost.bin", 4352, "mqtt.example.com"),
        ("NeoSetMQTTPort.bin", 2048, "8883"),
        ("NeoSetOTAUpdate.bin", 15872, "cdn.growatt.com"),
    ],
)
def test_config_messages(file_name, exp_register, exp_value_contains):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_config_message(unscrambled)
    assert result["device_id"] == (NOAH_TEST_DEVICE_ID if expects_noah_devid(file_name) else NEO_TEST_DEVICE_ID)
    assert result["config_type"] == 1
    assert result["register_no"] == exp_register
    assert exp_value_contains in result["value"]


MODBUS_ROUNDTRIP_FILES = [
    ("NeoInputRegister.bin", GrowattModbusMessage),
    ("NeoOutputPowerLimit.bin", GrowattModbusMessage),
    ("NeoReadOutputPowerLimit.bin", GrowattModbusFunctionSingle),
    ("NeoSetOutputPowerLimit.bin", GrowattModbusFunctionSingle),
    ("NeoReadInputRegisters.bin", GrowattModbusMessage),
    ("NeoReadSingleRegister_3.bin", GrowattModbusMessage),
]

MODBUS_PARSE_FILES = [
    "NoahReadInputRegisters_0-124.bin",
    "NoahPresetSingle_OutputLimit.bin",
]


@pytest.mark.parametrize(("file_name", "msg_type"), MODBUS_ROUNDTRIP_FILES)
def test_modbus_roundtrip(file_name, msg_type):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    parsed = msg_type.parse_grobro(unscrambled)
    assert parsed is not None, f"Failed to parse {file_name}"
    assert parsed.device_id == NEO_TEST_DEVICE_ID
    rebuilt = parsed.build_grobro()
    expect = unscrambled[:-2]
    assert rebuilt == expect, f"Round-trip mismatch in {file_name}"


@pytest.mark.parametrize("file_name", MODBUS_PARSE_FILES)
def test_modbus_parse(file_name):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    parsed = GrowattModbusMessage.parse_grobro(unscrambled)
    assert parsed is not None, f"Failed to parse {file_name}"
    assert parsed.device_id == NOAH_TEST_DEVICE_ID


CONFIG_READ_RESP_FILES = [
    "NeoConfigReadResponse_337.bin",
    "NeoConfigReadResponse_314.bin",
    "NeoConfigReadResponse_346.bin",
    "NeoConfigReadResponse_315.bin",
]


@pytest.mark.parametrize("file_name", CONFIG_READ_RESP_FILES)
def test_config_read_response(file_name):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_config_message(unscrambled)
    assert result["device_id"] == NEO_TEST_DEVICE_ID
    assert result["message_type"] == 0x0119


CONFIG_WRITE_ACK_FILES = [
    "NeoConfigWriteAck_DataInterval.bin",
    "NeoConfigWriteAck_MQTTPort.bin",
    "NeoConfigWriteAck_MQTTHost.bin",
    "NeoConfigWriteAck_OTA.bin",
    "NeoConfigWriteAck_FromDevice.bin",
    "NoahConfigWriteAck_Firmware1.bin",
    "NoahConfigWriteAck_Firmware2.bin",
    "NoahConfigWriteAck_mDNS1.bin",
    "NoahConfigWriteAck_mDNS2.bin",
]


@pytest.mark.parametrize("file_name", CONFIG_WRITE_ACK_FILES)
def test_config_write_ack(file_name):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_config_ack(unscrambled)
    expected = NOAH_TEST_DEVICE_ID if expects_noah_devid(file_name) else NEO_TEST_DEVICE_ID
    assert result["device_id"] == expected
    assert result["message_type"] == 0x0118


CONFIG_TLV_FILES = [
    "NeoConfigTLV_340.bin",
    "NeoConfigTLV_341.bin",
    "NoahTypeFE19_Config.bin",
    "NoahTypeFE19_Config3.bin",
]


@pytest.mark.parametrize("file_name", CONFIG_TLV_FILES)
def test_config_tlv(file_name):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    msg_type = struct.unpack_from(">H", unscrambled, 4)[0]
    assert msg_type in (340, 341, 387, 388)
    config_offset = parser.find_config_offset(unscrambled)
    config = parser.parse_config_type(unscrambled, config_offset)
    assert config is not None
    expected = NOAH_TEST_DEVICE_ID if expects_noah_devid(file_name) else NEO_TEST_DEVICE_ID
    assert config.serial_number == expected


def test_noah_preset_multiple_charge_limit():
    data = (DATA_DIR / "NoahPresetMultiple_ChargeLimit.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_message(unscrambled)
    assert result is not None
    assert result["message_type"] in (0x0110, 0x0111)


def test_noah_type0103_holding_registers():
    data = (DATA_DIR / "NoahType0103_HoldingRegs.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_0103(unscrambled)
    assert result["message_type"] == 0x0103
    assert result["device_id"] == NOAH_TEST_DEVICE_ID
    assert result["register_count"] > 0


def test_noah_type0110_response():
    data = (DATA_DIR / "NoahType0110_PresetMResp.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_0110(unscrambled)
    assert result["message_type"] == 0x0110
    assert result["device_id"] == NOAH_TEST_DEVICE_ID
    assert len(result["registers"]) > 0


def test_noah_type0125_serial_response():
    data = (DATA_DIR / "NoahType0125_SerialResp.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_0125(unscrambled)
    assert result["message_type"] == 0x0125
    assert result["device_id"] == NOAH_TEST_DEVICE_ID


def test_noah_typeFE18_datetime():
    data = (DATA_DIR / "NoahTypeFE18_DateTime.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_fe18(unscrambled)
    assert result["message_type"] == 0xFE18
    assert result["device_id"] == NOAH_TEST_DEVICE_ID
    assert "2025" in result["datetime"]


def test_noah_typeFE19_devstatus():
    data = (DATA_DIR / "NoahTypeFE19_DevStatus.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_fe19(unscrambled)
    assert result["message_type"] == 0xFE19
    assert result["device_id"] == NOAH_TEST_DEVICE_ID
    assert result["subtype"] == parser.FE19_SUBTYPE_DEV_STATUS


NOAH_FE19_CONFIG_FILES = [
    "NoahTypeFE19_Config.bin",
    "NoahTypeFE19_Config2.bin",
    "NoahTypeFE19_Config3.bin",
]


@pytest.mark.parametrize("file_name", NOAH_FE19_CONFIG_FILES)
def test_noah_typeFE19_full_config(file_name):
    data = (DATA_DIR / file_name).read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_fe19(unscrambled)
    assert result["message_type"] == 0xFE19
    assert result["device_id"] == NOAH_TEST_DEVICE_ID
    assert result["subtype"] == parser.FE19_SUBTYPE_FULL_CONFIG
    cfg = result["config"]
    assert cfg.serial_number == NOAH_TEST_DEVICE_ID, f"serial={cfg.serial_number}, tlv_offset={result.get('tlv_offset')}"


def test_noah_typeFE19_msg():
    data = (DATA_DIR / "NoahTypeFE19_Msg.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_fe19(unscrambled)
    assert result["message_type"] == 0xFE19
    assert result["device_id"] == NOAH_TEST_DEVICE_ID
    assert result["subtype"] == parser.FE19_SUBTYPE_DEV_STATUS


def test_noah_typeFE25_empty():
    data = (DATA_DIR / "NoahTypeFE25_Empty.bin").read_bytes()
    unscrambled = parser.unscramble(data)
    result = parser.parse_noah_fe25(unscrambled)
    assert result["message_type"] == 0xFE25
    assert result["device_id"] == NOAH_TEST_DEVICE_ID
    assert result["is_empty"]


def test_all_bin_files_device_id_sanitized():
    for fname in ALL_BIN_FILES:
        data = (DATA_DIR / fname).read_bytes()
        plain = parser.unscramble(data)
        dev = plain[8:24].rstrip(b'\x00').decode('ascii', errors='replace') if len(plain) >= 24 else ""
        if dev and dev != ("1" * 16):
            expected = NOAH_TEST_DEVICE_ID if expects_noah_devid(fname) else NEO_TEST_DEVICE_ID
            assert dev == expected, f"{fname}: device_id '{dev}' != '{expected}'"


if __name__ == "__main__":
    pass
