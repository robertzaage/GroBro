from unittest.mock import patch

import json

from grobro.model.growatt_registers import HomeAssistantInputRegister
from grobro.model.mqtt_config import MQTTConfig
from grobro.settings import HA_BASE_TOPIC
from grobro.ha.client import (
    Client,
    _compact_datalogger_version,
    _firmware_part_names,
    compose_combined_firmware,
)


def test_compose_noah_firmware_like_shinephone():
    payload = {
        "fw_version_part_1": 19,
        "fw_version_part_2": 19,
        "fw_version_part_3": 14,
    }

    assert compose_combined_firmware(payload, "4.0.1.9") == "19.19.14.4019"


def test_compose_noah_firmware_uses_dynamic_datalogger_version():
    payload = {
        "fw_version_part_1": 19,
        "fw_version_part_2": 19,
        "fw_version_part_3": 14,
    }

    assert compose_combined_firmware(payload, "4.0.2.0") == "19.19.14.4020"


def test_compose_nexa_firmware_uses_four_device_parts():
    payload = {
        "fw_version_part_1": 14,
        "fw_version_part_2": 12,
        "fw_version_part_3": 14,
        "fw_version_part_4": 11,
    }

    assert compose_combined_firmware(payload, "4.0.1.9") == "14.12.14.11.4019"


def test_compose_firmware_falls_back_to_device_version():
    payload = {
        "fw_version_part_1": 19,
        "fw_version_part_2": 19,
        "fw_version_part_3": 14,
    }

    assert compose_combined_firmware(payload, None) == "19.19.14"


def test_compose_firmware_requires_all_device_parts():
    payload = {
        "fw_version_part_1": 14,
        "fw_version_part_2": 12,
        "fw_version_part_3": 14,
        "fw_version_part_4": None,
    }

    assert compose_combined_firmware(payload, "4.0.1.9") is None


def test_compose_firmware_requires_firmware_parts():
    assert compose_combined_firmware({"Ppv": 123}, "4.0.1.9") is None


def test_compose_firmware_rejects_blank_device_part():
    payload = {
        "fw_version_part_1": 19,
        "fw_version_part_2": " ",
        "fw_version_part_3": 14,
    }

    assert compose_combined_firmware(payload, "4.0.1.9") is None


def test_blank_datalogger_version_falls_back_to_device_version():
    payload = {
        "fw_version_part_1": 19,
        "fw_version_part_2": 19,
        "fw_version_part_3": 14,
    }

    assert compose_combined_firmware(payload, "   ") == "19.19.14"


def test_invalid_datalogger_version_falls_back_to_device_version():
    payload = {
        "fw_version_part_1": 19,
        "fw_version_part_2": 19,
        "fw_version_part_3": 14,
    }

    assert compose_combined_firmware(payload, "4.0.x.9") == "19.19.14"


def test_firmware_part_names_put_malformed_suffix_last():
    payload = {
        "fw_version_part_x": 99,
        "fw_version_part_2": 19,
        "fw_version_part_1": 19,
    }

    assert _firmware_part_names(payload) == [
        "fw_version_part_1",
        "fw_version_part_2",
        "fw_version_part_x",
    ]


def test_compact_datalogger_version_rejects_empty_and_invalid_values():
    assert _compact_datalogger_version("") is None
    assert _compact_datalogger_version("4.0.beta.1") is None


def _client():
    with patch("grobro.ha.client.mqtt.Client"):
        return Client(MQTTConfig(host="localhost", port=1883))


def _discovery_publishes(client, device_id):
    topic = f"{HA_BASE_TOPIC}/device/{device_id}/config"
    return [c.args[1] for c in client._client.publish.call_args_list if c.args[0] == topic]


def test_noah_discovery_and_state_use_the_combined_firmware():
    client = _client()
    device_id = "0PVP0000TEST0001"
    payload = {"fw_version_part_1": 19, "fw_version_part_2": 19, "fw_version_part_3": 14}
    client.publish_input_register(HomeAssistantInputRegister(device_id=device_id, payload=payload))

    discovery = json.loads(_discovery_publishes(client, device_id)[-1])
    assert discovery["dev"]["sw_version"] == "19.19.14"
    assert discovery["cmps"][f"grobro_{device_id}_fw_version"]["value_template"] == "{{ value_json['fw_version'] }}"
    state = [c.args[1] for c in client._client.publish.call_args_list if c.args[0].endswith(f"{device_id}/state")][-1]
    assert json.loads(state)["fw_version"] == "19.19.14"


def test_firmware_update_republishes_discovery_without_clearing():
    client = _client()
    device_id = "0PVP0000TEST0001"
    payload = {"fw_version_part_1": 19, "fw_version_part_2": 19, "fw_version_part_3": 14}
    client.publish_input_register(HomeAssistantInputRegister(device_id=device_id, payload=payload))
    client.publish_input_register(HomeAssistantInputRegister(device_id=device_id, payload={**payload, "fw_version_part_3": 15}))

    publishes = _discovery_publishes(client, device_id)
    assert "" not in publishes
    assert json.loads(publishes[-1])["dev"]["sw_version"] == "19.19.15"


def test_other_devices_keep_the_firmware_parts_template():
    client = _client()
    client._publish_device_discovery("QMN000ABC1D2E3FG")
    discovery = json.loads(_discovery_publishes(client, "QMN000ABC1D2E3FG")[-1])
    assert all("fw_version" not in c.get("value_template", "") or "fw_version_part" in c["value_template"]
               for c in discovery["cmps"].values())
