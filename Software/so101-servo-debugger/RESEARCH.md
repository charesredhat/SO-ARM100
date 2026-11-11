# SO-101 servo debugger - research notes

Updated 2026-11-11 against LeRobot `main` (0.7.x) and the SO-ARM101 research notes in this folder.
The January 2026 version of this file had outdated import paths, a 12 V-only voltage rule and
Python 3.10 install steps; those are corrected below.

For the hardware variants, LeRobot version history and the ranked causes of jerky motion, see
[SO-ARM101-research-notes.md](SO-ARM101-research-notes.md). This file covers what the debugger
itself relies on.

---

## LeRobot today

### Commands
```bash
lerobot-find-port
lerobot-setup-motors --robot.type=so101_follower --robot.port=<port>
lerobot-setup-motors --teleop.type=so101_leader  --teleop.port=<port>
lerobot-calibrate    --robot.type=so101_follower --robot.port=<port> --robot.id=follower1
lerobot-teleoperate  --robot.type=so101_follower ... --teleop.type=so101_leader ... --fps=30
```
The old `python -m lerobot.find_port`, `python -m lerobot.setup_motors` and
`lerobot/scripts/control_robot.py` entry points are gone.

### Install
```bash
python3 -m venv ~/lerobot-env && source ~/lerobot-env/bin/activate
pip install 'lerobot[feetech]'          # Python 3.12+; [feetech] pulls rustypot + pyserial
```

### Python API (moved three times)
| Era | Import |
|---|---|
| 2024 | `lerobot.common.robot_devices.motors.feetech.FeetechMotorsBus` |
| 2025 | `lerobot.common.motors.feetech.FeetechMotorsBus` (what this file used to say) |
| 0.5-0.6 | `lerobot.motors.feetech.FeetechMotorsBus`, `lerobot.robots.so_follower.SO101Follower` |
| 0.7 | `lerobot.motors.SerialMotorsBus` (rustypot); `FeetechMotorsBus` is a deprecated alias that warns |

Current robot classes:
```python
from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
```
`OperatingMode`/`TorqueMode` are replaced by `bus.set_operating_mode("position")`; `broadcast_ping`,
`scan_port` and the Python control tables are removed in 0.7. That churn is why this debugger
speaks the servo protocol itself instead of importing LeRobot.

