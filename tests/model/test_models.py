import os
import json
import struct
import tempfile
from datetime import datetime

import pytest

from grobro.model.device_config import DeviceConfig
from grobro.model.modbus_function import (
    GrowattModbusFunctionMultiple,
    GrowattModbusFunctionSingle,
)
from grobro.model.modbus_message import (
    GrowattModbusBlock,
    GrowattModbusFunction,
    GrowattMetadata,
    GrowattModbusMessage,
)
from grobro.model.mqtt_config import MQTTConfig
from grobro.model.growatt_registers import (
    GrowattRegisterDataTypes,
    GrowattRegisterFloatOptions,
    GrowattRegisterEnumOptions,
    GrowattRegisterEnumTypes,
    GrowattRegisterDataType,
)


class TestDeviceConfig:
    def test_device_id_property(self):
        config = DeviceConfig(serial_number="ABC123")
        assert config.device_id == "ABC123"

    def test_to_file(self):
        config = DeviceConfig(serial_number="XYZ", data_interval="60")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            pass
        try:
            config.to_file(f.name)
            with open(f.name) as fh:
                data = json.load(fh)
            assert data["serial_number"] == "XYZ"
            assert data["data_interval"] == "60"
        finally:
            os.unlink(f.name)

    def test_from_file_not_found(self):
        result = DeviceConfig.from_file("/nonexistent/path.json")
        assert result is None

    def test_from_file_success(self):
        config = DeviceConfig(serial_number="SUCCESS", data_interval="30")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(config.model_dump_json(exclude_none=True))
            fname = f.name
        try:
            result = DeviceConfig.from_file(fname)
            assert result is not None
            assert result.serial_number == "SUCCESS"
            assert result.data_interval == "30"
        finally:
            os.unlink(fname)

    def test_from_file_parse_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json")
            fname = f.name
        try:
            result = DeviceConfig.from_file(fname)
            assert result is None
        finally:
            os.unlink(fname)


class TestGrowattRegisters:
    def test_parse_empty_data(self):
        dt = GrowattRegisterDataType(data_type=GrowattRegisterDataTypes.INT)
        assert dt.parse(b"") is None

    def test_parse_float(self):
        dt = GrowattRegisterDataType(
            data_type=GrowattRegisterDataTypes.FLOAT,
            float_options=GrowattRegisterFloatOptions(multiplier=0.1, delta=0),
        )
        val = dt.parse(struct.pack("!H", 1234))
        assert val == pytest.approx(123.4)

    def test_parse_signed_float(self):
        dt = GrowattRegisterDataType(
            data_type=GrowattRegisterDataTypes.SIGNED_FLOAT,
            float_options=GrowattRegisterFloatOptions(multiplier=2, delta=-1),
        )
        val = dt.parse(struct.pack(">h", -100))
        assert val == pytest.approx(-201.0)

    def test_parse_time_hhmm(self):
        dt = GrowattRegisterDataType(data_type=GrowattRegisterDataTypes.TIME_HHMM)
        val = dt.parse(struct.pack("!H", (13 << 8) | 30))
        assert val == 1330

    def test_parse_int(self):
        dt = GrowattRegisterDataType(data_type=GrowattRegisterDataTypes.INT)
        val = dt.parse(struct.pack("!H", 42))
        assert val == 42

    def test_parse_signed_int(self):
        dt = GrowattRegisterDataType(data_type=GrowattRegisterDataTypes.SIGNED_INT)
        val = dt.parse(struct.pack(">h", -7))
        assert val == -7

    def test_parse_enum_bitfield(self):
        dt = GrowattRegisterDataType(
            data_type=GrowattRegisterDataTypes.ENUM,
            enum_options=GrowattRegisterEnumOptions(
                enum_type=GrowattRegisterEnumTypes.BITFIELD,
                values={},
            ),
        )
        val = dt.parse(struct.pack("!H", 3))
        assert val is None

    def test_parse_enum_int_map_match(self):
        dt = GrowattRegisterDataType(
            data_type=GrowattRegisterDataTypes.ENUM,
            enum_options=GrowattRegisterEnumOptions(
                enum_type=GrowattRegisterEnumTypes.INT_MAP,
                values={1: "one", 2: "two"},
            ),
        )
        val = dt.parse(struct.pack("!H", 2))
        assert val == 2

    def test_parse_enum_int_map_no_match(self):
        dt = GrowattRegisterDataType(
            data_type=GrowattRegisterDataTypes.ENUM,
            enum_options=GrowattRegisterEnumOptions(
                enum_type=GrowattRegisterEnumTypes.INT_MAP,
                values={1: "one"},
            ),
        )
        val = dt.parse(struct.pack("!H", 99))
        assert val is None

    def test_parse_string(self):
        dt = GrowattRegisterDataType(data_type=GrowattRegisterDataTypes.STRING)
        val = dt.parse(b"Hello\x00\x00")
        assert val == "Hello"


