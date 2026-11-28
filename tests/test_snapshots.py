"""Snapshot tests of everything GroBro publishes to Home Assistant and sends to devices.

They pin the exact MQTT output (topics, payloads, retain flags, order) for every device
family, so refactorings can prove they don't change anything for Home Assistant.

Run with GROBRO_UPDATE_SNAPSHOTS=1 to rewrite the snapshots after an intended change.
"""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro import grobro, ha
from grobro.ha.discovery import iter_command_registers
from grobro.model import growatt_registers as regs
from grobro.model.growatt_registers import (
    HomeAssistantHoldingRegisterInput,
    HomeAssistantHoldingRegisterValue,
    HomeAssistantInputRegister,
)
from grobro.model.mqtt_config import MQTTConfig

DATA_DIR = Path(__file__).parent / "model" / "data"
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
UPDATE = os.getenv("GROBRO_UPDATE_SNAPSHOTS") == "1"


@pytest.fixture(autouse=True)
def _clean_state():
    regs.DETECTED_DEVICES.clear()
    regs.INVERTER_SERIALS.clear()
    # The version and the bridge start time would change the snapshots on every release and run
    clock = MagicMock()
    clock.time.return_value = 0
    with patch("grobro.version.VERSION", "0.0.0-test"), patch("grobro.stats.time", clock):
        yield
    regs.DETECTED_DEVICES.clear()
    regs.INVERTER_SERIALS.clear()


def _msg(topic, payload):
    m = MagicMock(spec=MQTTMessage)
    m.topic = topic
    m.payload = payload
    m.qos = 0
    m.retain = False
    m.properties.json.return_value = {"UserProperty": []}
    return m


def _setup():
    with patch("paho.mqtt.client.Client") as mqtt_cls:
        ha_mqtt, grobro_mqtt = MagicMock(), MagicMock()
        ha_mqtt.publish.return_value = (0, MagicMock())
        grobro_mqtt.publish.return_value = (0, MagicMock())
        mqtt_cls.side_effect = [ha_mqtt, grobro_mqtt]
        hc = ha.Client(MQTTConfig(host="localhost", port=1883))
        gc = grobro.Client(MQTTConfig(host="localhost", port=1883), MQTTConfig(host="forward", port=7006))
    gc.on_config = hc.set_config
    gc.on_input_register = hc.publish_input_register
    gc.on_holding_register_input = hc.publish_holding_register_input
    gc.on_config_read_response = hc.handle_config_read_response
    hc.on_command = MagicMock()
    hc.on_config_command = MagicMock()
    hc.on_config_read = MagicMock()
    return hc, gc, ha_mqtt, grobro_mqtt


def _publishes(mqtt_mock):
    out = []
    for c in mqtt_mock.publish.call_args_list:
        topic = c.args[0]
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("payload")
        if isinstance(payload, bytes):
            payload = payload.hex()
        retain = c.kwargs.get("retain", c.args[3] if len(c.args) > 3 else False)
        out.append([topic, payload, bool(retain)])
    return out


def _calls(mock):
    out = []
    for c in mock.call_args_list:
        out.append([a.model_dump(mode="json") if hasattr(a, "model_dump") else a for a in c.args])
    return out


def _dedupe(data):
    """Store long payloads once, publishes reference them by hash (keeps the snapshots small)."""
    payloads = {}
    for key in ("ha_publishes", "device_publishes"):
        for entry in data.get(key, []):
            payload = entry[1]
            if isinstance(payload, str) and len(payload) > 120:
                ref = "@" + hashlib.sha1(payload.encode()).hexdigest()[:12]
                payloads[ref] = json.loads(payload) if payload.startswith("{") else payload
                entry[1] = ref
    data["payloads"] = payloads
    return data


def _check(name, data):
    data = _dedupe(data)
    path = SNAPSHOT_DIR / f"{name}.json"
    text = json.dumps(data, indent=0, sort_keys=True, ensure_ascii=False) + "\n"
    if UPDATE or not path.exists():
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        path.write_text(text)
        if not UPDATE:
            pytest.fail(f"Snapshot {path.name} was missing and has been written, run the tests again")
    assert json.loads(text) == json.loads(path.read_text()), f"Snapshot {path.name} differs"


def _sample_value(name, reg, i, label=False):
    data = reg.growatt.data
    if data.data_type == "ENUM" and data.enum_options and isinstance(data.enum_options.values, dict):
        key = next(iter(data.enum_options.values))
        return data.enum_options.values[key] if label else int(key)
    if data.data_type == "STRING":
        return f"S{i:03d}"
    if data.data_type == "TIME_HHMM":
        return "12:30"
    return (i * 7) % 113 + (0.5 if "FLOAT" in str(data.data_type) else 0)


def _sample_payload(known_registers):
    return {name: _sample_value(name, reg, i) for i, (name, reg) in enumerate(known_registers.input_registers.items())}


def _command_value(cmd):
    ha_reg = cmd["ha"]
    if cmd["topic_root"] == "switch":
        return "ON"
    if cmd["topic_root"] == "time":
        return "12:30"
    if cmd["topic_root"] == "select":
        options = getattr(ha_reg, "options", None) or {}
        labels = list(options.values()) if isinstance(options, dict) else list(options)
        return str(labels[-1]) if labels else "0"
    if cmd["topic_root"] == "number":
        lo = getattr(ha_reg, "min", None)
        return str(lo if lo is not None else 1)
    return "1"


