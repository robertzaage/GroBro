from unittest.mock import MagicMock, patch

import grobro.ha.client as firmware_client
from grobro.ha.client import (
    Client,
    HA_BASE_TOPIC,
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


def test_discovery_invalid_json_is_forwarded_unchanged():
    client = Client.__new__(Client)
    original_publish = MagicMock()
    client._client = MagicMock()
    client._client.publish = original_publish

    device_id = "0PVP0000TEST0001"
    discovery_topic = f"{HA_BASE_TOPIC}/device/{device_id}/config"

    def fake_discovery(self, _device_id, _effective_max_bat=None):
        self._client.publish(discovery_topic, "{invalid-json", retain=True)

    with patch.object(
        firmware_client,
        "_ORIGINAL_PUBLISH_DEVICE_DISCOVERY",
        fake_discovery,
    ):
        client._Client__publish_device_discovery(device_id)

    original_publish.assert_called_once_with(
        discovery_topic,
        "{invalid-json",
        retain=True,
    )


def test_discovery_non_combined_device_delegates_to_base_client():
    client = Client.__new__(Client)
    client._client = MagicMock()

    with patch.object(
        firmware_client,
        "_ORIGINAL_PUBLISH_DEVICE_DISCOVERY",
        return_value="delegated",
    ) as base_discovery:
        result = client._Client__publish_device_discovery("QMN000ABC1D2E3FG")

    assert result == "delegated"
    base_discovery.assert_called_once_with(
        client,
        "QMN000ABC1D2E3FG",
        None,
    )
