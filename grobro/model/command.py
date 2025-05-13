from typing import Protocol
from pydantic import BaseModel
import struct

class Command(Protocol):
    @property
    def device_id(self) -> str:
        pass

    def build_grobro(self) -> bytes:
        pass



