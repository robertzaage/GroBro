# Parser and unscrambler for Growatt MQTT data packages.
# Automatically descrambles the binary data and decodes it into a structured format.

import struct
import json
import sys
import os
import logging
import grobro.model as model
from itertools import cycle
import importlib.resources as resources

LOG = logging.getLogger(__name__)


def unscramble(decdata: bytes):
    """
    Unscrambling algorithm based on XOR with "Growatt" mask
    """
    ndecdata = len(decdata)
    mask = "Growatt"
    hex_mask = ["{:02x}".format(ord(x)) for x in mask]
    nmask = len(hex_mask)

    unscrambled = bytes(decdata[0:8])  # Preserve the 8-byte header
    for i, j in zip(range(0, ndecdata - 8), cycle(range(0, nmask))):
        unscrambled += bytes([decdata[i + 8] ^ int(hex_mask[j], 16)])

    # hexdump(unscrambled)
    return unscrambled


def parse_config_type(data, offset) -> model.DeviceConfig:
    """
    Parse a configuration message starting at offset as a TLV block
    Each parameter is stored as:
      - 2 bytes: key_id (big-endian)
      - 2 bytes: key_len
      - key_len bytes: value (ASCII if possible, else hex)
    """
    config = {}
    end = len(data)
    raw_hex = data[offset:].hex()
    any_params = False

    param_map = {
        4: "data_interval",
        5: "unknown_5",
        6: "unknown_6",
        7: "password",
        8: "serial_number",
        9: "protocol_version",
        10: "unknown_10",
        11: "unknown_11",
        12: "dns_address",
        13: "device_type",
        14: "local_ip",
        15: "unknown_port",
        16: "mac_address",
        17: "remote_ip",
        18: "remote_port",
        19: "remote_url",
        20: "model_id",
        21: "sw_version",
        22: "hw_version",
        23: "unknown_23",
        24: "unknown_24",
        25: "subnet_mask",
        26: "default_gateway",
        27: "unknown_27",
        28: "unknown_28",
        29: "unknown_29",
        30: "timezone",
        31: "datetime",
        76: "wifi_signal",
    }

    max_len = 512

    while offset + 4 <= end:
        key_id = int.from_bytes(data[offset : offset + 2], "big")
        key_len = int.from_bytes(data[offset + 2 : offset + 4], "big")
        offset += 4

        if key_len == 0 or key_len > max_len or offset + key_len > end:
            break

        raw_val = data[offset : offset + key_len]
        offset += key_len

        try:
            val = raw_val.decode("ascii").strip("\x00")
            if any(ord(c) < 32 or ord(c) > 126 for c in val):
                raise ValueError()
        except Exception:
            val = raw_val.hex()

        label = param_map.get(key_id, f"param_{key_id}")
        config[label] = val
        any_params = True

    if not any_params:
        config["raw"] = raw_hex

    return model.DeviceConfig(**config)


def find_config_offset(data):
    """
    Heuristically search for the start of the TLV configuration block by
    looking for a repeating pattern of a 2-byte key followed by a 2-byte length.
    """
    for i in range(0x1C, len(data) - 4):
        key = int.from_bytes(data[i : i + 2], "big")
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if 0 < key < 1000 and 0 < length < 256:
            return i
    return 0x1C


def parse_config_message(data: bytes):
    config_read_struct = struct.Struct(">4sHH16s14sH1xH2x")

    (
        header,
        msg_len,
        msg_type,
        device_id,
        _padding,
        config_type,
        register_no,
    ) = config_read_struct.unpack_from(data)

    # remove trailing checksum
    value = data[config_read_struct.size:-2].decode("ascii")

    return {
        "header": header,
        "message_length": msg_len,
        "message_type": msg_type,
        "device_id": device_id.rstrip(b"\x00").decode("ascii"),
        "config_type": config_type,
        "register_no": register_no,
        "value": value,
    }
