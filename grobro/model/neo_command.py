from grobro.model.neo_messages import GrowattModbusFunction
import struct
from pydantic import BaseModel
from enum import Enum
from pylint.checkers.base import register
from typing import Optional

MODBUS_COMMAND_STRUCT = ">HHHBB30sHH"


class GrowattModbusCommand(BaseModel):
    """
    Represents a message that can be sent to the inverter
    to read or write holding registers.

    Structure:
        - H - 2 byte unknown
        - H - 2 byte constant 7
        - H - 2 byte message length (excluding register count, constant and message length)
        - B - 1 byte modbus device address (seems to be constant 1 in mqtt)
        - B - 1 byte function
        - 30s - 30 byte zero-padded device id
        - H - 2 byte register
        - H - 2 byte either: register (again) for READ_SINGLE_REGISTER or value for PRESET_SINGLE_REGISTER
    """

    device_id: str
    function: GrowattModbusFunction
    register: int
    value: int

    @staticmethod
    def parse_grobro(buffer) -> Optional["GrowattModbusMessage"]:
        (
            constant_1,
            constant_7,
            msg_len,
            constant_1,
            function,
            device_id_raw,
            register,
            value,
        ) = struct.unpack(MODBUS_COMMAND_STRUCT, buffer[0:42])

        device_id = device_id_raw.decode("ascii", errors="ignore").strip("\x00")

        return GrowattModbusCommand(
            device_id=device_id,
            function=function,
            register=register,
            value=value,
        )

    def build_grobro(self) -> bytes:
        return struct.pack(
            MODBUS_COMMAND_STRUCT,
            1,
            7,
            36,
            1,
            self.function,
            self.device_id.encode("ascii").ljust(30, b"\x00"),  # device_id
            self.register,
            self.value,
        )


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

c1 = GrowattModbusCommand(
    device_id="ADSF1234",
    function=GrowattModbusFunction.PRESET_SINGLE_REGISTER,
    register=3,
    value=42,
)
c2 = NeoSetOutputPowerLimit(
    device_id="ADSF1234",
    value=42,
)

print(c1.build_grobro().hex(" "))
print(c2.build_grobro().hex(" "))

