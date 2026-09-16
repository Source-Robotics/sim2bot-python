"""Example PC-side controller for Sim2Bot (WebSocket).

Connects to the local bridge as a controller, DISCOVERS the loaded robots with a
`describe` request (so it doesn't hard-code DOF/home), drives one robot's elbow
with a slow sine, toggles the gripper if the robot has one, and prints back the
telemetry the simulator streams (q, qd, tcp, gripper, sensors).

Multi-robot: pass a robot INDEX as the first argument (0 = primary robot,
1.. = additional robots in scene order). The simulator announces the available
robots, and the bridge tags telemetry with each robot's index/name. Commands
carry the same "robot" index; omit it to address robot 0.

Run the bridge first:

    pip install sim2bot
    sim2bot bridge --host 127.0.0.1 --port 8765

Then, in the browser app, open Tools -> Bridge and click Connect
(default ws://localhost:8765/ws). Finally run this script:

    python examples/controller_example.py          # drive robot 0
    python examples/controller_example.py 1        # drive robot 1 (e.g. a 2nd arm)

For raw TCP/UDP instead of WebSocket, see controller_example_udp.py.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys

import websockets

BRIDGE_URL = "ws://localhost:8765/ws"

# Fallback home configurations (radians), used only if discovery returns nothing
# (e.g. no simulator connected yet). Discovery via `describe` is preferred.
FALLBACK_HOME = [
    [0.0, 0.0, 0.0, -1.57079, 0.0, 1.57079, -0.7853],  # Franka Panda (7 DOF)
    [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0],  # UR5e (6 DOF)
]


def format_sensors(sensors: list) -> str:
    # Authored scene sensors that belong to this robot (IMU, force, jointpos, ...).
    parts = []
    for sensor in sensors:
        values = [round(v, 3) for v in sensor.get("values", [])]
        parts.append(f"{sensor.get('name')}({sensor.get('type')})={values}")
    return "  ".join(parts)


async def describe(ws: "websockets.WebSocketClientProtocol") -> list:
    """Ask the bridge what robots are loaded; return the `robots` descriptor list."""
    await ws.send(json.dumps({"type": "describe"}))
    # The reply may arrive after some telemetry; read until we see the scene.
    for _ in range(50):
        try:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        except asyncio.TimeoutError:
            break
        if message.get("type") == "scene":
            return message.get("robots", [])
    return []


async def read_telemetry(ws: "websockets.WebSocketClientProtocol") -> None:
    async for raw in ws:
        message = json.loads(raw)
        if message.get("type") == "telemetry":
            q = message.get("q", [])
            line = (
                f"robot {message.get('robot')} ({message.get('name')}) "
                f"q: {[round(value, 3) for value in q]}"
            )
            if message.get("gripper") is not None:
                line += f"  grip: {round(message['gripper'], 2)}"
            sensors = message.get("sensors")
            if sensors:
                line += f"  sensors: {format_sensors(sensors)}"
            print(line)


async def main(robot: int) -> None:
    async with websockets.connect(BRIDGE_URL) as ws:
        await ws.send(json.dumps({"type": "hello", "role": "controller"}))

        robots = await describe(ws)
        info = robots[robot] if robot < len(robots) else {}
        if info:
            print(
                f"discovered robot {robot}: {info.get('name')} "
                f"dof={info.get('dof')} hasGripper={info.get('hasGripper')} "
                f"locomotion={info.get('locomotion')}"
            )
        # Prefer the discovered home pose; fall back to a known one.
        home = info.get("home") or (
            FALLBACK_HOME[robot] if robot < len(FALLBACK_HOME) else [0.0] * 6
        )
        has_gripper = bool(info.get("hasGripper"))

        reader = asyncio.create_task(read_telemetry(ws))

        t = 0.0
        joint = min(3, len(home) - 1)  # wave a mid arm joint
        try:
            while True:
                target = list(home)
                target[joint] = home[joint] + 0.4 * math.sin(t)
                await ws.send(
                    json.dumps({"type": "joint_position", "robot": robot, "q": target})
                )
                if has_gripper:
                    # Slowly open/close the gripper in sync with the sine.
                    frac = 0.5 + 0.5 * math.sin(t)
                    await ws.send(
                        json.dumps({"type": "gripper", "robot": robot, "fraction": frac})
                    )
                await asyncio.sleep(0.05)
                t += 0.1
        finally:
            reader.cancel()


if __name__ == "__main__":
    robot_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    try:
        asyncio.run(main(robot_index))
    except KeyboardInterrupt:
        pass
