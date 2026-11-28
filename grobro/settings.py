"""All GroBro options, read once from the environment.

The Home Assistant add-on exports its options as environment variables (see run.sh), the
container image takes them directly. Modules read them as ``settings.<NAME>`` at runtime, so
tests can patch a single place.
"""

import os


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean option, accepting true/false in any case."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() == "true"


def env_int(name: str, default: int) -> int:
    """Read an integer option, falling back to the default if it is missing or invalid."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def parse_growatt_cloud(raw: str) -> tuple[str, bool, set[str]]:
    """Parse GROWATT_CLOUD: "true" forwards all devices, a comma-separated list only these serials."""
    value = (raw or "").strip()
    if value.lower() == "true":
        return "true", True, set()
    if value.lower() in ("", "false"):
        return "false", False, set()
    serials = {serial.strip() for serial in value.split(",") if serial.strip()}
    return value, bool(serials), serials


def parse_max_bat(raw: str) -> int | str:
    """Parse MAX_BAT: a fixed number of battery packs or "auto"."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


# General
LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR").upper()
MQTT_CLIENT_SUFFIX = os.getenv("MQTT_CLIENT_SUFFIX", "")
# Persistent storage for device configs, the value cache and message dumps.
# The HA add-on maps this via addon_config, plain Docker needs a volume.
CONFIG_DIR = os.getenv("CONFIG_DIR", "/config")
DUMP_MESSAGES = env_bool("DUMP_MESSAGES")
DUMP_DIR = os.path.join(CONFIG_DIR, "dump")

# Growatt Cloud forwarding
GROWATT_CLOUD, GROWATT_CLOUD_ENABLED, GROWATT_CLOUD_FILTER = parse_growatt_cloud(os.getenv("GROWATT_CLOUD", "false"))
GROWATT_CLOUD_CONFIG_FILTER = env_bool("GROWATT_CLOUD_CONFIG_FILTER")

# Home Assistant
HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")
# Availability of GroBro itself, set to offline by the MQTT last will
BRIDGE_AVAILABILITY_TOPIC = f"{HA_BASE_TOPIC}/grobro/bridge/availability"
# Home Assistant announces its (re)start on this topic
HA_STATUS_TOPIC = f"{HA_BASE_TOPIC}/status"
PUBLISH_SENSORS_RETAINED = env_bool("PUBLISH_SENSORS_RETAINED")
DEVICE_TIMEOUT = env_int("DEVICE_TIMEOUT", 0)
AVAILABILITY_SENSOR = env_bool("AVAILABILITY_SENSOR")
MAX_SLOTS = env_int("MAX_SLOTS", 1)
MAX_BAT = parse_max_bat(os.getenv("MAX_BAT", "auto"))
FILTER_DATA_GLITCHES = env_bool("FILTER_DATA_GLITCHES")
KEEP_BATTERY_POSITION = env_bool("KEEP_BATTERY_POSITION")
# Opt-in: fill in values missing from a payload from the last known ones and
# restore them after a restart
KEEP_LAST_VALUES = env_bool("KEEP_LAST_VALUES")
AUTO_TIME_SYNC = env_bool("AUTO_TIME_SYNC")
TIME_SYNC_HOURS = (0, 12)
