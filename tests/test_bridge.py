"""Integration tests for the Sim2Bot bridge relay.

These run the real bridge (uvicorn subprocess on test ports) and exercise it with
real clients — a fake "simulator" + a "controller" over each transport — so we
cover the actual relay, not a mock. Coverage: WebSocket describe/command/telemetry
relay, the bridge_status broadcast, raw TCP, raw UDP, the binary /video relay, and
the scene-cache lifecycle (cached on hello, cleared when the last simulator goes).

Run from the repository root:  pytest
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from websockets.sync.client import connect as ws_connect

# Test ports, offset from the defaults so a running bridge (8765/8770/8771) on the
# dev machine doesn't clash with the test instance.
WS_PORT = 8795
TCP_PORT = 8796
UDP_PORT = 8797
ROBOTS = [{"index": 0, "id": "franka_primary", "name": "Franka", "dof": 7}]


@pytest.fixture(scope="session")
def bridge():
    """Start the real bridge on test ports for the whole session."""
    env = {**os.environ, "BRIDGE_TCP_PORT": str(TCP_PORT), "BRIDGE_UDP_PORT": str(UDP_PORT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sim2bot.bridge_server:app",
         "--host", "127.0.0.1", "--port", str(WS_PORT), "--log-level", "warning"],
        env=env,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{WS_PORT}/health", timeout=0.5) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("bridge did not become healthy in time")
        yield {"ws": WS_PORT, "tcp": TCP_PORT, "udp": UDP_PORT}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# --- helpers ------------------------------------------------------------------


def ws(bridge, path="/ws"):
    return ws_connect(f"ws://127.0.0.1:{bridge['ws']}{path}")


def recv_json(conn, want_type=None, timeout=3.0):
    """Read JSON messages until one matches want_type (skipping e.g. bridge_status)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = conn.recv(timeout=max(0.05, deadline - time.time()))
        except TimeoutError:
            break
        msg = json.loads(raw)
        if want_type is None or msg.get("type") == want_type:
            return msg
    raise AssertionError(f"no {want_type!r} message within {timeout}s")


def hello_sim(conn, robots=ROBOTS, cameras=None, devices=None, room=None):
    message = {"type": "hello", "role": "simulator", "dof": 7,
               "robots": robots, "cameras": cameras or [], "devices": devices or []}
    if room:
        message["room"] = room
    conn.send(json.dumps(message))
    recv_json(conn, "bridge_status")  # sync point: hello processed + scene cached


def hello_controller(conn, room=None):
    message = {"type": "hello", "role": "controller"}
    if room:
        message["room"] = room
    conn.send(json.dumps(message))


def recv_line_json(sock_file):
    # The underlying socket already has a timeout (from create_connection), which
    # readline respects, so a missing newline raises rather than hanging forever.
    line = sock_file.readline()
    assert line, "socket closed before a line arrived"
    return json.loads(line)


# --- tests --------------------------------------------------------------------


def test_ws_describe_command_telemetry(bridge):
    with ws(bridge) as sim, ws(bridge) as ctrl:
        hello_sim(sim)
        hello_controller(ctrl)

        ctrl.send(json.dumps({"type": "describe"}))
        scene = recv_json(ctrl, "scene")
        assert [r["name"] for r in scene["robots"]] == ["Franka"]
        assert scene["robots"][0]["id"] == "franka_primary"

        ctrl.send(json.dumps({"type": "joint_position", "q": [0.1] * 7}))
        cmd = recv_json(sim, "joint_position")
        assert cmd["q"][0] == 0.1

        sim.send(json.dumps({"type": "telemetry", "robot": 0, "id": "franka_primary", "q": [0.2]}))
        tel = recv_json(ctrl, "telemetry")
        assert tel["q"][0] == 0.2
        assert tel["id"] == "franka_primary"


def test_ws_joint_trajectory_relay(bridge):
    with ws(bridge) as sim, ws(bridge) as ctrl:
        hello_sim(sim)
        hello_controller(ctrl)

        points = [{"t": 0.0, "q": [0.0] * 7}, {"t": 1.5, "q": [0.4] * 7}]
        ctrl.send(json.dumps({"type": "joint_trajectory", "points": points, "robot": 0, "loop": False}))
        cmd = recv_json(sim, "joint_trajectory")
        assert cmd["points"] == points
        assert cmd["loop"] is False

        ctrl.send(json.dumps({"type": "joint_trajectory_stop", "robot": 0}))
        cmd = recv_json(sim, "joint_trajectory_stop")
        assert cmd["robot"] == 0


def test_ws_room_device_discovery_and_command(bridge):
    devices = [{
        "id": "main_door",
        "name": "Main door",
        "kind": "door",
        "motion": "actuated",
        "wall": "Wall 1",
        "maxOpenDeg": 110,
    }]
    with ws(bridge) as sim, ws(bridge) as ctrl:
        hello_sim(sim, devices=devices)
        hello_controller(ctrl)

        ctrl.send(json.dumps({"type": "describe"}))
        scene = recv_json(ctrl, "scene")
        assert scene["devices"] == devices

        ctrl.send(json.dumps({
            "type": "room_opening",
            "opening": "main_door",
            "fraction": 0.75,
        }))
        command = recv_json(sim, "room_opening")
        assert command["opening"] == "main_door"
        assert command["fraction"] == 0.75


