"""List robots currently announced by the Sim2Bot browser simulator.

Run the Sim2Bot web app, open Tools -> Bridge, click Connect, then:

    pip install sim2bot
    python examples/list_robots.py

`Robot(auto_bridge=True)` starts the local bridge if it is not already running.
The browser simulator still needs to be open and connected because it owns the
MuJoCo scene and announces the loaded robots to the bridge.
"""

from __future__ import annotations

from sim2bot import Robot


def main() -> None:
    print("Waiting for browser simulator to announce robots...")
    print("In the Sim2Bot web app, open Tools -> Bridge -> Connect.")
    try:
        with Robot(auto_bridge=True, wait_for_sim=True, wait_for_sim_timeout=30) as robot:
            robots = robot.describe()
    except TimeoutError:
        print("No robots found.")
        print("Make sure the Sim2Bot web app shows Bridge status: connected.")
        return

    for info in robots:
        print(f"[{info.index}] {info.name}")
        print(f"  id: {info.id or '(not announced)'}")
        print(f"  dof: {info.dof}")
        print(f"  joints: {info.joint_names or '(not announced)'}")
        print(f"  home: {info.home or '(not announced)'}")
        print(f"  gripper: {info.has_gripper}")
        print(f"  locomotion: {info.locomotion}")
        print(f"  base_dof: {info.base_dof}")


if __name__ == "__main__":
    main()
