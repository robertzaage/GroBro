import struct
import crc
from grobro.grobro.builder import scramble, append_crc, hexdump

crc16 = crc.Calculator(crc.Crc16.MODBUS)


def test_scramble_roundtrip():
    original = b"\x00\x01\x00\x07\x00\x10\x01\x18" + b"ABCDEFGHIJ" + b"\x00" * 10
    scrambled = scramble(original)
    assert scrambled != original
    assert len(scrambled) == len(original)
    assert scrambled[:8] == original[:8]
    unscrambled = _unscramble(scrambled)
    assert unscrambled == original


def test_append_crc():
    pkt = b"test data"
    result = append_crc(pkt)
    assert len(result) == len(pkt) + 2
    stored_crc = struct.unpack("!H", result[-2:])[0]
    assert stored_crc == crc16.checksum(pkt)


def test_hexdump(capsys):
    data = b"Hello\x00World\xff"
    hexdump(data)
    captured = capsys.readouterr()
    assert "48 65 6C 6C 6F" in captured.out
    assert "Hello.World" in captured.out or "Hello.World." in captured.out


def _unscramble(pkt):
    mask = b"Growatt"
    out = bytearray(pkt[:8])
    out += bytes(b ^ mask[i % len(mask)] for i, b in enumerate(pkt[8:]))
    return bytes(out)
