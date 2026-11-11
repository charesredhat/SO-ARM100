"""Check the installed LeRobot version and the saved calibration files.

Old builds are a cause of jerky motion on their own: before v0.6.0 Feetech positions could overflow,
and before v0.6.1 a single failed bus read stalled or crashed teleop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from .findings import Finding, Severity, print_findings

POSITION_FIX = (0, 6, 0)  # 2026-07-06: Feetech position overflow fixed
RETRY_FIX = (0, 6, 1)  # 2026-08-03: SO arm reads retry on transient Feetech errors
POSITION_FIX_DATE = date(2026, 7, 6)

PROBE = ("import importlib.metadata as m, sys; v = m.version('lerobot'); "
         "rp = 'yes' if __import__('importlib').util.find_spec('rustypot') else 'no'; "
         "print(v, rp, sys.version.split()[0])")


def candidate_pythons() -> list[Path]:
    home = Path.home()
    paths = [home / "lerobot-env/bin/python", home / ".venv/bin/python",
             home / "miniconda3/envs/lerobot/bin/python", home / "anaconda3/envs/lerobot/bin/python"]
    if os.environ.get("VIRTUAL_ENV"):
        paths.insert(0, Path(os.environ["VIRTUAL_ENV"]) / "bin/python")
    paths.append(Path(sys.executable))
    seen: list[Path] = []
    for p in paths:
        if p.exists() and p not in seen:
            seen.append(p)
    return seen


def parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in text.split(".")[:3]:
        digits = "".join(c for c in piece if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def calibration_dir() -> Path:
    if os.environ.get("HF_LEROBOT_CALIBRATION"):
        return Path(os.environ["HF_LEROBOT_CALIBRATION"])
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface"))
    return hf_home / "lerobot/calibration"


def check_installs() -> list[Finding]:
    findings: list[Finding] = []
    found = False
    for python in candidate_pythons():
        try:
            out = subprocess.run([str(python), "-c", PROBE], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode != 0:
            continue
        found = True
        version, rustypot, pyver = out.stdout.split()
        v = parse_version(version)
        where = f"{python} (Python {pyver})"
        if v < POSITION_FIX:
            findings.append(Finding(
                Severity.ERROR, f"LeRobot {version} in {where} predates the Feetech position-overflow fix",
                "Builds before 0.6.0 could read wrong positions and before 0.6.1 stalled on one failed read: "
                "both show up as small jerky movements.",
                ["Make a fresh environment: python3 -m venv ~/lerobot-env && ~/lerobot-env/bin/pip install 'lerobot[feetech]'",
                 "Then delete the old calibration and recalibrate (see the calibration check)."]))
        elif v < RETRY_FIX:
            findings.append(Finding(
                Severity.WARNING, f"LeRobot {version} in {where} does not retry failed bus reads",
                "Teleop stalls or crashes with \"Failed to sync read 'Present_Position'\" when several joints move.",
                ["pip install -U 'lerobot[feetech]'  (0.6.1 or newer)"]))
        else:
            findings.append(Finding(Severity.OK, f"LeRobot {version} in {where}"
                                                 + ("" if rustypot == "yes" or v < (0, 7, 0) else " - but rustypot is missing")))
            if v >= (0, 7, 0) and rustypot != "yes":
                findings.append(Finding(Severity.ERROR, "LeRobot 0.7 needs the rustypot motor driver",
                                        fix=["pip install 'lerobot[feetech]'  (the [feetech] extra pulls it in)"]))
    if not found:
        findings.append(Finding(
            Severity.INFO, "No LeRobot install found in the usual places",
            "Looked in ~/lerobot-env, ~/.venv, conda envs named 'lerobot' and this Python.",
            ["python3 -m venv ~/lerobot-env && source ~/lerobot-env/bin/activate && pip install 'lerobot[feetech]'"]))
    return findings


def check_calibration() -> list[Finding]:
    root = calibration_dir()
    files = sorted(root.glob("**/*.json")) if root.exists() else []
    if not files:
        return [Finding(Severity.INFO, f"No calibration files in {root}",
                        "Expected before the first lerobot-calibrate run.")]
    findings: list[Finding] = []
    for f in files:
        made = datetime.fromtimestamp(f.stat().st_mtime).date()
        label = f"{f.relative_to(root)} (saved {made})"
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            findings.append(Finding(Severity.ERROR, f"{label}: unreadable JSON", fix=[f"rm '{f}' and recalibrate"]))
            continue
        bad = [joint for joint, c in data.items() if isinstance(c, dict)
               and c.get("range_min") is not None and c.get("range_max") is not None
               and c["range_min"] >= c["range_max"]]
        if bad:
            findings.append(Finding(Severity.ERROR, f"{label}: empty or inverted range for {bad}",
                                    "That joint was not swept to both limits during calibration.",
                                    [f"rm '{f}' and recalibrate, moving each joint to both hard limits."]))
        elif made < POSITION_FIX_DATE:
            findings.append(Finding(
                Severity.WARNING, f"{label}: made before the 2026 offset/overflow fixes",
                "Stale calibration makes the follower chase an unreachable target in small steps.",
                [f"rm '{f}', then lerobot-calibrate again with the joints centred first."]))
        else:
            findings.append(Finding(Severity.OK, f"{label}: {len(data)} joints"))
    return findings


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    findings = check_installs() + check_calibration()
    print_findings(findings)
    return 0 if all(f.severity < Severity.ERROR for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
