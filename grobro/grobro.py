# Parser and unscrambler for Growatt MQTT data packages.
# Automatically descrambles the binary data and decodes it into a structured format.

import struct
import json
import sys
import os
from itertools import cycle

def unscramble(decdata: bytes):
    """
    Unscrambling algorithm based on XOR with "Growatt" mask
    """
    ndecdata = len(decdata)
    mask = "Growatt"
    hex_mask = ['{:02x}'.format(ord(x)) for x in mask]
    nmask = len(hex_mask)

    unscrambled = bytes(decdata[0:8]) # Preserve the 8-byte header
    for i, j in zip(range(0, ndecdata - 8), cycle(range(0, nmask))):
        unscrambled += bytes([decdata[i + 8] ^ int(hex_mask[j], 16)])

    return unscrambled

def parse_timestamp(data, offset):
    try:
        year, month, day, hour, minute, second = struct.unpack_from('>6B', data, offset)
        if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60):
            return None
        return f"20{year:02d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
    except Exception:
        return None

def parse_modbus_block(data, offset, modbus_input_register_descriptions: list):
    """
    Parse one ModbusRegisterRead block
    The block is assumed to contain two 16-bit values:
      - start register (u16)
      - end register (u16)
    Followed by qty registers (each 2 bytes, big-endian)
    """
    start = struct.unpack_from('>H', data, offset)[0]
    offset += 2
    end_val = struct.unpack_from('>H', data, offset)[0]
    offset += 2
    qty = end_val - start + 1
    if qty < 1 or qty > 512:
        raise ValueError(f"Wrong register count: start={start}, end={end_val}, qty={qty}")
    
    unpack_typemap = {
        1: "!B",
        2: "!H",
        4: "!I"
    }

    input_registers = []
    for reg_desc in modbus_input_register_descriptions:
        register_no = reg_desc["register_no"]
        if register_no < start or register_no > end_val:
            continue

        reg_offset = offset + (register_no - start) * 2 + reg_desc.get('offset', 0)
        bytesize = reg_desc['size']
        value, = struct.unpack_from(unpack_typemap[bytesize], data, reg_offset)
        value *= reg_desc.get("multiplier", 1)
        
        if "value_options" in reg_desc:
            # Replace numeric value options with their actual meaning where possible
            options = reg_desc["value_options"]
            value = options.get(str(value), value)
        
        input_registers.append({
            'name': reg_desc['variable_name'],
            'unit': reg_desc['unit'],
            'value': value
        })
    offset += qty * 2
    
    return {'start': start, 'end': end_val, 'qty': qty, 'registers': input_registers}, offset

def parse_modbus_type(data, modbus_input_register_descriptions: list):
    """
    Parse a modbus-type message
    The structure is:
      - 2 bytes: sequence
      - 2 bytes: dunno (?)
      - 2 bytes: whoknows (?)
      - 2 bytes: maybetype (?)
      - 16 bytes: deviceId (ASCII)
      - 51 bytes: metaInfo
      - First ModbusRegisterRead block (2 bytes start, 2 bytes end, then registers)
      - Second ModbusRegisterRead block (same format)
    """
    result = {}
    offset = 0

    result['sequence'] = struct.unpack_from('>H', data, offset)[0]
    offset += 2

    result['dunno'] = struct.unpack_from('>H', data, offset)[0]
    offset += 2

    result['whoknows'] = struct.unpack_from('>H', data, offset)[0]
    offset += 2

    result['maybetype'] = struct.unpack_from('>H', data, offset)[0]
    offset += 2

    device_id_raw = data[offset:offset+16]
    result['device_id'] = device_id_raw.decode('ascii', errors='ignore').strip('\x00')
    offset += 16

    reserved_zero_1 = data[offset:offset+14]
    offset += 14

    device_sn = data[offset:offset+10].decode('ascii', errors='ignore').strip('\x00')
    offset += 10

    reserved_zero_2 = data[offset:offset+20]
    offset += 20

    ts_raw = data[offset:offset+7]
    timestamp_str = parse_timestamp(ts_raw, 0)
    offset += 7

    result['meta_info'] = {
        "device_sn": device_sn,
        "timestamp": timestamp_str
    }

    try:
        modbus1, offset = parse_modbus_block(data, offset, modbus_input_register_descriptions)
        result['modbus1'] = modbus1
    except Exception as e:
        result['modbus1_error'] = str(e)

    try:
        modbus2, offset = parse_modbus_block(data, offset, modbus_input_register_descriptions)
        result['modbus2'] = modbus2
    except Exception as e:
        result['modbus2_error'] = str(e)

    return result

