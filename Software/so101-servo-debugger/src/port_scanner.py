"""US-001: find the arm's USB serial port and check that this user can use it (Linux focus)."""

from __future__ import annotations

import argparse
import grp
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .findings import Finding, Severity, print_findings

# USB-serial chips found on SO-100/SO-101 controller boards.
KNOWN_ADAPTERS: dict[tuple[int, int], str] = {
    (0x1A86, 0x55D3): "CH343 (Waveshare bus servo adapter)",
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x5523): "CH341",
    (0x10C4, 0xEA60): "CP210x",
    (0x0403, 0x6001): "FTDI FT232",
}


@dataclass
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None

    @property
    def adapter(self) -> str | None:
        if self.vid is None or self.pid is None:
            return None
        return KNOWN_ADAPTERS.get((self.vid, self.pid))

    @property
    def usb_id(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}" if self.vid is not None and self.pid is not None else "-"


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as lp

    ports = []
    for p in lp.comports():
        if p.vid is None and not p.device.startswith(("/dev/ttyACM", "/dev/ttyUSB", "/dev/tty.usb", "/dev/cu.usb")):
            continue  # skip built-in UARTs like /dev/ttyS0
        ports.append(PortInfo(p.device, p.description or "", p.vid, p.pid, p.serial_number))
    return ports


