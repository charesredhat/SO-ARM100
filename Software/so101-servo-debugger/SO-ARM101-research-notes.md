# SO-ARM101 / LeRobot research notes (2026-11-11)

## 1. Current state of SO-ARM101 and LeRobot (as of Nov 2026)

### Hardware (TheRobotStudio/SO-ARM100 repo)
- SO-101 is the current design, SO-100 is deprecated. Changes: improved wiring
  (fixes the joint-3 cable disconnect), no gear removal during assembly, leader
  arm uses lighter geared motors.
- Follower: 6x STS3215 **7.4 V** 1/345 (C001). Leader: 3x 1/147 (C046),
  2x 1/191 (C044), 1x 1/345 (C001). Leader is always 7.4 V.
- Optional 12 V follower motors (C018, 30 kg.cm) need a 12 V 5 A+ supply.
- Official BOM power supply is a **5 V** barrel-jack unit for 7.4 V motors.
  The Waveshare Bus Servo Adapter passes the DC jack straight to the servos,
  it does not regulate. Plugging 12 V into C001 motors gives
  "Input voltage error" and the motors stop answering.
- Waveshare board jumpers must both be on **B** (USB) or the PC never reaches
  the bus.
- Repo CHANGELOG last entry is v0.1.13 (Jan 2025, STEP files). Seeed wiki
  pages were updated Mar 2026 and Nov 2026.

### Software (huggingface/lerobot)
| Version | Date | Relevant change |
|---|---|---|
| v0.7.0 | 2026-11-05 | Motor bus rewritten on rustypot `SerialMotorsBus`. `FeetechMotorsBus` is a deprecated alias. `OperatingMode`/`TorqueMode` replaced by `bus.set_operating_mode("position")`. `broadcast_ping`, `scan_port`, control tables removed. Calibration files unchanged. Linux torch uses CUDA 13. |
| v0.6.1 | 2026-08-03 | SO follower/leader bus reads **retry on transient Feetech errors** (fixes the mid-teleop disconnect in issue 3131). P coefficient configurable. Calibration range recording retries. |
| v0.6.0 | 2026-07-06 | Fixed Feetech **position readings overflowing**. `pip install lerobot` no longer pulls training deps. |
| v0.5.x | 2026-03/04 | SO-100/SO-101 code unified. Python 3.12+ required. 0.5.1 had a wrist_roll half-turn homing crash ("Magnitude exceeds 2047", issue 3193), later clamped (PR 4940). |

- CLI is now `lerobot-find-port`, `lerobot-setup-motors`, `lerobot-calibrate`,
  `lerobot-teleoperate`, `lerobot-record`. The old
  `python lerobot/scripts/control_robot.py ...` workflow is gone.
- Install: `pip install 'lerobot[feetech]'` (or `pip install -e ".[feetech]"` from a clone).
- Calibration lives in `~/.cache/huggingface/lerobot/calibration/<id>.json`.
- On connect LeRobot writes P=16, I=0, D=32 to every motor (Feetech default P=32 shakes).

## 2. Why an SO-ARM only makes small jerky movements (ranked)

1. **`max_relative_target` clamp too small.** Per-step degree cap on the
   follower. Value 1 = no visible motion, 3 = crawl, 20 = normal. Gravity
   droop larger than the cap means the follower stutters and never catches
   up. Pass `--robot.max_relative_target=20` (or `0`/`null` to disable).
2. **Power.** Servos cannot run from USB. Undersized or wrong-voltage supply
   causes brownout resets: the motor twitches, resets, twitches. Use the
   kit's 5 V supply rated 4 A or more per arm for C001 motors. 12 V only for
   C018 motors. Watch for the motor LEDs blinking on every move.
3. **Stale or bad calibration.** Follower chases an unreachable target and
   the clamp limits every step. Delete old calibration JSON and recalibrate
   with the current version. Old versions had the position overflow bug and
   the homing-offset sign-magnitude bug, both fixed in 2026.
4. **Linux serial hijack.**
   - `brltty` claims CH340/CH341 boards (`/dev/ttyUSB*`): `sudo apt remove brltty`.
   - `ModemManager` probes new `/dev/ttyACM*` with AT commands for several
     seconds after plug-in, which puts garbage on the servo bus and makes the
     port "busy": disable it or add a udev `ENV{ID_MM_DEVICE_IGNORE}="1"` rule.
   - User must be in `dialout` (log out and back in), or `chmod 666` per session.
   - `/dev/ttyACM0` and `1` swap between plug-ins. Use `/dev/serial/by-id/...`.
