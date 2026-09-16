"""Focused tests for the packaged Sim2Bot Python SDK and CLI."""

from __future__ import annotations

import argparse
import json
import time

import pytest

from sim2bot import Robot, RobotInfo, RobotState, RoomDeviceInfo, cli
from sim2bot.client import _video_url


def test_robot_info_and_state_include_stable_id():
    info = RobotInfo.from_json(
        {
            "index": 1,
            "id": "so101_clone_a",
            "name": "SO-ARM101",
            "dof": 5,
            "jointNames": ["shoulder_pan"],
            "home": [0],
            "category": "arm",
            "manufacturer": "Example Robotics",
            "license": "Apache-2.0",
            "sourceUrl": "https://example.com/robot",
            "sourceRevision": "v1.2.0",
            "snapshotDigest": "abc123",
            "capabilities": ["tcp", "camera"],
            "armActuators": ["shoulder_pan"],
            "gripperActuators": ["gripper"],
            "tcpSites": ["tcp"],
            "endEffectorBodies": ["tool0"],
            "keyframes": ["home", "rest"],
            "modelCameras": ["wrist_cam"],
            "modelSensors": ["force_sensor"],
            "variant": "Wide gripper",
        }
    )
    state = RobotState.from_json(
        {
            "robot": 1,
            "id": "so101_clone_a",
            "name": "SO-ARM101",
            "dof": 5,
            "t": 1.25,
            "q": [0.1],
            "target": [0.2],
            "qd": [0.3],
            "qdd": [0.4],
            "qddd": [0.5],
            "tcp": [1, 2, 3],
            "tcpOrientation": [0, 0, 0, 1],
            "tcpLinearVelocity": [0.1, 0.2, 0.3],
            "tcpAngularVelocity": [0.4, 0.5, 0.6],
            "tcpLinearAcceleration": [0.7, 0.8, 0.9],
            "tcpAngularAcceleration": [1.0, 1.1, 1.2],
            "tcpLinearJerk": [1.3, 1.4, 1.5],
            "tcpAngularJerk": [1.6, 1.7, 1.8],
            "roomDevices": [
                {"id": "door_a", "target": 0.75, "position": 0.5, "velocity": 0.1}
            ],
        }
    )

    assert info.id == "so101_clone_a"
    assert state.id == "so101_clone_a"
    assert info.snapshot_digest == "abc123"
    assert info.arm_actuators == ["shoulder_pan"]
    assert info.tcp_sites == ["tcp"]
    assert info.keyframes == ["home", "rest"]
    assert info.model_cameras == ["wrist_cam"]
    assert info.model_sensors == ["force_sensor"]
    assert info.variant == "Wide gripper"
    assert state.robot == info.index
    assert state.qdd == [0.4]
    assert state.qddd == [0.5]
    assert state.tcp_orientation == [0, 0, 0, 1]
    assert state.tcp_angular_jerk == [1.6, 1.7, 1.8]
    assert state.room_devices[0]["id"] == "door_a"


def test_room_device_discovery_and_command(monkeypatch):
    sent: list[str] = []
    robot = Robot()
    robot._scene = {
        "devices": [
            {
                "id": "main_door",
                "name": "Main door",
                "kind": "door",
                "motion": "actuated",
                "wall": "Wall 1",
                "maxOpenDeg": 110,
            }
        ]
    }
    devices = robot.room_devices()
    assert devices == [
        RoomDeviceInfo(
            id="main_door",
            name="Main door",
            kind="door",
            motion="actuated",
            wall="Wall 1",
            max_open_deg=110,
        )
    ]

    class DummyLock:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(robot, "_send_lock", DummyLock())
    monkeypatch.setattr(
        robot,
        "_conn",
        type("Conn", (), {"send": lambda self, raw: sent.append(raw)})(),
    )
    robot.set_room_opening("main_door", 1.4)
    assert json.loads(sent[0]) == {
        "type": "room_opening",
        "opening": "main_door",
        "fraction": 1.0,
    }


def test_wait_for_sim_times_out_when_no_robots(monkeypatch):
    robot = Robot()
    calls = 0

    def describe(timeout=2.0):
        nonlocal calls
        calls += 1
        assert timeout > 0
        return []

    monkeypatch.setattr(robot, "describe", describe)

    started = time.time()
    with pytest.raises(TimeoutError, match="Timed out waiting"):
        robot.wait_for_sim(timeout=0.03, poll_interval=0.005)

    assert calls >= 1
    assert time.time() - started < 1.0


def test_list_robots_json_outputs_stable_ids(monkeypatch, capsys):
    class FakeRobot:
        def __init__(self, **kwargs):
            assert kwargs["auto_bridge"] is True
            assert kwargs["wait_for_sim"] is True
            assert kwargs["wait_for_sim_timeout"] == 0.5
            assert kwargs["room"] == "bench-a"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def describe(self):
            return [
                RobotInfo(
                    index=0,
                    id="franka_primary",
                    name="Franka",
                    dof=7,
                    joint_names=["joint1"],
                    home=[0.0],
                    has_gripper=True,
                )
            ]

    monkeypatch.setattr("sim2bot.client.Robot", FakeRobot)

    code = cli.run_list_robots(
        argparse.Namespace(url="ws://localhost:8765/ws", room="bench-a", timeout=0.5, json=True)
    )
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code == 0
    assert payload[0]["index"] == 0
    assert payload[0]["id"] == "franka_primary"
    assert payload[0]["name"] == "Franka"


def test_robot_send_adds_room(monkeypatch):
    sent: list[str] = []
    robot = Robot(room="bench-a")
    robot._conn = object()

    class DummyLock:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(robot, "_send_lock", DummyLock())
    monkeypatch.setattr(robot, "_conn", type("Conn", (), {"send": lambda self, raw: sent.append(raw)})())

    robot.move_to([0.1, 0.2], robot=1)

    payload = json.loads(sent[0])
    assert payload["type"] == "joint_position"
    assert payload["room"] == "bench-a"
    assert payload["robot"] == 1


def test_video_url_preserves_room_query():
    assert _video_url("ws://localhost:8765/ws?room=bench-a") == "ws://localhost:8765/video?room=bench-a"
