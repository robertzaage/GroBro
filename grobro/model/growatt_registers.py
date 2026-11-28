from typing import Literal, Optional, Union
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
import importlib.resources as resources
import json
import struct

class GrowattRegisterDataTypes(str, Enum):
    ENUM = "ENUM"
    STRING = "STRING"
    FLOAT = "FLOAT"
    INT = "INT"
    SIGNED_FLOAT = "SIGNED_FLOAT"
    SIGNED_INT = "SIGNED_INT"
    TIME_HHMM = "TIME_HHMM"


class GrowattRegisterEnumTypes(str, Enum):
    INT_MAP = "INT_MAP"
    BITFIELD = "BITFIELD"


class GrowattRegisterFloatOptions(BaseModel):
    delta: float = 0
    multiplier: float = 1


class GrowattRegisterEnumOptions(BaseModel):
    enum_type: GrowattRegisterEnumTypes
    values: dict[int, str]


class GrowattRegisterDataType(BaseModel):
    data_type: GrowattRegisterDataTypes
    float_options: Optional[GrowattRegisterFloatOptions] = None
    enum_options: Optional[GrowattRegisterEnumOptions] = None
    mult: Optional[float] = None
    # Optional bit field inside the register, e.g. the enable flag of a packed time slot
    bit_shift: int = 0
    bit_width: Optional[int] = None

    def extract_bits(self, value: int) -> int:
        if self.bit_width is None:
            return value
        return (value >> self.bit_shift) & ((1 << self.bit_width) - 1)

    def parse(self, data_raw: bytes):
        if not data_raw:
            return None
        if self.data_type == GrowattRegisterDataTypes.STRING:
            return data_raw.decode("ascii", errors="ignore").strip("\x00")
        unpack_type = {1: "!B", 2: "!H", 4: "!I"}[len(data_raw)]
        is_signed = self.data_type in [GrowattRegisterDataTypes.SIGNED_INT, GrowattRegisterDataTypes.SIGNED_FLOAT]
        if is_signed:
            unpack_type = unpack_type.lower()
        if self.data_type in [GrowattRegisterDataTypes.FLOAT, GrowattRegisterDataTypes.SIGNED_FLOAT]:
            value = struct.unpack(unpack_type, data_raw)[0]
            if self.mult is not None:
                value *= self.mult
            elif self.float_options:
                value *= self.float_options.multiplier
                value += self.float_options.delta
            return round(value, 3)
        elif self.data_type == GrowattRegisterDataTypes.TIME_HHMM:
            value = self.extract_bits(struct.unpack(unpack_type, data_raw)[0])
            hour = (value >> 8) & 0xFF
            minute = value & 0xFF
            return f"{hour:02d}:{minute:02d}"
        elif self.data_type in [GrowattRegisterDataTypes.INT, GrowattRegisterDataTypes.SIGNED_INT]:
            return self.extract_bits(struct.unpack(unpack_type, data_raw)[0])
        elif self.data_type == GrowattRegisterDataTypes.ENUM:
            opts = self.enum_options
            value = self.extract_bits(struct.unpack(unpack_type, data_raw)[0])

            if opts.enum_type == GrowattRegisterEnumTypes.BITFIELD:
                return None  # TODO: implement

            elif opts.enum_type == GrowattRegisterEnumTypes.INT_MAP:
                return opts.values.get(int(value), "unknown")


class GrowattRegisterPosition(BaseModel):
    register_no: int
    offset: int = 0
    size: int = 2


class GrowattInputRegister(BaseModel):
    position: GrowattRegisterPosition
    data: GrowattRegisterDataType


class HomeAssistantHoldingRegister(BaseModel):
    name: str
    publish: bool
    type: str
    min: Optional[Union[int, float]] = None
    max: Optional[Union[int, float]] = None
    step: Optional[Union[int, float]] = None
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None
    options: Optional[dict[str, str]] = None
    entity_category: Optional[str] = None
    
    model_config = ConfigDict(extra="forbid")