class TestModbusFunctionMultiple:
    def test_parse_roundtrip(self):
        values = struct.pack("!HHH", 10, 20, 30)
        pkt = struct.pack(
            ">HHHBB30sHH",
            1, 7, 36 + len(values), 1, 16,
            b"QMN000ABC1D2E3FG".ljust(30, b"\x00"),
            100, 102,
        ) + values
        parsed = GrowattModbusFunctionMultiple.parse_grobro(pkt)
        assert parsed is not None
        assert parsed.function == 16
        assert parsed.start == 100
        assert parsed.end == 102
        assert parsed.device_id == "QMN000ABC1D2E3FG"

        rebuilt = parsed.build_grobro()
        assert rebuilt[:42] == pkt[:42]
        assert rebuilt[42:] == pkt[42:]

    def test_parse_function_3_roundtrip(self):
        pkt = struct.pack(
            ">HHHBB30sHH",
            1, 7, 36, 1, 3,
            b"SN123".ljust(30, b"\x00"),
            0, 0,
        )
        parsed = GrowattModbusFunctionSingle.parse_grobro(pkt)
        assert parsed is not None
        assert parsed.function == 3
        assert parsed.device_id == "SN123"
        rebuilt = parsed.build_grobro()
        assert rebuilt == pkt


class TestModbusBlock:
    def test_parse_error(self):
        result = GrowattModbusBlock.parse_grobro(b"")
        assert result is None

    @staticmethod
    def test_parse_and_build():
        data = struct.pack(">HH", 50, 52) + struct.pack("!HHH", 1, 2, 3)
        parsed = GrowattModbusBlock.parse_grobro(data)
        assert parsed is not None
        assert parsed.start == 50
        assert parsed.end == 52
        assert parsed.values == struct.pack("!HHH", 1, 2, 3)
        rebuilt = parsed.build_grobro()
        assert rebuilt == data


class TestModbusMessage:
    def test_get_data_match(self):
        msg = GrowattModbusMessage(
            unknown=0,
            device_id="TEST",
            function=GrowattModbusFunction.READ_INPUT_REGISTER,
            register_blocks=[
                GrowattModbusBlock(
                    start=100, end=101,
                    values=struct.pack("!HH", 10, 20),
                ),
            ],
        )
        from grobro.model.growatt_registers import GrowattRegisterPosition
        pos = GrowattRegisterPosition(register_no=100, offset=0, size=2)
        result = msg.get_data(pos)
        assert result == struct.pack("!H", 10)

    def test_get_data_no_match(self):
        msg = GrowattModbusMessage(
            unknown=0,
            device_id="TEST",
            function=GrowattModbusFunction.READ_INPUT_REGISTER,
            register_blocks=[
                GrowattModbusBlock(
                    start=100, end=101,
                    values=struct.pack("!HH", 10, 20),
                ),
            ],
        )
        from grobro.model.growatt_registers import GrowattRegisterPosition
        pos = GrowattRegisterPosition(register_no=200, offset=0, size=2)
        result = msg.get_data(pos)
        assert result is None

    def test_parse_msg_len_mismatch(self):
        header = struct.pack(">HHHBB30s", 0, 7, 99, 1, 3, b"TEST".ljust(30, b"\x00"))
        result = GrowattModbusMessage.parse_grobro(header + b"\x00\x00")
        assert result is None

    def test_parse_unknown_function(self):
        header = struct.pack(">HHHBB30s", 0, 7, 30, 1, 99, b"TEST".ljust(30, b"\x00"))
        result = GrowattModbusMessage.parse_grobro(header)
        assert result is None

    def test_parse_exception(self):
        result = GrowattModbusMessage.parse_grobro(b"")
        assert result is None


class TestGrowattMetadata:
    def test_parse_invalid_timestamp(self):
        buffer = b"SN123".ljust(30, b"\x00") + struct.pack(">7B", 99, 99, 99, 99, 99, 99, 99)
        result = GrowattMetadata.parse_grobro(buffer)
        assert result is not None
        assert result.device_sn == "SN123"
        assert result.timestamp is None

    def test_size(self):
        meta = GrowattMetadata(device_sn="TEST", timestamp=datetime(2025, 1, 1))
        assert meta.size() == 37


class TestMQTTConfig:
    @pytest.fixture
    def default(self):
        return MQTTConfig(
            host="default.com",
            port=1883,
            use_tls=False,
            username="user",
            password="pass",
        )

    def test_from_env_overrides(self, monkeypatch, default):
        monkeypatch.setenv("TEST_MQTT_HOST", "override.com")
        monkeypatch.setenv("TEST_MQTT_PORT", "8883")
        monkeypatch.setenv("TEST_MQTT_TLS", "true")
        monkeypatch.setenv("TEST_MQTT_USER", "override_user")
        monkeypatch.setenv("TEST_MQTT_PASS", "override_pass")

        result = MQTTConfig.from_env("TEST", default)
        assert result.host == "override.com"
        assert result.port == 8883
        assert result.use_tls is True
        assert result.username == "override_user"
        assert result.password == "override_pass"

    def test_from_env_uses_defaults(self, monkeypatch, default):
        monkeypatch.delenv("TEST2_MQTT_HOST", raising=False)
        monkeypatch.delenv("TEST2_MQTT_PORT", raising=False)
        monkeypatch.delenv("TEST2_MQTT_TLS", raising=False)
        monkeypatch.delenv("TEST2_MQTT_USER", raising=False)
        monkeypatch.delenv("TEST2_MQTT_PASS", raising=False)

        result = MQTTConfig.from_env("TEST2", default)
        assert result.host == "default.com"
        assert result.port == 1883
        assert result.use_tls is False
        assert result.username == "user"
        assert result.password == "pass"
