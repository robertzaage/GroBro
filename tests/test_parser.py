import json
from grobro.grobro import parser


def test_parse_noah_6f64():
    device_id = "0PVPTEST123456789"
    eco_sn = "b827eb999999"
    json_data = json.dumps({"manu": "everhome", "model": "EcoTracker", "sn": eco_sn, "t_act": 150})
    json_bytes = json_data.encode("ascii")
    json_len = len(json_bytes)
    total_len = 79 + json_len + 2
    data = bytearray(total_len)
    data[0:2] = b"\x00\x01"
    data[2:4] = b"\x00\x07"
    data[4:6] = total_len.to_bytes(2, "big")
    data[6:8] = b"\x6f\x64"
    data[8:8 + 30] = device_id.encode("ascii").ljust(30, b"\x00")
    data[38:38 + 30] = eco_sn.encode("ascii").ljust(30, b"\x00")
    data[68:75] = bytes([26, 5, 15, 17, 12, 9, 1])  # 2026-05-15 17:12:09.001
    data[75:79] = json_len.to_bytes(4, "big")
    data[79:79 + json_len] = json_bytes

    result = parser.parse_noah_6f64(bytes(data))
    assert result["message_type"] == 0x6F64
    assert result["device_id"] == device_id
    assert result["eco_tracker_sn"] == eco_sn
    assert "2026-05-15T17:12:09.001" in result["timestamp"]
    assert json.loads(result["data"])["t_act"] == 150

    # Should also work via dispatch
    dispatched = parser.parse_noah_message(bytes(data))
    assert dispatched is not None
    assert dispatched["message_type"] == 0x6F64


def test_parse_noah_message_too_short():
    assert parser.parse_noah_message(b"") is None
    assert parser.parse_noah_message(b"\x00\x01") is None


def test_parse_noah_message_unknown_type():
    data = b"\x00\x01\x00\x07\x00\x10\xff\xff" + b"\x00" * 16
    result = parser.parse_noah_message(data)
    assert result is None


def test_parse_noah_fe19_payload_too_short():
    data = b"\x00\x01\x00\x07\x00\x10\xfe\x19" + b"\x00" * 16 + b"\x00" * 17
    result = parser.parse_noah_fe19(data)
    assert result["message_type"] == 0xFE19
    assert "error" in result


def test_parse_config_type_non_ascii_value():
    data = b"\x00\x04\x00\x02\x00\x01"
    config = parser.parse_config_type(data, 0)
    d = config.model_dump()
    assert d.get("data_interval") == "0001"


def test_parse_config_type_no_params():
    data = b"\xff\xff\x00\x00\x00\xff"
    config = parser.parse_config_type(data, 0)
    assert config.model_dump().get("raw") is not None
