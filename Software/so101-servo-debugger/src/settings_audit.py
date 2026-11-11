"""Read each motor's stored settings and flag the ones known to cause buzzing or jerky motion."""

from __future__ import annotations

import argparse
import sys

from .feetech import DEFAULT_BAUDRATE, BusError, FeetechBus
from .findings import Finding, Severity, print_findings
from .so101 import FOLLOWER_PID, GRIPPER_LIMITS, JOINTS, joint_name

SETTINGS = ["Operating_Mode", "P_Coefficient", "I_Coefficient", "D_Coefficient", "Max_Torque_Limit",
            "Return_Delay_Time", "Min_Position_Limit", "Max_Position_Limit", "Phase", "Torque_Enable"]


def read_settings(bus: FeetechBus, motor_id: int) -> dict[str, int]:
    return {name: bus.read(motor_id, name) for name in SETTINGS}


def audit(bus: FeetechBus, ids: list[int] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for motor_id in ids or list(JOINTS):
        name = f"ID {motor_id} ({joint_name(motor_id)})"
        try:
            s = read_settings(bus, motor_id)
        except BusError as exc:
            findings.append(Finding(Severity.WARNING, f"{name}: settings unreadable ({exc})"))
            continue
        problems: list[Finding] = []
        if s["Operating_Mode"] != 0:
            problems.append(Finding(Severity.ERROR, f"{name}: Operating_Mode {s['Operating_Mode']}, not 0 (position)",
                                    "In velocity/PWM/step mode the joint will not hold positions.",
                                    ["LeRobot sets position mode on connect; or use this tool's option 7."]))
        pid = {k: s[k] for k in FOLLOWER_PID}
        if pid != FOLLOWER_PID:
            problems.append(Finding(
                Severity.WARNING, f"{name}: PID {pid['P_Coefficient']}/{pid['I_Coefficient']}/{pid['D_Coefficient']}, "
                                  f"LeRobot uses 16/0/32",
                "The Feetech default P=32 makes joints shake; non-default values make them buzz or lag. "
                "LeRobot rewrites these on every connect, so this only matters outside LeRobot.",
                ["Leave --robot.position_p_coefficient at 16; option 7 writes 16/0/32."]))
        if motor_id == 6:
            if s["Max_Torque_Limit"] > GRIPPER_LIMITS["Max_Torque_Limit"]:
                problems.append(Finding(
                    Severity.WARNING, f"{name}: gripper torque limit {s['Max_Torque_Limit']} (LeRobot caps it at 500)",
                    "A full-torque gripper squeezing an object is the usual way gripper servos die.",
                    ["Option 7 writes LeRobot's gripper limits (torque 500, current 250, overload 25)."]))
        elif s["Max_Torque_Limit"] < 800:
            problems.append(Finding(
                Severity.WARNING, f"{name}: Max_Torque_Limit {s['Max_Torque_Limit']} of 1000",
                "The torque limit acts like a power cap: a low value makes lifts slow and stepwise.",
                ["Raise it to 1000 unless you lowered it on purpose."]))
        if s["Return_Delay_Time"] > 0:
            problems.append(Finding(
                Severity.INFO, f"{name}: Return_Delay_Time {s['Return_Delay_Time']} (LeRobot uses 0)",
                "Each reply waits longer, which slows the control loop.",
                ["LeRobot's configure step resets it to 0."]))
        if s["Phase"] & 0x10:
            problems.append(Finding(
                Severity.INFO, f"{name}: Phase bit 4 set",
                "LeRobot clears this so positions stay within 0-4095; older builds left it set.",
                ["Reconnecting with LeRobot 0.7 clears it."]))
        if s["Min_Position_Limit"] >= s["Max_Position_Limit"] and (s["Min_Position_Limit"], s["Max_Position_Limit"]) != (0, 0):
            problems.append(Finding(Severity.ERROR, f"{name}: position limits {s['Min_Position_Limit']}-{s['Max_Position_Limit']} are inverted",
                                    fix=["Recalibrate with lerobot-calibrate (it rewrites the limits)."]))
        findings += problems or [Finding(Severity.OK, f"{name}: settings look right")]
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    args = parser.parse_args(argv)
    with FeetechBus.open(args.port, args.baudrate) as bus:
        findings = audit(bus)
    print_findings(findings)
    return 0 if all(f.severity < Severity.ERROR for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
