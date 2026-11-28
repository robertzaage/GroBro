"""Commands from Home Assistant, config reads and the time sync."""

from __future__ import annotations

import logging
from collections import deque
from threading import Timer
from typing import Optional

from grobro import settings
from grobro.model.growatt_registers import (
    get_known_registers,
)
from grobro.model.modbus_function import GrowattModbusFunctionSingle
from grobro.model.modbus_message import GrowattModbusFunction
from datetime import datetime

LOG = logging.getLogger(__name__)


def make_modbus_command(device_id: str, func: GrowattModbusFunction, register_no: int, value: Optional[int] = None) -> GrowattModbusFunctionSingle:
    """Build a single register Modbus command."""
    return GrowattModbusFunctionSingle(
        device_id=device_id,
        function=func,
        register=register_no,
        value=value if value is not None else register_no,
    )


def _to_register_value(reg, cmd_type: str, raw_value: str) -> int:
    """Convert a Home Assistant value into the raw 16 bit register value of a holding register."""
    if cmd_type == "switch":
        return 1 if raw_value.upper() == "ON" else 0
    if cmd_type == "time":
        hour, minute = map(int, raw_value.split(":")[:2])
        return (hour * 256) + minute
    if cmd_type == "select":
        options = getattr(reg.homeassistant, "options", None)
        if options:
            reverse_options = {value: int(key) for key, value in options.items()}
            if raw_value not in reverse_options:
                raise ValueError(f"unknown option {raw_value}")
            return reverse_options[raw_value]
        return int(raw_value)

    data = reg.growatt.data
    value = float(raw_value)
    if data.float_options and data.data_type in ("FLOAT", "SIGNED_FLOAT"):
        value = (value - data.float_options.delta) / data.float_options.multiplier
    elif data.mult:
        value = value / data.mult
    raw = int(round(value))
    # Negative values are written as two's complement
    return raw & 0xFFFF if raw < 0 else raw


# Home Assistant platforms GroBro receives commands for
COMMAND_PLATFORMS = ("number", "time", "button", "switch", "select", "config")


