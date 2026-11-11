# Fixing this SO-101: servos not responding, small jerky movements

The two problems this arm has had, what most likely caused them on this setup, and the order to
fix them in. Each step names the debugger check that proves it is fixed. Full commands and
background are in [SO-ARM101-Nobara-rebuild-checklist.md](SO-ARM101-Nobara-rebuild-checklist.md)
and [SO-ARM101-research-notes.md](SO-ARM101-research-notes.md).

Run the debugger from Konsole, not the VS Code terminal (see README):

```bash
cd Software/so101-servo-debugger
.venv/bin/python -m src.cli
```

## 1. What probably went wrong

The setup: Waveshare Bus Servo Adapter (A) on both arms, Nobara (Fedora-based), a LeRobot install
from mid-2025, motor voltage unknown. Any one of these explains both symptoms:

| Likely cause | Gives | Status on this PC (2026-11-11) |
|---|---|---|
| **12 V adapter on 7.4 V motors.** The Waveshare board does not regulate; its "9-12.6 V" print is for Waveshare's own servos. The standard kit is 7.4 V motors on a **5 V** supply. | "Input voltage error", servos stop answering | Unknown: check the motor stickers and the adapter label (step 0) |
| **Supply too weak or USB-only.** | Motor twitches, resets, twitches | Unknown: load test (step 5) |
| **LeRobot before 0.6.0/0.6.1.** Position overflow bug; no retry after one failed bus read. | Jerks, stalls, "Failed to sync read" | No LeRobot installed now: install fresh (step 2) |
| **Stale calibration** from that build. | Follower chases a wrong target in small steps | No calibration files now: calibrate fresh (step 8) |
| **ModemManager** probes new `/dev/ttyACM*` for 10-30 s. | Port busy, garbage on the bus right after plug-in | **Running** |
| **Not in `dialout`.** | "Permission denied" | **Not a member** |
| **brltty** can claim WCH USB-serial chips. | Port vanishes after plug-in | Installed; its rules do not currently match 1a86, so low risk |
| **Duplicate motor IDs** (every STS3215 ships as ID 1). | One joint twitches, others dead | Unknown: steps 4 and 6 |
| **`max_relative_target` set small.** | Follower crawls and stutters | Only if you passed it: leave it off or use 20+ |

## 2. Fix it, in this order

### Step 0 - Motor voltage and supply, before any power

Read the sticker on a motor (`STS3215 7.4V` or `STS3215 12V`) and the label on the adapter.

| Motors | Use | Never |
|---|---|---|
| 7.4 V (C001/C044/C046) - standard kit, every leader | 5 V, 4 A or more per arm | 12 V |
| 12 V (C018) - optional follower | 12 V, 5 A or more per arm | 5 V (works, but too weak: stepwise lifts) |

If the adapter you used was 12 V and the motors are 7.4 V, that was the fault. Power them from 5 V
and re-test; they usually recover.

**Proof:** option 4. A `CRITICAL ... ABOVE this motor's limit` line means the supply is wrong.
The tool reads each motor's `Max_Voltage_Limit` (14 V on 12 V motors) to tell the variants apart,
and reports "Supply looks like a 5 V supply" when it matches.

### Step 1 - Nobara prep (arms unplugged)

```bash
sudo usermod -aG dialout "$USER"          # then log out and back in
sudo systemctl disable --now ModemManager # no mobile modem on this PC
sudo dnf remove brltty                    # or: sudo systemctl mask --now brltty-udev.service
```

**Proof:** option 1 shows no `dialout`, ModemManager or brltty lines.

### Step 2 - Fresh LeRobot

```bash
python3 -m venv ~/lerobot-env && source ~/lerobot-env/bin/activate
pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # teleop needs no GPU
pip install 'lerobot[feetech]'
```

**Proof:** option 10 shows `LeRobot 0.7.x` with no warning (it also checks `rustypot`, which 0.7 needs).

### Step 3 - Board and port

Both jumpers on each Waveshare board on **B** (USB). Plug in one board by USB only.

**Proof:** option 1 lists the board as `CH343 (Waveshare bus servo adapter)` with a
`/dev/serial/by-id/...` name. Use that name in every LeRobot command: `/dev/ttyACM0` and `1` swap
between plug-ins. If both boards get the same by-id name, use the `/dev/serial/by-path/...` one and
always use the same USB sockets.

### Step 4 - Motor IDs, one motor at a time

Correct supply from step 0, then:

```bash
lerobot-setup-motors --robot.type=so101_follower --robot.port=/dev/serial/by-id/FOLLOWER
lerobot-setup-motors --teleop.type=so101_leader  --teleop.port=/dev/serial/by-id/LEADER
```

