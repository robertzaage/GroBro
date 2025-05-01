

#!/usr/bin/env python3
# Growatt NOAH/NEO register message decoder

import struct
import argparse
import pathlib
import json
import binascii
import sys
import string
import crc

crc16 = crc.Calculator(crc.Crc16.MODBUS)

def descramble(pkt: bytes) -> bytes:
    MASK = b"Growatt"
    body, crc_stored = pkt[:-2], pkt[-2:]
    if not crc16.verify(pkt[:-2], struct.unpack("!H", pkt[-2:])[0]):
        print("Warning! CRC mismatch – continuing anyway...", file=sys.stderr)
    out = bytearray(pkt[:8])
    out += bytes(b ^ MASK[i % len(MASK)] for i, b in enumerate(pkt[8:-2]))
    return bytes(out)

def hexdump(data: bytes, width: int = 16):
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_bytes = ' '.join(f'{b:02X}' for b in chunk)
        ascii_repr = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
        print(f"{i:08X}  {hex_bytes:<{width*3}}  |{ascii_repr}|")


def noah_decode_charge_limit(body: bytes):
    pos = body.find(b"\x00\xFA\x00\xFB")
    if pos == -1 or pos + 8 > len(body):
        return None
    upper = struct.unpack_from(">H", body, pos + 4)[0]
    lower = struct.unpack_from(">H", body, pos + 6)[0]
    return {"action": "charge_limit", "upper": upper, "lower": lower}

def noah_decode_slot(body: bytes):
    # Long format starts with FE, compact starts with 01
    pos = body.find(b"\xFE")
    compact = False
    if pos == -1:
        pos = body.find(b"\x01")
        compact = True
    if pos == -1 or pos + 13 > len(body):
        return None

    if compact:
        slot = body[pos + 1]
        sh, sm, eh, em = body[pos + 4 : pos + 8]
        power = struct.unpack_from(">H", body, pos + 10)[0]
    else:
        slot = body[pos + 1]
        sh, sm, eh, em = body[pos + 3 : pos + 7]
        power = struct.unpack_from(">H", body, pos + 9)[0]

    # TODO: Find out why slot numbers are off
    if (sh, sm, eh, em, power) == (0, 0, 0, 0, 0):
        return {
            "action": "slot_delete",
            "slot": slot,
        }

    return {
        "action": "slot_create",
        "slot": slot,
        "start": f"{sh:02d}:{sm:02d}",
        "end": f"{eh:02d}:{em:02d}",
        "power": power,
    }

def noah_decode_output_limit(body: bytes):
    pos = body.find(b"\xFC")
    if pos == -1 or pos + 3 > len(body):
        return None
    power = struct.unpack(">H", body[pos + 1 : pos + 3])[0]
    return {"action": "output_limit", "power": power}

def noah_decode_datetime(body: bytes):
    text = body[-19:].decode("ascii", "ignore")
    if text.count("-") == 2 and text.count(":") == 2:
        return {"datetime": text}
    return None

def noah_decode_inverter(body: bytes):
    pos = body.find(b"\x01\x2C")
    if pos == -1 or pos + 2 > len(body):
        return None
    model_id = body[-2:].hex()
    return {"action": "inverter_config", "model_id": model_id}

def noah_decode_smartpowerset(body: bytes):
    pos = body.find(b"\x01\x36\x01\x38")
    if pos == -1 or pos + 10 > len(body):
        return None
    setdown = struct.unpack(">H", body[pos + 4 : pos + 6])[0]
    setup = struct.unpack(">H", body[pos + 6 : pos + 8])[0]
    return {"action": "smart_powerset", "set_power_up": setup, "set_power_down": setdown}

def decode_noah(mtype: int, payload: bytes):
    body = payload
    for fn in (
        noah_decode_charge_limit,
        noah_decode_slot,
        noah_decode_output_limit,
        noah_decode_datetime,
        noah_decode_inverter,
        noah_decode_smartpowerset,
    ):
        res = fn(body)
        if res:
            return res
    return {"raw_hex": body.hex()}

# TODO: Merge tlv_parse and decode_register
def tlv_parse(buf: bytes):
    out, off = [], 0
    while off + 6 <= len(buf):
        reg, idk, ln = struct.unpack_from(">HHH", buf, off)
        off += 6
        if off + ln > len(buf):
            break
        val = buf[off : off + ln]
        off += ln
        try:
            val = val.decode("ascii")
        except UnicodeDecodeError:
            val = val.hex()
        out.append({"register": reg, "value": val})
    return out

def decode_register(payload: bytes) -> dict | None:
    # Handles both read request and answer
    reg = int.from_bytes(payload[0:3], "big")
    val_len = int.from_bytes(payload[4:5], "big")
    if val_len > 0:
        val_raw = payload[val_len + 1:]
        try:
            val = val_raw.decode("ascii")
            if all(ch in "0123456789" for ch in val):
                val = int(val)
        except UnicodeDecodeError:
            val = val_raw.hex()

        return {"register": reg, "value": val, "type": "response"}

    else:
        return {"register": reg, "value": None, "type": "request"}

# TODO: Create register mapping
def decode_payload(device_id: str,mtype: int, payload: bytes):
    # NEO
    #if device_id.startswith("QMN000"):
    if mtype == 0x0118:
        return {"tlvs": tlv_parse(payload[16:])}
    if mtype == 0x0119:
        return decode_register(payload[16:])
    # NOAH
    if device_id.startswith("0PVP"):
       return decode_noah(mtype, payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--hex", action="store_true", help="Output in hex format")
    args = ap.parse_args()

    blobs = []
    blobs += [(p, pathlib.Path(p).read_bytes()) for p in args.files]

    for name, blob in blobs:
        print(f"\n=== {name} ===")
        plain = descramble(blob)
        if args.hex:
            hexdump(plain)
            print()

        msg_len = struct.unpack_from(">H", plain, 4)[0]
        msg_type = struct.unpack_from(">H", plain, 6)[0]
        device_id = plain[8:24].decode("ascii", "ignore").rstrip("\x00")
        payload = plain[24:]

        print(
            json.dumps(
                {
                    "msg_len": msg_len,
                    "msg_type": msg_type,
                    "device_id": device_id,
                    "payload": decode_payload(device_id, msg_type, payload),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