def parse_config_type(data, offset):
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
        4: "unknown_4", 5: "unknown_5", 6: "unknown_6", 7: "password", 8: "serial_number",
        9: "protocol_version", 10: "unknown_10", 11: "unknown_11", 12: "dns_address",
        13: "device_type", 14: "unknown_ip", 15: "unknown_port", 16: "mac_address",
        17: "mqtt_host", 18: "mqtt_broker_port", 19: "mqtt_host_2", 20: "model_id",
        21: "sw_version", 22: "hw_version", 23: "unknown_23", 24: "unknown_24",
        25: "netmask", 26: "unknown_ip_2", 27: "unknown_27", 28: "unknown_28",
        29: "unknown_29", 30: "timezone", 31: "datetime", 76: "unknown_76"
    }

    max_len = 512

    while offset + 4 <= end:
        key_id = int.from_bytes(data[offset:offset+2], 'big')
        key_len = int.from_bytes(data[offset+2:offset+4], 'big')
        offset += 4

        if key_len == 0 or key_len > max_len or offset + key_len > end:
            break

        raw_val = data[offset:offset + key_len]
        offset += key_len

        try:
            val = raw_val.decode('ascii').strip('\x00')
            if any(ord(c) < 32 or ord(c) > 126 for c in val):
                raise ValueError()
        except Exception:
            val = raw_val.hex()

        label = param_map.get(key_id, f"param_{key_id}")
        config[label] = val
        any_params = True

    if not any_params:
        config["raw"] = raw_hex

    return config

def find_config_offset(data):
    """
    Heuristically search for the start of the TLV configuration block by
    looking for a repeating pattern of a 2-byte key followed by a 2-byte length.
    """
    for i in range(0x1C, len(data) - 4):
        key = int.from_bytes(data[i:i+2], 'big')
        length = int.from_bytes(data[i+2:i+4], 'big')
        if 0 < key < 1000 and 0 < length < 256:
            return i
    return 0x1C

def parse_growatt_file(filepath, modbus_input_register_descriptions: list):
    with open(filepath, 'rb') as f:
        raw = f.read()

    data = unscramble(raw)

    result = {}
    result['file'] = os.path.basename(filepath)
    result['msg_counter'] = struct.unpack_from('>H', data, 0)[0]
    result['device_id'] = data[8:24].decode('ascii', errors='ignore').strip('\x00')

    if result['msg_counter'] == 1:
        config_offset = find_config_offset(data)
        result['config'] = parse_config_type(data, config_offset)
    else:
        result.update(parse_modbus_type(data, modbus_input_register_descriptions))

    return result

def load_modbus_input_register_file(filepath):
    with open(filepath, "rb") as f:
        data = json.load(f)

    return data["input_registers"]

 
if __name__ == "__main__":
    modbus_input_register_descriptions = load_modbus_input_register_file("growatt_input_registers.json")
    input_file = sys.argv[1]
    try:
        parsed = parse_growatt_file(input_file, modbus_input_register_descriptions)
    except Exception as e:
        print(f"Error parsing file {input_file}: {e}")
        sys.exit(1)

    print(json.dumps(parsed, indent=2))
