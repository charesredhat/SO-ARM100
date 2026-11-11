# SO-ARM100 / SO-101 servo debugger

Diagnoses the two problems this arm has had: **servos that do not respond** and **small, jerky
movements**. It checks the Linux side (permissions, ModemManager, brltty, port names), the servo
bus (IDs, baudrates, broken cable chain), the power supply (wrong voltage, brownouts), the servo
settings LeRobot depends on, and the LeRobot install and calibration.

It talks to the Feetech STS3215 servos directly over the serial port, so it needs only
`pyserial` and keeps working when LeRobot reorganises its motor code (it did three times in 2025-26).
Use it next to LeRobot, not instead of it: LeRobot still does setup, calibration and teleop.

**To fix the arm, start with [TROUBLESHOOTING.md](TROUBLESHOOTING.md).**

| File | What it is |
|------|------------|
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Step-by-step fix for this arm's past problems, using this tool |
| [SO-ARM101-Nobara-rebuild-checklist.md](SO-ARM101-Nobara-rebuild-checklist.md) | Full clean rebuild on Nobara with the Waveshare adapter (research from Nov 2026) |
| [SO-ARM101-research-notes.md](SO-ARM101-research-notes.md) | Background: hardware variants, LeRobot versions, ranked causes of jerky motion, sources |
| [RESEARCH.md](RESEARCH.md) | Protocol and LeRobot API notes used to build the debugger |
| `prd.json`, `progress.txt`, `setup-ralph.sh` | The Ralph agent plan this tool started from |

## Quick start

```bash
cd Software/so101-servo-debugger
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m src.cli                    # interactive menu
.venv/bin/python -m src.report_generator       # every read-only check, saved to diagnostics/report.md
```

Run it from a host terminal (Konsole). The VS Code Flatpak sandbox cannot see `/dev/ttyACM*`, so
the arm looks unplugged from there (the tool warns about this). Close LeRobot first: only one
program can hold the port.

## What each check catches

| Menu | Module | Catches |
|------|--------|---------|
| 1 | `port_scanner` | Not in `dialout`; ModemManager probing `/dev/ttyACM*`; brltty rules claiming WCH (1a86) chips; WCH vendor driver (`/dev/ttyCH343USB*`); no USB device; boards sharing a serial number. Prints the `/dev/serial/by-id` / `by-path` name and a udev rule. |
| 2 | `bus_tester` | Motors at the wrong baudrate, motors still on factory ID 1, two motors sharing an ID, nothing answering. |
| 3 | `motor_ping` | Where the daisy chain breaks ("motors 4-6 silent" means the cable from 3 to 4), single wrong-ID motors, intermittent motors, reply times. |
| 4 | `power_diagnostics` | 12 V adapter on 7.4 V motors (CRITICAL), 12 V motors starved on 5 V, mixed motor voltages, low supply, voltage drop along the chain, overheating, servo error flags. Tells 7.4 V from 12 V motors by `Max_Voltage_Limit`. |
| 5 | `power_diagnostics` | Idle monitor: motors that drop out and come back. Load test: moves one joint about 13 degrees and catches brownouts (supply sagging below 4.8 V on 7.4 V motors). |
| 6 | `motor_reset` | Sets ID and baudrate of the one connected motor, found at whatever ID/baud it has. Writes EEPROM. |
| 7 | `motor_reset` | Writes LeRobot's settings: position mode, PID 16/0/32, gripper torque/current limits. Torque left off. |
| 8 | `settings_audit` | PID not 16/0/32 (buzzing), low `Max_Torque_Limit` (slow stepwise lifts), wrong operating mode, inverted limits. |
| 9 | `motor_ping.read_stress` | Teleop-style back-to-back position reads: failure rate and loop speed ("Failed to sync read" territory). |
| 10 | `lerobot_env` | LeRobot older than 0.6.0/0.6.1 (position overflow, no read retries), 0.7 without rustypot, stale or broken calibration JSON. |
| 11 | `report_generator` | All read-only checks in order, markdown report with the worst problems first. |

Options 1-4 and 8-11 only read. Options 5 (load test), 6 and 7 ask before moving or writing
anything. A factory reset is not offered on purpose: it would wipe the IDs and baudrate.

Each module also runs on its own, for example:

```bash
.venv/bin/python -m src.port_scanner
.venv/bin/python -m src.bus_tester --port /dev/serial/by-id/usb-1a86_USB_Single_Serial_XXXX-if00
.venv/bin/python -m src.motor_ping --port /dev/ttyACM0
.venv/bin/python -m src.power_diagnostics --port /dev/ttyACM0 --monitor 15
.venv/bin/python -m src.motor_reset --port /dev/ttyACM0 set-id 6     # gripper, alone on the board
```

## Tests

The tests run against a simulated servo bus (`tests/fake_bus.py`) with the real servo rules:
7.4 V and 12 V voltage limits, EEPROM lock, duplicate-ID collisions, brownouts and drop-outs.
No arm is needed.

```bash
.venv/bin/pip install mypy types-pyserial
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/mypy --strict src tests
```

Not yet run against the real arm: register addresses come from LeRobot's Feetech table
(v0.6.1), and the error-bit meanings for overcurrent/overload come from community drivers.

## Layout

```
so101-servo-debugger/
├── src/
│   ├── feetech.py            Feetech STS protocol: ping, read, write, register table
│   ├── so101.py              Joint IDs, gear ratios, supply types, LeRobot settings
│   ├── findings.py           Severity + fix result type
│   ├── port_scanner.py       Linux/USB checks            (US-001)
│   ├── bus_tester.py         Baudrate and ID scan        (US-002)
│   ├── motor_ping.py         Per-joint ping, read stress (US-003)
│   ├── power_diagnostics.py  Voltage, monitor, load test (US-004)
│   ├── motor_reset.py        Set ID/baud, configure      (US-005)
│   ├── settings_audit.py     Stored settings check
│   ├── lerobot_env.py        LeRobot version, calibration
│   ├── report_generator.py   Full report                 (US-006)
│   └── cli.py                Menu                        (US-007)
├── tests/                    Simulated bus + 25 tests
└── diagnostics/              Saved reports
```

## Ralph agent

This started as a [Ralph](https://github.com/snarktank/ralph) plan (`prd.json`); all seven stories
are now implemented. `setup-ralph.sh` copies Ralph and the plan into another project if you want
an agent to extend it:

```bash
./setup-ralph.sh /path/to/your/lerobot-project
```

It looks for Ralph in `projects2/Ralph-agent` (override with `RALPH_DIR=...`).

## Resources

- [LeRobot SO-101 guide](https://huggingface.co/docs/lerobot/so101)
- [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100) (BOM, prints, wiring)
- [LeRobot releases](https://github.com/huggingface/lerobot/releases)
