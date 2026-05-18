import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.grobro.client import Client, get_property, dump_message_binary
import grobro.grobro.client as grobro_client
from grobro.model.mqtt_config import MQTTConfig


DATA_DIR = __file__[: __file__.rfind("/")] + "/model/data"


@pytest.fixture
def mock_mqtt():
    with patch("grobro.grobro.client.mqtt.Client") as mock_cls:
        instance = MagicMock()
        instance.publish.return_value = (0, MagicMock())
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def client(mock_mqtt):
    cfg = MQTTConfig(host="localhost", port=1883)
    forward = MQTTConfig(host="forward.com", port=7006)
    c = Client(cfg, forward)
    c.on_config = MagicMock()
    c.on_input_register = MagicMock()
    c.on_holding_register_input = MagicMock()
    c.on_config_read_response = MagicMock()
    return c


def _msg(topic: str, payload: bytes, properties=None):
    m = MagicMock(spec=MQTTMessage)
    m.topic = topic
    m.payload = payload
    m.qos = 0
    m.retain = False
    m.properties.json.return_value = {"UserProperty": properties or []}
    return m


class TestModule:
    def test_get_property_found(self):
        msg = _msg("c/foo", b"")
        result = get_property(msg, "forwarded-for")
        assert result is None  # no UserProperty

    def test_get_property_missing(self):
        msg = _msg("c/foo", b"", [("other", "val")])
        result = get_property(msg, "forwarded-for")
        assert result is None

    def test_dump_message_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("grobro.grobro.client.DUMP_DIR", tmp):
                dump_message_binary("s/33/test", b"hello")
                files = os.listdir(tmp + "/s/33/test")
                assert len(files) == 1
                assert (Path(tmp) / "s/33/test" / files[0]).read_bytes() == b"hello"

    def test_dump_message_binary_error(self):
        with patch("grobro.grobro.client.os.makedirs", side_effect=OSError("denied")):
            dump_message_binary("s/33/test", b"data")
            # should not raise

    def test_dump_message_binary_write_error(self):
        with patch("builtins.open", side_effect=OSError("denied")):
            dump_message_binary("s/33/test", b"data")
            # should not raise

    def test_growatt_cloud_disabled(self):
        assert hasattr(grobro_client, "GROWATT_CLOUD_ENABLED")


class TestClientLifecycle:
    def test_init_plain(self):
        cfg = MQTTConfig(host="h", port=1883)
        with patch("grobro.grobro.client.mqtt.Client") as mc:
            instance = MagicMock()
            mc.return_value = instance
            c = Client(cfg, cfg)
            assert c._client is instance
            instance.connect.assert_called_once_with("h", 1883, 60)

    def test_init_with_auth_tls(self):
        cfg = MQTTConfig(host="h", port=8883, username="u", password="p", use_tls=True)
        with patch("grobro.grobro.client.mqtt.Client") as mc:
            instance = MagicMock()
            mc.return_value = instance
            Client(cfg, cfg)
            instance.username_pw_set.assert_called_once_with("u", "p")
            instance.tls_set.assert_called_once()
            instance.tls_insecure_set.assert_called_once_with(True)

    def test_start_stop(self, client):
        client.start()
        client._client.loop_start.assert_called_once()
        client.stop()
        client._client.loop_stop.assert_called_once()
        client._client.disconnect.assert_called_once()

    def test_stop_with_forward_clients(self, client):
        fc = MagicMock()
        client._forward_clients["fw1"] = fc
        client.stop()
        fc.loop_stop.assert_called_once()
        fc.disconnect.assert_called_once()

    def test_on_connect(self, client):
        client._client.on_connect(None, None, None, 0, None)
        client._client.subscribe.assert_called_once_with("c/#")


class TestClientSend:
    def test_send_command(self, client):
        from grobro.model.modbus_function import GrowattModbusFunctionSingle
        cmd = GrowattModbusFunctionSingle(
            device_id="QMN000ABC1D2E3FG",
            function=3,
            register=100,
            value=100,
        )
        client.send_command(cmd)
        client._client.publish.assert_called_once()
        args = client._client.publish.call_args
        assert args[0][0] == "s/33/QMN000ABC1D2E3FG"
        assert len(args[0][1]) > 0

    def test_send_config_read_message(self, client):
        client.send_config_read_message("QMN000ABC1D2E3FG", 1280)
        client._client.publish.assert_called_once()
        topic = client._client.publish.call_args[0][0]
        assert topic == "s/33/QMN000ABC1D2E3FG"

    def test_send_config_message(self, client):
        client.send_config_message("QMN000ABC1D2E3FG", 1280, "60")
        client._client.publish.assert_called_once()
        topic = client._client.publish.call_args[0][0]
        assert topic == "s/33/QMN000ABC1D2E3FG"

    def test_send_command_failure(self, client):
        client._client.publish.return_value = (1, None)
        from grobro.model.modbus_function import GrowattModbusFunctionSingle
        cmd = GrowattModbusFunctionSingle(
            device_id="QMN000ABC1D2E3FG", function=3, register=100, value=100
        )
        client.send_command(cmd)


