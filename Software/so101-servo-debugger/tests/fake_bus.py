"""A simulated Feetech bus: servos with memory tables, baudrates, locks, faults and brownouts."""

from __future__ import annotations

from src.feetech import BAUD_CODES, INST_PING, INST_READ, INST_WRITE, REGISTERS, checksum

CODE_TO_BAUD = {code: baud for baud, code in BAUD_CODES.items()}


def _put(mem: bytearray, name: str, value: int) -> None:
    address, size = REGISTERS[name]
    mem[address:address + size] = value.to_bytes(size, "little")


class FakeServo:
    def __init__(self, motor_id: int, baudrate: int = 1_000_000, volts: float = 5.0, twelve_volt: bool = False,
                 load_volts: float | None = None, error: int = 0, answer_every: int = 1):
        self.mem = bytearray(256)
        _put(self.mem, "Model_Number", 777)
        _put(self.mem, "ID", motor_id)
        _put(self.mem, "Baud_Rate", BAUD_CODES[baudrate])
        _put(self.mem, "Max_Voltage_Limit", 140 if twelve_volt else 80)
        _put(self.mem, "Min_Voltage_Limit", 40)
        _put(self.mem, "Max_Temperature_Limit", 70)
        _put(self.mem, "Present_Temperature", 31)
        _put(self.mem, "P_Coefficient", 16)
        _put(self.mem, "D_Coefficient", 32)
        _put(self.mem, "Max_Torque_Limit", 1000)
        _put(self.mem, "Max_Position_Limit", 4095)
        _put(self.mem, "Present_Position", 2048)
        _put(self.mem, "Lock", 1)
        self.volts = volts
        self.load_volts = load_volts
        self.error = error
        self.answer_every = answer_every  # 1 = always, 3 = every third request
        self.requests = 0
        _put(self.mem, "Present_Voltage", round(volts * 10))

    @property
    def motor_id(self) -> int:
        return self.mem[5]

    @property
    def baudrate(self) -> int:
        return CODE_TO_BAUD[self.mem[6]]

    def handle(self, instruction: int, params: bytes) -> tuple[int, bytes] | None:
        """Return (reply id, reply params) or None for no answer."""
        self.requests += 1
        if self.requests % self.answer_every:
            return None
        torque_on = self.mem[REGISTERS["Torque_Enable"][0]] == 1
        volts = self.load_volts if torque_on and self.load_volts is not None else self.volts
        if volts < 3.5:
            return None  # browned out: the motor resets and says nothing
        _put(self.mem, "Present_Voltage", round(volts * 10))
        if instruction == INST_PING:
            return self.motor_id, b""
        if instruction == INST_READ:
            address, size = params[0], params[1]
            return self.motor_id, bytes(self.mem[address:address + size])
        if instruction == INST_WRITE:
            address, data = params[0], params[1:]
            locked = self.mem[REGISTERS["Lock"][0]] == 1
            if address < 40 and locked:
                return self.motor_id, b""  # EEPROM is write-protected while locked
            self.mem[address:address + len(data)] = data
            return self.motor_id, b""  # after an ID change this is already the new ID
        return None


class FakeTransport:
    def __init__(self, servos: list[FakeServo], baudrate: int = 1_000_000):
        self.servos = servos
        self.baudrate = baudrate
        self.rx = b""
        self.writes: list[bytes] = []

    def reset_input_buffer(self) -> None:
        self.rx = b""

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        assert data[:2] == b"\xff\xff"
        motor_id, length, instruction = data[2], data[3], data[4]
        params = data[5:5 + length - 2]
        assert checksum(data[2:-1]) == data[-1], "tool sent a bad checksum"
        replies = []
        for servo in self.servos:
            if servo.motor_id == motor_id and servo.baudrate == self.baudrate:
                reply = servo.handle(instruction, params)
                if reply is not None:
                    replies.append(self._packet(reply[0], servo.error, reply[1]))
        if len(replies) == 1:
            self.rx += replies[0]
        elif len(replies) > 1:  # two motors talking at once: bytes collide
            merged = bytearray(replies[0])
            for other in replies[1:]:
                for i, b in enumerate(other[: len(merged)]):
                    merged[i] |= b ^ 0x5A if i > 1 else b
            self.rx += bytes(merged)
        return len(data)

    @staticmethod
    def _packet(motor_id: int, error: int, params: bytes) -> bytes:
        body = bytes([motor_id, len(params) + 2, error]) + params
        return b"\xff\xff" + body + bytes([checksum(body)])

    def read(self, size: int = 1) -> bytes:
        out, self.rx = self.rx[:size], self.rx[size:]
        return out


def arm(**kwargs: object) -> list[FakeServo]:
    """Six correctly set-up SO-101 follower servos."""
    return [FakeServo(i, **kwargs) for i in range(1, 7)]  # type: ignore[arg-type]