def stable_names(device: str) -> list[str]:
    """/dev/serial/by-id and by-path links for a device: /dev/ttyACM0 and 1 swap between plug-ins."""
    names = []
    for folder in ("/dev/serial/by-id", "/dev/serial/by-path"):
        if not os.path.isdir(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            link = os.path.join(folder, entry)
            if os.path.realpath(link) == os.path.realpath(device):
                names.append(link)
    return names


def vendor_ch343_devices() -> list[str]:
    """WCH's own ch343 driver renames the board to /dev/ttyCH343USB*, which LeRobot does not list."""
    try:
        return sorted("/dev/" + n for n in os.listdir("/dev") if n.startswith("ttyCH343USB"))
    except OSError:
        return []


def brltty_claims_wch() -> list[str]:
    """brltty udev rule files that match WCH (1a86) USB-serial chips."""
    hits = []
    for folder in ("/usr/lib/udev/rules.d", "/lib/udev/rules.d", "/etc/udev/rules.d"):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if "brltty" not in name:
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    if "1a86" in fh.read().lower():
                        hits.append(path)
            except OSError:
                continue
    return hits


def in_flatpak() -> bool:
    return os.path.exists("/.flatpak-info")


def service_active(name: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    result = subprocess.run(["systemctl", "is-active", "--quiet", name], check=False)
    return result.returncode == 0


def package_installed(name: str) -> bool:
    for cmd in (["rpm", "-q", name], ["dpkg", "-s", name]):
        if shutil.which(cmd[0]):
            return subprocess.run(cmd, capture_output=True, check=False).returncode == 0
    return False


def udev_rule(port: PortInfo, name: str = "so101_follower") -> str:
    match = f'ATTRS{{idVendor}}=="{port.vid:04x}", ATTRS{{idProduct}}=="{port.pid:04x}"' if port.vid is not None and port.pid is not None else ""
    if port.serial_number:
        match += f', ATTRS{{serial}}=="{port.serial_number}"'
    return (
        f'SUBSYSTEM=="tty", {match}, MODE="0660", GROUP="dialout", '
        f'ENV{{ID_MM_DEVICE_IGNORE}}="1", SYMLINK+="{name}"'
    )


def check_port_access(port: PortInfo) -> list[Finding]:
    findings: list[Finding] = []
    dev = port.device
    if os.access(dev, os.R_OK | os.W_OK):
        findings.append(Finding(Severity.OK, f"{dev}: readable and writable by you"))
        return findings
    try:
        group = grp.getgrgid(os.stat(dev).st_gid).gr_name
    except (KeyError, OSError):
        group = "dialout"
    member = group in {grp.getgrgid(g).gr_name for g in os.getgroups()}
    user = os.environ.get("USER", "$USER")
    if member:
        detail = f"You are in '{group}', but this login session predates it."
        fix = ["Log out and back in (or reboot) so the new group membership applies."]
    else:
        detail = f"The port belongs to group '{group}' and you are not in it."
        fix = [f"sudo usermod -aG {group} {user}   then log out and back in",
               f"quick test only (resets on replug): sudo chmod 666 {dev}"]
    findings.append(Finding(Severity.ERROR, f"{dev}: permission denied", detail, fix))
    return findings


def scan() -> tuple[list[PortInfo], list[Finding]]:
    findings: list[Finding] = []

    if in_flatpak():
        findings.append(Finding(
            Severity.ERROR, "Running inside a Flatpak sandbox (e.g. VS Code from Flathub)",
            "The sandbox usually cannot see /dev/ttyACM*, so the arm looks unplugged.",
            ["Run from a host terminal (Konsole), or prefix with: flatpak-spawn --host"],
        ))

    user = os.environ.get("USER", "$USER")
    try:
        dialout = grp.getgrnam("dialout")
    except KeyError:
        dialout = None
    if dialout is not None:
        session = dialout.gr_gid in os.getgroups()
        listed = user in dialout.gr_mem
        if not session and not listed:
            findings.append(Finding(
                Severity.ERROR, f"You ({user}) are not in the 'dialout' group",
                "Serial ports on Fedora/Nobara belong to dialout, so LeRobot gets 'Permission denied'.",
                [f"sudo usermod -aG dialout {user}   then log out and back in (a reboot is simplest)"],
            ))
        elif listed and not session:
            findings.append(Finding(
                Severity.WARNING, "You were added to 'dialout' but this login session predates it",
                fix=["Log out and back in (or reboot)."],
            ))

    ports = list_ports()
    vendor = vendor_ch343_devices()
    if vendor:
        findings.append(Finding(
            Severity.ERROR, f"WCH's vendor ch343 driver is loaded ({', '.join(vendor)})",
            "It renames the Waveshare board to /dev/ttyCH343USB*, which lerobot-find-port does not list. "
            "The kernel's own cdc_acm driver (/dev/ttyACM*) is the one to use.",
            ["sudo rmmod ch343, then remove the module installed by the earlier attempt and replug."],
        ))
    if not ports:
        findings.append(Finding(
            Severity.ERROR, "No USB serial port found",
            "The controller board's USB chip did not show up. Servo power is not needed for this "
            "step - the board enumerates from USB alone.",
            ["Use a data USB cable (many short cables are charge-only); try another port.",
             "Check:  lsusb | grep -i 1a86   - a WCH CH343 'USB Single Serial' is the Waveshare board.",
             "Watch the kernel log while plugging in:  journalctl -kf | grep -iE 'ch34|cdc_acm|tty(ACM|USB)|brltty'",
             "Waveshare board: both jumpers must be on the B (USB) side.",
             "Listed by lsusb but no /dev/ttyACM*: brltty grabbed it (dmesg mentions brltty) or you have "
             "not logged out since joining dialout."],
        ))

    mm_active = service_active("ModemManager")
    brltty = package_installed("brltty")
    brltty_rules = brltty_claims_wch() if brltty else []
    if brltty_rules:
        findings.append(Finding(
            Severity.WARNING, "brltty's udev rules match WCH (1a86) USB-serial chips",
            "The braille daemon can claim the board as it appears (dmesg shows \"... while 'brltty' sets config\").\n"
            + "\n".join(brltty_rules),
            ["No braille display:  sudo dnf remove brltty",
             "Keep brltty but stop it touching serial devices:  sudo systemctl mask --now brltty-udev.service"],
        ))
    for port in ports:
        label = port.adapter or "unrecognised adapter"
        findings.append(Finding(
            Severity.INFO, f"Found {port.device}  [{port.usb_id}] {label}",
            f"{port.description}" + (f"  serial {port.serial_number}" if port.serial_number else ""),
        ))
        findings += check_port_access(port)
        if mm_active and "ttyACM" in port.device:
            findings.append(Finding(
                Severity.WARNING, f"{port.device}: ModemManager is running and probes this port",
                "For 10-30 s after plug-in it sends AT commands to new /dev/ttyACM devices: the port is "
                "'Device or resource busy' and the servo bus gets garbage.",
                ["You use no mobile modem:  sudo systemctl disable --now ModemManager",
                 "Otherwise add the udev rule below (ENV{ID_MM_DEVICE_IGNORE}=\"1\")."],
            ))
        names = stable_names(port.device)
        by_id = [n for n in names if "/by-id/" in n]
        if names:
            findings.append(Finding(
                Severity.INFO, f"Use a stable name for {port.device}",
                "/dev/ttyACM0 and 1 swap between plug-ins:\n" + "\n".join(names),
                [f"Pass --robot.port={by_id[0] if by_id else names[0]} to LeRobot."],
            ))
        if port.vid is not None:
            findings.append(Finding(
                Severity.INFO, f"udev rule for {port.device} (optional)",
                "Fixed name plus ModemManager ignore:\n" + udev_rule(port),
                ["Save as /etc/udev/rules.d/99-so101.rules, then: sudo udevadm control --reload && sudo udevadm trigger"],
            ))

    serials = [p.serial_number for p in ports if p.serial_number]
    if len(serials) != len(set(serials)) or (len(ports) > 1 and len(serials) < len(ports)):
        findings.append(Finding(
            Severity.WARNING, "Your boards do not have distinct USB serial numbers",
            "CH343 boards can share one serial, so both arms get the same /dev/serial/by-id name.",
            ["Use /dev/serial/by-path/... (it encodes the physical USB socket) and always plug each arm "
             "into the same socket."],
        ))
    return ports, findings


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    ports, findings = scan()
    print_findings(findings)
    return 0 if ports and all(f.severity < Severity.ERROR for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
