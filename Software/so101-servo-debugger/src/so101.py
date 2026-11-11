"""SO-100 / SO-101 arm layout, from the LeRobot SO-101 guide and TheRobotStudio bill of materials."""

from __future__ import annotations

from dataclasses import dataclass

# Daisy-chain order: controller board -> 1 -> 2 -> 3 -> 4 -> 5 -> 6.
JOINTS: dict[int, str] = {
    1: "shoulder_pan",
    2: "shoulder_lift",
    3: "elbow_flex",
    4: "wrist_flex",
    5: "wrist_roll",
    6: "gripper",
}

# Leader arm gear ratios per joint (follower uses 1/345 on every joint).
LEADER_GEARS: dict[int, str] = {1: "1/191", 2: "1/345", 3: "1/191", 4: "1/147", 5: "1/147", 6: "1/147"}

# LeRobot's SOFollower.configure() values (robots/so_follower).
FOLLOWER_PID = {"P_Coefficient": 16, "I_Coefficient": 0, "D_Coefficient": 32}
GRIPPER_LIMITS = {"Max_Torque_Limit": 500, "Protection_Current": 250, "Overload_Torque": 25}


@dataclass(frozen=True)
class SupplyType:
    name: str
    servos: str
    low: float
    high: float


# Measured at the servo (Present_Voltage). Standard SO-101 kits are 7.4 V servos on a 5 V supply;
# the 12 V servo option needs a 12 V 5 A+ supply. The leader arm is always 7.4 V / 5 V.
SUPPLIES = [
    SupplyType("5 V supply", "7.4 V STS3215 (standard follower, every leader)", 4.3, 6.8),
    SupplyType("7.4 V battery/supply", "7.4 V STS3215", 6.8, 8.6),
    SupplyType("12 V supply", "12 V STS3215 (high-torque follower)", 10.5, 13.5),
]


def joint_name(motor_id: int) -> str:
    return JOINTS.get(motor_id, f"id {motor_id}")


def classify_supply(volts: float) -> SupplyType | None:
    for supply in SUPPLIES:
        if supply.low <= volts <= supply.high:
            return supply
    return None
