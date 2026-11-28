import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
KEY = re.compile(r"^  ([A-Z_]+):", re.MULTILINE)


def _addon_options():
    # The schema lists every option, including optional ones without a default
    schema = (ROOT / "config.yaml").read_text().split("\nschema:\n", 1)[1]
    return set(KEY.findall(schema))


@pytest.mark.parametrize("language", ["en", "de", "es", "fr", "nl"])
def test_translation_covers_all_addon_options(language):
    translation = (ROOT / "translations" / f"{language}.yaml").read_text()
    keys = KEY.findall(translation)
    assert set(keys) == _addon_options()
    assert translation.count("    name: ") == len(keys)
    assert translation.count("    description: ") == len(keys)
