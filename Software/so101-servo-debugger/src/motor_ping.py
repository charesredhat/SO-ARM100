"""US-003: ping each SO-101 joint repeatedly, time the replies and locate breaks in the chain."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

from .feetech import DEFAULT_BAUDRATE, BusError, CorruptResponse, FeetechBus
from .findings import Finding, Severity, print_findings
from .so101 import JOINTS, joint_name


@dataclass
class PingStats:
    motor_id: int
    sent: int = 0
    ok: int = 0
    corrupt: int = 0
    times_ms: list[float] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.ok / self.sent if self.sent else 0.0


def ping_all(bus: FeetechBus, ids: list[int] | None = None, repeats: int = 5) -> dict[int, PingStats]:
    stats = {i: PingStats(i) for i in ids or list(JOINTS)}
    for _ in range(repeats):
        for motor_id, s in stats.items():
            s.sent += 1
            try:
                packet = bus.ping(motor_id)
            except CorruptResponse:
                s.corrupt += 1
                continue
            except BusError:
                continue
            s.ok += 1
            s.times_ms.append(packet.elapsed_ms)
    return stats


def diagnose(stats: dict[int, PingStats]) -> list[Finding]:
    findings: list[Finding] = []
    dead = [i for i, s in stats.items() if s.ok == 0]
    for motor_id, s in stats.items():
        name = f"ID {motor_id} ({joint_name(motor_id)})"
        if s.ok == s.sent:
            avg = sum(s.times_ms) / len(s.times_ms)
            findings.append(Finding(Severity.OK, f"{name}: {s.ok}/{s.sent} replies, avg {avg:.1f} ms, max {max(s.times_ms):.1f} ms"))
        elif s.ok:
            findings.append(Finding(
                Severity.ERROR, f"{name}: intermittent, {s.ok}/{s.sent} replies",
                "A motor that answers only sometimes almost always has a loose or damaged 3-pin cable, "
                "or a supply that sags.",
                [f"Reseat both cables at motor {motor_id}; swap in a spare cable.", "Run the power monitor (option 5)."],
            ))
        elif s.corrupt:
            findings.append(Finding(Severity.ERROR, f"{name}: only garbled replies",
                                    "Two motors probably share this ID.",
                                    ["Set unique IDs one motor at a time (lerobot-setup-motors or option 6)."]))

    if dead:
        first = min(dead)
        beyond = [i for i in stats if i >= first]
        if dead == beyond and first > min(stats):
            prev = first - 1
            findings.append(Finding(
                Severity.ERROR, f"Chain breaks after motor {prev}: IDs {dead} do not answer",
                f"Everything from motor {first} ({joint_name(first)}) onwards is silent, so the signal or power "
                f"stops between motor {prev} and motor {first}.",
                [f"Check the cable from motor {prev} to motor {first} (both ends) and swap it.",
                 f"Each motor passes power to the next: a failed motor {first} or its socket can also cut off the rest.",
                 f"Test motor {first} alone on the board to rule it out."],
            ))
        elif dead == list(stats):
            findings.append(Finding(
                Severity.ERROR, "No joint answers",
                "Nothing on the chain replies at 1 Mbps.",
                ["Check servo power and the board-to-motor-1 cable.",
                 "Run the bus scan (option 2): the motors may use another baudrate or the factory ID 1."],
            ))
        else:
            for motor_id in dead:
                findings.append(Finding(
                    Severity.ERROR, f"ID {motor_id} ({joint_name(motor_id)}) does not answer, but motors after it do",
                    "The chain is intact, so this motor has the wrong ID, a wrong baudrate, or is faulty.",
                    ["Run the bus scan (option 2) to see if it answers under another ID/baudrate.",
                     f"Connect motor {motor_id} alone and set its ID (lerobot-setup-motors or option 6)."],
                ))
    return findings


def read_stress(bus: FeetechBus, seconds: float = 5.0, ids: list[int] | None = None) -> list[Finding]:
    """Read every joint's position back to back, like a teleop loop, and count failed cycles.

    Older LeRobot crashed on a single failed read ("Failed to sync read 'Present_Position'"); a few
    failures per thousand is normal, more means cabling, power or USB trouble.
    """
    ids = ids or list(JOINTS)
    cycles = failed_cycles = 0
    per_motor = {i: 0 for i in ids}
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        cycles += 1
        bad = False
        for motor_id in ids:
            try:
                bus.read(motor_id, "Present_Position")
            except BusError:
                per_motor[motor_id] += 1
                bad = True
        failed_cycles += bad
    elapsed = time.monotonic() - start
    rate = cycles / elapsed if elapsed else 0.0
    share = failed_cycles / cycles if cycles else 1.0
    worst_ids = [i for i, n in per_motor.items() if n]
    summary = f"{cycles} full-arm reads in {elapsed:.1f} s ({rate:.0f} Hz), {failed_cycles} with an error ({share:.1%})"
    if share == 0:
        return [Finding(Severity.OK, summary)]
    detail = "Errors per joint: " + ", ".join(f"{joint_name(i)} {per_motor[i]}" for i in worst_ids)
    if share < 0.005:
        return [Finding(Severity.INFO, summary, detail + "\nLeRobot 0.6.1+ retries reads like these, so they are harmless there.")]
    return [Finding(
        Severity.ERROR, summary, detail,
        ["Reseat the cables at the joints listed; replace any with bent pins.",
         "Use a short, good USB cable straight into the PC (no hub).",
         "Make sure LeRobot is 0.6.1 or newer (it retries failed reads) and try --fps=30.",
         "Check the supply under load (option 5 load test)."],
    )]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args(argv)
    with FeetechBus.open(args.port, args.baudrate) as bus:
        stats = ping_all(bus, repeats=args.repeats)
    findings = diagnose(stats)
    print_findings(findings)
    return 0 if all(f.severity < Severity.ERROR for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
