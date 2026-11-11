"""US-006: run every read-only check in order and write a markdown report with fixes.

Follows the troubleshooting order: system -> USB port -> bus -> each joint -> power -> settings ->
bus reliability. Nothing here moves the arm or writes to the motors.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import lerobot_env, port_scanner
from .bus_tester import diagnose as diagnose_bus, scan_bus
from .feetech import DEFAULT_BAUDRATE, FeetechBus
from .findings import Finding, Severity, print_findings, worst
from .motor_ping import diagnose as diagnose_ping, ping_all, read_stress
from .power_diagnostics import check_power, monitor
from .settings_audit import audit

REPORT_DIR = Path(__file__).resolve().parent.parent / "diagnostics"


def run_all(port: str | None = None, monitor_seconds: float = 5.0) -> dict[str, list[Finding]]:
    sections: dict[str, list[Finding]] = {}
    sections["LeRobot install and calibration"] = lerobot_env.check_installs() + lerobot_env.check_calibration()
    ports, sections["USB port and Linux setup"] = port_scanner.scan()

    if port is None:
        if len(ports) == 1:
            port = ports[0].device
        elif len(ports) > 1:
            sections["Motor bus"] = [Finding(Severity.INFO, "Several ports found; pass --port to test one arm",
                                             ", ".join(p.device for p in ports))]
            return sections
        else:
            return sections

    try:
        bus = FeetechBus.open(port)
    except Exception as exc:  # pyserial raises SerialException / OSError variants
        sections["Motor bus"] = [Finding(
            Severity.ERROR, f"Cannot open {port}: {exc}",
            "'Device or resource busy' right after plug-in is ModemManager; otherwise another program has it open.",
            ["Close LeRobot/phosphobot; see the ModemManager fix above."])]
        return sections

    with bus:
        scan = scan_bus(bus, ids=range(0, 21))
        sections["Motor bus"] = diagnose_bus(scan)
        if not scan.at(DEFAULT_BAUDRATE):
            return sections
        bus.set_baudrate(DEFAULT_BAUDRATE)
        sections["Each joint"] = diagnose_ping(ping_all(bus))
        _, sections["Power"] = check_power(bus)
        sections["Power"] += monitor(bus, monitor_seconds)
        sections["Motor settings"] = audit(bus)
        sections["Bus reliability"] = read_stress(bus, 5.0)
    return sections


def to_markdown(sections: dict[str, list[Finding]], port: str | None) -> str:
    everything = [f for fs in sections.values() for f in fs]
    problems = sorted((f for f in everything if f.severity >= Severity.WARNING), key=lambda f: -f.severity)
    lines = [f"# SO-101 diagnostic report - {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Port: `{port or 'auto'}`  |  Overall: **{worst(everything).name}**", ""]
    lines += ["## Fix these first", ""]
    if not problems:
        lines.append("Nothing above INFO level. If the arm still jerks, see TROUBLESHOOTING.md section 3.")
    for i, f in enumerate(problems, 1):
        lines.append(f"{i}. **[{f.severity.name}] {f.title}**")
        if f.detail:
            lines += [f"   {line}" for line in f.detail.splitlines()]
        lines += [f"   - {step}" for step in f.fix]
    for title, findings in sections.items():
        lines += ["", f"## {title}", ""]
        lines += [f"- `{f.severity.name}` {f.title}" for f in findings]
    return "\n".join(lines) + "\n"


def write_report(sections: dict[str, list[Finding]], port: str | None) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    text = to_markdown(sections, port)
    path = REPORT_DIR / f"report-{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(text)
    (REPORT_DIR / "report.md").write_text(text)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="default: the only USB serial port found")
    parser.add_argument("--monitor", type=float, default=5.0, help="seconds to watch for drop-outs")
    args = parser.parse_args(argv)
    sections = run_all(args.port, args.monitor)
    for title, findings in sections.items():
        print(f"\n== {title}")
        print_findings(findings)
    path = write_report(sections, args.port)
    print(f"\nReport saved to {path}")
    return 0 if worst([f for fs in sections.values() for f in fs]) < Severity.ERROR else 1


if __name__ == "__main__":
    sys.exit(main())
