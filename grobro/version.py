"""The GroBro version, read from the add-on's config.yaml (the image ships it in /app)."""

import re
from pathlib import Path

_CONFIG_YAML = Path(__file__).resolve().parent.parent / "config.yaml"


def read_version(path: Path = _CONFIG_YAML) -> str:
    try:
        match = re.search(r'^version:\s*"?([^"\s]+)"?', path.read_text(), re.MULTILINE)
    except OSError:
        return "unknown"
    return match.group(1) if match else "unknown"


VERSION = read_version()
