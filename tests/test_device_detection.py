from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.grobro.builder import scramble
from grobro.grobro.client import Client
from grobro.grobro.parser import unscramble
from grobro.model import growatt_registers as regs
from grobro.model.mqtt_config import MQTTConfig

DATA_DIR = Path(__file__).parent / "model" / "data"


@pytest.fixture(autouse=True)
def clear_detected_devices():
    regs.DETECTED_DEVICES.clear()
    yield
    regs.DETECTED_DEVICES.clear()


@pytest.mark.parametrize(
    "inverter_serial, name, registers",
    [
        ("PYL0TEST01", "MOD TL3-XH", regs.KNOWN_MOD_REGISTERS),
        ("KLN0TEST01", "MID TL3-XH", regs.KNOWN_MOD_REGISTERS),
        ("HJU0TEST01", "MIN TL-XH", regs.KNOWN_XH2_REGISTERS),
        ("QYL0TEST01", "MIN TL-X", regs.KNOWN_XH2_REGISTERS),
    ],
)
def test_detect_device_by_inverter_serial(inverter_serial, name, registers):
    assert regs.detect_device_by_inverter_serial("XYZ0000TEST0001", inverter_serial) is registers
    assert regs.get_known_registers("XYZ0000TEST0001") is registers
    assert regs.get_device_type_name("XYZ0000TEST0001") == name


def test_known_prefix_is_never_overridden():
    assert regs.detect_device_by_inverter_serial("QMN000ABC1D2E3FG", "PYL0TEST01") is None
    assert regs.get_known_registers("QMN000ABC1D2E3FG") is regs.KNOWN_NEO_REGISTERS
    assert regs.get_device_type_name("QMN000ABC1D2E3FG") == "NEO"


def test_unknown_inverter_stays_unknown():
    assert regs.detect_device_by_inverter_serial("XYZ0000TEST0001", "ZZZ0TEST01") is None
    assert regs.detect_device_by_inverter_serial("XYZ0000TEST0001", "") is None
    assert regs.get_known_registers("XYZ0000TEST0001") is None
    assert regs.get_device_type_name("XYZ0000TEST0001") == "UNKNOWN"


def _client():
    with patch("grobro.grobro.client.mqtt.Client"):
        client = Client(MQTTConfig(host="localhost", port=1883), MQTTConfig(host="localhost", port=1883))
    client.on_input_register = MagicMock()
    return client


def _msg(topic, payload):
    msg = MagicMock(spec=MQTTMessage)
    msg.topic = topic
    msg.payload = payload
    msg.qos = 0
    msg.retain = False
    msg.properties.json.return_value = {"UserProperty": []}
    return msg


def _frame_with_inverter_serial(serial):
    frame = bytearray(unscramble((DATA_DIR / "NeoReadInputRegisters.bin").read_bytes()))
    # The metadata with the 30 byte inverter serial follows the 38 byte header
    frame[38:68] = serial.encode("ascii").ljust(30, b"\x00")
    return scramble(bytes(frame))


def test_unknown_datalogger_with_known_inverter_is_decoded():
    client = _client()
    client._client.on_message(None, None, _msg("c/33/XYZ0000TEST0001", _frame_with_inverter_serial("PYL0TEST01")))

    state = client.on_input_register.call_args.args[0]
    assert state.device_id == "XYZ0000TEST0001"
    assert "Vac3" in state.payload
    assert regs.get_device_type_name("XYZ0000TEST0001") == "MOD TL3-XH"


def test_unknown_datalogger_with_unknown_inverter_is_ignored():
    client = _client()
    client._client.on_message(None, None, _msg("c/33/XYZ0000TEST0001", _frame_with_inverter_serial("ZZZ0TEST01")))

    client.on_input_register.assert_not_called()


def test_detected_device_gets_discovery():
    from grobro.ha.client import Client as HaClient

    regs.detect_device_by_inverter_serial("XYZ0000TEST0001", "PYL0TEST01")
    with patch("grobro.ha.client.mqtt.Client") as factory, patch("grobro.ha.client.os.listdir", return_value=[]):
        ha = HaClient(MQTTConfig(host="localhost", port=1883))
        ha._Client__publish_device_discovery("XYZ0000TEST0001")

    topics = [call.args[0] for call in factory.return_value.publish.call_args_list]
    assert "homeassistant/device/XYZ0000TEST0001/config" in topics


def test_shinewifi_x_follows_the_inverter_serial():
    # VWQ (ShineWiFi-X) falls back to MOD, but a known inverter serial wins
    assert regs.get_known_registers("VWQ0000TEST0001") is regs.KNOWN_MOD_REGISTERS
    assert regs.detect_device_by_inverter_serial("VWQ0000TEST0001", "HJU0TEST01") is regs.KNOWN_XH2_REGISTERS
    assert regs.get_known_registers("VWQ0000TEST0001") is regs.KNOWN_XH2_REGISTERS
    assert regs.get_device_type_name("VWQ0000TEST0001") == "MIN TL-XH"


def test_shinewifi_x_with_unknown_inverter_stays_mod():
    assert regs.detect_device_by_inverter_serial("VWQ0000TEST0001", "ZZZ0TEST01") is None
    assert regs.get_known_registers("VWQ0000TEST0001") is regs.KNOWN_MOD_REGISTERS
    assert regs.get_device_type_name("VWQ0000TEST0001") == "MOD"
