# SO-ARM101 rebuild checklist: Nobara Linux + Waveshare Bus Servo Adapter (A)

Written 2026-11-11 against LeRobot 0.7.1 (main). Companion to
`SO-ARM101-research-notes.md`, which has the background and sources.

Your known setup: Waveshare Bus Servo Adapter (A) on both arms, Nobara
(Fedora-based), previous LeRobot install from mid-2025 or later (the
`lerobot-calibrate` era), motor voltage variant unknown.

Why mid-2025 LeRobot plus Fedora is a plausible explanation for "small jerky
movements" on its own:

- Builds before v0.6.0 (Jul 2026) had a Feetech position-overflow bug.
- Builds before v0.6.1 (Aug 2026) had no retry on transient Feetech read
  errors, so teleop stalled or crashed whenever several joints moved at once.
- Fedora ships ModemManager, which sends AT commands to every new
  `/dev/ttyACM*` for a few seconds, putting garbage on the servo bus.

---

## Step 0. Identify motors and supply BEFORE applying power

Do not plug any power into the arm until this step is done.

**A. Look at the sticker on each motor.** Feetech prints the voltage on the
label: `STS3215 7.4V` or `STS3215 12V`. The standard SO-101 kit is 7.4 V on
both arms. The leader is always 7.4 V.

**B. Look at the power adapter(s) from the kit.** The official BOM supply is
**5 V** with a 5.5 x 2.1 mm plug. If your adapter says 12 V and your motors
say 7.4 V, that adapter was the problem: the Waveshare board does not
regulate, the DC jack feeds the servo rail directly, and 7.4 V motors on
12 V raise "Input voltage error" and stop responding.

The "9 to 12.6 V" printed on the Waveshare board is for Waveshare's own
12 V ST3215 servos. Ignore it for 7.4 V motors.

**C. Confirm from the motor itself** once a single motor is on the bus at the
correct voltage (do this in Step 4, after IDs are set):

```python
from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
r = SO101Follower(SO101FollowerConfig(port="/dev/serial/by-id/XXXX", id="follower1"))
r.connect(calibrate=False)
for m in r.bus.motors:
    print(m,
          "Present_Voltage=", r.bus.read("Present_Voltage", m) / 10, "V",
          "Max_Voltage_Limit=", r.bus.read("Max_Voltage_Limit", m) / 10, "V",
          "Temp=", r.bus.read("Present_Temperature", m), "C")
r.disconnect()
```

12 V motors report a Max_Voltage_Limit of 14.0 V. A limit well below that
means a 7.4 V motor. Present_Voltage should read close to your supply.

Decision:

| Motor sticker | Supply to use | Notes |
|---|---|---|
| 7.4 V (C001/C044/C046) | 5 V, 4 A or more per arm | Official kit. Below spec torque is fine. |
| 12 V (C018) | 12 V, 5 A or more per arm | Only if you bought the 12 V follower option. |
| Mixed | Separate supplies per arm | Never share one supply across arms with different voltages. |

---

## Step 1. Nobara (Fedora) system prep

Run with the arms unplugged.

```bash
# 1a. Serial group. Log out and back in afterwards (reboot is simplest).
sudo usermod -aG dialout "$USER"

# 1b. brltty: check whether it is installed and whether its rule grabs WCH (1a86) chips.
rpm -q brltty
grep -l 1a86 /usr/lib/udev/rules.d/*brltty* 2>/dev/null
```

If brltty is installed and the grep prints a file, pick one:

```bash
# Option 1 (simplest, you have no braille display):
sudo dnf remove brltty
```

```bash
# Option 2 (keep brltty, just stop it from touching serial devices):
sudo systemctl mask --now brltty-udev.service
```

```bash
# 1c. ModemManager: stop it probing the servo boards.
sudo systemctl disable --now ModemManager
```

If you need ModemManager for a cellular modem, use a udev ignore rule
instead (Step 3 has the rule file).

```bash
# 1d. Build tools and Python. Nobara ships Python 3.13, which LeRobot supports (3.12+).
sudo dnf install -y python3 python3-devel git gcc cmake ffmpeg
python3 --version
```

---

## Step 2. Fresh LeRobot environment

Do not reuse the old environment. Old calibration files are deleted later
in Step 5.

```bash
python3 -m venv ~/lerobot-env
source ~/lerobot-env/bin/activate
pip install --upgrade pip
```

Teleoperation does not need a GPU. Installing CPU-only torch first keeps the
install small and avoids the CUDA 13 driver requirement LeRobot 0.7 added on
Linux:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

```bash
pip install 'lerobot[feetech]'
```

```bash
python -c "import lerobot, rustypot; print(lerobot.__version__)"
```

Expect 0.7.x. If you later train on an NVIDIA GPU, reinstall torch with the
CUDA build then (driver 580 or newer for CUDA 13).

---

## Step 3. Board and ports

Set both jumpers on each Waveshare board to **B** (USB). On A the PC cannot
reach the bus at all.

Plug in ONE board by USB only, no DC power yet:

```bash
lsusb | grep -i 1a86
dmesg | tail -n 20
ls -l /dev/ttyACM* /dev/serial/by-id/ /dev/serial/by-path/
```

Expected: the board is a WCH CH343 ("USB Single Serial"), the kernel's
built-in `cdc_acm` driver binds it, and it appears as `/dev/ttyACM0`.

Things that mean Step 1 is not finished:

- `lsusb` shows it but no `/dev/ttyACM*`: brltty grabbed it, or you did not
  log out after the group change. `dmesg` will show
  "claimed by ... while 'brltty' sets config".
- `/dev/ttyACM0` exists but opening it says "Device or resource busy" for
  the first 10 to 30 s after plug-in: ModemManager is still running.
