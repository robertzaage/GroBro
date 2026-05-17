import os
import json
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock
from threading import Timer

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.ha.client import (
    Client,
    get_known_registers,
    get_device_type_name,
    map_enum_value,
    make_modbus_command,
    iter_command_registers,
)
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.modbus_function import GrowattModbusFunctionSingle
from grobro.model.mqtt_config import MQTTConfig
from grobro.model.device_config import DeviceConfig


DATA_DIR = __file__[: __file__.rfind("/")] + "/model/data"


def _msg(topic: str, payload: bytes = b""):
    m = MagicMock(spec=MQTTMessage)
    m.topic = topic
    m.payload = payload
    m.qos = 0
    m.retain = False
    m.properties = MagicMock()
    return m


class TestHelpers:
    def test_get_known_registers_neo(self):
        r = get_known_registers("QMN000ABC1D2E3FG")
        assert r is not None
        assert hasattr(r, "input_registers")

    def test_get_known_registers_noah(self):
        r = get_known_registers("0PVP0000TEST0001")
        assert r is not None

    def test_get_known_registers_nexa(self):
        r = get_known_registers("0HVR1234567890")
        assert r is not None

    def test_get_known_registers_spf(self):
        r = get_known_registers("HAQ1234567890")
        assert r is not None

    def test_get_known_registers_unknown(self):
        assert get_known_registers("UNKNOWN") is None

    def test_get_device_type_name_neo(self):
        assert get_device_type_name("QMN000ABC1D2E3FG") == "NEO"

    def test_get_device_type_name_noah(self):
        assert get_device_type_name("0PVP0000TEST0001") == "NOAH"

    def test_get_device_type_name_nexa(self):
        assert get_device_type_name("0HVR1234567890") == "NEXA"

    def test_get_device_type_name_spf(self):
        assert get_device_type_name("HAQ1234567890") == "SPF"

    def test_get_device_type_name_unknown(self):
        assert get_device_type_name("UNKNOWN") == "UNKNOWN"

    def test_make_modbus_command_read(self):
        cmd = make_modbus_command(
            "QMN000ABC1D2E3FG",
            GrowattModbusFunction.READ_SINGLE_REGISTER,
            100,
        )
        assert isinstance(cmd, GrowattModbusFunctionSingle)
        assert cmd.device_id == "QMN000ABC1D2E3FG"
        assert cmd.register_no == 100
        assert cmd.value == 100

    def test_make_modbus_command_write(self):
        cmd = make_modbus_command(
            "QMN000ABC1D2E3FG",
            GrowattModbusFunction.PRESET_SINGLE_REGISTER,
            100,
            value=42,
        )
        assert cmd.value == 42

    def test_iter_command_registers(self):
        from grobro.model.growatt_registers import KNOWN_NEO_REGISTERS
        entries = list(iter_command_registers(KNOWN_NEO_REGISTERS))
        assert len(entries) > 0
        for e in entries:
            assert "name" in e
            assert "topic_root" in e
            assert "is_config" in e

    def test_map_enum_value_non_enum(self):
        reg = MagicMock()
        reg.growatt.data.data_type = "INT"
        result = map_enum_value(reg, 42)
        assert result == 42

    def test_map_enum_value_int_map(self):
        reg = MagicMock()
        reg.growatt.data.data_type = "ENUM"
        reg.growatt.data.enum_options.enum_type = "INT_MAP"
        reg.growatt.data.enum_options.values.get = MagicMock(return_value="mapped_5")
        result = map_enum_value(reg, 5)
        assert result == "mapped_5"

    def test_map_enum_value_exception(self):
        reg = MagicMock()
        reg.growatt.data.data_type = "ENUM"
        reg.growatt.data.enum_options.enum_type = "INT_MAP"
        reg.growatt.data.enum_options.values.get.side_effect = Exception("fail")
        result = map_enum_value(reg, 5)
        assert result == 5


@pytest.fixture(autouse=True)
def _cleanup_config_files():
    yield
    for name in os.listdir("."):
        if name.startswith("config_") and name.endswith(".json"):
            os.unlink(name)


