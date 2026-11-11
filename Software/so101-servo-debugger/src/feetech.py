"""Minimal client for Feetech STS/SMS servos (protocol 0), as used by the SO-100/SO-101.

Talks to the bus directly with pyserial, so it keeps working when LeRobot reorganises its
motor code (`lerobot.common.motors` -> `lerobot.motors.feetech` -> `lerobot.motors.SerialMotorsBus`).

Register addresses are LeRobot's STS_SMS_SERIES_CONTROL_TABLE (lerobot v0.6.1,
src/lerobot/motors/feetech/tables.py); they are fixed by the servo firmware.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

HEADER = b"\xff\xff"
BROADCAST_ID = 0xFE
MAX_ID = 252

INST_PING = 0x01
INST_READ = 0x02
INST_WRITE = 0x03

STS3215_MODEL_NUMBER = 777

# name: (address, size in bytes). Addresses below 40 live in EEPROM and need Lock = 0 to write.
REGISTERS: dict[str, tuple[int, int]] = {
    "Firmware_Major_Version": (0, 1),
    "Firmware_Minor_Version": (1, 1),
    "Model_Number": (3, 2),
    "ID": (5, 1),
    "Baud_Rate": (6, 1),
    "Return_Delay_Time": (7, 1),
    "Response_Status_Level": (8, 1),
    "Min_Position_Limit": (9, 2),
    "Max_Position_Limit": (11, 2),
    "Max_Temperature_Limit": (13, 1),
    "Max_Voltage_Limit": (14, 1),
    "Min_Voltage_Limit": (15, 1),
    "Max_Torque_Limit": (16, 2),
    "Phase": (18, 1),
    "Unloading_Condition": (19, 1),
    "LED_Alarm_Condition": (20, 1),
    "P_Coefficient": (21, 1),
    "D_Coefficient": (22, 1),
    "I_Coefficient": (23, 1),
    "Protection_Current": (28, 2),
    "Homing_Offset": (31, 2),
    "Operating_Mode": (33, 1),
    "Overload_Torque": (36, 1),
    "Torque_Enable": (40, 1),
    "Goal_Position": (42, 2),
    "Lock": (55, 1),
    "Present_Position": (56, 2),
    "Present_Speed": (58, 2),
    "Present_Load": (60, 2),
    "Present_Voltage": (62, 1),
    "Present_Temperature": (63, 1),
    "Status": (65, 1),
    "Moving": (66, 1),
    "Present_Current": (69, 2),
}

# Sign-magnitude encodings: the bit that carries the sign.
SIGN_BITS: dict[str, int] = {
    "Present_Load": 10,
    "Homing_Offset": 11,
    "Goal_Position": 15,
    "Present_Position": 15,
    "Present_Speed": 15,
}

# Baud_Rate register value for each baudrate (STS/SMS series).
BAUD_CODES: dict[int, int] = {
    1_000_000: 0,
    500_000: 1,
    250_000: 2,
    128_000: 3,
    115_200: 4,
    57_600: 5,
    38_400: 6,
    19_200: 7,
}
DEFAULT_BAUDRATE = 1_000_000

# Error byte of every status packet (also the Status register). Bits 0-3 are documented by
# Feetech; bit 5 (overload) is reported by community drivers, so treat it as a strong hint.
ERROR_BITS: dict[int, str] = {
    0: "input voltage out of range",
    1: "angle sensor fault",
    2: "overheating",
    3: "overcurrent",
    5: "overload",
}

CURRENT_UNIT_MA = 6.5  # Present_Current unit


class Transport(Protocol):
    """The subset of pyserial.Serial used here (lets tests plug in a simulated bus)."""

    baudrate: int

    def write(self, data: bytes) -> int | None: ...

    def read(self, size: int = 1) -> bytes: ...

    def reset_input_buffer(self) -> None: ...

    def close(self) -> None: ...


class BusError(Exception):
    """Base class for bus communication failures."""


class NoResponse(BusError):
    """No status packet arrived before the timeout."""


class CorruptResponse(BusError):
    """A reply arrived but was malformed: usually two motors sharing one ID, or electrical noise."""


@dataclass
class StatusPacket:
    motor_id: int
    error: int
    params: bytes
    elapsed_ms: float

    @property
    def errors(self) -> list[str]:
        return decode_errors(self.error)


def checksum(body: bytes) -> int:
    return (~sum(body)) & 0xFF


def build_packet(motor_id: int, instruction: int, params: bytes = b"") -> bytes:
    body = bytes([motor_id, len(params) + 2, instruction]) + params
    return HEADER + body + bytes([checksum(body)])


def decode_errors(error: int) -> list[str]:
    known = [text for bit, text in ERROR_BITS.items() if error & (1 << bit)]
    unknown = error & ~sum(1 << b for b in ERROR_BITS)
    if unknown:
        known.append(f"unknown error bits 0x{unknown:02x}")
    return known


def decode_value(name: str, raw: int) -> int:
    bit = SIGN_BITS.get(name)
    if bit is not None and raw & (1 << bit):
        return -(raw & ((1 << bit) - 1))
    return raw


def encode_value(name: str, value: int) -> int:
    bit = SIGN_BITS.get(name)
    if bit is not None and value < 0:
        return (1 << bit) | (-value)
    return value


class FeetechBus:
    """One serial port talking to a daisy chain of Feetech servos."""

    def __init__(self, transport: Transport, timeout: float = 0.02):
        self.transport = transport
        self.timeout = timeout

    @classmethod
    def open(cls, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 0.02) -> FeetechBus:
        import serial  # imported here so the pure-protocol helpers work without pyserial

        transport = serial.Serial(port, baudrate=baudrate, timeout=timeout, write_timeout=0.5)
        return cls(transport, timeout)

    @property
    def baudrate(self) -> int:
        return self.transport.baudrate

    def set_baudrate(self, baudrate: int) -> None:
        self.transport.baudrate = baudrate

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> FeetechBus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low level -----------------------------------------------------------------------

    def _read_exact(self, size: int, deadline: float) -> bytes:
        data = b""
        while len(data) < size:
            if time.perf_counter() > deadline:
                break
            chunk = self.transport.read(size - len(data))
            if chunk:
                data += chunk
            elif time.perf_counter() > deadline:
                break
        return data

    def transact(self, motor_id: int, instruction: int, params: bytes = b"") -> StatusPacket:
        self.transport.reset_input_buffer()
        start = time.perf_counter()
        self.transport.write(build_packet(motor_id, instruction, params))
        deadline = start + self.timeout

        # Find the header, skipping any noise before it.
        window = b""
        while window[-2:] != HEADER:
            byte = self._read_exact(1, deadline)
            if not byte:
                raise NoResponse(f"motor {motor_id}: no status packet")
            window = (window + byte)[-2:]

        head = self._read_exact(2, deadline)
        if len(head) < 2:
            raise CorruptResponse(f"motor {motor_id}: truncated status packet")
        reply_id, length = head[0], head[1]
        rest = self._read_exact(length, deadline)
        elapsed_ms = (time.perf_counter() - start) * 1000
        if len(rest) < length or length < 2:
            raise CorruptResponse(f"motor {motor_id}: truncated status packet")
        error, params_back, chk = rest[0], rest[1:-1], rest[-1]
        if checksum(bytes([reply_id, length, error]) + params_back) != chk:
            raise CorruptResponse(f"motor {motor_id}: bad checksum (two motors with the same ID?)")
        if reply_id != motor_id:
            raise CorruptResponse(f"asked motor {motor_id}, motor {reply_id} answered")
        return StatusPacket(reply_id, error, params_back, elapsed_ms)

    # -- public --------------------------------------------------------------------------

    def ping(self, motor_id: int) -> StatusPacket:
        return self.transact(motor_id, INST_PING)

    def read_raw(self, motor_id: int, address: int, size: int) -> int:
        packet = self.transact(motor_id, INST_READ, bytes([address, size]))
        if len(packet.params) != size:
            raise CorruptResponse(f"motor {motor_id}: expected {size} bytes, got {len(packet.params)}")
        return int.from_bytes(packet.params, "little")

    def read(self, motor_id: int, name: str) -> int:
        address, size = REGISTERS[name]
        return decode_value(name, self.read_raw(motor_id, address, size))

    def write(self, motor_id: int, name: str, value: int) -> StatusPacket:
        address, size = REGISTERS[name]
        raw = encode_value(name, value)
        if not 0 <= raw < (1 << (8 * size)):
            raise ValueError(f"{name}={value} does not fit in {size} byte(s)")
        return self.transact(motor_id, INST_WRITE, bytes([address]) + raw.to_bytes(size, "little"))