class HomeassistantInputRegister(BaseModel):
    name: str
    publish: bool
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None
    entity_category: Optional[str] = None
    # Value while missing from the payload: "zero" or "hold" the last known
    # value. Derived from device_class/unit_of_measurement if unset.
    offline: Optional[Literal["zero", "hold"]] = None


class HomeAssistantHoldingRegisterValue(BaseModel):
    name: str
    value: Union[str, float, int]
    register_def: HomeAssistantHoldingRegister = Field(alias="register")

    model_config = ConfigDict(populate_by_name=True)


class HomeAssistantHoldingRegisterInput(BaseModel):
    device_id: str
    payload: list[HomeAssistantHoldingRegisterValue] = []


class HomeAssistantInputRegister(BaseModel):
    device_id: str
    payload: dict[str, Union[str, float, int]] = {}


class GroBroInputRegister(BaseModel):
    growatt: GrowattInputRegister
    homeassistant: HomeassistantInputRegister


class GroBroHoldingRegister(BaseModel):
    growatt: Optional[GrowattInputRegister] = None
    homeassistant: HomeAssistantHoldingRegister

class GroBroConfigRegister(BaseModel):
    register_no: int
    data: GrowattRegisterDataType

class HomeAssistantConfigRegister(BaseModel):
    publish: bool
    name: str
    type: str  # sensor | number | button
    min: Optional[int] = None
    max: Optional[int] = None
    step: Optional[int] = None
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None
    entity_category: Optional[str] = None

class GroBroConfigRegisterDef(BaseModel):
    growatt: GroBroConfigRegister
    homeassistant: HomeAssistantConfigRegister

class GroBroRegisters(BaseModel):
    input_registers: dict[str, GroBroInputRegister]
    holding_registers: dict[str, GroBroHoldingRegister]
    config_registers: dict[str, GroBroConfigRegisterDef] = {}

with resources.files(__package__).joinpath("growatt_neo_registers.json").open("rb") as f:
    KNOWN_NEO_REGISTERS = GroBroRegisters.model_validate(json.load(f))
with resources.files(__package__).joinpath("growatt_noah_registers.json").open("rb") as f:
    KNOWN_NOAH_REGISTERS = GroBroRegisters.model_validate(json.load(f))

# NEO dataloggers expose Wi-Fi RSSI through config register 76 as NOAH/NEXA do.
# Keep the HA metadata identical to the established NOAH definition.
if "wifi_signal_strength" not in KNOWN_NEO_REGISTERS.config_registers:
    KNOWN_NEO_REGISTERS.config_registers["wifi_signal_strength"] = (
        KNOWN_NOAH_REGISTERS.config_registers["wifi_signal_strength"].model_copy(deep=True)
    )

with resources.files(__package__).joinpath("growatt_nexa_registers.json").open("rb") as f:
    KNOWN_NEXA_REGISTERS = GroBroRegisters.model_validate(json.load(f))
with resources.files(__package__).joinpath("growatt_spf_registers.json").open("rb") as f:
    KNOWN_SPF_REGISTERS = GroBroRegisters.model_validate(json.load(f))
# MIN TL-XH2 hybrid inverter family (ShineWiFi-X2 dongle, ZGQ serial prefix).
# This is gen-2 specifically — XH gen 1 (ShineWiFi-X) uses the legacy port-5279
# protocol handled by Grott, not GroBro. Starts as a copy of NEO with the same
# register layout for the inverter core (block 3000-3499); battery/BMS/EPS/BDC
# register positions are unverified pending an XH2 owner with a battery installed.
with resources.files(__package__).joinpath("growatt_xh2_registers.json").open("rb") as f:
    KNOWN_XH2_REGISTERS = GroBroRegisters.model_validate(json.load(f))
