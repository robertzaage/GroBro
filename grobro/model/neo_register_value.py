"""
Map an inverter answer to NeoReadRegister into a NeoSetRegister
instance so the rest of GroBro can treat it as a value update
"""

import struct
import logging
from typing import Optional

from .neo_register import NeoSetRegister
from grobro.util import tlv

LOG = logging.getLogger(__name__)

_HEADER = ">HHHH16s8sQ"
MSG_TYPE = 0x0119 # value-read answer

def parse_register_value(buf: bytes) -> Optional[NeoSetRegister]:
    """
    Return a NeoSetRegister with the value that came back from the inverter
    – or None if the buffer is not a register-value packet.
    """
    try:
        _, _, _len, msg_type, dev_id_raw, _zeros, ctr = struct.unpack_from(
            _HEADER, buf, 0
        )
        if msg_type != MSG_TYPE:
            return None

        chunks = tlv.parse(buf[40:])
        if not chunks:
            return None

        first = chunks[0]
        return NeoSetRegister(
            device_id=dev_id_raw.rstrip(b"\x00").decode(),
            register=first["param"],
            value=first["value"],
            param=first["param"],
            msg_ctr=ctr,
        )
    except Exception as exc: # bad framing, ignore
        LOG.debug("parse_register_value: %s", exc)
        return None