class TestClientOnMessage:
    def test_forwarded_message_skipped(self, client):
        msg = _msg("c/test", b"data", [("forwarded-for", "ha")])
        client._client.on_message(None, None, msg)
        assert not client._client.publish.called

    def test_config_message_neo_340(self, client):
        data = (Path(DATA_DIR) / "NeoConfigTLV_340.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()

    def test_config_message_neo_341(self, client):
        data = (Path(DATA_DIR) / "NeoConfigTLV_341.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()

    def test_config_read_response_281(self, client):
        data = (Path(DATA_DIR) / "NeoConfigReadResponse_337.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG", data)
        client._client.on_message(None, None, msg)
        client._client.publish.assert_called()  # publishes back to HA topic

    def test_config_write_ack_280(self, client):
        data = (Path(DATA_DIR) / "NeoConfigWriteAck_DataInterval.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG", data)
        client._client.on_message(None, None, msg)
        # no error, returns cleanly

    def test_modbus_input_register_neo(self, client):
        data = (Path(DATA_DIR) / "NeoReadInputRegisters.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG", data)
        client._client.on_message(None, None, msg)
        client.on_input_register.assert_called_once()

    def test_modbus_single_register_neo(self, client):
        data = (Path(DATA_DIR) / "NeoReadSingleRegister_3.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG", data)
        client._client.on_message(None, None, msg)
        client.on_holding_register_input.assert_called_once()

    def test_modbus_prese_single_noah(self, client):
        data = (Path(DATA_DIR) / "NoahPresetSingle_OutputLimit.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        # PRESET_SINGLE_REGISTER response is not routed to handlers

    def test_modbus_input_noah(self, client):
        data = (Path(DATA_DIR) / "NoahReadInputRegisters_0-124.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        client.on_input_register.assert_called_once()

    @patch("grobro.grobro.client.GROWATT_CLOUD", "true")
    @patch("grobro.grobro.client.GROWATT_CLOUD_ENABLED", True)
    def test_growatt_cloud_forwarding(self, client):
        client._forward_clients = {}
        with patch.object(client, "_Client__connect_to_growatt_server") as mock_connect:
            fc = MagicMock()
            mock_connect.return_value = fc
            data = (Path(DATA_DIR) / "NeoConfigTLV_340.bin").read_bytes()
            msg = _msg("c/33/QMN000ABC1D2E3FG", data)
            client._client.on_message(None, None, msg)
            mock_connect.assert_called_once_with("QMN000ABC1D2E3FG")
            fc.publish.assert_called_once()

    def test_noah_type0103(self, client):
        data = (Path(DATA_DIR) / "NoahType0103_HoldingRegs.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        # NOAH type 259 is not handled by client, no callback called

    def test_noah_type0110(self, client):
        data = (Path(DATA_DIR) / "NoahType0110_PresetMResp.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        # This is a preset response, handled gracefully

    def test_noah_type0125(self, client):
        data = (Path(DATA_DIR) / "NoahType0125_SerialResp.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        # Serial response, handled gracefully

    def test_noah_type_fe19_config(self, client):
        data = (Path(DATA_DIR) / "NoahTypeFE19_Config.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()

    def test_noah_type_fe19_config2(self, client):
        data = (Path(DATA_DIR) / "NoahTypeFE19_Config2.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()

    def test_noah_type_fe19_config3(self, client):
        data = (Path(DATA_DIR) / "NoahTypeFE19_Config3.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()

    def test_noah_type_fe19_devstatus(self, client):
        data = (Path(DATA_DIR) / "NoahTypeFE19_DevStatus.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        # DevStatus (msg_type=53) not handled as config, just no crash

    def test_noah_type_fe25(self, client):
        data = (Path(DATA_DIR) / "NoahTypeFE25_Empty.bin").read_bytes()
        msg = _msg("c/33/0PVP0000TEST0001", data)
        client._client.on_message(None, None, msg)
        # keepalive, no callbacks

    def test_shinewelink_fe19_fullconfig(self, client):
        data = (Path(DATA_DIR) / "ShineWeLinkFE19_FullConfig.bin").read_bytes()
        msg = _msg("c/33/RAQ0E8H042", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()

    def test_shinewelink_config_0129(self, client):
        data = (Path(DATA_DIR) / "ShineWeLinkConfigDump.bin").read_bytes()
        msg = _msg("c/33/RAQ0TEST01", data)
        client._client.on_message(None, None, msg)
        assert client.on_config.call_count == 2
        raq_call, ptq_call = client.on_config.call_args_list
        assert raq_call[0][0] == "RAQ0TEST01"
        assert ptq_call[0][0].startswith("PTQ")

    def test_topic_sanitization_normal(self, client):
        data = (Path(DATA_DIR) / "NeoConfigTLV_340.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()
        dev_id, _ = client.on_config.call_args[0]
        assert dev_id == "QMN000ABC1D2E3FG"

    def test_topic_sanitization_control_chars(self, client):
        data = (Path(DATA_DIR) / "NeoConfigTLV_340.bin").read_bytes()
        msg = _msg("c/33/QMN000ABC1D2E3FG\x10", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_called_once()
        dev_id, _ = client.on_config.call_args[0]
        assert "\x10" not in dev_id
        assert dev_id == "QMN000ABC1D2E3FG"

    def test_shinewelink_fe19_devstatus(self, client):
        data = (Path(DATA_DIR) / "ShineWeLinkFE19_DevStatus1.bin").read_bytes()
        msg = _msg("c/33/RAQ0E8H042", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_not_called()

    def test_shinewelink_fe19_devstatus2(self, client):
        data = (Path(DATA_DIR) / "ShineWeLinkFE19_DevStatus2.bin").read_bytes()
        msg = _msg("c/33/RAQ0E8H042", data)
        client._client.on_message(None, None, msg)
        client.on_config.assert_not_called()

    def test_shinewelink_input_register(self, client):
        data = (Path(DATA_DIR) / "ShineWeLinkReadInputRegisters.bin").read_bytes()
        msg = _msg("c/33/RAQ0E8H042", data)
        client._client.on_message(None, None, msg)
        client.on_input_register.assert_called_once()

    def test_shinewelink_holding_register(self, client):
        data = (Path(DATA_DIR) / "ShineWeLinkReadHoldingRegisters.bin").read_bytes()
        msg = _msg("c/33/RAQ0E8H042", data)
        # Function 3 is parsed by parse_grobro but not routed to a callback
        client._client.on_message(None, None, msg)

    def test_shinewelink_fe25_keepalive(self, client):
        data = (Path(DATA_DIR) / "ShineWeLinkFE25_Keepalive.bin").read_bytes()
        msg = _msg("c/33/RAQ0E8H042", data)
        client._client.on_message(None, None, msg)
        # keepalive, no callbacks

    def test_dump_messages_in_on_message(self, client):
        with patch("grobro.grobro.client.DUMP_MESSAGES", True):
            with patch("grobro.grobro.client.dump_message_binary") as mock_dump:
                data = (Path(DATA_DIR) / "NeoConfigTLV_340.bin").read_bytes()
                msg = _msg("c/33/QMN000ABC1D2E3FG", data)
                client._client.on_message(None, None, msg)
                mock_dump.assert_called_once()

    def test_unknown_device_type_modbus(self, client):
        data = (Path(DATA_DIR) / "NeoReadInputRegisters.bin").read_bytes()
        msg = _msg("c/33/UNKN00000000001", data)
        client._client.on_message(None, None, msg)
        client.on_input_register.assert_not_called()

    def test_modbus_input_nexa(self, client):
        data = (Path(DATA_DIR) / "NeoReadInputRegisters.bin").read_bytes()
        msg = _msg("c/33/0HVR000TEST0001", data)
        client._client.on_message(None, None, msg)
        client.on_input_register.assert_called_once()

    def test_modbus_input_spf(self, client):
        data = (Path(DATA_DIR) / "NeoReadInputRegisters.bin").read_bytes()
        msg = _msg("c/33/HAQ000TEST0001", data)
        client._client.on_message(None, None, msg)
        client.on_input_register.assert_called_once()

    def test_invalid_payload_processing(self, client):
        msg = _msg("c/33/QMN000ABC1D2E3FG", b"garbage")
        client._client.on_message(None, None, msg)


class TestClientCloudConfig:
    @patch("grobro.grobro.client.GROWATT_CLOUD", "true")
    @patch("grobro.grobro.client.GROWATT_CLOUD_ENABLED", True)
    @patch("grobro.grobro.client.GROWATT_CLOUD_FILTER", set())
    @patch("grobro.grobro.client.GROWATT_CLOUD_CONFIG_FILTER", "true")
    def test_config_filter_blocks_0118(self, client):
        with patch.object(client, "_Client__connect_to_growatt_server") as mock_connect:
            data = (Path(DATA_DIR) / "NeoConfigWriteAck_DataInterval.bin").read_bytes()
            msg = _msg("c/33/QMN000ABC1D2E3FG", data)
            client._client.on_message(None, None, msg)
            mock_connect.assert_not_called()

    @patch("grobro.grobro.client.GROWATT_CLOUD", "true")
    @patch("grobro.grobro.client.GROWATT_CLOUD_ENABLED", True)
    @patch("grobro.grobro.client.GROWATT_CLOUD_FILTER", set())
    @patch("grobro.grobro.client.GROWATT_CLOUD_CONFIG_FILTER", "true")
    def test_config_filter_invalid_payload(self, client):
        with patch.object(client, "_Client__connect_to_growatt_server") as mock_connect:
            msg = _msg("c/33/QMN000ABC1D2E3FG", b"garbage")
            client._client.on_message(None, None, msg)
            mock_connect.assert_not_called()

    @patch("grobro.grobro.client.GROWATT_CLOUD", "true")
    @patch("grobro.grobro.client.GROWATT_CLOUD_ENABLED", True)
    @patch("grobro.grobro.client.GROWATT_CLOUD_FILTER", set())
    def test_cloud_forwarding_exception(self, client):
        with patch.object(
            client, "_Client__connect_to_growatt_server", side_effect=Exception("boom")
        ):
            data = (Path(DATA_DIR) / "NeoConfigTLV_340.bin").read_bytes()
            msg = _msg("c/33/QMN000ABC1D2E3FG", data)
            client._client.on_message(None, None, msg)
            client.on_config.assert_called_once()


class TestClientForward:
    def test_forward_client_message(self, client):
        with patch("grobro.grobro.client.GROWATT_CLOUD", "true"):
            client._Client__on_message_forward_client(None, None, _msg(
                "s/QMN000ABC1D2E3FG", b"data", [("forwarded-for", "growatt")],
            ))
        client._client.publish.assert_called_once()

    def test_connect_to_growatt_server(self, client):
        with patch("grobro.grobro.client.mqtt.Client") as mc:
            fc = MagicMock()
            mc.return_value = fc
            result = client._Client__connect_to_growatt_server("test-dev")
            assert result is fc
            fc.connect.assert_called_once()
            fc.subscribe.assert_called_once_with("+/test-dev")
            fc.loop_start.assert_called_once()
            # second call returns cached
            result2 = client._Client__connect_to_growatt_server("test-dev")
            assert result2 is fc
            assert mc.call_count == 1

    def test_forward_client_dump_messages(self, client):
        with patch("grobro.grobro.client.DUMP_MESSAGES", True):
            with patch("grobro.grobro.client.dump_message_binary") as mock_dump:
                with patch("grobro.grobro.client.GROWATT_CLOUD", "true"):
                    client._Client__on_message_forward_client(None, None, _msg(
                        "s/device1", b"data",
                    ))
                    mock_dump.assert_called_once()

    def test_forward_client_cloud_disabled(self, client):
        with patch("grobro.grobro.client.GROWATT_CLOUD_ENABLED", False):
            client._Client__on_message_forward_client(None, None, _msg(
                "s/device1", b"data",
            ))
            client._client.publish.assert_not_called()

    def test_forward_client_device_not_in_filter(self, client):
        client._Client__on_message_forward_client(None, None, _msg(
            "s/device1", b"data",
        ))
        client._client.publish.assert_not_called()

    def test_forward_client_publish_exception(self, client):
        with patch("grobro.grobro.client.GROWATT_CLOUD", "true"):
            with patch.object(client._client, "publish", side_effect=Exception("boom")):
                client._Client__on_message_forward_client(None, None, _msg(
                    "s/device1", b"data",
                ))


class TestExtractDeviceId:
    """Tests for _extract_device_id, which sanitizes the device serial from MQTT topics."""

    def test_clean_neo_serial(self):
        from grobro.grobro.client import _extract_device_id
        assert _extract_device_id("c/33/QMN000ABC123") == "QMN000ABC123"

    def test_clean_noah_serial(self):
        from grobro.grobro.client import _extract_device_id
        assert _extract_device_id("c/0PVP000ABC123") == "0PVP000ABC123"

    def test_strips_trailing_control_char(self):
        """ShineWiFi-X2 (XH/XH2 dongle) appends 0x18 after the serial in
        SUBSCRIBE topics. Must be stripped to produce a clean device_id."""
        from grobro.grobro.client import _extract_device_id
        assert _extract_device_id("s/33/ZGQ0F5601J\x18") == "ZGQ0F5601J"

    def test_strips_question_mark(self):
        """The same XH dongle quirk also produces topics like
        `s/33/ZGQ0F5601J?\\x18` — the `?` is printable but not a valid
        serial character, must be stripped."""
        from grobro.grobro.client import _extract_device_id
        assert _extract_device_id("s/33/ZGQ0F5601J?\x18") == "ZGQ0F5601J"

    def test_strips_non_alphanumeric(self):
        from grobro.grobro.client import _extract_device_id
        assert _extract_device_id("c/X/AB-CD_EF.GH") == "ABCDEFGH"
