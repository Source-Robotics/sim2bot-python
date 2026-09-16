"""Example controller using the sim2bot Python SDK (the clean, recommended path).

Discovers the loaded robot, drives a joint sine + gripper, prints telemetry, and
pulls a few camera frames. Compare with controller_example.py (raw WebSocket) and
controller_example_udp.py (raw UDP/TCP) for the low-level protocol.

Install the SDK first:

    pip install sim2bot                # control + telemetry
    pip install "sim2bot[cv2]"         # + decode camera frames to images

Run this script, then connect the browser sim (Tools -> Bridge -> Connect):

    python examples/controller_example_sdk.py
    python examples/controller_example_sdk.py 1   # drive robot index 1
"""

from __future__ import annotations

import math
import sys
import time

from sim2bot import Robot


def main(robot_index: int) -> None:
    with Robot(auto_bridge=True, wait_for_sim=True, wait_for_sim_timeout=30) as robot:
        infos = robot.describe()
        info = infos[robot_index] if robot_index < len(infos) else infos[0]
        print(
            f"robot {info.index}: {info.name} id={info.id or '(none)'} dof={info.dof} "
            f"hasGripper={info.has_gripper} locomotion={info.locomotion}"
        )

        cams = robot.cameras()
        print("cameras:", [c.get("id") for c in cams] or "(none)")

        # Optionally pull a few frames from the first camera.
        if cams:
            with robot.camera(cams[0]["id"], fps=15, width=320, height=240) as feed:
                for _ in range(5):
                    frame = feed.read(timeout=3.0)
                    if frame is None:
                        break
                    print(f"  frame {frame.sequence} {len(frame.data)} B  age={frame.age*1000:.0f}ms")

        # Drive a slow joint sine + gripper for a few seconds.
        home = info.home or [0.0] * info.dof
        joint = min(3, len(home) - 1)
        t = 0.0
        end = time.time() + 5.0
        while time.time() < end:
            target = list(home)
            if joint >= 0:
                target[joint] = home[joint] + 0.4 * math.sin(t)
            robot.move_to(target, robot=info.index)
            if info.has_gripper:
                robot.gripper(0.5 + 0.5 * math.sin(t), robot=info.index)
            state = robot.state(info.index)
            if state:
                print("  q:", [round(v, 3) for v in state.q])
            time.sleep(0.1)
            t += 0.2


if __name__ == "__main__":
    index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    try:
        main(index)
    except KeyboardInterrupt:
        pass