def test_ws_bridge_status_counts_controllers(bridge):
    with ws(bridge) as sim:
        hello_sim(sim)
        with ws(bridge) as ctrl:
            hello_controller(ctrl)
            status = recv_json(sim, "bridge_status")
            assert status["controllers"] >= 1
            assert status["simulators"] >= 1


def test_ws_rooms_isolate_commands_and_describe(bridge):
    robots_a = [{"index": 0, "id": "robot_a", "name": "Room A", "dof": 7}]
    robots_b = [{"index": 0, "id": "robot_b", "name": "Room B", "dof": 7}]
    with ws(bridge) as sim_a, ws(bridge) as sim_b, ws(bridge) as ctrl_a, ws(bridge) as ctrl_b:
        hello_sim(sim_a, robots=robots_a, room="alpha")
        hello_sim(sim_b, robots=robots_b, room="beta")
        hello_controller(ctrl_a, room="alpha")
        hello_controller(ctrl_b, room="beta")

        ctrl_a.send(json.dumps({"type": "describe"}))
        ctrl_b.send(json.dumps({"type": "describe"}))
        assert recv_json(ctrl_a, "scene")["robots"][0]["id"] == "robot_a"
        assert recv_json(ctrl_b, "scene")["robots"][0]["id"] == "robot_b"

        ctrl_a.send(json.dumps({"type": "joint_position", "q": [0.11] * 7}))
        assert recv_json(sim_a, "joint_position")["q"][0] == 0.11
        with pytest.raises(AssertionError):
            recv_json(sim_b, "joint_position", timeout=0.3)


def test_default_room_uses_newest_simulator(bridge):
    with ws(bridge) as old_sim, ws(bridge) as new_sim, ws(bridge) as ctrl:
        hello_sim(old_sim, robots=[{"index": 0, "id": "old", "name": "Old", "dof": 7}])
        hello_sim(new_sim, robots=[{"index": 0, "id": "new", "name": "New", "dof": 7}])
        hello_controller(ctrl)

        ctrl.send(json.dumps({"type": "joint_position", "q": [0.42] * 7}))
        assert recv_json(new_sim, "joint_position")["q"][0] == 0.42
        with pytest.raises(AssertionError):
            recv_json(old_sim, "joint_position", timeout=0.3)


def test_tcp_describe_command_telemetry(bridge):
    with ws(bridge) as sim:
        hello_sim(sim)
        s = socket.create_connection(("127.0.0.1", bridge["tcp"]), timeout=3)
        f = s.makefile("rb")
        try:
            s.sendall(json.dumps({"type": "describe"}).encode() + b"\n")
            scene = recv_line_json(f)
            assert scene["type"] == "scene" and scene["robots"][0]["name"] == "Franka"

            s.sendall(json.dumps({"type": "joint_velocity", "qd": [0.5]}).encode() + b"\n")
            cmd = recv_json(sim, "joint_velocity")
            assert cmd["qd"][0] == 0.5

            sim.send(json.dumps({"type": "telemetry", "robot": 0, "q": [0.3]}))
            tel = recv_line_json(f)
            assert tel["type"] == "telemetry" and tel["q"][0] == 0.3
        finally:
            f.close()
            s.close()


def test_udp_describe_command_telemetry(bridge):
    with ws(bridge) as sim:
        hello_sim(sim)
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(3)
        addr = ("127.0.0.1", bridge["udp"])
        try:
            u.sendto(json.dumps({"type": "describe"}).encode(), addr)
            scene = json.loads(u.recvfrom(65535)[0])
            assert scene["type"] == "scene" and scene["robots"][0]["name"] == "Franka"

            u.sendto(json.dumps({"type": "joint_position", "q": [0.7]}).encode(), addr)
            cmd = recv_json(sim, "joint_position")
            assert cmd["q"][0] == 0.7

            sim.send(json.dumps({"type": "telemetry", "robot": 0, "q": [0.9]}))
            tel = json.loads(u.recvfrom(65535)[0])
            assert tel["type"] == "telemetry" and tel["q"][0] == 0.9
        finally:
            u.close()


def test_video_relay(bridge):
    with ws(bridge, "/video") as simv, ws(bridge, "/video") as ctrlv:
        simv.send(json.dumps({"type": "hello", "role": "simulator-video"}))
        ctrlv.send(json.dumps({"type": "hello", "role": "controller-video"}))
        time.sleep(0.3)  # let the controller-video hello register
        payload = bytes([1, 2, 3, 4, 5])
        simv.send(payload)
        frame = ctrlv.recv(timeout=3)
        assert isinstance(frame, (bytes, bytearray)) and bytes(frame) == payload


def test_scene_cache_clears_on_simulator_disconnect(bridge):
    with ws(bridge) as sim:
        hello_sim(sim)
    time.sleep(0.4)  # let the disconnect process + cache clear
    with ws(bridge) as ctrl:
        ctrl.send(json.dumps({"type": "hello", "role": "controller"}))
        ctrl.send(json.dumps({"type": "describe"}))
        scene = recv_json(ctrl, "scene")
        assert scene["robots"] == []
