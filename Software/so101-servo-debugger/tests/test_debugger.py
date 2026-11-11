"""Run from the project folder:  python -m unittest discover -s tests -t ."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from src import lerobot_env
from src.bus_tester import diagnose as diagnose_bus, scan_bus
from src.feetech import FeetechBus, build_packet, decode_errors, decode_value, encode_value
from src.findings import Finding, Severity
from src.motor_ping import diagnose as diagnose_ping, ping_all, read_stress
from src.motor_reset import SetupError, configure, set_id_and_baud
from src.power_diagnostics import check_power, load_test, monitor
from src.report_generator import to_markdown
from src.settings_audit import audit
from tests.fake_bus import FakeServo, FakeTransport, arm


def bus_for(servos: list[FakeServo], baudrate: int = 1_000_000) -> FeetechBus:
    return FeetechBus(FakeTransport(servos, baudrate), timeout=0.003)


def titles(findings: list[Finding], severity: Severity | None = None) -> list[str]:
    return [f.title for f in findings if severity is None or f.severity == severity]


class Protocol(unittest.TestCase):
    def test_ping_packet_matches_feetech_spec(self) -> None:
        self.assertEqual(build_packet(1, 0x01), bytes.fromhex("ffff01020 1fb".replace(" ", "")))

    def test_sign_magnitude(self) -> None:
        self.assertEqual(decode_value("Present_Load", (1 << 10) | 100), -100)
        self.assertEqual(encode_value("Goal_Position", -5), (1 << 15) | 5)
        self.assertEqual(decode_value("Present_Voltage", 50), 50)

    def test_error_bits(self) -> None:
        self.assertEqual(decode_errors(0b101), ["input voltage out of range", "overheating"])

    def test_read_and_write(self) -> None:
        servo = FakeServo(3)
        bus = bus_for([servo])
        self.assertEqual(bus.read(3, "Model_Number"), 777)
        bus.write(3, "Goal_Position", 1000)
        self.assertEqual(bus.read(3, "Goal_Position"), 1000)


class BusScan(unittest.TestCase):
    def test_healthy_arm(self) -> None:
        findings = diagnose_bus(scan_bus(bus_for(arm()), ids=range(0, 10)))
        self.assertEqual(len(titles(findings, Severity.OK)), 6)
        self.assertFalse(titles(findings, Severity.ERROR))

    def test_wrong_baudrate_is_reported(self) -> None:
        servos = arm()
        servos[3] = FakeServo(4, baudrate=115_200)
        findings = diagnose_bus(scan_bus(bus_for(servos), ids=range(0, 10)))
        self.assertTrue(any("115200" in t for t in titles(findings, Severity.ERROR)))

    def test_duplicate_ids_are_reported(self) -> None:
        findings = diagnose_bus(scan_bus(bus_for([FakeServo(1), FakeServo(1)]), ids=range(0, 5)))
        self.assertTrue(any("Garbled" in t for t in titles(findings, Severity.ERROR)))

    def test_nothing_answers(self) -> None:
        findings = diagnose_bus(scan_bus(bus_for([]), baudrates=[1_000_000], ids=range(0, 5)))
        self.assertIn("No motor answered at any baudrate", titles(findings))


class Ping(unittest.TestCase):
    def test_chain_break_located(self) -> None:
        findings = diagnose_ping(ping_all(bus_for(arm()[:3]), repeats=2))
        self.assertTrue(any("Chain breaks after motor 3" in t for t in titles(findings)))

    def test_single_missing_motor(self) -> None:
        servos = [s for s in arm() if s.motor_id != 2]
        findings = diagnose_ping(ping_all(bus_for(servos), repeats=2))
        self.assertTrue(any("ID 2" in t and "motors after it do" in t for t in titles(findings)))

    def test_intermittent_motor(self) -> None:
        servos = arm()
        servos[4] = FakeServo(5, answer_every=2)
        findings = diagnose_ping(ping_all(bus_for(servos), repeats=6))
        self.assertTrue(any("intermittent" in t for t in titles(findings, Severity.ERROR)))

    def test_read_stress_counts_failures(self) -> None:
        servos = arm()
        servos[2] = FakeServo(3, answer_every=4)
        findings = read_stress(bus_for(servos), seconds=0.3)
        self.assertEqual(findings[0].severity, Severity.ERROR)
        clean = read_stress(bus_for(arm()), seconds=0.2)
        self.assertEqual(clean[0].severity, Severity.OK)


class Power(unittest.TestCase):
    def test_standard_kit_on_5v(self) -> None:
        _, findings = check_power(bus_for(arm(volts=5.1)))
        self.assertIn("Supply looks like a 5 V supply (5.1 V)", titles(findings))
        self.assertFalse(titles(findings, Severity.CRITICAL) + titles(findings, Severity.ERROR))

    def test_12v_adapter_on_7v4_motors_is_critical(self) -> None:
        _, findings = check_power(bus_for(arm(volts=12.1)))
        self.assertEqual(len(titles(findings, Severity.CRITICAL)), 6)

    def test_12v_motors_on_5v_are_weak(self) -> None:
        _, findings = check_power(bus_for(arm(volts=5.0, twelve_volt=True)))
        self.assertIn("12 V motors are running from about 5 V", titles(findings))

    def test_mixed_motor_voltages(self) -> None:
        servos = arm(volts=5.0)
        servos[0] = FakeServo(1, volts=5.0, twelve_volt=True)
        _, findings = check_power(bus_for(servos))
        self.assertIn("This arm mixes 7.4 V and 12 V motors on one supply", titles(findings))

    def test_monitor_catches_dropouts(self) -> None:
        servos = arm()
        servos[1] = FakeServo(2, answer_every=3)
        findings = monitor(bus_for(servos), seconds=0.4)
        self.assertTrue(any("dropped out" in t for t in titles(findings, Severity.ERROR)))

    def test_load_test_sag_and_brownout(self) -> None:
        sag = load_test(bus_for([FakeServo(3, volts=5.0, load_volts=4.3)]), 3, seconds=0.5)
        self.assertTrue(any("sagged to 4.3 V" in t for t in titles(sag, Severity.ERROR)))
        brownout = load_test(bus_for([FakeServo(3, volts=5.0, load_volts=3.0)]), 3, seconds=0.5)
        self.assertTrue(titles(brownout, Severity.CRITICAL))
        steady = FakeServo(3, volts=5.0, load_volts=4.9)
        ok = load_test(bus_for([steady]), 3, seconds=0.5)
        self.assertTrue(any("held up" in t for t in titles(ok, Severity.OK)))
        self.assertEqual(steady.mem[40], 0, "torque must be off after the test")


class Setup(unittest.TestCase):
    def test_set_id_from_factory_default(self) -> None:
        servo = FakeServo(1, baudrate=115_200)
        set_id_and_baud(bus_for([servo]), 4, search_ids=range(0, 5))
        self.assertEqual((servo.motor_id, servo.baudrate, servo.mem[55]), (4, 1_000_000, 1))

    def test_refuses_with_two_motors(self) -> None:
        with self.assertRaises(SetupError):
            set_id_and_baud(bus_for([FakeServo(1), FakeServo(2)]), 4, search_ids=range(0, 5))

    def test_configure_writes_lerobot_settings(self) -> None:
        servos = arm()
        servos[0].mem[21] = 32  # Feetech default P
        servos[5].mem[16:18] = (1000).to_bytes(2, "little")
        bus = bus_for(servos)
        self.assertTrue(titles(audit(bus, [1]), Severity.WARNING))
        configure(bus)
        self.assertEqual(servos[0].mem[21], 16)
        self.assertEqual(int.from_bytes(servos[5].mem[16:18], "little"), 500)
        self.assertTrue(all(s.mem[55] == 1 and s.mem[40] == 0 for s in servos))
        self.assertEqual(titles(audit(bus), Severity.OK), [f"ID {i} ({n}): settings look right" for i, n in
                         zip(range(1, 7), ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"])])

    def test_low_torque_limit_flagged(self) -> None:
        servo = FakeServo(2)
        servo.mem[16:18] = (300).to_bytes(2, "little")
        self.assertTrue(any("Max_Torque_Limit 300" in t for t in titles(audit(bus_for([servo]), [2]))))


class Environment(unittest.TestCase):
    def test_parse_version(self) -> None:
        self.assertLess(lerobot_env.parse_version("0.5.1"), lerobot_env.RETRY_FIX)
        self.assertGreaterEqual(lerobot_env.parse_version("0.7.1rc1"), lerobot_env.RETRY_FIX)

    def test_calibration_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "robots/so101_follower"
            folder.mkdir(parents=True)
            good = {"shoulder_pan": {"id": 1, "range_min": 700, "range_max": 3400}}
            bad = {"wrist_roll": {"id": 5, "range_min": 2048, "range_max": 2048}}
            (folder / "good.json").write_text(json.dumps(good))
            (folder / "bad.json").write_text(json.dumps(bad))
            old = folder / "old.json"
            old.write_text(json.dumps(good))
            os.utime(old, (1_700_000_000, 1_700_000_000))  # Nov 2023
            os.environ["HF_LEROBOT_CALIBRATION"] = tmp
            try:
                findings = lerobot_env.check_calibration()
            finally:
                del os.environ["HF_LEROBOT_CALIBRATION"]
        severities = {f.title.split(" ")[0].split("/")[-1]: f.severity for f in findings}
        self.assertEqual(severities, {"bad.json": Severity.ERROR, "good.json": Severity.OK, "old.json": Severity.WARNING})


class Report(unittest.TestCase):
    def test_markdown_lists_worst_first(self) -> None:
        text = to_markdown({"A": [Finding(Severity.WARNING, "warn one"), Finding(Severity.CRITICAL, "bad supply", fix=["unplug"])]}, "/dev/x")
        self.assertLess(text.index("bad supply"), text.index("warn one"))
        self.assertIn("Overall: **CRITICAL**", text)
        self.assertIn("- unplug", text)


if __name__ == "__main__":
    unittest.main()
