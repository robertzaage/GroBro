import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from grobro.grobro.builder import scramble
from grobro.grobro.client import Client
from grobro.grobro.parser import unscramble
from grobro.ha.client import Client as HaClient
from grobro.model import growatt_registers as regs
from grobro.model.mqtt_config import MQTTConfig

DATA_DIR = Path(__file__).parent / "model" / "data"
SPH_ID = "XYZ0000TEST0001"


@pytest.fixture(autouse=True)
def clear_detected_devices():
    regs.DETECTED_DEVICES.clear()
    yield
    regs.DETECTED_DEVICES.clear()


def _msg(topic, payload=b""):
    msg = MagicMock(spec=MQTTMessage)
    msg.topic = topic
    msg.payload = payload
    msg.qos = 0
    msg.retain = False
    msg.properties.json.return_value = {"UserProperty": []}
    return msg


@pytest.fixture
def sph_payload():
    # Reuse a real input register frame and turn its two blocks into the SPH ranges
    frame = bytearray(unscramble((DATA_DIR / "NeoReadInputRegisters.bin").read_bytes()))
    frame[38:68] = b"YRP0TEST01".ljust(30, b"\x00")
    first, second = 75, 75 + 4 + 125 * 2
    frame[first : first + 4] = struct.pack(">HH", 0, 124)
    frame[second : second + 4] = struct.pack(">HH", 1000, 1124)

    def put(register_no, raw):
        offset = (first + 4 + register_no * 2) if register_no < 1000 else (second + 4 + (register_no - 1000) * 2)
        frame[offset : offset + len(raw)] = raw

    put(1, struct.pack(">I", 50000))  # Ppv 5000.0 W
    put(35, struct.pack(">i", -12000))  # Pac -1200.0 W
    put(118, struct.pack(">H", 1))  # Priority Battery First
    put(1000, struct.pack(">H", 5))  # PV Bat Online
    put(1014, struct.pack(">H", 76))  # SOC 76 %
    put(1029, struct.pack(">i", 3000))  # Grid export 300.0 W
    put(1044, struct.pack(">I", 55))  # Grid import today 5.5 kWh

    with patch("grobro.grobro.client.mqtt.Client"):
        client = Client(MQTTConfig(host="localhost", port=1883), MQTTConfig(host="localhost", port=1883))
    client.on_input_register = MagicMock()
    client._client.on_message(None, None, _msg(f"c/33/{SPH_ID}", scramble(bytes(frame))))
    return client.on_input_register.call_args.args[0].payload


@pytest.mark.parametrize(
    "key, expected",
    [
        ("Ppv", 5000.0),
        ("Pac", -1200.0),
        ("Priority", "Battery First"),
        ("Inverter_Status", "PV Bat Online"),
        ("SOC", 76),
        ("Ptogrid_total", 300.0),
        ("Etouser_today", 5.5),
    ],
)
def test_sph_is_detected_and_decoded(sph_payload, key, expected):
    assert regs.get_device_type_name(SPH_ID) == "SPH"
    assert sph_payload[key] == pytest.approx(expected) if isinstance(expected, float) else sph_payload[key] == expected


@pytest.fixture
def ha_client():
    with patch("grobro.ha.client.mqtt.Client"), patch("grobro.ha.client.os.listdir", return_value=[]):
        client = HaClient(MQTTConfig(host="localhost", port=1883))
    client.on_command = MagicMock()
    return client


@pytest.mark.parametrize(
    "device_id, platform, name, payload, register_no, value",
    [
        ("ZGQ0000TEST0001", "number", "charge_stop_soc", b"90", 3048, 90),
        ("ZGQ0000TEST0001", "number", "discharge_stop_soc", b"20", 3037, 20),
        ("ZGQ0000TEST0001", "switch", "ac_charge", b"ON", 3049, 1),
        ("VWQ0000TEST0001", "number", "charge_rate", b"50", 3047, 50),
        ("VWQ0000TEST0001", "number", "on_grid_discharge_stop_soc", b"15", 3067, 15),
        ("ZGQ0000TEST0001", "switch", "inverter_power", b"OFF", 0, 0),
    ],
)
def test_xh2_and_mod_battery_controls(ha_client, device_id, platform, name, payload, register_no, value):
    ha_client._client.on_message(None, None, _msg(f"homeassistant/{platform}/grobro/{device_id}/{name}/set", payload))
    command = ha_client.on_command.call_args_list[0].args[0]
    assert command.register_no == register_no
    assert command.value == value


@pytest.mark.parametrize(
    "platform, name, payload, register_no, value",
    [
        ("time", "grid_first_1_start_time", b"05:30", 1080, 5 * 256 + 30),
        ("switch", "battery_first_2_enabled", b"ON", 1105, 1),
        ("number", "battery_first_stop_soc", b"95", 1091, 95),
        ("select", "priority", b"Battery First", 1044, 1),
    ],
)
def test_sph_controls(ha_client, platform, name, payload, register_no, value):
    regs.detect_device_by_inverter_serial(SPH_ID, "YRP0TEST01")
    ha_client._client.on_message(None, None, _msg(f"homeassistant/{platform}/grobro/{SPH_ID}/{name}/set", payload))
    command = ha_client.on_command.call_args_list[0].args[0]
    assert command.register_no == register_no
    assert command.value == value
