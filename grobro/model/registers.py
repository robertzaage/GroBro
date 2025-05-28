from typing import Optional
from enum import Enum
from pydantic import BaseModel
import importlib.resources as resources
import json

with resources.files(__package__).joinpath("growatt_neo_registers.json").open(
    "rb"
) as f:
    NEO_REGISTERS = json.load(f)
with resources.files(__package__).joinpath("growatt_noah_registers.json").open(
    "rb"
) as f:
    NOAH_REGISTERS = json.load(f)


class GrowattRegisterDataTypes(str, Enum):
    ENUM = "ENUM"
    STRING = "STRING"
    FLOAT = "FLOAT"


class GrowattRegisterEnumTypes(str, Enum):
    INT_MAP = "INT_MAP"
    BITFIELD = "BITFIELD"


class GrowattRegisterFloatOptions(BaseModel):
    delta: float = 1
    multiplier: float = 1


class GrowattRegisterEnumOptions(BaseModel):
    enum_type: GrowattRegisterEnumTypes
    values: dict[int, str]


class GrowattRegisterDataType(BaseModel):
    data_type: GrowattRegisterDataTypes
    float_options: Optional[GrowattRegisterFloatOptions] = None
    enum_options: Optional[GrowattRegisterEnumOptions] = None


class GrowattRegisterPosition(BaseModel):
    register_no: int
    offset: int = 0
    size: int = 2


class GrowattInputRegister(BaseModel):
    position: GrowattRegisterPosition
    data: GrowattRegisterDataType


class HomeassistantInputRegister(BaseModel):
    name: str
    publish: bool
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: str

    class Config:
        extra = "forbid"


class GroBroInputRegister(BaseModel):
    growatt: GrowattInputRegister
    homeassistant: HomeassistantInputRegister


class GroBroRegisters(BaseModel):
    input_registers: dict[str, GroBroInputRegister]
