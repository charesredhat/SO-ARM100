"""US-007: interactive menu for debugging an SO-100/SO-101 arm.  Run:  python -m src.cli"""

from __future__ import annotations

import sys
from collections.abc import Callable

from . import lerobot_env, port_scanner
from .bus_tester import diagnose as diagnose_bus, scan_bus
from .feetech import DEFAULT_BAUDRATE, FeetechBus
from .findings import Finding, Severity, print_findings
from .motor_ping import diagnose as diagnose_ping, ping_all, read_stress
from .motor_reset import SetupError, configure, set_id_and_baud
from .power_diagnostics import check_power, load_test, monitor
from .report_generator import run_all, write_report
from .settings_audit import audit
from .so101 import JOINTS

MENU = """
SO-101 servo debugger                              port: {port}
  1  Check Linux setup and USB port (dialout, ModemManager, brltty)
  2  Scan the bus at every baudrate
  3  Ping each joint (finds breaks in the cable chain)
  4  Power: supply voltage, motor voltage variant, temperature, errors
  5  Power monitor / load test (drop-outs, brownouts)
  6  Set ID and baudrate of ONE motor              (writes EEPROM)
  7  Write LeRobot's settings to all joints        (writes EEPROM)
  8  Motor settings audit (PID, torque limit, mode)
  9  Bus reliability test (teleop-style reads)
 10  LeRobot version and calibration files
 11  Full diagnostic report (read-only, saved to diagnostics/)
  p  Choose port        0  Quit
"""


def ask(prompt: str, default: str = "") -> str:
    answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return answer or default


def yes(prompt: str) -> bool:
    return ask(f"{prompt} (y/N)").lower() in ("y", "yes")


def choose_port(current: str | None) -> str | None:
    ports, _ = port_scanner.scan()
    if not ports:
        print("No USB serial ports found - run option 1 for the reason.")
        return current
    for i, p in enumerate(ports, 1):
        names = port_scanner.stable_names(p.device)
        print(f"  {i}  {p.device}  {p.adapter or p.description}  {names[0] if names else ''}")
    pick = ask("Port number", "1")
    if pick.isdigit() and 1 <= int(pick) <= len(ports):
        chosen = ports[int(pick) - 1]
        names = port_scanner.stable_names(chosen.device)
        return names[0] if names else chosen.device
    return current


def with_bus(port: str | None, action: Callable[[FeetechBus], list[Finding]]) -> None:
    if port is None:
        print("Choose a port first (p).")
        return
    try:
        with FeetechBus.open(port, DEFAULT_BAUDRATE) as bus:
            print_findings(action(bus))
    except SetupError as exc:
        print_findings([Finding(Severity.ERROR, str(exc))])
    except Exception as exc:  # serial errors: busy port, unplugged board
        print_findings([Finding(Severity.ERROR, f"Bus error on {port}: {exc}",
                                fix=["Run option 1; close other programs using the port."])])


def menu_load_test(bus: FeetechBus) -> list[Finding]:
    joint = int(ask("Joint ID to move a little (1-6)", "3"))
    print(f"This switches torque on for joint {joint} only and moves it about 13 degrees either way for 3 s.")
    print("Keep hands and objects clear; make sure the arm is resting safely.")
    if not yes("Go ahead?"):
        return []
    return load_test(bus, joint)


def main() -> int:
    ports = port_scanner.list_ports() if not port_scanner.in_flatpak() else []
    port: str | None = None
    if len(ports) == 1:
        names = port_scanner.stable_names(ports[0].device)
        port = names[0] if names else ports[0].device
    while True:
        print(MENU.format(port=port or "not chosen"))
        try:
            choice = ask("Choose").lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "0":
            return 0
        if choice == "p":
            port = choose_port(port)
        elif choice == "1":
            print_findings(port_scanner.scan()[1])
        elif choice == "2":
            full = yes("Scan all IDs 0-252? (slow; default 0-20)")
            with_bus(port, lambda b: diagnose_bus(scan_bus(b, ids=None if full else range(0, 21))))
        elif choice == "3":
            with_bus(port, lambda b: diagnose_ping(ping_all(b)))
        elif choice == "4":
            with_bus(port, lambda b: check_power(b)[1])
        elif choice == "5":
            if yes("Run the load test (moves one joint)? No = idle monitor only"):
                with_bus(port, menu_load_test)
            else:
                seconds = float(ask("Seconds to monitor", "15"))
                with_bus(port, lambda b: monitor(b, seconds))
        elif choice == "6":
            print("Connect ONLY the motor to set to the board (nothing daisy-chained), with power on.")
            new_id = int(ask(f"New ID ({', '.join(f'{i}={n}' for i, n in JOINTS.items())})"))
            if yes(f"Write ID {new_id} and {DEFAULT_BAUDRATE} baud to that motor?"):
                with_bus(port, lambda b: set_id_and_baud(b, new_id))
        elif choice == "7":
            if yes("Write position mode, PID 16/0/32 and gripper limits to IDs 1-6 (torque left off)?"):
                with_bus(port, configure)
        elif choice == "8":
            with_bus(port, audit)
        elif choice == "9":
            with_bus(port, lambda b: read_stress(b, float(ask("Seconds", "10"))))
        elif choice == "10":
            print_findings(lerobot_env.check_installs() + lerobot_env.check_calibration())
        elif choice == "11":
            sections = run_all(port)
            for title, findings in sections.items():
                print(f"\n== {title}")
                print_findings(findings)
            print(f"\nReport saved to {write_report(sections, port)}")
        else:
            print("Unknown choice.")


if __name__ == "__main__":
    sys.exit(main())