FAMILIES = [
    ("neo", "QMN000SNAP00001", None),
    ("noah", "0PVP0000SNAP001", None),
    ("nexa", "0HVR000SNAP0001", None),
    ("spf", "HAQ000SNAP00001", None),
    ("xh2", "ZGQ000SNAP00001", None),
    ("mod", "VWQ000SNAP00001", None),
    ("mod_detected_tlxh", "VWQ000SNAP00002", "HJU0SNAP01"),
    ("sph_detected", "XYZ000SNAP00001", "YRP0SNAP01"),
]


@pytest.mark.parametrize("name, device_id, inverter_serial", FAMILIES, ids=[f[0] for f in FAMILIES])
def test_family_snapshot(name, device_id, inverter_serial):
    with patch("grobro.settings.MAX_SLOTS", 9):
        hc, gc, ha_mqtt, _ = _setup()
        if inverter_serial:
            regs.detect_device_by_inverter_serial(device_id, inverter_serial)
            regs.remember_inverter_serial(device_id, inverter_serial)
        known = regs.get_known_registers(device_id)

        # Input registers: discovery and state
        hc.publish_input_register(HomeAssistantInputRegister(device_id=device_id, payload=_sample_payload(known)))
        # A second, partial payload exercises the change detection
        first = next(iter(known.input_registers))
        hc.publish_input_register(HomeAssistantInputRegister(device_id=device_id, payload={first: 1}))

        # Holding registers
        holding = [
            HomeAssistantHoldingRegisterValue(name=n, value=_sample_value(n, r, i, label=True), register=r.homeassistant)
            for i, (n, r) in enumerate(known.holding_registers.items())
        ]
        hc.publish_holding_register_input(HomeAssistantHoldingRegisterInput(device_id=device_id, payload=holding))

        # Commands from Home Assistant
        for cmd in iter_command_registers(known):
            topic = f"homeassistant/{cmd['topic_root']}/grobro/{device_id}/{cmd['cmd_id']}/set"
            if cmd["is_config"] and cmd["cmd_id"] == "31":
                continue  # time sync uses the current time
            hc._client.on_message(None, None, _msg(topic, _command_value(cmd).encode()))
        for reg_name in list(known.holding_registers)[:3]:
            hc._client.on_message(None, None, _msg(f"homeassistant/button/grobro/{device_id}/{reg_name}/read", b""))

        # Home Assistant restart
        hc._client.on_message(None, None, _msg("homeassistant/status", b"online"))

    _check(f"family_{name}", {
        "ha_publishes": _publishes(ha_mqtt),
        "commands": _calls(hc.on_command),
        "config_commands": _calls(hc.on_config_command),
    })


FIXTURE_RUNS = [
    ("neo", [
        ("c/33/QMN000ABC1D2E3FG", "NeoConfigTLV_340.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoConfigTLV_341.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoReadInputRegisters.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoInputRegister.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoInputRegister_NEO2000.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoReadOutputPowerLimit.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoReadSingleRegister_3.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoConfigReadResponse_314.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoConfigReadResponse_337.bin"),
        ("c/33/QMN000ABC1D2E3FG", "NeoConfigWriteAck_DataInterval.bin"),
    ]),
    ("noah", [
        ("c/33/0PVP0000TEST0001", "NoahTypeFE19_Config.bin"),
        ("c/33/0PVP0000TEST0001", "NoahTypeFE19_DevStatus.bin"),
        ("c/33/0PVP0000TEST0001", "NoahReadInputRegisters_0-124.bin"),
        ("c/33/0PVP0000TEST0001", "NoahType0103_HoldingRegs.bin"),
        ("c/33/0PVP0000TEST0001", "NoahType0110_PresetMResp.bin"),
        ("c/33/0PVP0000TEST0001", "NoahType0125_SerialResp.bin"),
        ("c/33/0PVP0000TEST0001", "NoahTypeFE18_DateTime.bin"),
        ("c/33/0PVP0000TEST0001", "NoahConfigWriteAck_Firmware1.bin"),
        ("c/33/0PVP0000TEST0001", "NoahTypeFE25_Empty.bin"),
    ]),
    ("shinewelink", [
        ("c/33/RAQ0E8H042", "ShineWeLinkConfigDump.bin"),
        ("c/33/RAQ0E8H042", "ShineWeLinkFE19_DevStatus1.bin"),
        ("c/33/RAQ0E8H042", "ShineWeLinkFE19_FullConfig.bin"),
        ("c/33/RAQ0E8H042", "ShineWeLinkFE25_Keepalive.bin"),
        ("c/33/RAQ0E8H042", "ShineWeLinkReadInputRegisters.bin"),
        ("c/33/RAQ0E8H042", "ShineWeLinkReadHoldingRegisters.bin"),
    ]),
    ("unknown_datalogger", [
        ("c/33/XYZ0000SNAP0001", "NeoReadInputRegisters.bin"),
    ]),
]


@pytest.mark.parametrize("name, messages", FIXTURE_RUNS, ids=[r[0] for r in FIXTURE_RUNS])
def test_fixture_snapshot(name, messages):
    hc, gc, ha_mqtt, grobro_mqtt = _setup()
    for topic, fixture in messages:
        gc._client.on_message(None, None, _msg(topic, (DATA_DIR / fixture).read_bytes()))
    _check(f"fixtures_{name}", {
        "ha_publishes": _publishes(ha_mqtt),
        "device_publishes": _publishes(grobro_mqtt),
    })
