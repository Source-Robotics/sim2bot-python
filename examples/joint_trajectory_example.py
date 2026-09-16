"""Play a timed joint trajectory on one Sim2Bot robot (a simplified
FollowJointTrajectory), and show cancelling it partway through.

Run this script first, then open the Sim2Bot web app and click
Tools -> Bridge -> Connect. The script starts the bridge automatically and waits
for the browser simulator to announce robots.

    pip install sim2bot
    python examples/joint_trajectory_example.py

To play back a recording exported from the app's Motion panel ("Record & replay"
-> Export JSON), load the file and divide its `t` (milliseconds) by 1000:

    import json
    data = json.load(open("sim2bot-recording-....json"))
    points = [(frame["t"] / 1000, frame["q"]) for frame in data["trajectory"]]
    robot.move_trajectory(points)
"""

from __future__ import annotations

import time

from sim2bot import Robot


def main() -> None:
    print("Waiting for browser simulator...")
    print("In the Sim2Bot web app, open Tools -> Bridge -> Connect.")
    with Robot(auto_bridge=True, wait_for_sim=True, wait_for_sim_timeout=30) as robot:
        info = robot.describe()[0]
        print(f"Using [{info.index}] {info.name}")

        time.sleep(0.3)  # let the first telemetry packet arrive
        home = robot.state(info.index).q
        bent = [home[i] + (0.5 if i % 2 == 0 else -0.5) for i in range(info.dof)]

        # A 3-second out-and-back trajectory: home -> bent -> home, held at the
        # end. Waypoint times are seconds from the start of the trajectory.
        points = [(0.0, home), (1.5, bent), (3.0, home)]
        print("Playing a 3s trajectory (home -> bent -> home)...")
        robot.move_trajectory(points, robot=info.index)
        time.sleep(3.5)  # a little past the trajectory's own duration
        print(f"  q at end (should be back near home): {[round(v, 3) for v in robot.state(info.index).q]}")

        # Looping: repeats until stop_trajectory() or a new command is sent.
        print("Looping the same trajectory for 4s, then stopping mid-motion...")
        robot.move_trajectory(points, robot=info.index, loop=True)
        time.sleep(4.0)
        robot.stop_trajectory(robot=info.index)
        print(f"  q after stop (frozen in place): {[round(v, 3) for v in robot.state(info.index).q]}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
