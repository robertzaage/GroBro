from typing import Optional, Union
from enum import Enum
from pydantic import BaseModel
import importlib.resources as resources
import json


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


class GrowattHoldingOutputRegister(BaseModel):
    position: GrowattRegisterPosition


class GrowattHoldingRegister(BaseModel):
    input: Optional[GrowattInputRegister] = None
    output: Optional[GrowattHoldingOutputRegister] = None


class HomeAssistantHoldingRegister(BaseModel):
    name: str
    publish: bool
    type: str
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None

    class Config:
        extra = "forbid"


class HomeassistantInputRegister(BaseModel):
    name: str
    publish: bool
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None


class HomeAssistantHoldingRegisterValue(BaseModel):
    name: str
    value: Union[str, float]
    register: HomeAssistantHoldingRegister


class HomeAssistantHoldingRegisterInput(BaseModel):
    device_id: str
    payload: list[HomeAssistantHoldingRegisterValue] = []


class HomeAssistantState(BaseModel):
    device_id: str
    payload: dict[str, Union[str, float]] = {}


class GroBroInputRegister(BaseModel):
    growatt: GrowattInputRegister
    homeassistant: HomeassistantInputRegister


class GroBroHoldingRegister(BaseModel):
    growatt: GrowattHoldingRegister
    homeassistant: HomeAssistantHoldingRegister


class GroBroRegisters(BaseModel):
    input_registers: dict[str, GroBroInputRegister]
    holding_registers: dict[str, GroBroHoldingRegister]


with resources.files(__package__).joinpath("growatt_neo_registers.json").open(
    "rb"
) as f:
    KNOWN_NEO_REGISTERS = GroBroRegisters.parse_obj(json.load(f))
with resources.files(__package__).joinpath("growatt_noah_registers.json").open(
    "rb"
) as f:
    KNOWN_NOAH_REGISTERS = GroBroRegisters.parse_obj(json.load(f))
