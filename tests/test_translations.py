import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
KEY = re.compile(r"^  ([A-Z_]+):", re.MULTILINE)


def _addon_options():
    config = (ROOT / "config.yaml").read_text()
    options = config.split("\noptions:\n", 1)[1].split("\nschema:\n", 1)[0]
    return set(KEY.findall(options))


@pytest.mark.parametrize("language", ["en", "de", "es", "fr", "nl"])
def test_translation_covers_all_addon_options(language):
    translation = (ROOT / "translations" / f"{language}.yaml").read_text()
    keys = KEY.findall(translation)
    assert set(keys) == _addon_options()
    assert translation.count("    name: ") == len(keys)
    assert translation.count("    description: ") == len(keys)
