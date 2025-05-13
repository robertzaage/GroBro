import struct
from pydantic.main import BaseModel
from enum import Enum


class NeoOutputPowerLimit(BaseModel):
    """
    Represents a message sent by the inverter to publish the currently set output power limit.
    """

    device_id: str
    value: int

    def build_grobro(self) -> bytes:
        return struct.pack(
            ">HHHH16s14BHHH",
            1,  # unknown, fixed header?
            7,  # unknown, fixed header?
            NeoMessageTypes.OUTPUT_POWER_LIMIT.grobro_type,  # msg_type
            261,  # msg_type pt.2?
            self.device_id.encode("ascii").ljust(16, b"\x00"),  # device_id
            *([0] * 14),  # free space
            3,  # unknown, fixed prefix?
            3,  # unknown, fixed prefix?
            self.value,  # the actual value
        )

    @staticmethod
    def parse_grobro(buffer) -> "NeoOutputPowerLimit":
        unpacked = struct.unpack(
            ">HHHH16s14BHHH",
            buffer[0:44],
        )
        device_id = unpacked[4].decode("ascii", errors="ignore").strip("\x00")
        return NeoOutputPowerLimit(
            device_id=device_id,
            value=unpacked[-1],
        )


class NeoMessageTypes(Enum):
    OUTPUT_POWER_LIMIT = (38, NeoOutputPowerLimit)

    def __init__(self, grobro_type, model):
        self.grobro_type = 38
        self.model = model
