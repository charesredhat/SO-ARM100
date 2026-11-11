"""US-004: supply voltage, motor voltage variant, temperature, error flags, drop-outs and brownouts.

The Waveshare Bus Servo Adapter does not regulate: its DC jack feeds the servos directly. So the
voltage each servo reports is the supply voltage, and a 12 V adapter on 7.4 V motors (the standard
SO-101 kit) gives "Input voltage error" and silent motors.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass

from .feetech import CURRENT_UNIT_MA, DEFAULT_BAUDRATE, BusError, FeetechBus, decode_errors
from .findings import Finding, Severity, print_findings
from .so101 import JOINTS, classify_supply, joint_name

TWELVE_VOLT_LIMIT = 13.0  # 12 V motors report Max_Voltage_Limit 14.0 V; 7.4 V motors well below
BROWNOUT_7V4 = 4.8  # Present_Voltage while moving, 7.4 V motors on a 5 V supply
BROWNOUT_12V = 10.5


@dataclass
class MotorPower:
    motor_id: int
    volts: float
    min_limit: float
    max_limit: float
    temp_c: int
    max_temp_c: int
    status: int
    load: int
    current_ma: float

    @property
    def is_12v_motor(self) -> bool:
        return self.max_limit >= TWELVE_VOLT_LIMIT

    @property
    def variant(self) -> str:
        return "12 V motor" if self.is_12v_motor else "7.4 V motor"


def read_power(bus: FeetechBus, motor_id: int) -> MotorPower:
    return MotorPower(
        motor_id=motor_id,
        volts=bus.read(motor_id, "Present_Voltage") / 10,
        min_limit=bus.read(motor_id, "Min_Voltage_Limit") / 10,
        max_limit=bus.read(motor_id, "Max_Voltage_Limit") / 10,
        temp_c=bus.read(motor_id, "Present_Temperature"),
        max_temp_c=bus.read(motor_id, "Max_Temperature_Limit"),
        status=bus.read(motor_id, "Status"),
        load=bus.read(motor_id, "Present_Load"),
        current_ma=bus.read(motor_id, "Present_Current") * CURRENT_UNIT_MA,
    )


def check_power(bus: FeetechBus, ids: list[int] | None = None) -> tuple[list[MotorPower], list[Finding]]:
    readings: list[MotorPower] = []
    findings: list[Finding] = []
    for motor_id in ids or list(JOINTS):
        try:
            readings.append(read_power(bus, motor_id))
        except BusError as exc:
            findings.append(Finding(Severity.WARNING, f"ID {motor_id} ({joint_name(motor_id)}): no power data ({exc})"))
    if not readings:
        findings.append(Finding(Severity.ERROR, "No motor answered, so no voltage could be read",
                                fix=["Check the supply is on; run the bus scan (option 2)."]))
        return readings, findings

    for r in readings:
        name = f"ID {r.motor_id} ({joint_name(r.motor_id)})"
        findings.append(Finding(
            Severity.OK, f"{name}: {r.volts:.1f} V ({r.variant}, limits {r.min_limit:.1f}-{r.max_limit:.1f} V), "
                         f"{r.temp_c} C, {r.current_ma:.0f} mA"))
        if r.volts > r.max_limit:
            findings.append(Finding(
                Severity.CRITICAL, f"{name}: supply {r.volts:.1f} V is ABOVE this motor's {r.max_limit:.1f} V limit",
                "This is the classic SO-101 mistake: a 12 V adapter on 7.4 V motors. The motor raises "
                "'Input voltage error' and stops answering; long exposure can damage it.",
                ["Unplug the DC supply now.",
                 "Use the kit's 5 V supply (4 A or more per arm) for 7.4 V motors (C001/C044/C046).",
                 "The '9-12.6 V' printed on the Waveshare board is for Waveshare's own 12 V servos - ignore it."],
            ))
        elif r.volts < r.min_limit:
            findings.append(Finding(
                Severity.ERROR, f"{name}: supply {r.volts:.1f} V is below this motor's {r.min_limit:.1f} V limit",
                fix=["Check the DC jack is fully seated and the supply is rated for the arm (4 A+ at 5 V)."]))
        if r.temp_c >= r.max_temp_c - 10:
            findings.append(Finding(
                Severity.WARNING, f"{name}: {r.temp_c} C, close to its {r.max_temp_c} C cut-off",
                "Hot servos come from holding a load too long or a stalled joint (often the gripper squeezing an object).",
                ["Let it cool with torque off; check the joint moves freely by hand."]))
        if r.status:
            findings.append(Finding(Severity.ERROR, f"{name} reports: {', '.join(decode_errors(r.status))}",
                                    fix=["See TROUBLESHOOTING.md for each error."]))

    volts = [r.volts for r in readings]
    median = statistics.median(volts)
    supply = classify_supply(median)
    variants = {r.variant for r in readings}
    if len(variants) > 1:
        findings.append(Finding(
            Severity.ERROR, "This arm mixes 7.4 V and 12 V motors on one supply",
            ", ".join(f"ID {r.motor_id}: {r.variant}" for r in readings),
            ["Use one motor voltage per arm, each arm on its own matching supply."]))
    if supply is None:
        findings.append(Finding(Severity.WARNING, f"Supply reads {median:.1f} V, which matches no SO-101 supply type"))
    else:
        findings.append(Finding(Severity.INFO, f"Supply looks like a {supply.name} ({median:.1f} V)"))
        if supply.name == "5 V supply" and any(r.is_12v_motor for r in readings):
            findings.append(Finding(
                Severity.WARNING, "12 V motors are running from about 5 V",
                "They will answer but are very weak: lifts stall and the arm moves in small steps.",
                ["Use a 12 V supply rated 5 A or more for 12 V (C018) motors."]))
    if max(volts) - min(volts) > 0.5:
        findings.append(Finding(
            Severity.WARNING, f"Voltage differs {max(volts) - min(volts):.1f} V along the chain",
            "A drop along the daisy chain points to a poor connector or thin cable.",
            ["Reseat the 3-pin cables, starting where the voltage first drops."]))
    return readings, findings


def monitor(bus: FeetechBus, seconds: float = 10.0, ids: list[int] | None = None) -> list[Finding]:
    """Poll every motor for a while: catches intermittent drop-outs and voltage sag."""
    ids = ids or list(JOINTS)
    dropouts = {i: 0 for i in ids}
    polls = 0
    low: dict[int, float] = {}
    high: dict[int, float] = {}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        polls += 1
        for motor_id in ids:
            try:
                v = bus.read(motor_id, "Present_Voltage") / 10
            except BusError:
                dropouts[motor_id] += 1
                continue
            low[motor_id] = min(low.get(motor_id, v), v)
            high[motor_id] = max(high.get(motor_id, v), v)
        time.sleep(0.05)

    findings: list[Finding] = []
    for motor_id in ids:
        name = f"ID {motor_id} ({joint_name(motor_id)})"
        if dropouts[motor_id] == polls:
            findings.append(Finding(Severity.ERROR, f"{name}: never answered during the monitor"))
        elif dropouts[motor_id]:
            findings.append(Finding(
                Severity.ERROR, f"{name}: dropped out {dropouts[motor_id]} of {polls} times",
                "The motor disappears and comes back: a brownout reset or a loose cable.",
                ["Watch the motor LEDs - a blink means the motor reset.", "Reseat or replace its cables."]))
        elif motor_id in low and high[motor_id] - low[motor_id] >= 0.3:
            findings.append(Finding(Severity.WARNING, f"{name}: voltage wandered {low[motor_id]:.1f}-{high[motor_id]:.1f} V while idle",
                                    fix=["Check the supply plug and the DC jack on the board."]))
        elif motor_id in low:
            findings.append(Finding(Severity.OK, f"{name}: steady, {low[motor_id]:.1f}-{high[motor_id]:.1f} V over {polls} polls"))
    return findings


def load_test(bus: FeetechBus, motor_id: int, steps: int = 150, seconds: float = 3.0) -> list[Finding]:
    """Move ONE joint a little either side of where it is and watch the supply sag.

    Torque is switched on only for this joint and switched off again afterwards. 150 steps is
    about 13 degrees on a 4096-step servo.
    """
    name = f"ID {motor_id} ({joint_name(motor_id)})"
    start = bus.read(motor_id, "Present_Position")
    is_12v = bus.read(motor_id, "Max_Voltage_Limit") / 10 >= TWELVE_VOLT_LIMIT
    threshold = BROWNOUT_12V if is_12v else BROWNOUT_7V4
    idle = bus.read(motor_id, "Present_Voltage") / 10
    lowest = idle
    errors: set[str] = set()
    dropouts = 0
    try:
        bus.write(motor_id, "Torque_Enable", 1)
        end = time.monotonic() + seconds
        direction = 1
        while time.monotonic() < end:
            try:
                bus.write(motor_id, "Goal_Position", max(0, min(4095, start + direction * steps)))
            except BusError:
                dropouts += 1
            direction = -direction
            leg_end = time.monotonic() + 0.5
            while time.monotonic() < leg_end:
                try:
                    lowest = min(lowest, bus.read(motor_id, "Present_Voltage") / 10)
                    errors.update(decode_errors(bus.read(motor_id, "Status")))
                except BusError:
                    dropouts += 1
        try:
            bus.write(motor_id, "Goal_Position", start)
            time.sleep(0.5)
        except BusError:
            dropouts += 1
    finally:
        try:
            bus.write(motor_id, "Torque_Enable", 0)
        except BusError:
            pass

    findings = [Finding(Severity.INFO, f"{name}: idle {idle:.1f} V, lowest while moving {lowest:.1f} V")]
    if dropouts:
        findings.append(Finding(
            Severity.CRITICAL, f"{name}: stopped answering {dropouts} times while moving",
            "The motor resets when it draws current: a brownout. This is the 'twitches, stops, twitches' "
            "behaviour of an undersized or USB-only supply.",
            ["Use the kit's 5 V supply rated 4 A or more per arm (12 V 5 A+ for 12 V motors)."]))
    elif lowest < threshold:
        findings.append(Finding(
            Severity.ERROR, f"{name}: supply sagged to {lowest:.1f} V (below {threshold} V) while moving",
            "The supply cannot deliver the current; with several joints moving it will brown out.",
            ["Use a stronger supply (4 A+ at 5 V per arm for 7.4 V motors); check the barrel plug."]))
    else:
        findings.append(Finding(Severity.OK, f"{name}: supply held up under load"))
    if errors:
        findings.append(Finding(Severity.ERROR, f"{name} flagged while moving: {', '.join(sorted(errors))}"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--monitor", type=float, default=0, help="also poll for this many seconds")
    args = parser.parse_args(argv)
    with FeetechBus.open(args.port, args.baudrate) as bus:
        _, findings = check_power(bus)
        if args.monitor:
            findings += monitor(bus, args.monitor)
    print_findings(findings)
    return 0 if all(f.severity < Severity.ERROR for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