### What LeRobot writes on connect (`SOFollower.configure`)
- Position mode on every joint; `P_Coefficient` 16, `I_Coefficient` 0, `D_Coefficient` 32
  (Feetech's default P=32 shakes). P is configurable as `--robot.position_p_coefficient`.
- Gripper only: `Max_Torque_Limit` 500, `Protection_Current` 250, `Overload_Torque` 25.
- `configure_motors()`: `Return_Delay_Time` 0; on the STS3215 clears `Phase` bit 4 so positions
  stay in 0-4095.
- Defaults: `max_relative_target` None, `use_degrees` True, `disable_torque_on_disconnect` True.
- Calibration: one JSON per arm `id` under `~/.cache/huggingface/lerobot/calibration/`
  (override with `HF_LEROBOT_CALIBRATION`), per joint `id`, `drive_mode`, `homing_offset`,
  `range_min`, `range_max`.

---

## SO-101 hardware facts used by the checks

- Joint IDs and chain order: board -> 1 shoulder_pan -> 2 shoulder_lift -> 3 elbow_flex ->
  4 wrist_flex -> 5 wrist_roll -> 6 gripper. So "motors 4-6 silent" points at the 3-4 cable.
- Follower: 6x STS3215 7.4 V 1/345 (C001). Leader: 1/191, 1/345, 1/191, 1/147, 1/147, 1/147
  (joints 1-6). Leader always 7.4 V. Optional 12 V follower motors (C018).
- Supply: **5 V** (4 A+ per arm) for 7.4 V motors; 12 V 5 A+ only for 12 V motors.
- The Waveshare Bus Servo Adapter (A) passes the DC jack straight to the servos. Its USB chip is
  a WCH CH343 (`1a86:55d3`, "USB Single Serial") handled by the kernel's `cdc_acm` as
  `/dev/ttyACM*`. Jumpers must both be on B (USB).
- Variant from the motor itself: 12 V motors report `Max_Voltage_Limit` 14.0 V; 7.4 V motors far less.

---

## Feetech STS protocol (what `src/feetech.py` implements)

Packet: `FF FF ID LEN INSTR PARAMS... CHK`, `LEN = len(PARAMS) + 2`,
`CHK = ~(ID + LEN + INSTR + sum(PARAMS)) & 0xFF`. Reply: `FF FF ID LEN ERROR PARAMS... CHK`.
Instructions: ping `0x01`, read `0x02` (address, size), write `0x03` (address, bytes). Values are
little-endian. Ping of ID 1: `FF FF 01 02 01 FB`.

Registers (LeRobot v0.6.1 `STS_SMS_SERIES_CONTROL_TABLE`; addresses < 40 are EEPROM and need
`Lock` (55) = 0 to write):

| Register | Addr | Size | Notes |
|---|---|---|---|
| Model_Number | 3 | 2 | 777 = STS3215 |
| ID / Baud_Rate | 5 / 6 | 1 | Baud codes: 0=1M, 1=500k, 2=250k, 3=128k, 4=115200, 5=57600, 6=38400, 7=19200 |
| Return_Delay_Time | 7 | 1 | LeRobot uses 0 |
| Min/Max_Position_Limit | 9 / 11 | 2 | |
| Max_Temperature_Limit | 13 | 1 | degrees C |
| Max/Min_Voltage_Limit | 14 / 15 | 1 | 0.1 V units |
| Max_Torque_Limit | 16 | 2 | 0-1000; acts like a power cap |
| Phase | 18 | 1 | bit 4 cleared by LeRobot |
| P/D/I_Coefficient | 21 / 22 / 23 | 1 | |
| Protection_Current | 28 | 2 | |
| Homing_Offset | 31 | 2 | sign bit 11 |
| Operating_Mode | 33 | 1 | 0 position, 1 velocity, 2 PWM, 3 step |
| Overload_Torque | 36 | 1 | |
| Torque_Enable | 40 | 1 | |
| Goal_Position | 42 | 2 | sign bit 15 |
| Lock | 55 | 1 | |
| Present_Position / Speed / Load | 56 / 58 / 60 | 2 | sign bits 15 / 15 / 10 |
| Present_Voltage / Temperature | 62 / 63 | 1 | 0.1 V, degrees C |
| Status | 65 | 1 | same bits as the reply error byte |
| Present_Current | 69 | 2 | 6.5 mA units |

Error byte: bit 0 input voltage, bit 1 angle sensor, bit 2 overheating, bit 3 overcurrent
(Feetech); bit 5 overload (community drivers, not confirmed by Feetech documentation).

New motors are all ID 1 at 1 Mbps. Two motors on one ID answer together and corrupt each other's
reply: the debugger reports that as "garbled replies".

---

## Linux serial problems (Fedora/Nobara)

| Problem | Effect | Fix |
|---|---|---|
| Not in `dialout` | Permission denied | `sudo usermod -aG dialout $USER`, log out/in (`chmod 666` lasts until replug) |
| ModemManager | Probes new `/dev/ttyACM*` 10-30 s: port busy, garbage on the bus | `sudo systemctl disable --now ModemManager` or udev `ENV{ID_MM_DEVICE_IGNORE}="1"` |
| brltty | Claims some WCH chips as a braille display | `sudo dnf remove brltty` or `sudo systemctl mask --now brltty-udev.service` |
| WCH vendor `ch343` driver | Board becomes `/dev/ttyCH343USB*`, invisible to LeRobot | `sudo rmmod ch343`, remove the module |
| `/dev/ttyACM0`/`1` swap | Wrong arm opened | `/dev/serial/by-id/...`, or `by-path` when boards share a serial |
| VS Code Flatpak | Sandbox cannot see `/dev/ttyACM*` | Run from Konsole or `flatpak-spawn --host` |

---

## Ralph agent framework

Ralph is an autonomous loop that works through `prd.json` user stories, one fresh AI session per
story, with memory in git history and `progress.txt`. Files: `ralph.sh` (loop), `prd.json`
(stories), `progress.txt`, `CLAUDE.md` / `prompt.md` (agent instructions). Stories must each fit
in one context window. Run with `./ralph.sh --tool claude 10`. All seven stories in this
`prd.json` are now implemented; add new stories with `"passes": false` to extend it.

---

## Sources
- LeRobot SO-101 guide: https://huggingface.co/docs/lerobot/so101
- LeRobot source: https://github.com/huggingface/lerobot (`src/lerobot/motors/`, `robots/so_follower/`;
  Feetech table from tag v0.6.1 `motors/feetech/tables.py`)
- TheRobotStudio/SO-ARM100 bill of materials: https://github.com/TheRobotStudio/SO-ARM100
- Feetech protocol overview: https://aifitlab-wiki.super.site/feetech-servo-motor-docs/feetech-scs-protocol
- Remaining sources: see [SO-ARM101-research-notes.md](SO-ARM101-research-notes.md) section 4
- Ralph: https://github.com/snarktank/ralph