@pytest.fixture
def mock_mqtt():
    with patch("grobro.ha.client.mqtt.Client") as mock_cls:
        instance = MagicMock()
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def ha_client(mock_mqtt):
    with patch("grobro.ha.client.os.listdir", return_value=[]):
        cfg = MQTTConfig(host="localhost", port=1883)
        c = Client(cfg)
        c.on_command = MagicMock()
        c.on_config_command = MagicMock()
        c.on_config_read = MagicMock()
        c.on_config_read_response = MagicMock()
        return c


class TestClientLifecycle:
    def test_init(self, ha_client):
        assert ha_client._client is not None
        ha_client._client.connect.assert_called_once_with("localhost", 1883, 60)

    def test_init_with_auth_tls(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            instance = MagicMock()
            mc.return_value = instance
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                cfg = MQTTConfig(host="h", port=8883, username="u", password="p", use_tls=True)
                c = Client(cfg)
                instance.username_pw_set.assert_called_once_with("u", "p")
                instance.tls_set.assert_called_once()

    def test_init_loads_config_files(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            instance = MagicMock()
            mc.return_value = instance
            with patch("grobro.ha.client.os.listdir", return_value=["config_QMN000ABC1D2E3FG.json"]):
                with patch("grobro.model.device_config.DeviceConfig.from_file") as from_file:
                    from_file.return_value = DeviceConfig(serial_number="QMN000ABC1D2E3FG")
                    cfg = MQTTConfig(host="localhost", port=1883)
                    c = Client(cfg)
                    assert "QMN000ABC1D2E3FG" in c._config_cache

    def test_start_stop(self, ha_client):
        ha_client.start()
        ha_client._client.loop_start.assert_called_once()
        ha_client.stop()
        ha_client._client.loop_stop.assert_called_once()
        ha_client._client.disconnect.assert_called_once()


class TestClientConfig:
    def test_set_config_new(self, ha_client):
        cfg = DeviceConfig(serial_number="QMN000ABC1D2E3FG")
        ha_client.set_config(cfg)
        assert ha_client._config_cache["QMN000ABC1D2E3FG"] == cfg
        # discovery should attempt publish
        assert ha_client._client.publish.called

    def test_set_config_unchanged(self, ha_client):
        cfg = DeviceConfig(serial_number="QMN000ABC1D2E3FG")
        ha_client._config_cache["QMN000ABC1D2E3FG"] = cfg
        ha_client._discovery_cache.append("QMN000ABC1D2E3FG")
        with patch("grobro.ha.client.model.DeviceConfig.from_file") as from_file:
            from_file.return_value = cfg
            ha_client.set_config(cfg)


class TestClientPublishInput:
    def test_publish_input_register(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        state = HomeAssistantInputRegister(
            device_id="QMN000ABC1D2E3FG",
            payload={"Ppv": 1234, "Fac": 5000},
        )
        ha_client.publish_input_register(state)
        ha_client._client.publish.assert_called()
        topics = [c[0][0] for c in ha_client._client.publish.call_args_list]
        assert any("state" in t for t in topics)

    def test_publish_input_register_bat_temp_filter(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        from grobro.model.growatt_registers import KNOWN_NEO_REGISTERS
        bat_keys = [k for k in KNOWN_NEO_REGISTERS.input_registers.keys()
                     if k.startswith("bat") and k.endswith("_temp")]
        if not bat_keys:
            pytest.skip("No batX_temp registers in NEO definition")
        payload = {k: -273.1 for k in bat_keys}
        payload["Ppv"] = 100
        state = HomeAssistantInputRegister(
            device_id="QMN000ABC1D2E3FG",
            payload=payload,
        )
        ha_client.publish_input_register(state)
        published = None
        for call_args in ha_client._client.publish.call_args_list:
            topic = call_args[0][0]
            if "state" in topic:
                published = json.loads(call_args[0][1])
                break
        assert published is not None
        for k in bat_keys:
            assert published.get(k) is None
        assert published.get("Ppv") == 100


class TestClientHoldingInput:
    def test_publish_holding_register_input(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantHoldingRegisterInput, HomeAssistantHoldingRegisterValue
        from grobro.model.growatt_registers import HomeAssistantHoldingRegister
        state = HomeAssistantHoldingRegisterInput(
            device_id="QMN000ABC1D2E3FG",
            payload=[
                HomeAssistantHoldingRegisterValue(
                    name="output_power_limit",
                    value=80,
                    register=HomeAssistantHoldingRegister(name="Output Power Limit", publish=True, type="number"),
                ),
            ],
        )
        ha_client.publish_holding_register_input(state)
        ha_client._client.publish.assert_called_once()
        topic = ha_client._client.publish.call_args[0][0]
        assert "output_power_limit" in topic
        assert "get" in topic

    def test_publish_holding_register_error(self, caplog, ha_client):
        ha_client._client.publish.side_effect = Exception("publish error")
        from grobro.model.growatt_registers import (
            HomeAssistantHoldingRegisterInput,
            HomeAssistantHoldingRegisterValue,
            HomeAssistantHoldingRegister,
        )
        state = HomeAssistantHoldingRegisterInput(
            device_id="QMN000ABC1D2E3FG",
            payload=[
                HomeAssistantHoldingRegisterValue(
                    name="test_reg",
                    value=42,
                    register=HomeAssistantHoldingRegister(name="Test", publish=True, type="number"),
                ),
            ],
        )
        ha_client.publish_holding_register_input(state)
        assert "publish error" in caplog.text


class TestClientOnMessage:
    def test_unknown_device_type(self, ha_client):
        msg = _msg("homeassistant/number/grobro/UNKNOWN/output_power_limit/set", b"100")
        ha_client._client.on_message(None, None, msg)
        ha_client.on_command.assert_not_called()

    def test_invalid_topic_format(self, ha_client):
        msg = _msg("homeassistant/too/few/parts")
        ha_client._client.on_message(None, None, msg)
        ha_client.on_command.assert_not_called()

    def test_button_read_all(self, ha_client):
        msg = _msg("homeassistant/button/grobro/QMN000ABC1D2E3FG/read_all/read")
        with patch("grobro.ha.client.Timer") as mock_timer:
            timer_instance = MagicMock()
            mock_timer.return_value = timer_instance
            ha_client._client.on_message(None, None, msg)
        ha_client.on_command.assert_called()
        assert mock_timer.called

    def test_button_read_single(self, ha_client):
        msg = _msg("homeassistant/button/grobro/QMN000ABC1D2E3FG/output_power_limit/read")
        ha_client._client.on_message(None, None, msg)
        ha_client.on_command.assert_called_once()

    def test_number_set(self, ha_client):
        msg = _msg("homeassistant/number/grobro/QMN000ABC1D2E3FG/output_power_limit/set", b"75")
        ha_client._client.on_message(None, None, msg)
        assert ha_client.on_command.call_count == 2

    def test_switch_set_on(self, ha_client):
        msg = _msg("homeassistant/switch/grobro/QMN000ABC1D2E3FG/output_power_limit/set", b"ON")
        ha_client._client.on_message(None, None, msg)
        write_call = ha_client.on_command.call_args_list[0][0][0]
        assert write_call.value == 1

    def test_switch_set_off(self, ha_client):
        msg = _msg("homeassistant/switch/grobro/QMN000ABC1D2E3FG/output_power_limit/set", b"OFF")
        ha_client._client.on_message(None, None, msg)
        write_call = ha_client.on_command.call_args_list[0][0][0]
        assert write_call.value == 0

    def test_config_set_sync_time(self, ha_client):
        msg = _msg("homeassistant/config/grobro/QMN000ABC1D2E3FG/31/set", b"")
        ha_client._client.on_message(None, None, msg)
        ha_client.on_config_command.assert_called_once()
        dev, reg, val = ha_client.on_config_command.call_args[0]
        assert reg == 31
        assert "20" in val

    def test_config_set_normal(self, ha_client):
        msg = _msg("homeassistant/config/grobro/QMN000ABC1D2E3FG/1280/set", b"60")
        ha_client._client.on_message(None, None, msg)
        ha_client.on_config_command.assert_called_once_with(
            "QMN000ABC1D2E3FG", 1280, "60"
        )


class TestClientDeviceInfo:
    def test_device_info_from_cache(self, ha_client):
        cfg = DeviceConfig(serial_number="QMN000ABC1D2E3FG", device_type="55", sw_version="v1.0")
        ha_client._config_cache["QMN000ABC1D2E3FG"] = cfg
        info = ha_client._Client__device_info_from_config("QMN000ABC1D2E3FG")
        assert info["identifiers"] == ["QMN000ABC1D2E3FG"]
        assert info["model"] == "NEO-series"
        assert info["sw_version"] == "v1.0"

    def test_device_info_fallback_file(self, ha_client):
        with patch("grobro.model.device_config.DeviceConfig.from_file") as from_file:
            from_file.return_value = DeviceConfig(serial_number="QMN000ABC1D2E3FG", device_type="55")
            info = ha_client._Client__device_info_from_config("QMN000ABC1D2E3FG")
            assert info["model"] == "NEO-series"

    def test_device_info_fallback_minimal(self, ha_client):
        with patch("grobro.model.device_config.DeviceConfig.from_file", return_value=None):
            info = ha_client._Client__device_info_from_config("QMN000ABC1D2E3FG")
            assert info["model"] == "NEO-series"

    def test_device_info_with_mac(self, ha_client):
        cfg = DeviceConfig(serial_number="QMN000ABC1D2E3FG", mac_address="aa:bb:cc:dd:ee:ff")
        ha_client._config_cache["QMN000ABC1D2E3FG"] = cfg
        info = ha_client._Client__device_info_from_config("QMN000ABC1D2E3FG")
        assert info["connections"] == [["mac", "aa:bb:cc:dd:ee:ff"]]

    def test_device_info_with_hw_version(self, ha_client):
        cfg = DeviceConfig(serial_number="0PVP0000TEST0001", hw_version="2.0")
        ha_client._config_cache["0PVP0000TEST0001"] = cfg
        info = ha_client._Client__device_info_from_config("0PVP0000TEST0001")
        assert info["hw_version"] == "2.0"

    def test_device_info_with_model_id(self, ha_client):
        cfg = DeviceConfig(serial_number="QMN000ABC1D2E3FG", model_id="MIN-XH")
        ha_client._config_cache["QMN000ABC1D2E3FG"] = cfg
        info = ha_client._Client__device_info_from_config("QMN000ABC1D2E3FG")
        assert "MIN-XH" in info["model"]


class TestClientDiscovery:
    def test_discovery_publishes(self, ha_client):
        ha_client._discovery_cache.append("QMN000ABC1D2E3FG")
        ha_client._Client__publish_device_discovery("QMN000ABC1D2E3FG")
        assert ha_client._client.publish.called

    def test_discovery_skip_unchanged_payload(self, ha_client):
        ha_client._Client__publish_device_discovery("QMN000ABC1D2E3FG")
        first_calls = len(ha_client._client.publish.call_args_list)
        ha_client._Client__publish_device_discovery("QMN000ABC1D2E3FG")
        assert len(ha_client._client.publish.call_args_list) > first_calls

    def test_discovery_unknown_device(self, ha_client):
        ha_client._Client__publish_device_discovery("UNKNOWN")
        # logs info, returns early

    def test_migrate_entity_discovery(self, ha_client):
        known = get_known_registers("QMN000ABC1D2E3FG")
        ha_client._Client__migrate_entity_discovery("QMN000ABC1D2E3FG", known)
        assert ha_client._client.publish.called


class TestClientAvailability:
    def test_publish_availability_online(self, ha_client):
        ha_client._Client__publish_availability("QMN000ABC1D2E3FG", True)
        topics = [c[0][0] for c in ha_client._client.publish.call_args_list]
        assert any("availability" in t for t in topics)

    def test_publish_availability_offline_no_sensor(self, ha_client):
        ha_client._Client__publish_availability("QMN000ABC1D2E3FG", False)
        topics = [c[0][0] for c in ha_client._client.publish.call_args_list]
        assert all("online" not in t for t in topics)

    @patch("grobro.ha.client.AVAILABILITY_SENSOR", True)
    def test_publish_availability_offline_with_sensor(self, ha_client):
        ha_client._Client__publish_availability("QMN000ABC1D2E3FG", False)
        topics = [c[0][0] for c in ha_client._client.publish.call_args_list]
        assert any("online" in t for t in topics)

    @patch("grobro.ha.client.AVAILABILITY_SENSOR", True)
    def test_publish_availability_online_with_sensor(self, ha_client):
        ha_client._Client__publish_availability("QMN000ABC1D2E3FG", True)
        topics = [c[0][0] for c in ha_client._client.publish.call_args_list]
        assert any("availability" in t for t in topics)
        assert any("online" in t for t in topics)


class TestClientDeviceTimer:
    @patch("grobro.ha.client.DEVICE_TIMEOUT", 10)
    def test_device_timer(self, ha_client):
        ha_client._Client__reset_device_timer("QMN000ABC1D2E3FG")
        assert "QMN000ABC1D2E3FG" in ha_client._device_timers
        ha_client._Client__reset_device_timer("QMN000ABC1D2E3FG")
        assert "QMN000ABC1D2E3FG" in ha_client._device_timers

    def test_set_device_offline(self, ha_client):
        ha_client._Client__publish_availability = MagicMock()
        with patch("grobro.ha.client.DEVICE_TIMEOUT", 10):
            ha_client._Client__reset_device_timer("QMN000ABC1D2E3FG")
            timer = ha_client._device_timers["QMN000ABC1D2E3FG"]
            timer.function(timer.args[0])
            ha_client._Client__publish_availability.assert_called_once_with(
                "QMN000ABC1D2E3FG", False
            )


class TestClientConfigReadSequencing:
    @pytest.fixture(autouse=True)
    def _reset_shared_state(self):
        Client._config_read_queues.clear()
        Client._config_read_inflight.clear()
        Client._config_read_timers.clear()

    def test_kickoff_inflight(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c.on_config_read = MagicMock()
                c._Client__kickoff_next_config_read("QMN000ABC1D2E3FG")
                c.on_config_read.assert_not_called()

    def test_kickoff_with_queue(self):
        from collections import deque
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c.on_config_read = MagicMock()
                c._config_read_queues["QMN000ABC1D2E3FG"] = deque([1280, 2048])
                with patch("grobro.ha.client.Timer"):
                    c._Client__kickoff_next_config_read("QMN000ABC1D2E3FG")
                c.on_config_read.assert_called_once_with("QMN000ABC1D2E3FG", 1280)

    def test_kickoff_skips_if_inflight(self):
        from collections import deque
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c.on_config_read = MagicMock()
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 999
                c._config_read_queues["QMN000ABC1D2E3FG"] = deque([1280])
                c._Client__kickoff_next_config_read("QMN000ABC1D2E3FG")
                c.on_config_read.assert_not_called()

    def test_handle_config_read_response(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 1280
                with patch("grobro.ha.client.Timer"):
                    c.handle_config_read_response("QMN000ABC1D2E3FG", 1280)
                assert "QMN000ABC1D2E3FG" not in c._config_read_inflight

    def test_handle_config_read_response_wrong_register(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 1280
                with patch("grobro.ha.client.Timer"):
                    c.handle_config_read_response("QMN000ABC1D2E3FG", 999)
                assert c._config_read_inflight["QMN000ABC1D2E3FG"] == 1280

    def test_config_read_timeout(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 1280
                c._Client__config_read_timeout("QMN000ABC1D2E3FG", 1280)
                assert "QMN000ABC1D2E3FG" not in c._config_read_inflight

    def test_config_read_timeout_wrong_register(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 1280
                c._Client__config_read_timeout("QMN000ABC1D2E3FG", 999)
                assert c._config_read_inflight["QMN000ABC1D2E3FG"] == 1280


class TestClientNoahOnMessage:
    def test_button_read_all_noah(self, ha_client):
        msg = _msg("homeassistant/button/grobro/0PVP0000TEST0001/read_all/read")
        with patch("grobro.ha.client.Timer") as mock_timer:
            timer_instance = MagicMock()
            mock_timer.return_value = timer_instance
            ha_client._client.on_message(None, None, msg)
        ha_client.on_command.assert_called()