Each motor must be **alone on the board** when you press Enter (gripper first, shoulder pan last).
If LeRobot cannot find a motor - it may be on another baudrate or ID from an earlier attempt -
use option 6 instead: it finds the single motor at whatever ID and baudrate it has and sets it.

**Proof:** chain all six, then option 2 (all six at 1000000 baud, no "Garbled replies") and
option 3 (6/6 replies each, no chain break).

### Step 5 - Power under load

Option 5, load test, joint 2 (shoulder lift, the heaviest) then joint 3.

**Proof:** "supply held up under load". A sag below 4.8 V (7.4 V motors) or a motor that stops
answering while moving is a brownout: use a stronger supply and check the barrel plug. Also watch
the motor LEDs during teleop later: a blink on each move is a brownout.

### Step 6 - Motor settings

Option 8. LeRobot writes PID 16/0/32 and the gripper limits itself on every connect, so warnings
here matter only if the arm misbehaves outside LeRobot. A `Max_Torque_Limit` warning on a joint
does matter: a low limit makes lifts slow and stepwise. Option 7 writes LeRobot's values.

### Step 7 - Bus reliability

Option 9 for 10 seconds.

**Proof:** 0 errors, or under 0.5 % (LeRobot 0.6.1+ retries those). More: reseat cables at the
joints it names, use a short USB cable without a hub, and re-run step 5.

### Step 8 - Calibrate from scratch

```bash
rm -rf ~/.cache/huggingface/lerobot/calibration/
lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/serial/by-id/FOLLOWER --robot.id=follower1
lerobot-calibrate --teleop.type=so101_leader  --teleop.port=/dev/serial/by-id/LEADER  --teleop.id=leader1
```

Put every joint in the middle of its range before pressing Enter (wrist roll centred), then sweep
each joint slowly to both hard limits.

**Proof:** option 10 shows both files with no "empty or inverted range".

### Step 9 - First teleop

No cameras; leader in the same pose as the follower; 30 fps; no `max_relative_target`:

```bash
lerobot-teleoperate \
  --robot.type=so101_follower --robot.port=/dev/serial/by-id/FOLLOWER --robot.id=follower1 \
  --teleop.type=so101_leader  --teleop.port=/dev/serial/by-id/LEADER  --teleop.id=leader1 \
  --fps=30
```

Move one joint at a time, then several. Lower the follower to rest before Ctrl-C (torque is
released on exit). Close teleop before running the debugger again: only one program can hold the port.

## 3. Symptom to cause

| Symptom | Likely cause | Debugger |
|---|---|---|
| No motor answers at all | Supply off or wrong; board jumpers on A; cable to motor 1 | 1, 2, 4 |
| "Input voltage error" | 12 V supply on 7.4 V motors | 4 |
| Only one joint moves, others dead | Duplicate IDs, or a break in the chain | 2, 3 |
| Joints from N onwards dead | Cable or socket between N-1 and N | 3 |
| Twitches, stops, twitches | Brownout: supply too weak | 5 (load test) |
| Fine idle, fails when several joints move | Bus errors, cabling, old LeRobot | 9, 10 |
| Follower lags far behind the leader | `max_relative_target` set | (command line) |
| Buzzing or oscillating joints | PID not 16/0/32 | 8 |
| Slow, stepwise lifts | Low torque limit, or 12 V motors on 5 V | 8, 4 |
| One joint grinds or moves one way only | Binding or a dying servo | Torque off, move by hand; swap the servo |
| Gripper stiff, "Incorrect status packet" | Gripper servo burnt out (overload) | 3, 4; replace it |
| Stepwise only with cameras on | Camera reads blocking the loop | Test without cameras |
| Port missing or busy just after plug-in | ModemManager, brltty, `dialout` | 1 |

## 4. Error messages

| Message | Meaning | Fix |
|---|---|---|
| `There is no status packet!` | No reply from that motor | Option 3 to find which; then power, cable, ID |
| `Input voltage error` | Supply outside the motor's limits | Step 0 |
| `Incorrect status packet` / garbled reply | Two motors share an ID, or a dying servo | Step 4; option 2 |
| `Failed to sync read 'Present_Position'` | A bus read failed mid-teleop | LeRobot 0.6.1+; option 9; cables |
| `Magnitude exceeds 2047` | Wrist roll far from centre at calibration (old versions) | Centre wrist roll; LeRobot 0.7 |
| `Permission denied: '/dev/ttyACM0'` | Not in `dialout` | Step 1 |
| `Device or resource busy` | ModemManager or another program holds the port | Step 1; close LeRobot |

## 5. Save this time

Put these in this folder so the next attempt starts warm: both calibration JSON files, the
`diagnostics/report.md` from a working arm, `ls -l /dev/serial/by-id/`, `pip freeze` from
`~/lerobot-env`, photos of a motor sticker and the supply label, and the teleop command that worked.
