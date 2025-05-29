from rope.base import serializer
from typing import Optional
from datetime import datetime
import struct
import logging
from pydantic.main import BaseModel
from enum import Enum
from pylint.checkers.base import register
from grobro.model.growatt_registers import GrowattRegisterPosition

LOG = logging.getLogger(__name__)

HEADER_STRUCT = ">HHHBB30s"


class GrowattModbusBlock(BaseModel):
    """
    Represents a block of modbus registers.
    start, end are the number of the first and last register included.
    values are the registers between start and end, each value 2 bytes.

    Each register block:
        - H - 2 byte start register
        - H - 2 byte end register (M=end-start+1)
        - M x H - M x 2 byte register values
    """

    start: int
    end: int
    values: bytes

    @staticmethod
    def parse_grobro(buffer) -> Optional["GrowattModbusBlock"]:
        try:
            (start, end) = struct.unpack(">HH", buffer[0:4])
            num_blocks = end - start + 1
            result = GrowattModbusBlock(
                start=start, end=end, values=buffer[4 : 4 + num_blocks * 2]
            )
            assert len(result.values) == num_blocks * 2
            return result
        except Exception as e:
            LOG.warn("Parsing GrowattModbusBlock: %s", e)

    def build_grobro(self) -> bytes:
        result = struct.pack(">HH", self.start, self.end) + self.values
        return result

    def size(self):
        return 4 + len(self.values)


class GrowattModbusFunction(int, Enum):
    READ_HOLDING_REGISTER = 3
    READ_INPUT_REGISTER = 4
    READ_SINGLE_REGISTER = 5
    PRESET_SINGLE_REGISTER = 6
    PRESET_MULTIPLE_REGISTER = 16


class GrowattMetadata(BaseModel):
    """
    Represents metadata within a READ_INPUT_REGISTER message.

    Structure:
    - 30s - zero padded device serial
    - 7B - timestamp in interesting format
    """

    device_sn: str
    timestamp: Optional[datetime]

    def size(self):
        return 37

    @staticmethod
    def parse_grobro(buffer) -> Optional["GrowattMetadata"]:
        offset = 0
        device_serial_raw = struct.unpack(">30s", buffer[offset : offset + 30])[0]
        device_serial = device_serial_raw.decode("ascii", errors="ignore").strip("\x00")
        offset += 30
        year, month, day, hour, minute, second, millis = struct.unpack(
            ">7B", buffer[offset : offset + 7]
        )
        timestamp = None
        try:
            timestamp = datetime(
                year + 2000, month, day, hour, minute, second, microsecond=millis * 1000
            )
        except Exception:
            pass
        return GrowattMetadata(device_sn=device_serial, timestamp=timestamp)

    def build_grobro(self) -> bytes:
        result = struct.pack(
            ">30s7B",
            self.device_sn.encode("ascii").ljust(30, b"\x00"),  # device_id
            self.timestamp.year - 2000,
            self.timestamp.month,
            self.timestamp.day,
            self.timestamp.hour,
            self.timestamp.minute,
            self.timestamp.second,
            int(self.timestamp.microsecond / 1000),
        )
        return result


class GrowattModbusMessage(BaseModel):
    """
    Represents a block of modbus registers sent by the growatt device.

    Header Structure:
        - H - 2 byte unknown
        - H - 2 byte constant 7
        - H - 2 byte message length (excluding register count, constant and message length)
        - B - 1 byte modbus device address (seems to be constant 1 in mqtt)
        - B - 1 byte function
        - 30s - 30 byte zero-padded device id
        - optional GrowattModbusMetadata - only present when function == READ_INPUT_REGISTER
        - N register blocks
    """

    unknown: int
    device_id: str
    metadata: Optional[GrowattMetadata] = None
    function: GrowattModbusFunction
    register_blocks: list[GrowattModbusBlock]

    @property
    def msg_len(self):
        result = 32  # 2 byte msg_type + 30 byte device id
        if self.metadata:
            result += self.metadata.size()
        for block in self.register_blocks:
            result += block.size()
        return result

    def get_data(self, pos: GrowattRegisterPosition):
        for block in self.register_blocks:
            if block.start > pos.register_no or block.end < pos.register_no:
                continue
            block_pos = (pos.register_no - block.start) * 2 + pos.offset
            return block.values[block_pos : block_pos + pos.size]
        return None

    @staticmethod
    def parse_grobro(buffer) -> Optional["GrowattModbusMessage"]:
        try:
            (unknown, constant_7, msg_len, constant_1, function, device_id_raw) = (
                struct.unpack(
                    HEADER_STRUCT,
                    buffer[0:38],
                )
            )
            if msg_len != len(buffer[8:]):
                return None
            device_id = device_id_raw.decode("ascii", errors="ignore").strip("\x00")
            if function not in [e.value for e in GrowattModbusFunction]:
                LOG.info("unknown modbus function for %s: %s", device_id, function)
                return None

            register_blocks = []
            offset = 38

            metadata = None
            if function == GrowattModbusFunction.READ_INPUT_REGISTER:
                metadata = GrowattMetadata.parse_grobro(buffer[offset:])
                offset += metadata.size()

            while len(buffer) > offset + 6:
                block = GrowattModbusBlock.parse_grobro(buffer[offset:])
                register_blocks.append(block)
                offset += block.size()

            return GrowattModbusMessage(
                unknown=unknown,
                metadata=metadata,
                device_id=device_id,
                function=function,
                register_blocks=register_blocks,
            )
        except Exception as e:
            LOG.warn("parsing GrowattModbusMessage: %s", e)

    def build_grobro(self) -> bytes:
        result = struct.pack(
            HEADER_STRUCT,
            self.unknown,
            7,
            self.msg_len,
            1,
            self.function,
            self.device_id.encode("ascii").ljust(30, b"\x00"),  # device_id
        )
        if self.metadata:
            result += self.metadata.build_grobro()
        for block in self.register_blocks:
            result += block.build_grobro()
        return result