5. **Transient bus read errors.** "Failed to sync read 'Present_Position'"
   crashes old versions, worse when several joints move at once. Fixed by
   retries in v0.6.1. Dropping `--fps=30` also helps.
6. **Duplicate motor IDs.** All STS3215 ship as ID 1. If IDs were set with
   more than one motor on the chain, several motors answer at once and only
   one or random joints move. Re-run `lerobot-setup-motors` with one motor
   connected at a time.
7. **PID / torque limit.** Non-default P makes joints buzz. `Torque_Limit`
   behaves like a PWM cap, so a low value makes lifts slow and stepwise.
8. **Leader and follower start in different poses.** The follower lunges to
   match. Line them up before launching teleop.
9. **Mechanical binding** on one joint (cable tension, bracket contact, tight
   print). Check with torque off by hand. Use the repo gauge files to verify
   print fit.
10. **Slow control loop.** Blocking camera reads can drop a 60 Hz loop to
    about 7 Hz and the arm moves in visible steps. Test teleop with no cameras.
11. **Dead servo.** Gripper servos die from overload (closing the leader
    gripper on a held object). Symptom: joint stiff, "Incorrect status packet".

## 3. Suggested clean retry on Linux

```bash
sudo apt remove brltty
sudo usermod -aG dialout "$USER"      # then log out / in
sudo systemctl disable --now ModemManager
python3.12 -m venv ~/lerobot-env && source ~/lerobot-env/bin/activate
pip install 'lerobot[feetech]'
lerobot-find-port                      # note /dev/serial/by-id path
ls -l /dev/serial/by-id/
```

Then with only the 5 V supply and board jumpers on B:

```bash
lerobot-setup-motors --robot.type=so101_follower --robot.port=/dev/serial/by-id/<follower>
lerobot-setup-motors --teleop.type=so101_leader  --teleop.port=/dev/serial/by-id/<leader>
rm -f ~/.cache/huggingface/lerobot/calibration/*/*.json
lerobot-calibrate --robot.type=so101_follower --robot.port=... --robot.id=follower1
lerobot-calibrate --teleop.type=so101_leader  --teleop.port=... --teleop.id=leader1
lerobot-teleoperate --robot.type=so101_follower --robot.port=... --robot.id=follower1 \
  --robot.max_relative_target=20 \
  --teleop.type=so101_leader --teleop.port=... --teleop.id=leader1 --fps=30
```

If it still stutters: watch motor LEDs during moves (power), run
`dmesg -w` while plugging in (brltty/ModemManager), and test one joint at a
time with torque off by hand (binding).

## 4. Sources
- https://github.com/TheRobotStudio/SO-ARM100
- https://github.com/TheRobotStudio/SO-ARM100/blob/main/CHANGELOG.md
- https://github.com/TheRobotStudio/SO-ARM100/issues/174 (voltage / Waveshare)
- https://github.com/huggingface/lerobot/releases
- https://huggingface.co/docs/lerobot/so101
- https://github.com/huggingface/lerobot/issues/3131 (sync read disconnects)
- https://github.com/huggingface/lerobot/issues/2819 (dead gripper servo)
- https://github.com/huggingface/lerobot/issues/3193 and /pull/4940 (homing offset overflow)
- https://github.com/huggingface/lerobot/issues/1483 (max_relative_target behaviour)
- https://github.com/TusharBajaj07/so101-lerobot-setup (STS3215 lessons, PID, torque)
- https://forum.hiwonder.com/t/so101-arm-elbow-servo-joint-3-grinds-jitters-and-only-drives-one-direction-during-teleop/213
- https://stefanopineda.com/robot_setup (Ubuntu runbook: brltty, dialout, udev)
- https://github.com/isaac-sim/IsaacLab/pull/8576 (dialout)
- https://github.com/RLinf/RLinf/pull/1560 (camera reads slowing the loop)
- https://wiki.seeedstudio.com/lerobot_so100m_new/
- https://github.com/vicfei/awesome-so-arm
