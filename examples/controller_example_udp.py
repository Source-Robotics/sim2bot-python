"""Example PC-side controller for Sim2Bot over raw UDP / TCP.

Same idea as controller_example.py, but using the bridge's raw-socket endpoints
instead of WebSocket — for controller code that speaks UDP or TCP rather than WS.
It DISCOVERS the loaded robots with a `describe` request, then drives an arm with
a joint sine (+ gripper) or a mobile/aerial base with a base_velocity command,
depending on what's loaded.

The bridge listens on (defaults, override with BRIDGE_UDP_PORT / BRIDGE_TCP_PORT):
    UDP  127.0.0.1:8771   (one JSON object per datagram)
    TCP  127.0.0.1:8770   (newline-delimited JSON: one JSON object + "\n" per line)

Both carry the same command/telemetry schema as WebSocket, including the optional
"robot" index for multi-robot scenes.

Run the bridge (uvicorn ...), connect the browser sim, then:
    python examples/controller_example_udp.py udp 0     # drive robot 0 over UDP
    python examples/controller_example_udp.py tcp 1     # drive robot 1 over TCP
"""

from __future__ import annotations

import json
import math
import socket
import sys
import time

HOST = "127.0.0.1"
UDP_PORT = 8771
TCP_PORT = 8770

# Fallback home (radians) if discovery returns nothing (no sim connected yet).
FALLBACK_HOME = [
    [0.0, 0.0, 0.0, -1.57079, 0.0, 1.57079, -0.7853],  # Franka Panda
    [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0],  # UR5e
]


def joint_target(home: list[float], t: float) -> list[float]:
    joint = min(3, len(home) - 1)
    target = list(home)
    target[joint] = home[joint] + 0.4 * math.sin(t)
    return target


def telemetry_line(msg: dict) -> str:
    line = f"robot {msg.get('robot')} q: {[round(v, 3) for v in msg.get('q', [])]}"
    if msg.get("gripper") is not None:
        line += f"  grip: {round(msg['gripper'], 2)}"
    sensors = msg.get("sensors")
    if sensors:
        parts = [
            f"{s.get('name')}({s.get('type')})={[round(v, 3) for v in s.get('values', [])]}"
            for s in sensors
        ]
        line += "  sensors: " + "  ".join(parts)
    return line


def command_for(info: dict, home: list[float], robot: int, t: float) -> dict:
    """Build the next command: base_velocity for a mobile/aerial base, else joints."""
    if info.get("locomotion") in ("mobile", "mobile-manipulator", "aerial"):
        # Drive in a slow circle; vz lifts an aerial base off the ground.
        vz = 0.2 if info.get("locomotion") == "aerial" else 0.0
        return {
            "type": "base_velocity",
            "robot": robot,
            "vx": 0.3 * math.cos(t),
            "vy": 0.3 * math.sin(t),
            "vz": vz,
            "omega": 0.4,
        }
    return {"type": "joint_position", "robot": robot, "q": joint_target(home, t)}


def discover_udp(sock: socket.socket, robot: int) -> dict:
    sock.sendto(json.dumps({"type": "describe"}).encode(), (HOST, UDP_PORT))
    for _ in range(50):
        try:
            data, _ = sock.recvfrom(65535)
            msg = json.loads(data.decode())
        except (socket.timeout, ValueError):
            continue
        if msg.get("type") == "scene":
            robots = msg.get("robots", [])
            return robots[robot] if robot < len(robots) else {}
    return {}


def discover_tcp(sock: socket.socket, robot: int) -> dict:
    sock.sendall(json.dumps({"type": "describe"}).encode() + b"\n")
    buffer = b""
    for _ in range(50):
        try:
            buffer += sock.recv(65535)
        except (socket.timeout, ValueError):
            continue
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            try:
                msg = json.loads(line.decode())
            except ValueError:
                continue
            if msg.get("type") == "scene":
                robots = msg.get("robots", [])
                return robots[robot] if robot < len(robots) else {}
    return {}


def home_for(info: dict, robot: int) -> list[float]:
    return info.get("home") or (
        FALLBACK_HOME[robot] if robot < len(FALLBACK_HOME) else [0.0] * 6
    )


def run_udp(robot: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.2)
    info = discover_udp(sock, robot)
    home = home_for(info, robot)
    sock.settimeout(0.01)
    t = 0.0
    while True:
        packet = command_for(info, home, robot, t)
        sock.sendto(json.dumps(packet).encode(), (HOST, UDP_PORT))
        try:  # telemetry is streamed back to the sender's address
            data, _ = sock.recvfrom(65535)
            msg = json.loads(data.decode())
            if msg.get("type") == "telemetry":
                print(telemetry_line(msg))
        except (socket.timeout, ValueError):
            pass
        time.sleep(0.05)
        t += 0.1


def run_tcp(robot: int) -> None:
    sock = socket.create_connection((HOST, TCP_PORT))
    sock.settimeout(0.2)
    info = discover_tcp(sock, robot)
    home = home_for(info, robot)
    sock.settimeout(0.01)
    buffer = b""
    t = 0.0
    while True:
        packet = command_for(info, home, robot, t)
        sock.sendall(json.dumps(packet).encode() + b"\n")
        try:  # newline-delimited telemetry
            buffer += sock.recv(65535)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                msg = json.loads(line.decode())
                if msg.get("type") == "telemetry":
                    print(telemetry_line(msg))
        except (socket.timeout, ValueError):
            pass
        time.sleep(0.05)
        t += 0.1


if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "udp"
    robot_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    try:
        (run_tcp if transport == "tcp" else run_udp)(robot_index)
    except KeyboardInterrupt:
        pass
