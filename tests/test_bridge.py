"""The GroBro device in Home Assistant, its statistics and the discovery polish."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro import version
from grobro.grobro.client import Client as GrobroClient
from grobro.ha.bridge import bridge_id
from grobro.ha.client import Client as HaClient
from grobro.ha.discovery import display_precision
from grobro.model import growatt_registers as regs
from grobro.model.growatt_registers import GrowattRegisterDataType
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


@pytest.fixture
def stats():
    return BridgeStats()


@pytest.fixture
def ha_client(stats):
    with patch("grobro.ha.client.mqtt.Client"), patch("grobro.ha.client.os.listdir", return_value=[]):
        c = HaClient(MQTTConfig(host="localhost", port=1883), stats=stats)
    c.on_command = MagicMock()
    return c


@pytest.fixture
def grobro_client(stats):
    with patch("grobro.grobro.client.mqtt.Client"):
        c = GrobroClient(MQTTConfig(host="localhost", port=1883), MQTTConfig(host="forward", port=7006), stats=stats)
    c.on_input_register = MagicMock()
    c.on_holding_register_input = MagicMock()
    c.on_config = MagicMock()
    return c


def _published(client, topic):
    for c in reversed(client._client.publish.call_args_list):
        if c.args[0] == topic:
            return c.args[1], c.kwargs.get("retain")
    raise AssertionError(f"{topic} not published")


class TestVersion:
    def test_reads_config_yaml(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text('name: "GroBro"\nversion: "3.1.0"\nslug: grobro\n')
        assert version.read_version(path) == "3.1.0"

    def test_missing_file(self, tmp_path):
        assert version.read_version(tmp_path / "missing.yaml") == "unknown"

    def test_no_version_line(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("name: GroBro\n")
        assert version.read_version(path) == "unknown"

    def test_repository_version(self):
        assert version.VERSION != "unknown"


class TestStats:
    def test_counts_messages_and_errors_per_device(self, stats):
        stats.message_received("A")
        stats.message_received("A")
        stats.message_received("B")
        stats.decode_error("B")
        stats.decode_error()
        devices = stats.devices()
        assert stats.messages == 3
        assert stats.decode_errors == 2
        assert devices["A"]["messages"] == 2
        assert devices["B"]["decode_errors"] == 1
        assert "last_seen" in devices["A"]

    def test_devices_returns_copies(self, stats):
        stats.message_received("A")
        stats.devices()["A"]["messages"] = 99
        assert stats.devices()["A"]["messages"] == 1


class TestGrobroClientStats:
    def test_messages_are_counted(self, grobro_client, stats):
        payload = (DATA_DIR / "NeoInputRegister.bin").read_bytes()
        grobro_client._client.on_message(None, None, _msg("c/33/QMN000000000000", payload))
        assert stats.messages == 1
        assert stats.decode_errors == 0
        assert "QMN000000000000" in stats.devices()

    def test_decode_errors_are_counted(self, grobro_client, stats):
        grobro_client._client.on_message(None, None, _msg("c/33/QMN000000000000", b"\x01"))
        assert stats.decode_errors == 1
        assert stats.devices()["QMN000000000000"]["decode_errors"] == 1

    def test_forwarded_messages_are_not_counted(self, grobro_client, stats):
        msg = _msg("c/33/QMN000000000000", b"\x01")
        msg.properties.json.return_value = {"UserProperty": [["forwarded-for", "ha"]]}
        grobro_client._client.on_message(None, None, msg)
        assert stats.messages == 0

    def test_connection_state(self, grobro_client, stats):
        grobro_client._on_connect(None, None, None, MagicMock(is_failure=False), None)
        assert stats.source_connected
        grobro_client._on_disconnect(None, None, None, "gone", None)
        assert not stats.source_connected

    def test_failed_connect_is_not_connected(self, grobro_client, stats):
        grobro_client._on_connect(None, None, None, MagicMock(is_failure=True), None)
        assert not stats.source_connected


class TestBridgeDevice:
    def test_bridge_id(self, monkeypatch):
        assert bridge_id() == "grobro_bridge"
        monkeypatch.setattr("grobro.settings.MQTT_CLIENT_SUFFIX", "second")
        assert bridge_id() == "grobro_bridge_second"

    def test_discovery_on_connect(self, ha_client):
        ha_client._on_connect(None, None, None, 0, None)
        payload, retain = _published(ha_client, "homeassistant/device/grobro_bridge/config")
        discovery = json.loads(payload)
        assert retain
        assert discovery["dev"]["identifiers"] == ["grobro_bridge"]
        assert discovery["dev"]["sw_version"] == version.VERSION
        assert discovery["o"]["sw"] == version.VERSION
        assert discovery["avty"] == [{"t": "homeassistant/grobro/bridge/availability"}]
        cmps = discovery["cmps"]
        button = cmps.pop("grobro_bridge_support_bundle")
        assert button["command_topic"] == "homeassistant/button/grobro/grobro_bridge/support_bundle/set"
        assert {c["platform"] for c in cmps.values()} == {"sensor", "binary_sensor"}
        assert all(c["state_topic"] == "homeassistant/grobro/grobro_bridge/state" for c in cmps.values())
        assert cmps["grobro_bridge_devices"]["json_attributes_topic"] == "homeassistant/grobro/grobro_bridge/devices"

    def test_state(self, ha_client, stats):
        regs.INVERTER_SERIALS["VWQ0000000001"] = "XHL0000001"
        try:
            stats.set_source_connected(True)
            stats.message_received("QMN000000000000")
            stats.message_received("VWQ0000000001")
            stats.decode_error("VWQ0000000001")
            ha_client._publish_bridge_state()
            state = json.loads(_published(ha_client, "homeassistant/grobro/grobro_bridge/state")[0])
            devices = json.loads(_published(ha_client, "homeassistant/grobro/grobro_bridge/devices")[0])
        finally:
            regs.INVERTER_SERIALS.clear()
        assert state["source_connected"] == "ON"
        assert state["devices"] == 2
        assert state["messages"] == 2
        assert state["decode_errors"] == 1
        assert state["version"] == version.VERSION
        assert state["started"].endswith("+00:00")
        assert devices["QMN000000000000"]["type"] == "NEO"
        assert devices["VWQ0000000001"]["inverter_serial"] == "XHL0000001"
        assert devices["VWQ0000000001"]["decode_errors"] == 1

    def test_republished_after_ha_restart(self, ha_client):
        ha_client._client.on_message(None, None, _msg("homeassistant/status", b"online"))
        _published(ha_client, "homeassistant/device/grobro_bridge/config")
        _published(ha_client, "homeassistant/grobro/grobro_bridge/state")

    def test_refresh_timer(self, ha_client):
        with patch("grobro.ha.bridge.Timer") as timer_cls:
            ha_client.start()
            timer_cls.assert_called_once()
            refresh = timer_cls.call_args.args[1]
            refresh()
        _published(ha_client, "homeassistant/grobro/grobro_bridge/state")
        assert timer_cls.call_count == 2  # rescheduled
        ha_client.stop()
        ha_client._bridge_timer.cancel.assert_called()

    def test_refresh_survives_errors(self, ha_client):
        with patch("grobro.ha.bridge.Timer") as timer_cls, patch.object(
            ha_client, "_publish_bridge_state", side_effect=RuntimeError("boom")
        ):
            ha_client._schedule_bridge_state()
            timer_cls.call_args.args[1]()
        assert timer_cls.call_count == 2


class TestDiscoveryPolish:
    def _discovery(self, client, device_id):
        client._publish_device_discovery(device_id)
        return json.loads(_published(client, f"homeassistant/device/{device_id}/config")[0])

    def test_devices_are_connected_via_the_bridge(self, ha_client):
        discovery = self._discovery(ha_client, "QMN000000000000")
        assert discovery["dev"]["via_device"] == "grobro_bridge"
        assert discovery["o"]["sw"] == version.VERSION

    def test_display_precision_in_discovery(self, ha_client):
        cmps = self._discovery(ha_client, "QMN000000000000")["cmps"]
        assert cmps["grobro_QMN000000000000_Vac1"]["suggested_display_precision"] == 1
        # Sensors without a unit (status codes) get none
        assert all(
            "suggested_display_precision" not in c
            for c in cmps.values()
            if c.get("platform") == "sensor" and not c.get("unit_of_measurement")
        )

    @pytest.mark.parametrize(
        "data, expected",
        [
            ({"data_type": "FLOAT", "float_options": {"multiplier": 0.1}}, 1),
            ({"data_type": "SIGNED_FLOAT", "float_options": {"multiplier": 0.01}}, 2),
            ({"data_type": "FLOAT", "mult": 0.001}, 3),
            ({"data_type": "FLOAT", "float_options": {"delta": -30000, "multiplier": 1}}, 0),
            ({"data_type": "FLOAT"}, 0),
            ({"data_type": "INT"}, None),
            ({"data_type": "STRING"}, None),
        ],
    )
    def test_display_precision(self, data, expected):
        assert display_precision(GrowattRegisterDataType.model_validate(data)) == expected
