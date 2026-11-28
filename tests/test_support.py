"""The anonymized support bundle."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro import grobro, ha, support
from grobro.grobro import parser
from grobro.model import growatt_registers as regs
from grobro.model.mqtt_config import MQTTConfig
from grobro.stats import BridgeStats

DATA_DIR = Path(__file__).parent / "model" / "data"


def _msg(topic, payload=b""):
    msg = MagicMock(spec=MQTTMessage)
    msg.topic = topic
    msg.payload = payload
    msg.qos = 0
    msg.retain = False
    msg.properties.json.return_value = {"UserProperty": []}
    return msg


@pytest.fixture(autouse=True)
def _clean_state():
    regs.DETECTED_DEVICES.clear()
    regs.INVERTER_SERIALS.clear()
    yield
    regs.DETECTED_DEVICES.clear()
    regs.INVERTER_SERIALS.clear()


class TestAnonymizer:
    def test_masks_keep_prefix_and_length(self):
        anon = support.Anonymizer()
        anon.add("QMN0000000000A")
        masked = anon.text("device QMN0000000000A")
        assert masked == "device QMN0XXXXXXXXX1"
        assert len(masked) == len("device QMN0000000000A")

    def test_distinct_serials_get_distinct_masks(self):
        anon = support.Anonymizer()
        anon.add("QMN0000000000A")
        anon.add("QMN0000000000B")
        assert anon.text("QMN0000000000A") != anon.text("QMN0000000000B")

    def test_frames_keep_their_layout(self):
        anon = support.Anonymizer()
        anon.add("QMN0000000000A")
        frame = b"\x00\x01QMN0000000000A\x00\x00"
        assert anon.frame(frame) == b"\x00\x01QMN0XXXXXXXXX1\x00\x00"

    @pytest.mark.parametrize("value", [None, 5, "abc", "has space", ""])
    def test_ignores_non_serials(self, value):
        anon = support.Anonymizer()
        anon.add(value)
        assert anon.text("abc has space") == "abc has space"


class TestFrameRecorder:
    def test_keeps_last_frames(self):
        recorder = support.FrameRecorder(maxlen=2)
        for i in range(3):
            recorder.record("A", bytes([i]))
        assert [frame for _, frame in recorder.frames()["A"]] == [b"\x01", b"\x02"]


def _clients(stats):
    with patch("paho.mqtt.client.Client") as mqtt_cls:
        mqtt_cls.side_effect = [MagicMock(), MagicMock()]
        hc = ha.Client(MQTTConfig(host="localhost", port=1883), stats=stats)
        gc = grobro.Client(MQTTConfig(host="localhost", port=1883), MQTTConfig(host="forward", port=7006), stats=stats)
    gc.on_config = hc.set_config
    gc.on_input_register = hc.publish_input_register
    gc.on_holding_register_input = hc.publish_holding_register_input
    return hc, gc


def test_bundle_of_a_real_device_contains_no_serials(tmp_path):
    stats = BridgeStats()
    hc, gc = _clients(stats)
    payload = (DATA_DIR / "NeoInputRegister.bin").read_bytes()
    device_id = "QMN000000000000"
    gc._client.on_message(None, None, _msg(f"c/33/{device_id}", payload))
    hc._holding_values[(device_id, "output_power_limit")] = 80

    hc._client.on_message(None, None, _msg("homeassistant/button/grobro/grobro_bridge/support_bundle/set", b"PRESS"))

    files = os.listdir(tmp_path / "config" / "support")
    assert len(files) == 1 and files[0].startswith("grobro-support-")
    text = (tmp_path / "config" / "support" / files[0]).read_text()
    bundle = json.loads(text)
    assert device_id not in text
    # The serials inside the frame are masked too
    frame = parser.unscramble(payload)
    for serial in (frame[8:38], frame[38:68]):
        serial = serial.rstrip(b"\x00").decode()
        assert len(serial) > support.KEEP_PREFIX
        assert serial not in text
        assert serial.encode().hex() not in text

    (alias, device), = bundle["devices"].items()
    assert alias.startswith("QMN0") and len(alias) == len(device_id)
    assert device["type"] == "NEO"
    assert device["messages"] == 1
    assert device["frames"] and len(bytes.fromhex(device["frames"][0]["hex"])) == len(parser.unscramble(payload))
    assert device["state"]
    assert device["holding_registers"] == {"output_power_limit": 80}
    assert "GROWATT_CLOUD" not in bundle["settings"]
    assert stats.last_support_bundle.endswith(files[0])
    # The GroBro device shows the file name
    state = [c for c in hc._client.publish.call_args_list if c.args[0] == "homeassistant/grobro/grobro_bridge/state"]
    assert json.loads(state[-1].args[1])["last_support_bundle"] == files[0]


def test_battery_serials_are_masked():
    stats = BridgeStats()
    stats.message_received("0PVP0000TEST0001")
    states = {"homeassistant/grobro/0PVP0000TEST0001/state": json.dumps({"bat1_serial": "0PVP0000BAT00001", "mode": "Load First"})}
    bundle = support.build_bundle(stats, states, {})
    text = json.dumps(bundle)
    assert "0PVP0000BAT00001" not in text
    assert "Load First" in text


def test_config_frames_are_not_recorded():
    stats = BridgeStats()
    _, gc = _clients(stats)
    gc.on_config = MagicMock()
    payload = (DATA_DIR / "NeoConfigTLV_340.bin").read_bytes()
    gc._client.on_message(None, None, _msg("c/33/QMN000000000000", payload))
    assert stats.frames.frames() == {}


def test_unknown_devices_are_recorded():
    """The frames of devices GroBro doesn't know yet are what a new device support needs."""
    stats = BridgeStats()
    _, gc = _clients(stats)
    gc._request_device_type_code = MagicMock()
    frame = bytearray(parser.unscramble((DATA_DIR / "NeoInputRegister.bin").read_bytes()))
    frame[38:68] = b"ZZZ0000000".ljust(30, b"\x00")
    from grobro.grobro.builder import scramble
    gc._client.on_message(None, None, _msg("c/33/ZZZ0000000000", scramble(bytes(frame))))
    assert "ZZZ0000000000" in stats.frames.frames()


