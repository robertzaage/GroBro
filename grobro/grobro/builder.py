import crc
import struct

crc16 = crc.Calculator(crc.Crc16.MODBUS)


def scramble(pkt: bytes) -> bytes:
    mask = b"Growatt"
    out = bytearray(pkt[:8])
    out += bytes(b ^ mask[i % len(mask)] for i, b in enumerate(pkt[8:]))
    return bytes(out)


def append_crc(pkt: bytes) -> bytes:
    csum = crc16.checksum(pkt)
    return pkt + struct.pack("!H", csum)


def hexdump(data: bytes, width: int = 16) -> None:
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        print(f"{i:08X}  {hex_part:<{width * 3}} |{asc_part}|")
