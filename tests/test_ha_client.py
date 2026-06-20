import os
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.ha.client import (
    Client,
    get_known_registers,
    get_device_type_name,
    map_enum_value,
    make_modbus_command,
    iter_command_registers,
    _get_bat_number,
    _detect_bat_count,
    _resolve_max_bat,
    _MAX_BAT_CACHE,
    _LAST_BAT_SERIALS,
    KEEP_BATTERY_POSITION,
)
from grobro.model.modbus_message import GrowattModbusFunction, GrowattModbusMessage
from grobro.model.modbus_function import GrowattModbusFunctionSingle
from grobro.model.mqtt_config import MQTTConfig
from grobro.model.device_config import DeviceConfig
from grobro.model.growatt_registers import HomeAssistantInputRegister
from grobro.grobro.parser import unscramble


DATA_DIR = __file__[: __file__.rfind("/")] + "/model/data"


def _msg(topic: str, payload: bytes = b""):
    m = MagicMock(spec=MQTTMessage)
    m.topic = topic
    m.payload = payload
    m.qos = 0
    m.retain = False
    m.properties = MagicMock()
    return m


def _load_payload(file_name: str) -> dict:
    with open(os.path.join(DATA_DIR, file_name), "rb") as f:
        data = f.read()
    unscrambled = unscramble(data)
    msg = GrowattModbusMessage.parse_grobro(unscrambled)
    known = get_known_registers(msg.device_id)
    payload = {}
    for name, reg in known.input_registers.items():
        data_raw = msg.get_data(reg.growatt.position)
        value = reg.growatt.data.parse(data_raw)
        if value is not None:
            payload[name] = value
    return payload


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

    def test_get_known_registers_raq(self):
        r = get_known_registers("RAQ0E8H042")
        assert r is not None

    def test_get_device_type_name_raq(self):
        assert get_device_type_name("RAQ0E8H042") == "ShineWeLink"

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


class TestBatNumber:
    def test_bat1_temp(self):
        assert _get_bat_number("bat1_temp") == 1

    def test_bat4_temp(self):
        assert _get_bat_number("bat4_temp") == 4

    def test_bat_2_ser_part_1(self):
        assert _get_bat_number("bat2_ser_part_1") == 2

    def test_bat_1_soc_pct(self):
        assert _get_bat_number("bat_1_soc_pct") == 1

    def test_bat_sysstate(self):
        assert _get_bat_number("bat_sysstate") is None

    def test_bat_cnt(self):
        assert _get_bat_number("bat_cnt") is None

    def test_battery1Soc(self):
        assert _get_bat_number("battery1Soc") == 1

    def test_battery4Soc(self):
        assert _get_bat_number("battery4Soc") == 4

    def test_batteryPackageQuantity(self):
        assert _get_bat_number("batteryPackageQuantity") is None

    def test_batteryCycles(self):
        assert _get_bat_number("batteryCycles") is None

    def test_non_bat_name(self):
        assert _get_bat_number("Ppv") is None


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
                Client(cfg)
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
        ha_client.set_config("QMN000ABC1D2E3FG", cfg)
        assert ha_client._config_cache["QMN000ABC1D2E3FG"] == cfg
        # discovery should attempt publish
        assert ha_client._client.publish.called

    def test_set_config_unchanged(self, ha_client):
        cfg = DeviceConfig(serial_number="QMN000ABC1D2E3FG")
        ha_client._config_cache["QMN000ABC1D2E3FG"] = cfg
        ha_client._discovery_cache.append("QMN000ABC1D2E3FG")
        with patch("grobro.ha.client.model.DeviceConfig.from_file") as from_file:
            from_file.return_value = cfg
            ha_client.set_config("QMN000ABC1D2E3FG", cfg)

    def test_set_config_topic_serial_trumps_config_serial(self, ha_client):
        """set_config must use the MQTT topic serial (device_id), not config.serial_number,
        to avoid merging multiple devices behind a shared data logger (#178)."""
        topic_serial = "RAQ0TEST01"
        fe19_serial = "PTQ0TEST01"
        cfg = DeviceConfig(serial_number=fe19_serial)
        ha_client.set_config(topic_serial, cfg)
        assert ha_client._config_cache[topic_serial] == cfg
        assert fe19_serial not in ha_client._config_cache
        discovery_topic_called = any(
            f"/device/{topic_serial}/config" in c[0][0]
            for c in ha_client._client.publish.call_args_list
        )
        assert discovery_topic_called, f"Discovery should use topic serial '{topic_serial}'"