- Device shows as `/dev/ttyCH343USB0`: a vendor driver from a previous
  attempt is installed. Remove it (`sudo rmmod ch343` and delete the
  module) so `cdc_acm` is used; LeRobot's port finder only lists ACM/USB names.

**Stable names.** `/dev/ttyACM0` and `1` swap between plug-ins. Prefer
`/dev/serial/by-id/...`. CH343 boards may have no unique serial, in which
case both arms get the same by-id name. Then use `/dev/serial/by-path/...`,
which encodes the physical USB socket, and always plug each arm into the
same socket.

Optional udev rule file `/etc/udev/rules.d/99-so101.rules` (replace the
serial or add a by-path match after checking `udevadm info -a -n /dev/ttyACM0`):

```
# Keep ModemManager away from the servo boards (alternative to disabling the service)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ENV{ID_MM_DEVICE_IGNORE}="1", MODE="0666"
```

Then `sudo udevadm control --reload && sudo udevadm trigger` and replug.

Now plug in the second board and run:

```bash
lerobot-find-port
```

Record the follower and leader paths. Use them in every command below.

---

## Step 4. Motor IDs, one motor at a time

Every STS3215 ships as ID 1. Duplicated IDs give exactly the "one joint
twitches, the rest do nothing" symptom. Redo this even if you did it before.

Connect the correct supply from Step 0 and USB to the follower board. Connect
ONLY the gripper motor to the board, nothing daisy-chained behind it.

```bash
lerobot-setup-motors --robot.type=so101_follower --robot.port=/dev/serial/by-id/FOLLOWER
```

Follow the prompts: gripper (6), wrist_roll (5), wrist_flex (4), elbow_flex
(3), shoulder_lift (2), shoulder_pan (1). Each motor must be alone on the
board when you press Enter.

Repeat for the leader:

```bash
lerobot-setup-motors --teleop.type=so101_leader --teleop.port=/dev/serial/by-id/LEADER
```

If a motor answers "Input voltage error" here, stop: wrong supply (Step 0).
If a motor is not found at all: check the 3-pin cable seating, and that the
supply LED on the board is on.

Then daisy-chain all six and run the register-read snippet from Step 0C to
confirm all six answer with sane voltage and temperature.

---

## Step 5. Calibrate from scratch

Delete every old calibration first. Stale files from the 2025 build carry
the old offset bugs.

```bash
rm -rf ~/.cache/huggingface/lerobot/calibration/
```

Follower, then leader. Before pressing Enter, put every joint in the MIDDLE
of its range (the mid pose in the LeRobot video, elbow roughly 90 degrees,
wrist roll centered). Then sweep each joint slowly to both hard limits.

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/serial/by-id/FOLLOWER --robot.id=follower1
```

```bash
lerobot-calibrate --teleop.type=so101_leader --teleop.port=/dev/serial/by-id/LEADER --teleop.id=leader1
```

Known trap: wrist_roll far from center at the moment you press Enter can
crash with "Magnitude exceeds 2047" on older versions. 0.7.x clamps it, but
still center the wrist roll first.

---

## Step 6. First teleoperation test

No cameras. Put the leader in the same pose as the follower before starting,
otherwise the follower lunges. Start at 30 fps.

```bash
lerobot-teleoperate \
  --robot.type=so101_follower --robot.port=/dev/serial/by-id/FOLLOWER --robot.id=follower1 \
  --teleop.type=so101_leader  --teleop.port=/dev/serial/by-id/LEADER  --teleop.id=leader1 \
  --fps=30
```

Defaults in 0.7.1 that matter: `max_relative_target` is off (None), PID is
P=16 I=0 D=32, `num_read_retries` is 2, torque is released on exit. Lower
the follower to rest BEFORE pressing Ctrl-C or it drops.

Move one joint at a time first, then several. Watch the motor LEDs: a
blink on every move is a brownout (supply too weak).

---

## Step 7. If it still jerks: symptom to cause

| Symptom | Likely cause | Check |
|---|---|---|
| Follower twitches then stops, repeats | Supply current too low, or USB-only power | Motor LEDs flash on movement; `Present_Voltage` sags below 4.8 V (7.4 V motors) while moving |
| Only one joint moves, others dead | Duplicate IDs or a bad 3-pin cable | Redo Step 4; swap cables |
| Works idle, breaks when several joints move | Bus errors | Run with `--fps=30`; raise `--robot.num_read_retries=5`; reseat cables; shorter USB lead |
| Follower lags far behind leader | A clamp was set | Make sure you are not passing `--robot.max_relative_target`; if you want one, use 20 or more |
| Joints buzz or oscillate | PID changed | Leave `--robot.position_p_coefficient` at 16 |
| One joint grinds, moves one way only | Mechanical binding or a dying servo | Torque off, move by hand through the full range; swap in another servo |
| "Input voltage error" | Wrong supply voltage | Step 0 |
| Port missing or busy right after plug-in | brltty / ModemManager / dialout | Step 1 |
| Follower stops at random after a few seconds | Old LeRobot without retries | Confirm `lerobot.__version__` is 0.7.x in the active venv |
| Motion stepwise with cameras on, smooth without | Camera read blocking the loop | Test without cameras; use lower camera resolution/fps |

Useful live diagnostics while teleop runs in another terminal:

```bash
dmesg -w                     # USB resets, driver reclaims
journalctl -f -u ModemManager # must be silent (service disabled)
```

---

## Step 8. What to save this time

Put these in this folder so the next attempt starts warm:

- `~/.cache/huggingface/lerobot/calibration/` (both JSON files)
- Output of `lsusb`, `ls -l /dev/serial/by-id/`, `pip freeze`
- Motor sticker photo and supply label photo
- The exact teleop command that worked
