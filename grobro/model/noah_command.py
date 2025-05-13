from pydantic import BaseModel
import struct
from enum import Enum


class NoahSmartPower(BaseModel):
    """
    Represents a message that can be sent to NOAH
    to set the smart power diff.
    """

    device_id: str
    power_diff: int

    @staticmethod
    def parse_ha(device_id, payload) -> "NeoReadOutputPowerLimit":
        return NeoReadOutputPowerLimit(
            device_id=device_id,
            value=int(payload.decode()),
        )

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


class NoahCommandTypes(Enum):
    SMART_POWER = (
        "smart_power",
        "number",
        NoahSmartPower,
    )

    def __init__(self, name, ha_type, model):
        self.ha_name = name
        self.ha_type = ha_type
        self.model = model

    def matches(self, name, ha_type) -> bool:
        return self.ha_name == name and self.ha_type == ha_type