class TestClientPublishInput:
    def test_publish_input_register(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        payload = _load_payload("NeoReadInputRegisters.bin")
        state = HomeAssistantInputRegister(
            device_id="QMN000ABC1D2E3FG",
            payload=payload,
        )
        ha_client.publish_input_register(state)
        ha_client._client.publish.assert_called()
        expected_topic = "homeassistant/grobro/QMN000ABC1D2E3FG/state"
        published = None
        for call_args in ha_client._client.publish.call_args_list:
            if call_args[0][0] == expected_topic:
                published = json.loads(call_args[0][1])
                break
        assert published is not None
        assert published.get("Ppv") == 525.1
        assert published.get("Inverter_Status") == "NormalStatus"

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

    def test_device_info_with_masked_mac(self, ha_client):
        cfg = DeviceConfig(serial_number="0PVP0000TEST0001", mac_address="aa:bb:cc:dd:ee:xx")
        ha_client._config_cache["0PVP0000TEST0001"] = cfg
        info = ha_client._Client__device_info_from_config("0PVP0000TEST0001")
        assert "connections" not in info

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
        topics = [c[0][0] for c in ha_client._client.publish.call_args_list]
        assert any("Ppv/config" in t for t in topics)
        assert any("Fac/config" in t for t in topics)
        assert any("Inverter_Status/config" in t for t in topics)


class TestNeoPvCountDetection:
    def test_neo2000_detects_4_inputs_from_fixture(self, ha_client):
        payload = _load_payload("NeoInputRegister_NEO2000.bin")
        device_id = "QMN0ANONYMIZED01"
        ha_client._Client__detect_neo_pv_count(device_id, payload)
        assert ha_client._neo_pv_count.get(device_id) == 4

    def test_non_neo_not_detected(self, ha_client):
        ha_client._Client__detect_neo_pv_count(
            "0PVP0000TEST0001", {"Ppv": 500, "Ppv1": 250, "Ppv2": 250}
        )
        assert "0PVP0000TEST0001" not in ha_client._neo_pv_count

    def test_ptq_detected(self, ha_client):
        ha_client._Client__detect_neo_pv_count(
            "PTQ0TEST1234567", {"Ppv": 600, "Ppv1": 150, "Ppv2": 150, "Ppv3": 150, "Ppv4": 150}
        )
        assert ha_client._neo_pv_count.get("PTQ0TEST1234567") == 4

    def test_zero_power_no_detection(self, ha_client):
        ha_client._Client__detect_neo_pv_count(
            "QMN000TESTDEVICE1", {"Ppv": 0, "Ppv1": 0, "Ppv2": 0, "Ppv3": 0, "Ppv4": 0}
        )
        assert "QMN000TESTDEVICE1" not in ha_client._neo_pv_count

    def test_already_cached_returns_early(self, ha_client):
        ha_client._neo_pv_count["QMN000CACHED1"] = 2
        ha_client._Client__detect_neo_pv_count(
            "QMN000CACHED1", {"Ppv": 999, "Ppv1": 100, "Ppv2": 100, "Ppv3": 400, "Ppv4": 400}
        )
        assert ha_client._neo_pv_count["QMN000CACHED1"] == 2

    def test_2_input_neo_detected(self, ha_client):
        ha_client._Client__detect_neo_pv_count(
            "QMN000TEST2IN", {"Ppv": 500.0, "Ppv1": 250.0, "Ppv2": 250.0, "Ppv3": 0, "Ppv4": 0}
        )
        assert ha_client._neo_pv_count.get("QMN000TEST2IN") == 2

    def test_sum4_mismatch_no_detection(self, ha_client):
        ha_client._Client__detect_neo_pv_count(
            "QMN000MISMATCH", {"Ppv": 500.0, "Ppv1": 100.0, "Ppv2": 100.0, "Ppv3": 100.0, "Ppv4": 100.0}
        )
        assert "QMN000MISMATCH" not in ha_client._neo_pv_count


class TestNeoPvCountDiscovery:
    def _get_discovery_payload(self, ha_client, device_id):
        for call_args in ha_client._client.publish.call_args_list:
            if call_args.args[0] == f"homeassistant/device/{device_id}/config" and call_args.args[1]:
                return json.loads(call_args.args[1])
        return None

    def test_pv3_pv4_included_when_count_4(self, ha_client):
        ha_client._neo_pv_count["QMN000TESTDISC1"] = 4
        ha_client._Client__publish_device_discovery("QMN000TESTDISC1")
        payload = self._get_discovery_payload(ha_client, "QMN000TESTDISC1")
        assert payload is not None
        cmps = payload["cmps"]
        assert "grobro_QMN000TESTDISC1_Vpv3" in cmps
        assert "grobro_QMN000TESTDISC1_Ipv3" in cmps
        assert "grobro_QMN000TESTDISC1_Ppv3" in cmps
        assert "grobro_QMN000TESTDISC1_Vpv4" in cmps
        assert "grobro_QMN000TESTDISC1_Ipv4" in cmps
        assert "grobro_QMN000TESTDISC1_Ppv4" in cmps

    def test_pv3_pv4_excluded_when_no_count(self, ha_client):
        ha_client._Client__publish_device_discovery("QMN000TESTDISC2")
        payload = self._get_discovery_payload(ha_client, "QMN000TESTDISC2")
        assert payload is not None
        cmps = payload["cmps"]
        assert "grobro_QMN000TESTDISC2_Vpv3" not in cmps
        assert "grobro_QMN000TESTDISC2_Ppv3" not in cmps
        assert "grobro_QMN000TESTDISC2_Vpv4" not in cmps
        assert "grobro_QMN000TESTDISC2_Ppv4" not in cmps

    def test_pv3_pv4_excluded_when_count_2(self, ha_client):
        ha_client._neo_pv_count["QMN000TESTDISC3"] = 2
        ha_client._Client__publish_device_discovery("QMN000TESTDISC3")
        payload = self._get_discovery_payload(ha_client, "QMN000TESTDISC3")
        assert payload is not None
        cmps = payload["cmps"]
        assert "grobro_QMN000TESTDISC3_Vpv3" not in cmps
        assert "grobro_QMN000TESTDISC3_Ppv3" not in cmps
        assert "grobro_QMN000TESTDISC3_Vpv4" not in cmps
        assert "grobro_QMN000TESTDISC3_Ppv4" not in cmps

    def test_discovery_runs_after_detection(self, ha_client):
        payload = _load_payload("NeoInputRegister_NEO2000.bin")
        device_id = "QMN0ANONYMIZED01"
        state = HomeAssistantInputRegister(device_id=device_id, payload=payload)
        ha_client.publish_input_register(state)
        disc = self._get_discovery_payload(ha_client, device_id)
        assert disc is not None
        cmps = disc["cmps"]
        assert "grobro_QMN0ANONYMIZED01_Vpv3" in cmps
        assert "grobro_QMN0ANONYMIZED01_Ppv3" in cmps


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
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c.on_config_read = MagicMock()
                c._Client__kickoff_next_config_read("QMN000ABC1D2E3FG")
                c.on_config_read.assert_not_called()

    def test_kickoff_with_queue(self):
        from collections import deque
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c.on_config_read = MagicMock()
                c._config_read_queues["QMN000ABC1D2E3FG"] = deque([1280, 2048])
                with patch("grobro.ha.client.Timer"):
                    c._Client__kickoff_next_config_read("QMN000ABC1D2E3FG")
                c.on_config_read.assert_called_once_with("QMN000ABC1D2E3FG", 1280)

    def test_kickoff_skips_if_inflight(self):
        from collections import deque
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c.on_config_read = MagicMock()
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 999
                c._config_read_queues["QMN000ABC1D2E3FG"] = deque([1280])
                c._Client__kickoff_next_config_read("QMN000ABC1D2E3FG")
                c.on_config_read.assert_not_called()

    def test_handle_config_read_response(self):
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 1280
                with patch("grobro.ha.client.Timer"):
                    c.handle_config_read_response("QMN000ABC1D2E3FG", 1280)
                assert "QMN000ABC1D2E3FG" not in c._config_read_inflight

    def test_handle_config_read_response_wrong_register(self):
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 1280
                with patch("grobro.ha.client.Timer"):
                    c.handle_config_read_response("QMN000ABC1D2E3FG", 999)
                assert c._config_read_inflight["QMN000ABC1D2E3FG"] == 1280

    def test_config_read_timeout(self):
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._config_read_inflight["QMN000ABC1D2E3FG"] = 1280
                c._Client__config_read_timeout("QMN000ABC1D2E3FG", 1280)
                assert "QMN000ABC1D2E3FG" not in c._config_read_inflight

    def test_config_read_timeout_wrong_register(self):
        with patch("grobro.ha.client.mqtt.Client"):
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


    def test_publish_input_register_noah(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        payload = _load_payload("NoahReadInputRegisters_0-124.bin")
        state = HomeAssistantInputRegister(
            device_id="0PVP0000TEST0001",
            payload=payload,
        )
        ha_client.publish_input_register(state)
        expected_topic = "homeassistant/grobro/0PVP0000TEST0001/state"
        published = None
        for call_args in ha_client._client.publish.call_args_list:
            if call_args[0][0] == expected_topic:
                published = json.loads(call_args[0][1])
                break
        assert published is not None
        assert published.get("out_power") == 356.0
        assert published.get("bat1_temp") == 24.0
        assert published.get("bat_1_soc_pct") == 100
        assert "bat2_temp" not in published
        assert published.get("tot_bat_soc_pct") == 100
        assert published.get("bat_cnt") == 1
        assert published.get("bat_cyclecnt") == 14
        assert published.get("bat_sysstate") == "Idle"


class TestMaxBat:
    def test_publish_input_register_filters_bat_above_max(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        real_payload = _load_payload("NoahReadInputRegisters_0-124.bin")
        with patch("grobro.ha.client.MAX_BAT", 1):
            state = HomeAssistantInputRegister(
                device_id="0PVP0000TEST0001",
                payload=real_payload,
            )
            ha_client.publish_input_register(state)
        published = None
        expected_topic = "homeassistant/grobro/0PVP0000TEST0001/state"
        for entry in ha_client._client.publish.call_args_list:
            if entry.args[0] == expected_topic:
                published = json.loads(entry.args[1])
                break
        assert published is not None
        assert published.get("out_power") is not None
        assert published.get("bat1_temp") == 24.0
        assert published.get("bat_1_soc_pct") == 100
        assert "bat2_temp" not in published
        assert "bat_2_soc_pct" not in published

    def test_discovery_filters_bat_above_max(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                with patch("grobro.ha.client.MAX_BAT", 1):
                    c = Client(MQTTConfig(host="localhost", port=1883))
                    c._Client__publish_device_discovery("0PVP0000TEST0001")
        discovery_calls = [c for c in mc.return_value.publish.call_args_list
                           if "device" in c[0][0] and "config" in c[0][0] and c[0][1]]
        assert discovery_calls, "No discovery calls found"
        payload = json.loads(discovery_calls[-1][0][1])
        cmps = payload.get("cmps", {})
        cmp_names = [v["name"] for v in cmps.values()]
        assert any("Bat1" in n for n in cmp_names)
        assert any("Battery 1" in n for n in cmp_names)
        assert not any("Bat2" in n for n in cmp_names)
        assert not any("Bat3" in n for n in cmp_names)
        assert not any("Bat4" in n for n in cmp_names)
        assert not any("Battery 2" in n for n in cmp_names)
        assert any("Battery Count" in n for n in cmp_names)

    def test_max_bat_default(self):
        from grobro.ha.client import MAX_BAT
        assert MAX_BAT == "auto"

    def test_detect_bat_count_all_empty(self):
        payload = {"bat1_temp": 24.0}
        assert _detect_bat_count(payload) == 4

    def test_detect_bat_count_bat_cnt_1_empty_serials(self):
        """Tower 2: 1 battery, serial parts empty → bat_cnt wins (was returning 4)."""
        payload = {"bat_cnt": 1, "bat2_ser_part_1": "", "bat3_ser_part_1": "", "bat4_ser_part_1": ""}
        assert _detect_bat_count(payload) == 1

    def test_detect_bat_count_bat_cnt_overrides_serials(self):
        """bat_cnt takes priority even when serial parts have values."""
        payload = {"bat_cnt": 1, "bat2_ser_part_1": "SN002"}
        assert _detect_bat_count(payload) == 1

    def test_detect_bat_count_bat_cnt_2(self):
        """Tower 1: 2 batteries."""
        payload = {"bat_cnt": 2, "bat2_ser_part_1": "SN002"}
        assert _detect_bat_count(payload) == 2

    def test_detect_bat_count_serial_fallback(self):
        """No bat_cnt in payload → detect from serial parts."""
        payload = {"bat2_ser_part_1": "SN002"}
        assert _detect_bat_count(payload) == 2

    def test_detect_bat_count_serial_multi(self):
        payload = {"bat2_ser_part_1": "A", "bat3_ser_part_1": "B"}
        assert _detect_bat_count(payload) == 3

    def test_detect_bat_count_serial_all_four(self):
        payload = {"bat2_ser_part_1": "A", "bat3_ser_part_1": "B", "bat4_ser_part_1": "C"}
        assert _detect_bat_count(payload) == 4

    def test_resolve_max_bat_int(self):
        with patch("grobro.ha.client.MAX_BAT", 2):
            assert _resolve_max_bat("device1") == 2
            assert _resolve_max_bat("device1", {"bat2_ser_part_1": "X"}) == 2

    def test_resolve_max_bat_auto_caches(self):
        _MAX_BAT_CACHE.clear()
        with patch("grobro.ha.client.MAX_BAT", "auto"):
            payload = {"bat2_ser_part_1": "X", "bat3_ser_part_1": "Y"}
            assert _resolve_max_bat("device1", payload) == 3
            assert _MAX_BAT_CACHE.get("device1") == 3

    def test_resolve_max_bat_auto_fallback_no_payload(self):
        _MAX_BAT_CACHE.clear()
        with patch("grobro.ha.client.MAX_BAT", "auto"):
            assert _resolve_max_bat("unknown") == 4

    def test_auto_mode_keeps_all_when_serials_empty(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        payload = {"bat1_temp": 24, "bat2_temp": 25, "bat3_temp": 26, "bat4_temp": 27}
        with patch("grobro.ha.client.MAX_BAT", "auto"):
            state = HomeAssistantInputRegister(device_id="0PVP0000TEST0001", payload=dict(payload))
            ha_client.publish_input_register(state)
        published = None
        for entry in ha_client._client.publish.call_args_list:
            if entry.args[0] == "homeassistant/grobro/0PVP0000TEST0001/state":
                published = json.loads(entry.args[1])
                break
        assert published is not None
        assert published.get("bat1_temp") == 24
        assert published.get("bat2_temp") == 25
        assert published.get("bat3_temp") == 26
        assert published.get("bat4_temp") == 27

    def test_auto_mode_with_bat2_serial_shows_correct_batteries(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        payload = {
            "bat1_temp": 24, "bat2_temp": 25, "bat3_temp": 26,
            "bat2_ser_part_1": "SN002",
        }
        with patch("grobro.ha.client.MAX_BAT", "auto"):
            state = HomeAssistantInputRegister(device_id="0PVP0000TEST0002", payload=dict(payload))
            ha_client.publish_input_register(state)
        published = None
        for entry in ha_client._client.publish.call_args_list:
            if entry.args[0] == "homeassistant/grobro/0PVP0000TEST0002/state":
                published = json.loads(entry.args[1])
                break
        assert published is not None
        assert published.get("bat1_temp") == 24
        assert published.get("bat2_temp") == 25
        assert "bat3_temp" not in published


class TestEdgeCases:
    def test_map_enum_bitfield(self):
        from grobro.ha.client import map_enum_value
        reg = MagicMock()
        reg.growatt.data.data_type = "ENUM"
        reg.growatt.data.enum_options.enum_type = "BITFIELD"
        result = map_enum_value(reg, 5)
        assert result == 5

    def test_publish_unknown_payload_key(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        state = HomeAssistantInputRegister(
            device_id="QMN000ABC1D2E3FG",
            payload={"nonexistent_key": 42},
        )
        ha_client.publish_input_register(state)
        ha_client._client.publish.assert_called()

    def test_device_timer_enabled(self):
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                with patch("grobro.ha.client.DEVICE_TIMEOUT", 5):
                    c = Client(MQTTConfig(host="localhost", port=1883))
                    c._Client__publish_device_discovery = MagicMock()
                    c._Client__publish_availability = MagicMock()
                    from grobro.model.growatt_registers import HomeAssistantInputRegister
                    state = HomeAssistantInputRegister(
                        device_id="QMN000ABC1D2E3FG",
                        payload={"Ppv": 100},
                    )
                    c.publish_input_register(state)
                    assert "QMN000ABC1D2E3FG" in c._device_timers

    def test_on_connect_logs(self, caplog):
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                caplog.set_level("DEBUG")
                c._Client__on_connect(None, None, None, 0, None)
                assert "Connected to HA MQTT server" in caplog.text

    def test_slot_name_parse_error(self, ha_client):
        msg = _msg("homeassistant/button/grobro/QMN000ABC1D2E3FG/read_all/read")
        with patch("grobro.ha.client.MAX_SLOTS", 1):
            ha_client._client.on_message(None, None, msg)
        ha_client.on_command.assert_called()

    def test_device_info_fallback_new_device(self):
        with patch("grobro.ha.client.mqtt.Client"):
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                with patch("grobro.model.device_config.DeviceConfig.from_file", return_value=None):
                    c = Client(MQTTConfig(host="localhost", port=1883))
                    info = c._Client__device_info_from_config("NEW_DEVICE_ID")
                    assert info["identifiers"] == ["NEW_DEVICE_ID"]
                    assert info["model"] == "UNKNOWN-series"

    def test_config_read_timeout_clears_state(self, ha_client):
        ha_client._config_read_queues.clear()
        ha_client._config_read_inflight.clear()
        ha_client._config_read_inflight["QMN000ABC1D2E3FG"] = 999
        ha_client._Client__config_read_timeout("QMN000ABC1D2E3FG", 999)
        assert "QMN000ABC1D2E3FG" not in ha_client._config_read_inflight

    def test_discovery_online_entity_with_device_timeout(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                with patch("grobro.ha.client.DEVICE_TIMEOUT", 10):
                    with patch("grobro.ha.client.AVAILABILITY_SENSOR", True):
                        c = Client(MQTTConfig(host="localhost", port=1883))
                        c._Client__publish_device_discovery("QMN000ABC1D2E3FG")
        discovery_calls = [c for c in mc.return_value.publish.call_args_list
                           if "device" in c[0][0] and "config" in c[0][0] and c[0][1]]
        assert discovery_calls
        payload = json.loads(discovery_calls[-1][0][1])
        cmps = payload.get("cmps", {})
        cmp_names = [v["name"] for v in cmps.values()]
        assert any("Online" in n for n in cmp_names)


class TestCombinedSerial:
    def test_combined_serial_in_state_payload(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        payload = {
            "bat1_temp": 24,
            "bat2_ser_part_1": "SN02",
            "bat2_ser_part_2": "ABCD",
            "bat2_ser_part_3": "EFGH",
            "bat2_ser_part_4": "IJKL",
            "bat3_ser_part_1": "SN03",
            "bat4_ser_part_1": "SN04",
            "bat4_ser_part_2": "BAT4",
        }
        state = HomeAssistantInputRegister(device_id="0PVP0000TEST0001", payload=dict(payload))
        ha_client.publish_input_register(state)
        published = None
        for entry in ha_client._client.publish.call_args_list:
            if entry.args[0] == "homeassistant/grobro/0PVP0000TEST0001/state":
                published = json.loads(entry.args[1])
                break
        assert published is not None
        assert published.get("bat1_temp") == 24
        assert published.get("bat2_serial") == "SN02ABCDEFGHIJKL"
        assert published.get("bat3_serial") == "SN03"
        assert published.get("bat4_serial") == "SN04BAT4"
        assert "bat2_ser_part_1" not in published
        assert "bat2_ser_part_2" not in published
        assert "bat2_ser_part_3" not in published
        assert "bat2_ser_part_4" not in published

    def test_combined_serial_respects_max_bat(self, ha_client):
        from grobro.model.growatt_registers import HomeAssistantInputRegister
        payload = {
            "bat1_temp": 24,
            "bat2_ser_part_1": "SN002",
            "bat2_ser_part_2": "ABCD",
            "bat3_ser_part_1": "SN003",
        }
        with patch("grobro.ha.client.MAX_BAT", 1):
            state = HomeAssistantInputRegister(device_id="0PVP0000TEST0001", payload=dict(payload))
            ha_client.publish_input_register(state)
        published = None
        for entry in ha_client._client.publish.call_args_list:
            if entry.args[0] == "homeassistant/grobro/0PVP0000TEST0001/state":
                published = json.loads(entry.args[1])
                break
        assert published is not None
        assert "bat2_serial" not in published
        assert "bat3_serial" not in published

    def test_discovery_excludes_ser_part_includes_combined(self):
        with patch("grobro.ha.client.mqtt.Client") as mc:
            with patch("grobro.ha.client.os.listdir", return_value=[]):
                c = Client(MQTTConfig(host="localhost", port=1883))
                c._Client__publish_device_discovery("0PVP0000TEST0001")
        discovery_calls = [c for c in mc.return_value.publish.call_args_list
                           if "device" in c[0][0] and "config" in c[0][0] and c[1]]
        assert discovery_calls
        payload = json.loads(discovery_calls[-1][0][1])
        cmps = payload.get("cmps", {})
        cmp_names = [v["name"] for v in cmps.values()]
        # No ser_part entities
        assert not any("Ser Part" in n for n in cmp_names)
        assert not any("ser_part" in n for n in cmp_names)
        # Combined serial entities present
        assert any("Bat2 Serial" in n for n in cmp_names)
        assert any("Bat3 Serial" in n for n in cmp_names)
        assert any("Bat4 Serial" in n for n in cmp_names)


class TestBatteryPositionWatch:
    def setup_method(self):
        _LAST_BAT_SERIALS.clear()

    def test_position_change_detected(self, ha_client, caplog):
        caplog.set_level(logging.WARNING)
        with patch("grobro.ha.client.KEEP_BATTERY_POSITION", True):
            # First call: SN002 at Bat2, SN003 at Bat3
            state1 = HomeAssistantInputRegister(
                device_id="0PVP0000TEST0001",
                payload={"bat2_ser_part_1": "SN002", "bat3_ser_part_1": "SN003"},
            )
            ha_client.publish_input_register(state1)
            caplog.clear()

            # Second call: SN003 now at Bat2 (moved from Bat3)
            state2 = HomeAssistantInputRegister(
                device_id="0PVP0000TEST0001",
                payload={"bat2_ser_part_1": "SN003"},
            )
            ha_client.publish_input_register(state2)

        assert "SN003" in caplog.text
        assert "Bat3" in caplog.text
        assert "Bat2" in caplog.text
        assert "re-enumeration" in caplog.text

    def test_no_warning_stable_positions(self, ha_client, caplog):
        caplog.set_level(logging.WARNING)
        with patch("grobro.ha.client.KEEP_BATTERY_POSITION", True):
            state1 = HomeAssistantInputRegister(
                device_id="0PVP0000TEST0001",
                payload={"bat2_ser_part_1": "SN002", "bat3_ser_part_1": "SN003"},
            )
            ha_client.publish_input_register(state1)
            caplog.clear()

            state2 = HomeAssistantInputRegister(
                device_id="0PVP0000TEST0001",
                payload={"bat2_ser_part_1": "SN002", "bat3_ser_part_1": "SN003"},
            )
            ha_client.publish_input_register(state2)

        assert "moved" not in caplog.text
        assert "re-enumeration" not in caplog.text

    def test_disabled_by_default(self, ha_client, caplog):
        caplog.set_level(logging.WARNING)
        state1 = HomeAssistantInputRegister(
            device_id="0PVP0000TEST0001",
            payload={"bat2_ser_part_1": "SN002", "bat3_ser_part_1": "SN003"},
        )
        ha_client.publish_input_register(state1)
        caplog.clear()

        state2 = HomeAssistantInputRegister(
            device_id="0PVP0000TEST0001",
            payload={"bat2_ser_part_1": "SN003"},
        )
        ha_client.publish_input_register(state2)

        assert "moved" not in caplog.text
        assert "re-enumeration" not in caplog.text
