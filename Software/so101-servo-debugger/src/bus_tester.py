"""US-002: scan the bus at every baudrate and report which motor IDs answer."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from .feetech import BAUD_CODES, DEFAULT_BAUDRATE, MAX_ID, STS3215_MODEL_NUMBER, BusError, CorruptResponse, FeetechBus
from .findings import Finding, Severity, print_findings
from .so101 import JOINTS, joint_name


@dataclass
class FoundMotor:
    motor_id: int
    baudrate: int
    model: int | None
    errors: list[str]


@dataclass
class ScanResult:
    motors: list[FoundMotor] = field(default_factory=list)
    corrupt: dict[int, set[int]] = field(default_factory=dict)  # baudrate -> ids that gave garbled replies

    def at(self, baudrate: int) -> list[FoundMotor]:
        return [m for m in self.motors if m.baudrate == baudrate]


def scan_bus(bus: FeetechBus, baudrates: list[int] | None = None, ids: range | None = None) -> ScanResult:
    result = ScanResult()
    for baud in baudrates or list(BAUD_CODES):
        bus.set_baudrate(baud)
        for motor_id in ids or range(0, MAX_ID + 1):
            try:
                packet = bus.ping(motor_id)
            except CorruptResponse:
                result.corrupt.setdefault(baud, set()).add(motor_id)
                continue
            except BusError:
                continue
            try:
                model: int | None = bus.read(motor_id, "Model_Number")
            except BusError:
                model = None
            result.motors.append(FoundMotor(motor_id, baud, model, packet.errors))
    bus.set_baudrate(DEFAULT_BAUDRATE)
    return result


def diagnose(result: ScanResult) -> list[Finding]:
    findings: list[Finding] = []
    if not result.motors and not result.corrupt:
        return [Finding(
            Severity.ERROR, "No motor answered at any baudrate",
            "The USB side works, but nothing on the servo bus replies.",
            ["Check the servo power supply is plugged in and switched on (the USB cable does not power servos).",
             "Check the 3-pin cable from the board to motor 1 (shoulder pan) is fully seated.",
             "Waveshare board: both jumpers on B (USB).",
             "Test one motor alone on the board: motors that were never set up all answer as ID 1.",
             "Close anything else using the port (LeRobot, phosphobot, another debugger run)."],
        )]

    for motor in result.motors:
        model = "STS3215" if motor.model == STS3215_MODEL_NUMBER else f"model {motor.model}"
        findings.append(Finding(Severity.OK, f"ID {motor.motor_id} ({joint_name(motor.motor_id)}) answered at {motor.baudrate} baud, {model}"))
        if motor.errors:
            findings.append(Finding(Severity.WARNING, f"ID {motor.motor_id} reports: {', '.join(motor.errors)}",
                                    fix=["Run the power check (option 4) for details."]))

    other_bauds = sorted({m.baudrate for m in result.motors if m.baudrate != DEFAULT_BAUDRATE})
    if other_bauds:
        ids = sorted({m.motor_id for m in result.motors if m.baudrate != DEFAULT_BAUDRATE})
        findings.append(Finding(
            Severity.ERROR, f"Motor(s) {ids} use {other_bauds} baud; LeRobot expects {DEFAULT_BAUDRATE}",
            "Usually motors taken from another robot, or a setup run that stopped half-way.",
            ["Connect one motor at a time and run: lerobot-setup-motors --robot.type=so101_follower --robot.port=<port>",
             "or this tool's option 6 (set ID and baudrate)."],
        ))

    default_ids = {m.motor_id for m in result.at(DEFAULT_BAUDRATE)}
    if default_ids == {1} and not result.corrupt:
        findings.append(Finding(
            Severity.WARNING, "Only ID 1 answers",
            "Either just motor 1 is connected, or the motors still have their factory ID (every new STS3215 is ID 1).",
            ["If more motors are chained, set unique IDs one motor at a time (lerobot-setup-motors or option 6)."],
        ))

    for baud, garbled in result.corrupt.items():
        findings.append(Finding(
            Severity.ERROR, f"Garbled replies from ID(s) {sorted(garbled)} at {baud} baud",
            "Two or more motors answering to the same ID corrupt each other's reply.",
            ["Disconnect all but one motor, give each a unique ID (lerobot-setup-motors), then re-chain them."],
        ))

    missing = [i for i in JOINTS if i not in default_ids]
    if default_ids and missing and default_ids != {1}:
        findings.append(Finding(Severity.WARNING, f"SO-101 joint IDs missing at {DEFAULT_BAUDRATE} baud: {missing}",
                                fix=["Run the ping test (option 3) to locate the break in the chain."]))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--full", action="store_true", help="scan IDs 0-252 (slow); default 0-20")
    args = parser.parse_args(argv)
    with FeetechBus.open(args.port) as bus:
        result = scan_bus(bus, ids=None if args.full else range(0, 21))
    findings = diagnose(result)
    print_findings(findings)
    return 0 if all(f.severity < Severity.ERROR for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
