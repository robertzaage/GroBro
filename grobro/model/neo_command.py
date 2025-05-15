import struct
from pydantic import BaseModel
from enum import Enum


class NeoReadOutputPowerLimit(BaseModel):
    """
    Represents a message that can be sent to the inverter
    to request a NeoOutputPowerLimit message.
    """

    device_id: str

    @staticmethod
    def parse_ha(device_id, payload) -> "NeoReadOutputPowerLimit":
        return NeoReadOutputPowerLimit(
            device_id=device_id,
        )

    def build_grobro(self) -> bytes:
        return struct.pack(
            ">HHHH16s14BHH",
            1,  # unknown, fixed header?
            7,  # unknown, fixed header?
            36,  # msg_type
            261,  # msg_type pt.2?
            self.device_id.encode("ascii").ljust(16, b"\x00"),  # device_id
            *([0] * 14),  # free space
            3,  # unknown, fixed prefix?
            3,  # unknown, fixed prefix?
        )

    @staticmethod
    def parse_grobro(buffer) -> "NeoReadOutputPowerLimit":
        unpacked = struct.unpack(
            ">HHHH16s14BHH",
            buffer[0:42],
        )
        return NeoReadOutputPowerLimit(
            device_id=unpacked[4],
        )


class NeoSetOutputPowerLimit(BaseModel):
    """
    Represents a message that can be sent to the inverter
    to set a output power limit.
    """

    device_id: str
    value: int

    @staticmethod
    def parse_ha(device_id, payload) -> "NeoReadOutputPowerLimit":
        return NeoSetOutputPowerLimit(
            device_id=device_id,
            value=int(payload.decode()),
        )

    def build_grobro(self) -> bytes:
        return struct.pack(
            ">HHHH16s14BHH",
            1,  # unknown, fixed header?
            7,  # unknown, fixed header?
            36,  # msg_type
            262,  # msg_type pt.2?
            self.device_id.encode("ascii").ljust(16, b"\x00"),  # device_id
            *([0] * 14),  # free space
            3,  # unknown, fixed prefix?
            self.value,  # the value to set
        )

    @staticmethod
    def parse_grobro(buffer) -> "NeoSetOutputPowerLimit":
        unpacked = struct.unpack(
            ">HHHH16s14BHH",
            buffer[0:42],
        )
        return NeoSetOutputPowerLimit(
            device_id=unpacked[4],
            value=unpacked[-1],
        )


class NeoCommandTypes(Enum):
    OUTPUT_POWER_LIMIT = (
        "output_power_limit",
        "number",
        NeoSetOutputPowerLimit,
    )
    OUTPUT_POWER_LIMIT_READ = (
        "output_power_limit_read",
        "button",
        NeoReadOutputPowerLimit,
    )

    def __init__(self, name, ha_type, model):
        self.ha_name = name
        self.ha_type = ha_type
        self.model = model

    def matches(self, name, ha_type) -> bool:
        return self.ha_name == name and self.ha_type == ha_type
