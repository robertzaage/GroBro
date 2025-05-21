# grobro/model/neo_register.py
#
# Generic register read / write support for Growatt NEO-series inverters.
# Provides two classes:
#   - NeoSetRegister   = write a value to a register (msg-type 0x0118)
#   - NeoReadRegister  = read  a value from a register (msg-type 0x0119)

from typing import Union, Optional
import struct
import json
import logging

from pydantic import BaseModel

from grobro.util import tlv

LOG = logging.getLogger(__name__)

_HEADER_STRUCT = ">HHHH16s8sQ"        # 1,7,length,msgType,devId,8×0,counter
_BLANK         = b"\x00" * 8          # fixed padding


class _BaseRegister(BaseModel):
    device_id: str
    register: int
    value: Union[int, str, bytes] = 0
    param: Optional[int] = None        # falls back to register when None
    msg_ctr: int = 1                   # 8-byte counter in Growatt protocol

    # Build common header
    def _header(self, msg_type: int, payload_len: int) -> bytes:
        length = 0x22 + payload_len
        return struct.pack(
            _HEADER_STRUCT,
            1,                          # constant
            7,                          # constant
            length,
            msg_type,
            self.device_id.encode().ljust(16, b"\x00"),
            _BLANK,
            self.msg_ctr,
        )


class NeoSetRegister(_BaseRegister):
    """Write value to register"""

    # GroBros command-protocol method
    def build_grobro(self) -> bytes:
        p = self.param if self.param is not None else self.register
        tlv_blob = tlv.encode(p, self.value)
        return self._header(0x0118, len(tlv_blob)) + tlv_blob

    @staticmethod
    def parse_grobro(buffer: bytes) -> "NeoSetRegister":
        (
            _h1, _h2, _len, _type,
            dev_id_raw, _zeros, ctr
        ) = struct.unpack_from(_HEADER_STRUCT, buffer, 0)

        chunks = tlv.parse(buffer[40:])
        if not chunks:
            raise ValueError("missing TLV")
        first = chunks[0]

        return NeoSetRegister(
            device_id=dev_id_raw.rstrip(b"\x00").decode(),
            register=first["param"],
            value=first["value"],
            param=first["param"],
            msg_ctr=ctr,
        )

    @staticmethod
    def parse_ha(
        device_id: str,
        payload: bytes,
        *,
        register_hint: int | None = None,
    ) -> "NeoSetRegister":
        if payload.strip().startswith(b"{"):
            data = json.loads(payload.decode())
            register = int(data.get("register", register_hint))
            value    = data["value"]
        else:
            register = register_hint
            value    = int(payload.decode())

        if register is None:
            raise ValueError("Register not specified for NeoSetRegister")

        return NeoSetRegister(
            device_id=device_id,
            register=register,
            value=value,
        )


class NeoReadRegister(_BaseRegister):
    """Request the current value of register"""

    def build_grobro(self) -> bytes:
        payload = struct.pack(">H", self.register)
        return self._header(0x0119, len(payload)) + payload

    @staticmethod
    def parse_ha(
        device_id: str,
        payload: bytes,
        *,
        register_hint: int | None = None,
    ) -> "NeoReadRegister":
        if payload.strip().startswith(b"{"):
            data = json.loads(payload.decode())
            register = int(data.get("register", register_hint))
        else:
            register = register_hint

        if register is None:
            raise ValueError("Register not specified for NeoReadRegister")

        return NeoReadRegister(device_id=device_id, register=register)


__all__ = ["NeoSetRegister", "NeoReadRegister"]