# MOD 3-phase inverter family (ShineWiFi-X dongle).
# Uses the standard 3000-3249 input register layout shared with MIN/TL-XH, all
# three phases published and battery values read from the first BDC (3169+).
with resources.files(__package__).joinpath("growatt_mod_registers.json").open("rb") as f:
    KNOWN_MOD_REGISTERS = GroBroRegisters.model_validate(json.load(f))

# SPH/SPA storage inverter family: PV and grid values in input registers 0-124,
# storage values in 1000-1124, priority and time slots in holding registers 1044-1118.
with resources.files(__package__).joinpath("growatt_sph_registers.json").open("rb") as f:
    KNOWN_SPH_REGISTERS = GroBroRegisters.model_validate(json.load(f))


# Device routing by the serial in the MQTT topic (datalogger or integrated inverter serial)
DEVICE_PREFIXES: list[tuple[str, str, GroBroRegisters]] = [
    ("QMN", "NEO", KNOWN_NEO_REGISTERS),
    ("0PVP", "NOAH", KNOWN_NOAH_REGISTERS),
    ("0HVR", "NEXA", KNOWN_NEXA_REGISTERS),
    ("HAQ", "SPF", KNOWN_SPF_REGISTERS),
    # ShineWeLink-X2 datalogger, its LoRa inverters (PTQ) are registered as NEO devices
    ("RAQ", "ShineWeLink", KNOWN_NEO_REGISTERS),
    ("PTQ", "NEO", KNOWN_NEO_REGISTERS),
    ("ZGQ", "MIN-XH2", KNOWN_XH2_REGISTERS),
    ("VWQ", "MOD", KNOWN_MOD_REGISTERS),
]

# Inverter serial prefixes (reported in the metadata of every input register message)
# of the supported inverter families. Used for dataloggers whose own serial prefix is
# unknown. Single-phase models of the 3000 register layout use the XH2 map, three-phase
# models the MOD map and the SPH/SPA storage inverters their own map.
INVERTER_PREFIXES: dict[str, tuple[str, GroBroRegisters]] = {
    **{p: ("MIN TL-XH", KNOWN_XH2_REGISTERS) for p in ("ABJ", "SKL", "XVM", "SMN", "JGQ", "HJU", "VFJ")},
    **{p: ("MIN TL-X", KNOWN_XH2_REGISTERS) for p in ("QYL", "XTD", "BDK", "DCF", "WVN")},
    **{p: ("MIC TL-X", KNOWN_XH2_REGISTERS) for p in ("FPH", "FWJ")},
    **{p: ("MOD TL3-XH", KNOWN_MOD_REGISTERS) for p in ("XHL", "MFK", "PYL", "JCM", "MEK", "DFK", "FMP", "FPP", "FQP", "CZM")},
    **{p: ("MOD TL3-HU", KNOWN_MOD_REGISTERS) for p in ("DPS", "DMS", "DKS", "DO1", "TTS", "TSS", "EGR")},
    **{p: ("MID TL3-XH", KNOWN_MOD_REGISTERS) for p in ("KLN", "KMN", "KNN", "RKM", "DLP")},
    **{p: ("MID TL3-XL", KNOWN_MOD_REGISTERS) for p in ("GXF",)},
    **{p: ("MAX TL3", KNOWN_MOD_REGISTERS) for p in ("NAH",)},
    **{p: ("WIT", KNOWN_MOD_REGISTERS) for p in ("0PE", "0PC", "0PH", "0HU")},
    **{p: ("SPH", KNOWN_SPH_REGISTERS) for p in ("YRP", "DIE", "PYH", "NLC", "NRC")},
    **{p: ("SPA", KNOWN_SPH_REGISTERS) for p in ("WPD",)},
}

# Dataloggers used with different inverter families. Their devices are identified by the
# inverter serial, the map in DEVICE_PREFIXES is only the fallback (ShineWiFi-X: MOD).
GENERIC_DATALOGGER_PREFIXES = ("VWQ",)

