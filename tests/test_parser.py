import struct
from grobro.grobro import parser


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
