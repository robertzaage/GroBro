from typing import Optional
import struct
import logging
from pydantic.main import BaseModel
from enum import Enum

LOG = logging.getLogger(__name__)


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
            38,  # msg_type
            261,  # msg_type pt.2?
            self.device_id.encode("ascii").ljust(16, b"\x00"),  # device_id
            *([0] * 14),  # free space
            3,  # unknown, fixed prefix?
            3,  # unknown, fixed prefix?
            self.value,  # the actual value
        )

    @staticmethod
    def parse_grobro(buffer) -> Optional["NeoOutputPowerLimit"]:
        try:
            unpacked = struct.unpack(
                ">HHHH16s14BHHH",
                buffer[0:44],
            )
            if unpacked[2] != 38:
                return None  # msq_type doesn't fit
            if unpacked[20] != 3:
                return None # we've seen this message type with 2 and value 0

            device_id = unpacked[4].decode("ascii", errors="ignore").strip("\x00")
            return NeoOutputPowerLimit(
                device_id=device_id,
                value=unpacked[-1],
            )
        except Exception as e:
            LOG.debug("parse NeoOutputPowerLimit: %s", e)
            return None

