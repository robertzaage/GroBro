import struct
from typing import Union

def _to_ascii(value: Union[int, str, bytes]) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, int):
        return str(value).encode("ascii")
    return value.encode("ascii")

def encode(param: int, value: Union[int, str, bytes]) -> bytes:
    val = _to_ascii(value)
    tlv_len = 4 + len(val)
    return struct.pack(">HHH", tlv_len, param, len(val)) + val

def parse(buf: bytes) -> list[dict[str, str]]:
    """Returns `[{'param': <int>, 'value': <str|hex-str>}, …]`"""
    out, off = [], 0
    while off + 6 <= len(buf):
        tlv_len, param, vlen = struct.unpack_from(">HHH", buf, off)
        off += 6
        if off + vlen > len(buf):
            break
        raw = buf[off : off + vlen]
        off += vlen
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError:
            raw = raw.hex()
        out.append({"param": param, "value": raw})
    return out
