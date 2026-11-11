"""US-005: give one motor its SO-101 ID and baudrate, and write LeRobot's settings to the arm.

These write to the motors' EEPROM. LeRobot's own `lerobot-setup-motors` does the same ID/baud step
and is the first choice; this exists for when it cannot find a motor (wrong baudrate, unknown ID).
A factory reset is deliberately not offered: the Feetech reset instruction also clears the ID
and baudrate, which is exactly what then needs redoing.
"""

from __future__ import annotations

import argparse
import sys

from .bus_tester import scan_bus
from .feetech import BAUD_CODES, DEFAULT_BAUDRATE, MAX_ID, BusError, FeetechBus
from .findings import Finding, Severity, print_findings
from .so101 import FOLLOWER_PID, GRIPPER_LIMITS, JOINTS, joint_name


class SetupError(Exception):
    pass


def find_single_motor(bus: FeetechBus, ids: range | None = None) -> tuple[int, int]:
    """Return (id, baudrate) of the only motor on the bus; refuse if there are several."""
    result = scan_bus(bus, ids=ids or range(0, MAX_ID + 1))
    if result.corrupt:
        raise SetupError("Garbled replies: several motors share an ID. Connect ONE motor to the board.")
    if not result.motors:
        raise SetupError("No motor found at any baudrate. Check the supply and the 3-pin cable.")
    if len(result.motors) > 1:
        found = ", ".join(f"ID {m.motor_id} @ {m.baudrate}" for m in result.motors)
        raise SetupError(f"More than one motor answers ({found}). Connect ONE motor to the board.")
    motor = result.motors[0]
    return motor.motor_id, motor.baudrate


def set_id_and_baud(bus: FeetechBus, new_id: int, new_baud: int = DEFAULT_BAUDRATE,
                    search_ids: range | None = None) -> list[Finding]:
    if not 0 <= new_id <= MAX_ID:
        raise SetupError(f"ID must be 0-{MAX_ID}")
    if new_baud not in BAUD_CODES:
        raise SetupError(f"Baudrate must be one of {sorted(BAUD_CODES)}")
    old_id, old_baud = find_single_motor(bus, search_ids)
    bus.set_baudrate(old_baud)
    bus.write(old_id, "Torque_Enable", 0)
    bus.write(old_id, "Lock", 0)
    # The acknowledgement of an ID or baud change may come from the new ID or at the new baudrate,
    # so verify with a ping instead of trusting the reply.
    try:
        bus.write(old_id, "ID", new_id)
    except BusError:
        pass
    try:
        bus.ping(new_id)
    except BusError as exc:
        raise SetupError(f"Motor did not answer as ID {new_id} after the ID change ({exc})") from exc
    try:
        bus.write(new_id, "Baud_Rate", BAUD_CODES[new_baud])
    except BusError:
        pass
    bus.set_baudrate(new_baud)
    try:
        bus.ping(new_id)
    except BusError as exc:
        raise SetupError(f"Motor did not answer at {new_baud} baud after the change ({exc})") from exc
    bus.write(new_id, "Lock", 1)
    return [Finding(Severity.OK, f"Motor ID {old_id} @ {old_baud} is now ID {new_id} ({joint_name(new_id)}) @ {new_baud}")]


def configure(bus: FeetechBus, ids: list[int] | None = None) -> list[Finding]:
    """Write what LeRobot's SO follower writes on connect: position mode, PID 16/0/32, gripper limits.

    Torque is left OFF so the arm cannot move when this finishes.
    """
    findings: list[Finding] = []
    for motor_id in ids or list(JOINTS):
        name = f"ID {motor_id} ({joint_name(motor_id)})"
        try:
            bus.write(motor_id, "Torque_Enable", 0)
            bus.write(motor_id, "Lock", 0)
            bus.write(motor_id, "Operating_Mode", 0)
            for register, value in FOLLOWER_PID.items():
                bus.write(motor_id, register, value)
            bus.write(motor_id, "Return_Delay_Time", 0)
            if motor_id == 6:
                for register, value in GRIPPER_LIMITS.items():
                    bus.write(motor_id, register, value)
            phase = bus.read(motor_id, "Phase")
            if phase & 0x10:
                bus.write(motor_id, "Phase", phase & ~0x10)
            bus.write(motor_id, "Lock", 1)
        except BusError as exc:
            findings.append(Finding(Severity.ERROR, f"{name}: could not configure ({exc})"))
            continue
        findings.append(Finding(Severity.OK, f"{name}: position mode, PID 16/0/32" + (", gripper limits" if motor_id == 6 else "")))
    return findings


def confirm(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set-id", help="ONE motor connected: set its ID (1-6) and baudrate")
    s.add_argument("new_id", type=int)
    s.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    s.add_argument("--quick", action="store_true", help="only search IDs 0-20")
    sub.add_parser("configure", help="write LeRobot's settings to IDs 1-6")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)

    with FeetechBus.open(args.port) as bus:
        try:
            if args.cmd == "set-id":
                if not args.yes and not confirm(f"Write ID {args.new_id} and {args.baudrate} baud to the ONE connected motor?"):
                    return 1
                findings = set_id_and_baud(bus, args.new_id, args.baudrate, range(0, 21) if args.quick else None)
            else:
                if not args.yes and not confirm("Write position mode and PID 16/0/32 to motors 1-6 (torque left off)?"):
                    return 1
                findings = configure(bus)
        except SetupError as exc:
            findings = [Finding(Severity.ERROR, str(exc))]
    print_findings(findings)
    return 0 if all(f.severity < Severity.ERROR for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
