import os
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from paho.mqtt.client import MQTTMessage

from grobro import ha, grobro
from grobro.model.mqtt_config import MQTTConfig

DATA_DIR = Path(__file__).parent / "model" / "data"


def _msg(topic: str, payload: bytes, properties=None):
    m = MagicMock(spec=MQTTMessage)
    m.topic = topic
    m.payload = payload
    m.qos = 0
    m.retain = False
    m.properties.json.return_value = {"UserProperty": properties or []}
    return m


def _extract_ha_publishes(ha_mqtt_mock):
    calls = ha_mqtt_mock.publish.call_args_list
    topics = {}
    for c in calls:
        topic = c[0][0]
        payload = c[0][1] if len(c[0]) > 1 else None
        if topic not in topics or payload:
            topics[topic] = payload
    return topics


class _IntegrationHelper:
    """Sets up grobro + ha clients wired together, returning both and the ha mqtt mock."""

    def setup(self):
        ha_cfg = MQTTConfig(host="localhost", port=1883)
        grobro_cfg = MQTTConfig(host="localhost", port=1883)
        forward_cfg = MQTTConfig(host="forward.com", port=7006)

        with patch("paho.mqtt.client.Client") as mqtt_mock_cls:
            ha_mqtt = MagicMock()
            ha_mqtt.publish.return_value = (0, MagicMock())
            grobro_mqtt = MagicMock()
            grobro_mqtt.publish.return_value = (0, MagicMock())

            mqtt_mock_cls.side_effect = [ha_mqtt, grobro_mqtt]

            with patch("grobro.ha.client.os.listdir", return_value=[]):
                hc = ha.Client(ha_cfg)
                gc = grobro.Client(grobro_cfg, forward_cfg)

        gc.on_config = hc.set_config
        gc.on_input_register = hc.publish_input_register
        gc.on_holding_register_input = hc.publish_holding_register_input

        return hc, gc, ha_mqtt

    def send(self, gc, topic: str, fixture: str):
        data = (DATA_DIR / fixture).read_bytes()
        msg = _msg(topic, data)
        gc._client.on_message(None, None, msg)


helper = _IntegrationHelper()


