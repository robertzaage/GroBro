import pytest

from grobro.model.growatt_registers import KNOWN_NEXA_REGISTERS, KNOWN_NOAH_REGISTERS


@pytest.mark.parametrize("slot", range(1, 10))
def test_nexa_slot_power_allows_1000w(slot):
    reg = KNOWN_NEXA_REGISTERS.holding_registers[f"slot{slot}_power"]
    assert reg.homeassistant.max == 1000


def test_nexa_default_power_allows_1000w():
    assert KNOWN_NEXA_REGISTERS.holding_registers["default_power"].homeassistant.max == 1000


def test_noah_slot_power_stays_at_800w():
    assert KNOWN_NOAH_REGISTERS.holding_registers["slot1_power"].homeassistant.max == 800