class CommandsMixin:
    """Turns Home Assistant commands into Modbus and config messages."""

    def _handle_command(self, msg):
        """Handle a command topic: <base>/<platform>/grobro/<device_id>/<name>/<set|read>."""
        parts = msg.topic.removeprefix(f"{settings.HA_BASE_TOPIC}/").split("/")
        if len(parts) != 5 or parts[0] not in COMMAND_PLATFORMS:
            return
        cmd_type, _, device_id, cmd_name, action = parts

        LOG.debug("Received %s %s command %s for device %s", cmd_type, action, cmd_name, device_id)

        known_registers = get_known_registers(device_id)
        if not known_registers:
            LOG.info("Unknown device type: %s", device_id)
            return

        if cmd_type == "button":
            self._handle_button(device_id, known_registers, cmd_name, action)
        elif cmd_type in {"number", "switch", "time", "select"} and action == "set":
            self._handle_set(device_id, known_registers, cmd_type, cmd_name, msg.payload.decode().strip())
        elif cmd_type == "config" and action == "set":
            self._handle_config(device_id, int(cmd_name), msg.payload.decode().strip())

    def _handle_button(self, device_id: str, known_registers, cmd_name: str, action: str):
        if cmd_name == "read_all":
            # Send all modbus reads first
            for name, register in known_registers.holding_registers.items():
                if name.startswith("slot"):
                    try:
                        if int(name[4]) > settings.MAX_SLOTS:
                            continue
                    except ValueError:
                        continue

                pos = register.growatt.position
                self.on_command(
                    make_modbus_command(
                        device_id,
                        GrowattModbusFunction.READ_SINGLE_REGISTER,
                        pos.register_no,
                    )
                )

            # Queue config reads
            if self.on_config_read:
                with self._config_read_lock:
                    q = self._config_read_queues.setdefault(device_id, deque())

                    for cfg in known_registers.config_registers.values():
                        q.append(cfg.growatt.register_no)

                # give the datalogger time to answer modbus reads
                kickoff = Timer(3.0, self._kickoff_next_config_read, args=(device_id,))
                kickoff.daemon = True
                kickoff.start()
            return

        if action == "read":
            reg = known_registers.holding_registers.get(cmd_name)
            if not reg:
                LOG.error("Unknown read command %s for device %s", cmd_name, device_id)
                return
            self.on_command(
                make_modbus_command(
                    device_id,
                    GrowattModbusFunction.READ_SINGLE_REGISTER,
                    reg.growatt.position.register_no,
                )
            )

    def _handle_set(self, device_id: str, known_registers, cmd_type: str, cmd_name: str, raw_value: str):
        """Number / switch / time / select: write the holding register and read it back."""
        reg = known_registers.holding_registers.get(cmd_name)
        if not reg:
            LOG.error("Unknown holding register %s for device %s", cmd_name, device_id)
            return

        try:
            parsed_value = _to_register_value(reg, cmd_type, raw_value)
        except ValueError as e:
            LOG.error("Invalid value %s for %s: %s", raw_value, cmd_name, e)
            return

        # Packed registers hold several fields, keep the other fields unchanged
        if reg.growatt.data.bit_width is not None:
            parsed_value = self._pack_register_value(device_id, known_registers, cmd_name, parsed_value)
            if parsed_value is None:
                return

        pos = reg.growatt.position
        LOG.debug("Setting %s register %s to value %s", cmd_name, pos.register_no, parsed_value)
        self.on_command(
            make_modbus_command(
                device_id,
                GrowattModbusFunction.PRESET_SINGLE_REGISTER,
                pos.register_no,
                parsed_value,
            )
        )

        LOG.debug("Triggering read-after-write for Command %s register %s", cmd_name, pos.register_no)
        self.on_command(
            make_modbus_command(
                device_id,
                GrowattModbusFunction.READ_SINGLE_REGISTER,
                pos.register_no,
            )
        )

    def _handle_config(self, device_id: str, register_no: int, raw_value: str):
        # Special case: Sync Time (register 31) sends the current time
        if register_no == 31:
            value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Normal config registers: use numeric or string value as-is
        else:
            value = raw_value

        if self.on_config_command:
            self.on_config_command(device_id, register_no, value)

    def _pack_register_value(self, device_id: str, known_registers, cmd_name: str, field_value: int) -> Optional[int]:
        """Combine a new field value with the last known values of the other fields in the same register."""
        reg = known_registers.holding_registers[cmd_name]
        register_no = reg.growatt.position.register_no
        raw = 0
        for name, other in known_registers.holding_registers.items():
            data = other.growatt.data
            if other.growatt.position.register_no != register_no or data.bit_width is None:
                continue
            if name == cmd_name:
                value = field_value
            else:
                current = self._holding_values.get((device_id, name))
                if current is None:
                    LOG.warning(
                        "Unknown value of %s for %s, reading register %s first. Please try again.",
                        name, device_id, register_no,
                    )
                    self.on_command(
                        make_modbus_command(device_id, GrowattModbusFunction.READ_SINGLE_REGISTER, register_no)
                    )
                    return None
                try:
                    value = _to_register_value(other, other.homeassistant.type, current)
                except ValueError:
                    LOG.error(
                        "Invalid value %s of %s for %s, cannot change %s. Please set %s first.",
                        current, name, device_id, cmd_name, name,
                    )
                    return None
            raw |= (value & ((1 << data.bit_width) - 1)) << data.bit_shift
        return raw

    def _schedule_time_sync(self):
        from datetime import datetime, timedelta

        now = datetime.now()
        candidates = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in settings.TIME_SYNC_HOURS]
        candidates += [c + timedelta(days=1) for c in candidates]
        next_sync = min(c for c in candidates if c > now)
        self._time_sync_timer = Timer((next_sync - now).total_seconds(), self.sync_time_of_all_devices, args=(True,))
        self._time_sync_timer.daemon = True
        self._time_sync_timer.start()

    def sync_time_of_all_devices(self, reschedule: bool = False):
        """Write the current time to every known device that supports it (config register 31)."""
        from datetime import datetime

        value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for device_id in list(self._discovery_cache):
            known_registers = get_known_registers(device_id)
            if not known_registers or not self.on_config_command:
                continue
            if any(reg.growatt.register_no == 31 for reg in known_registers.config_registers.values()):
                LOG.info("Synchronizing time of %s", device_id)
                self.on_config_command(device_id, 31, value)
        if reschedule:
            self._schedule_time_sync()

    def _kickoff_next_config_read(self, device_id: str):
        with self._config_read_lock:
            # already waiting for a response
            if device_id in self._config_read_inflight:
                return

            q = self._config_read_queues.get(device_id)
            if not q:
                return

            register_no = q.popleft()
            self._config_read_inflight[device_id] = register_no

        if self.on_config_read:
            self.on_config_read(device_id, register_no)

        # start 1 minute timeout
        timer = Timer(
            60,
            self._config_read_timeout,
            args=(device_id, register_no),
        )
        timer.daemon = True
        self._config_read_timers[device_id] = timer
        timer.start()

    def _config_read_timeout(self, device_id: str, register_no: int):
        with self._config_read_lock:
            inflight = self._config_read_inflight.get(device_id)
            if inflight != register_no:
                return

            LOG.warning(
                "Config read timeout for %s register=%s",
                device_id,
                register_no,
            )

            self._config_read_inflight.pop(device_id, None)
            self._config_read_timers.pop(device_id, None)

        # continue with next queued register
        self._kickoff_next_config_read(device_id)

    def handle_config_read_response(self, device_id: str, register_no: int, value: str | int):
        topic = f"{settings.HA_BASE_TOPIC}/config/grobro/{device_id}/{register_no}/get"
        self._client.publish(topic, value, retain=True)

        with self._config_read_lock:
            inflight = self._config_read_inflight.get(device_id)
            if inflight != register_no:
                return

            LOG.debug(
                "Config read completed for %s register=%s",
                device_id,
                register_no,
            )

            timer = self._config_read_timers.pop(device_id, None)
            if timer:
                timer.cancel()

            self._config_read_inflight.pop(device_id, None)

        # continue with next queued read
        self._kickoff_next_config_read(device_id)
