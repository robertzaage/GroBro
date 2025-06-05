#!/usr/bin/env python3

import argparse
import struct
import sys
import random
import time
import paho.mqtt.client as mqtt
import crc
import ssl

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
        chunk = data[i: i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        print(f"{i:08X}  {hex_part:<{width * 3}} |{asc_part}|")

# --- Message Builders ---

def build_charge_limit(device_id: str, upper: int, lower: int) -> bytes:
    header = struct.pack(">HHH", 1, 7, 40)
    mtype = 0x0110
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")
    payload = dev_bytes + (b"\x00" * 15) + b"\xFA\x00\xFB" + struct.pack(">HH", upper, lower)
    return header + struct.pack(">H", mtype) + payload

def build_output_limit(device_id: str, power: int) -> bytes:
    header = struct.pack(">HHH", 1, 7, 36)
    mtype = 0x0106
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")
    payload = dev_bytes + (b"\x00" * 15) + b"\xFC" + struct.pack(">H", power)
    return header + struct.pack(">H", mtype) + payload

def build_inverter_config(device_id: str, model_hex: str) -> bytes:
    # Hoymiles HMS-1600-4T = 0204
    # APsystems EZ1-M = 0401
    header = struct.pack(">HHH", 1, 7, 36)
    mtype = 0x0106
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")
    payload = dev_bytes + (b"\x00" * 14) + b"\x01\x2C" + bytes.fromhex(model_hex)
    return header + struct.pack(">H", mtype) + payload

def build_slot(device_id: str, action: str, slot: int, start: str = None, end: str = None, power: int = 0) -> bytes:
    import struct

    header = struct.pack(">HHH", 1, 7, 46)  # Fixed header fields
    mtype = 0x0110  # Message type
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")

    payload = dev_bytes
    payload += b"\x00" * 14  # Reserved padding

    """
    depending on the slot, we set the start and end register
    """
    control_bytes = {
            1: 0x00FE0102, # 254 - 258
            2: 0x01030107, # 259 - 263
            3: 0x0108010C, # 264 - 268
            4: 0x010D0111, # 269 - 273
            5: 0x01120116, # 274 - 278
    }.get(slot, 0x01010100)
    payload += control_bytes.to_bytes(4, byteorder='big')

    if action == "slot_create":
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        payload += struct.pack(">BBBB", sh, sm, eh, em)

        payload += b"\x00\x00"  # Reserved
        payload += struct.pack(">H", power)
        payload += b"\x00\x01"  # Fixed ending

    elif action == "slot_delete":
        payload += b"\x00" * 4  # Clear times
        payload += b"\x00\x00"  # Reserved
        payload += b"\x00" * 4  # Clear power/flag

    else:
        raise ValueError(f"Unknown slot action {action}")

    return header + struct.pack(">H", mtype) + payload

def build_smart_powerset(device_id: str, action: str, powerdiff: int) -> bytes:
    header = struct.pack(">HHH", 1, 7, 42)
    mtype = 0x0110
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")
    
    setup = 0
    setdown = 0
    if action == "power_set_up":
        setup = powerdiff
    elif action == "power_set_down":
        setdown = powerdiff
    else:
        raise ValueError(f"Unknown smart powerset action {action}")

    payload = dev_bytes + (b"\x00" * 14) + b"\x01\x36\x01\x38" + struct.pack(">HHH", setdown, setup, 1)

    return header + struct.pack(">H", mtype) + payload

# --- MQTT ---

def on_connect(client, userdata, flags, rc):
    if rc != 0:
        print(f"Failed to connect, return code {rc}")

def publish_message(broker, port, username, password, tls, device_id, payload):
    topic = f"s/33/{device_id}"
    client_id = f"grobro-{random.randint(0,9999)}"

    client = mqtt.Client(client_id=client_id)
    if username:
        client.username_pw_set(username, password)
    if tls:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)

    client.on_connect = on_connect
    client.connect(broker, port)
    client.loop_start()

    time.sleep(1)  # wait for connection
    result = client.publish(topic, payload)
    status = result[0]
    if status == 0:
        print(f"Sent message to topic {topic}")
    else:
        print(f"Failed to send message")
    time.sleep(1)
    client.loop_stop()

# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Growatt MQTT CLI Tool")
    parser.add_argument("--action", required=True, choices=[
        "charge_limit", "output_limit", "inverter_config",
        "slot_create", "slot_delete", "power_set_up", "power_set_down",
    ], help="Action to perform")

    parser.add_argument("--device-id", required=True, help="Growatt device ID / Serial Number")
    parser.add_argument("--mqtt-broker", required=True, help="MQTT broker address")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--mqtt-username", help="MQTT username (optional)")
    parser.add_argument("--mqtt-password", help="MQTT password (optional)")
    parser.add_argument("--mqtt-tls", action="store_true", help="Enable TLS (optional)")

    parser.add_argument("--hexdump", action="store_true", help="Hexdump plain and scrambled message before publishing")

    parser.add_argument("--upper", type=int, help="Upper limit (for charge_limit)")
    parser.add_argument("--lower", type=int, help="Lower limit (for charge_limit)")
    parser.add_argument("--power", type=int, help="Power setting (for output_limit, slot_create or power_set_up/down)")
    parser.add_argument("--model-id", help="Model ID in hex (for inverter_config)")
    parser.add_argument("--slot", type=int, help="Slot number (for slot_create/slot_delete)")
    parser.add_argument("--start", help="Start time HH:MM (for slot_create)")
    parser.add_argument("--end", help="End time HH:MM (for slot_create)")

    args = parser.parse_args()

    if args.action == "charge_limit":
        if args.upper is None or args.lower is None:
            print("Error: --upper and --lower are required for charge_limit")
            sys.exit(1)
        pkt = build_charge_limit(args.device_id, args.upper, args.lower)

    elif args.action == "output_limit":
        if args.power is None:
            print("Error: --power is required for output_limit")
            sys.exit(1)
        pkt = build_output_limit(args.device_id, args.power)

    elif args.action == "inverter_config":
        if not args.model_id:
            print("Error: --model-id is required for inverter_config")
            sys.exit(1)
        pkt = build_inverter_config(args.device_id, args.model_id)

    elif args.action in ("slot_create", "slot_delete"):
        if args.slot is None:
            print("Error: --slot is required for slot_create/slot_delete")
            sys.exit(1)
        if args.action == "slot_create":
            if not args.start or not args.end or args.power is None:
                print("Error: --start, --end and --power are required for slot_create")
                sys.exit(1)
        pkt = build_slot(args.device_id, args.action, args.slot, args.start, args.end, args.power)

    elif args.action in ("power_set_up", "power_set_down"):
        if args.power is None:
            print("Error: --power is required for power_set_up/power_set_down")
            sys.exit(1)

        pkt = build_smart_powerset(args.device_id, args.action, args.power) 

    else:
        print("Error: Unknown action")
        sys.exit(1)

    scrambled = scramble(pkt)
    final_payload = append_crc(scrambled)

    if args.hexdump:
        print("\n--- Message ---")
        hexdump(pkt)
        print("\n--- Final Message ---")
        hexdump(final_payload)

    publish_message(
        broker=args.mqtt_broker,
        port=args.mqtt_port,
        username=args.mqtt_username,
        password=args.mqtt_password,
        tls=args.mqtt_tls,
        device_id=args.device_id,
        payload=final_payload
    )

if __name__ == "__main__":
    main()
