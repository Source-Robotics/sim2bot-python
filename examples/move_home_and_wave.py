"""Move one Sim2Bot robot home, wave one joint, then return home.

Run this script first, then open the Sim2Bot web app and click
Tools -> Bridge -> Connect. The script starts the bridge automatically and waits
for the browser simulator to announce robots.

    pip install sim2bot
    python examples/move_home_and_wave.py        # robot index 0
    python examples/move_home_and_wave.py 1      # robot index 1
"""

from __future__ import annotations

import sys
import time
from typing import Optional

from sim2bot import Robot, RobotInfo


def clamp_to_limit(value: float, limit: Optional[list[float]]) -> float:
    if not limit or len(limit) < 2:
        return value
    lo, hi = limit
    return max(lo, min(hi, value))


def target_with_offset(info: RobotInfo, home: list[float], joint: int, offset: float) -> list[float]:
    target = list(home)
    limit = info.joint_limits[joint] if joint < len(info.joint_limits) else None
    target[joint] = clamp_to_limit(home[joint] + offset, limit)
    return target


def print_state(robot: Robot, index: int) -> None:
    state = robot.state(index)
    if not state:
        return
    print(f"  q: {[round(value, 3) for value in state.q]}")
    print(f"  tcp: {[round(value, 3) for value in state.tcp]}")


def main(robot_index: int) -> None:
    print("Waiting for browser simulator...")
    print("In the Sim2Bot web app, open Tools -> Bridge -> Connect.")
    with Robot(auto_bridge=True, wait_for_sim=True, wait_for_sim_timeout=30) as robot:
        robots = robot.describe()
        if robot_index >= len(robots):
            print(f"Robot index {robot_index} not found; using robot 0.")
            robot_index = 0
        info = robots[robot_index]
        home = info.home or [0.0] * info.dof
        joint = 0 if info.dof > 0 else -1
        print(f"Using [{info.index}] {info.name} id={info.id or '(none)'}")

        print("Moving home...")
        robot.move_to(home, robot=info.index)
        robot.wait_until_reached(home, robot=info.index, timeout=5.0)
        print_state(robot, info.index)

        if joint < 0:
            print("Robot has no arm joints to wave.")
            return

        for offset in (0.35, -0.35, 0.0):
            target = target_with_offset(info, home, joint, offset)
            print(f"Moving joint {joint + 1} to {round(target[joint], 3)}...")
            robot.move_to(target, robot=info.index)
            robot.wait_until_reached(target, robot=info.index, timeout=5.0)
            print_state(robot, info.index)
            time.sleep(0.3)

        if info.has_gripper:
            print("Opening and closing gripper...")
            robot.gripper(1.0, robot=info.index)
            time.sleep(0.5)
            robot.gripper(0.0, robot=info.index)


if __name__ == "__main__":
    index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    try:
        main(index)
    except KeyboardInterrupt:
        pass
