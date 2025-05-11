from typing import Protocol
from pydantic import BaseModel
import struct

class Command(Protocol):
    @property
    def device_id(self) -> str:
        pass

    def build_grobro(self) -> bytes:
        pass


class NoahSmartPowerCommand(BaseModel):
    device_id: str
    power_diff: int

    def build_grobro(self) -> bytes:
        header = struct.pack(">HHH", 1, 7, 42)
        mtype = 0x0110
        dev_bytes = self.device_id.encode("ascii").ljust(16, b"\x00")

        setup = 0
        setdown = 0
        if self.power_diff > 0:
            setup = self.power_diff
        else:
            setdown = -self.power_diff

        payload = (
            dev_bytes
            + (b"\x00" * 14)
            + b"\x01\x36\x01\x38"
            + struct.pack(">HHH", setdown, setup, 1)
        )

        return header + struct.pack(">H", mtype) + payload


class NeoSetWirkCommand(BaseModel):
    device_id: str
    value: int

    def build_grobro(self) -> bytes:
        header = struct.pack(">HHH", 1, 7, 36)
        mtype = 0x0106
        dev_bytes = self.device_id.encode("ascii").ljust(16, b"\x00")

        payload = (
            dev_bytes
            + (b"\x00" * 15)
            + b"\x03"
            + struct.pack(">H", self.value)
        )

        return header + struct.pack(">H", mtype) + payload