class TestNEOIntegration:

    def test_neo_config_triggers_discovery(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/QMN000ABC1D2E3FG", "NeoConfigTLV_340.bin")
                topics = _extract_ha_publishes(ha_mqtt)

                dev_topic = "homeassistant/device/QMN000ABC1D2E3FG/config"
                assert dev_topic in topics
                payload = json.loads(topics[dev_topic])
                dev = payload["dev"]
                assert dev["identifiers"] == ["QMN000ABC1D2E3FG"]
                assert "NEO" in dev["model"]
                assert "NEO" == topics["homeassistant/grobro/QMN000ABC1D2E3FG/type"]
            finally:
                os.chdir(old)

    def test_neo_input_registers_publish_state(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/QMN000ABC1D2E3FG", "NeoReadInputRegisters.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                assert "homeassistant/grobro/QMN000ABC1D2E3FG/state" in topics
            finally:
                os.chdir(old)

    def test_neo_sanitizes_dle_in_topic(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/QMN000ABC1D2E3FG\x10", "NeoConfigTLV_340.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                for t in topics:
                    assert "\x10" not in t
                assert "homeassistant/device/QMN000ABC1D2E3FG/config" in topics
            finally:
                os.chdir(old)


class TestNOAHIntegration:

    def test_noah_fe19_config_triggers_discovery(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/0PVP0000TEST0001", "NoahTypeFE19_Config.bin")
                topics = _extract_ha_publishes(ha_mqtt)

                dev_topic = "homeassistant/device/0PVP0000TEST0001/config"
                assert dev_topic in topics
                payload = json.loads(topics[dev_topic])
                dev = payload["dev"]
                assert dev["identifiers"] == ["0PVP0000TEST0001"]
                assert "NOAH" in dev["model"]
                assert "NOAH" == topics["homeassistant/grobro/0PVP0000TEST0001/type"]
            finally:
                os.chdir(old)

    def test_noah_fe19_devstatus_does_not_trigger_discovery(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/0PVP0000TEST0001", "NoahTypeFE19_DevStatus.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                dev_topics = [t for t in topics if t.startswith("homeassistant/device/")]
                assert not dev_topics
            finally:
                os.chdir(old)

    def test_noah_input_registers_publish_state(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/0PVP0000TEST0001", "NoahReadInputRegisters_0-124.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                assert "homeassistant/grobro/0PVP0000TEST0001/state" in topics
            finally:
                os.chdir(old)


class TestNEXAIntegration:

    def test_nexa_input_registers_publish_state(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/0HVR000TEST0001", "NeoReadInputRegisters.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                assert "homeassistant/grobro/0HVR000TEST0001/state" in topics
                assert "NEXA" == topics["homeassistant/grobro/0HVR000TEST0001/type"]
            finally:
                os.chdir(old)

    def test_nexa_unknown_device_modbus_skipped(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/UNKN00000000001", "NeoReadInputRegisters.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                assert "homeassistant/grobro/UNKN00000000001/state" not in topics
            finally:
                os.chdir(old)


class TestSPFIntegration:

    def test_spf_input_registers_publish_state(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/HAQ000TEST0001", "NeoReadInputRegisters.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                assert "homeassistant/grobro/HAQ000TEST0001/state" in topics
                assert "SPF" == topics["homeassistant/grobro/HAQ000TEST0001/type"]
            finally:
                os.chdir(old)


class TestShineWeLinkIntegration:

    def test_0129_config_with_dle_in_topic(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/RAQ0TEST01\x10", "ShineWeLinkConfigDump.bin")
                topics = _extract_ha_publishes(ha_mqtt)

                for t in topics:
                    assert "\x10" not in t

                dev_topic = "homeassistant/device/RAQ0TEST01/config"
                assert dev_topic in topics
                payload = json.loads(topics[dev_topic])
                dev = payload["dev"]
                assert dev["identifiers"] == ["RAQ0TEST01"]
                assert dev["serial_number"] == "RAQ0TEST01"
                assert "ShineWeLink" in dev["model"]
                assert "GTSW0000" in dev["model"]
                assert "RAQ0TEST01" in dev["name"]
                assert "Growatt" in dev["name"]

                serial = "homeassistant/grobro/RAQ0TEST01/serial"
                assert topics[serial] == "RAQ0TEST01"

                typ = "homeassistant/grobro/RAQ0TEST01/type"
                assert topics[typ] == "ShineWeLink"
            finally:
                os.chdir(old)

    def test_fe19_devstatus_does_not_trigger_discovery(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/RAQ0E8H042", "ShineWeLinkFE19_DevStatus1.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                dev_topics = [t for t in topics if t.startswith("homeassistant/device/")]
                assert not dev_topics
                sensor_topics = [t for t in topics if "grobro/" in t]
                assert not sensor_topics
            finally:
                os.chdir(old)

    def test_fe19_devstatus2_does_not_trigger_discovery(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/RAQ0E8H042", "ShineWeLinkFE19_DevStatus2.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                dev_topics = [t for t in topics if t.startswith("homeassistant/device/")]
                assert not dev_topics
            finally:
                os.chdir(old)

    def test_fe25_keepalive_does_not_trigger_discovery(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/RAQ0E8H042", "ShineWeLinkFE25_Keepalive.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                dev_topics = [t for t in topics if t.startswith("homeassistant/device/")]
                assert not dev_topics
            finally:
                os.chdir(old)

    def test_input_registers_with_dle_publish_clean_state(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/RAQ0E8H042\x10", "ShineWeLinkReadInputRegisters.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                for t in topics:
                    assert "\x10" not in t
                assert "homeassistant/grobro/RAQ0E8H042/state" in topics
                payload = json.loads(topics["homeassistant/grobro/RAQ0E8H042/state"])
                assert isinstance(payload, dict) and len(payload) > 0
            finally:
                os.chdir(old)

    def test_modbus_then_config_completes_device(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/RAQ0TEST01\x10", "ShineWeLinkReadInputRegisters.bin")
                phase1 = set(c[0][0] for c in ha_mqtt.publish.call_args_list if c[0][0])
                assert "homeassistant/grobro/RAQ0TEST01/state" in phase1

                helper.send(gc, "c/33/RAQ0TEST01\x10", "ShineWeLinkConfigDump.bin")
                # After config, PTQ inverter appears as separate device
                ptq_dev_topic = "homeassistant/device/PTQ0TEST01/config"
                ptq_calls = [c for c in ha_mqtt.publish.call_args_list
                             if c[0][0] == ptq_dev_topic and c[0][1]]
                assert ptq_calls
                payload = json.loads(ptq_calls[-1][0][1])
                assert "GTSW0000" in payload["dev"]["model"]
                assert payload["dev"]["identifiers"] == ["PTQ0TEST01"]
            finally:
                os.chdir(old)


class TestTopicSanitization:

    def test_all_device_prefixes_sanitize_dle(self):
        for prefix, device_id, fixture in [
            ("QMN", "QMN000ABC1D2E3FG", "NeoConfigTLV_340.bin"),
            ("0PVP", "0PVP0000TEST0001", "NoahTypeFE19_Config.bin"),
            ("0HVR", "0HVR000TEST0001", "NeoReadInputRegisters.bin"),
            ("HAQ", "HAQ000TEST0001", "NeoReadInputRegisters.bin"),
            ("RAQ", "RAQ0TEST01", "ShineWeLinkConfigDump.bin"),
        ]:
            hc, gc, ha_mqtt = helper.setup()
            old = os.getcwd()
            with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
                os.chdir(tmp)
                try:
                    helper.send(gc, f"c/33/{device_id}\x10", fixture)
                    topics = _extract_ha_publishes(ha_mqtt)
                    for t in topics:
                        assert "\x10" not in t, f"{prefix}: {t!r}"
                finally:
                    os.chdir(old)

    def test_control_chars_anywhere_in_serial_stripped(self):
        hc, gc, ha_mqtt = helper.setup()
        old = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="grobrot_") as tmp:
            os.chdir(tmp)
            try:
                helper.send(gc, "c/33/QMN\x00AB\x7fC1D2E3FG", "NeoConfigTLV_340.bin")
                topics = _extract_ha_publishes(ha_mqtt)
                dev_topic = "homeassistant/device/QMNABC1D2E3FG/config"
                assert dev_topic in topics, f"Topics: {list(topics)}"
            finally:
                os.chdir(old)