# Devices identified by their inverter serial, keyed by the serial in the MQTT topic
DETECTED_DEVICES: dict[str, tuple[str, GroBroRegisters]] = {}

# Serials of the inverters behind a datalogger, keyed by the serial in the MQTT topic
INVERTER_SERIALS: dict[str, str] = {}


def remember_inverter_serial(device_id: str, inverter_serial: Optional[str]) -> None:
    """Store the inverter serial reported in the metadata of a datalogger message."""
    serial = (inverter_serial or "").strip("\x00 ")
    if serial and serial != device_id and serial.isalnum():
        INVERTER_SERIALS[device_id] = serial


def get_known_registers(device_id: str) -> Optional[GroBroRegisters]:
    """Return the register map for a device, based on its serial prefix."""
    detected = DETECTED_DEVICES.get(device_id)
    if detected:
        return detected[1]
    for prefix, _, registers in DEVICE_PREFIXES:
        if device_id.startswith(prefix):
            return registers
    return None


def get_device_type_name(device_id: str) -> str:
    """Return the device type name for a device, based on its serial prefix."""
    detected = DETECTED_DEVICES.get(device_id)
    if detected:
        return detected[0]
    for prefix, name, _ in DEVICE_PREFIXES:
        if device_id.startswith(prefix):
            return name
    return "UNKNOWN"


def detect_device_by_inverter_serial(device_id: str, inverter_serial: Optional[str]) -> Optional[GroBroRegisters]:
    """Identify a device behind an unknown or generic datalogger by the serial of its inverter."""
    if not inverter_serial or device_id in DETECTED_DEVICES:
        return None
    generic = device_id.startswith(GENERIC_DATALOGGER_PREFIXES)
    if not generic and get_known_registers(device_id) is not None:
        return None
    match = INVERTER_PREFIXES.get(inverter_serial[:3])
    if match is None:
        return None
    DETECTED_DEVICES[device_id] = match
    return match[1]


# Device type codes (holding register 43, or 30000 on firmware with the VPP protocol) of the
# families with a known register map. Variants with a different register layout (e.g. SPH TL3,
# MOD/MID TL3-X) are left out on purpose.
DEVICE_TYPE_CODES: dict[int, tuple[str, GroBroRegisters]] = {
    5100: ("MIN TL-XH", KNOWN_XH2_REGISTERS),
    5200: ("MIN TL-X", KNOWN_XH2_REGISTERS),
    5201: ("MIN TL-X", KNOWN_XH2_REGISTERS),
    5400: ("MOD TL3-XH", KNOWN_MOD_REGISTERS),
    5401: ("MOD TL3-HU", KNOWN_MOD_REGISTERS),
    **{code: ("WIT", KNOWN_MOD_REGISTERS) for code in (5600, 5601, 5603, 5605)},
    **{code: ("SPH", KNOWN_SPH_REGISTERS) for code in (3501, 3502, 3503, 3504)},
    **{code: ("SPA", KNOWN_SPH_REGISTERS) for code in (3701, 3715, 3716, 3725, 3735)},
    **{code: ("SPF", KNOWN_SPF_REGISTERS) for code in (3400, 3401, 3402, 3403, 20105)},
}
DEVICE_TYPE_CODE_REGISTERS = (43, 30000)


def detect_device_by_type_code(device_id: str, type_code: Optional[int]) -> Optional[GroBroRegisters]:
    """Identify a device behind an unknown or generic datalogger by its device type code."""
    if not type_code or device_id in DETECTED_DEVICES:
        return None
    generic = device_id.startswith(GENERIC_DATALOGGER_PREFIXES)
    if not generic and get_known_registers(device_id) is not None:
        return None
    match = DEVICE_TYPE_CODES.get(type_code)
    if match is None:
        return None
    DETECTED_DEVICES[device_id] = match
    return match[1]