def test_write_failure_is_reported(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr("grobro.settings.CONFIG_DIR", str(blocker))
    assert support.write_bundle({}) is None


def test_failed_write_keeps_state(monkeypatch):
    stats = BridgeStats()
    hc, _ = _clients(stats)
    monkeypatch.setattr("grobro.support.write_bundle", lambda bundle: None)
    hc._handle_bridge_command("support_bundle")
    assert stats.last_support_bundle is None


def test_unknown_bridge_command_is_ignored():
    stats = BridgeStats()
    hc, _ = _clients(stats)
    hc._handle_bridge_command("other")
    assert stats.last_support_bundle is None


def test_noah_bundle_contains_no_serials(tmp_path):
    stats = BridgeStats()
    hc, gc = _clients(stats)
    payload = (DATA_DIR / "NoahReadInputRegisters_0-124.bin").read_bytes()
    frame = parser.unscramble(payload)
    device_id = frame[8:38].rstrip(b"\x00").decode()
    gc._client.on_message(None, None, _msg(f"c/33/{device_id}", payload))
    state = json.loads(hc._last_states[f"homeassistant/grobro/{device_id}/state"])
    serials = [device_id] + [v for k, v in state.items() if k.endswith("_serial") and isinstance(v, str) and len(v) > 4]

    bundle = support.build_bundle(stats, dict(hc._last_states), {})
    text = json.dumps(bundle)
    for serial in serials:
        assert serial not in text
        assert serial.encode().hex() not in text
    assert not any("ser_part" in key for device in bundle["devices"].values() for key in device["state"])


def test_bridge_ignores_other_platforms():
    stats = BridgeStats()
    hc, _ = _clients(stats)
    with patch.object(hc, "_handle_bridge_command") as handler:
        hc._client.on_message(None, None, _msg("homeassistant/number/grobro/grobro_bridge/support_bundle/set", b"1"))
        hc._client.on_message(None, None, _msg("homeassistant/button/grobro/grobro_bridge/support_bundle/read", b"1"))
    handler.assert_not_called()
