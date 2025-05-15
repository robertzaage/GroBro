from pathlib import Path
import pytest

from grobro.grobro import parser
from grobro.model.neo_command import NeoReadOutputPowerLimit, NeoSetOutputPowerLimit
from grobro.model.neo_messages import NeoOutputPowerLimit
from grobro.grobro.builder import append_crc
from grobro.grobro.builder import scramble

TEST_DEVICE_ID = "QMN000ABC1D2E3FG"


@pytest.mark.parametrize(
    "want_msg",
    [
        NeoOutputPowerLimit(device_id=TEST_DEVICE_ID, value=42),
        NeoSetOutputPowerLimit(device_id=TEST_DEVICE_ID, value=42),
        NeoReadOutputPowerLimit(device_id=TEST_DEVICE_ID),
    ],
)
def test_double(want_msg):
    fixture_path = Path(__file__).parent / "data" / f"{type(want_msg).__name__}.bin"
    # test parsing
    with open(fixture_path, "rb") as f:
        want_raw = parser.unscramble(f.read())
        got_msg = type(want_msg).parse_grobro(want_raw)
        assert got_msg == want_msg
    # test building
    got_raw = got_msg.build_grobro()
    assert got_raw == want_raw[0:-2]


if __name__ == "__main__":
    """
    util to generate syntetic test data.
    should only be used once after verifiying
    that parse + build of new type works with real data
    """
    msgs = [
        NeoReadOutputPowerLimit(device_id=TEST_DEVICE_ID),
        NeoSetOutputPowerLimit(device_id=TEST_DEVICE_ID, value=42),
        NeoOutputPowerLimit(device_id=TEST_DEVICE_ID, value=42),
    ]
    for msg in msgs:
        with open(f"tests/model/data/{type(msg).__name__}.bin", "wb") as f:
            msg_raw = msg.build_grobro()
            f.write(append_crc(scramble(msg_raw)))
