"""The SDK checked against the published protocol fixtures.

`fixtures/v1/` is this repository's contract: one example message per type, which
the Sim2Bot application checks its own implementation against. These tests check
the other side of it — that the Python client really puts those messages on the
wire, and really reads that telemetry back — so the two cannot drift apart
without a test failing here.

A client written in another language can read these tests as the worked example
of what each fixture means.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from sim2bot import Robot, RobotState

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "v1"
CLIENT_SOURCE = Path(__file__).resolve().parents[1] / "sim2bot" / "client.py"

# Message types the SDK sends that deliberately have no fixture: they carry no
# payload beyond the type itself and a room, so an example would pin nothing.
TYPES_WITHOUT_FIXTURE = {"describe"}

Q_START = [0, -0.45, 0, -2.2, 0, 1.75, 0.8]
Q_END = [0.2, -0.35, 0.15, -2, 0.1, 1.5, 0.6]


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


class RecordingConnection:
    """Stands in for the WebSocket and keeps what the client sent."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


class NullLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def recorder(monkeypatch) -> tuple[Robot, RecordingConnection]:
    """A Robot that records instead of connecting."""
    robot = Robot()
    connection = RecordingConnection()
    monkeypatch.setattr(robot, "_send_lock", NullLock())
    monkeypatch.setattr(robot, "_conn", connection)
    return robot, connection


def matches(sent: Any, expected: Any, where: str = "message") -> None:
    """Every field of the fixture must appear, with the same value, in `sent`.

    Extra fields in the sent message are allowed: the client always addresses a
    robot index, while a fixture may leave an optional field out.
    """
    if isinstance(expected, dict):
        assert isinstance(sent, dict), f"{where}: expected an object, got {type(sent).__name__}"
        for key, value in expected.items():
            assert key in sent, f"{where}: missing {key!r}"
            matches(sent[key], value, f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(sent, list), f"{where}: expected a list"
        assert len(sent) == len(expected), f"{where}: length {len(sent)} != {len(expected)}"
        for index, value in enumerate(expected):
            matches(sent[index], value, f"{where}[{index}]")
    elif isinstance(expected, bool) or expected is None:
        assert sent == expected, f"{where}: {sent!r} != {expected!r}"
    elif isinstance(expected, (int, float)):
        assert sent == pytest.approx(expected), f"{where}: {sent!r} != {expected!r}"
    else:
        assert sent == expected, f"{where}: {sent!r} != {expected!r}"


# --- the fixtures themselves --------------------------------------------------


def test_every_fixture_is_a_typed_object():
    files = sorted(FIXTURE_DIR.glob("*.json"))
    assert files, "no fixtures found"
    for path in files:
        message = json.loads(path.read_text())
        assert isinstance(message, dict), f"{path.name}: not an object"
        assert isinstance(message.get("type"), str) and message["type"], f"{path.name}: no type"
        room = message.get("room")
        if room is not None:
            assert re.fullmatch(r"[A-Za-z0-9._-]{1,80}", room), f"{path.name}: bad room"
        robot = message.get("robot")
        if robot is not None:
            assert isinstance(robot, int) and robot >= 0, f"{path.name}: bad robot index"


def test_every_command_the_sdk_sends_has_a_fixture():
    """A new message type must arrive with the example that documents it."""
    sent_types = set(re.findall(r'"type": "([a-z_]+)"', CLIENT_SOURCE.read_text()))
    documented = {json.loads(path.read_text())["type"] for path in FIXTURE_DIR.glob("*.json")}
    undocumented = sent_types - documented - TYPES_WITHOUT_FIXTURE
    assert not undocumented, f"no fixture for: {sorted(undocumented)}"


# --- commands the client puts on the wire -------------------------------------


def test_joint_position(recorder):
    robot, connection = recorder
    robot.move_to(Q_START, robot=0)
    matches(connection.messages[0], fixture("joint_position"))


def test_joint_velocity(recorder):
    robot, connection = recorder
    robot.set_velocity([0.1, 0.0, -0.25, 0.0, 0.3, 0.0, 0.0], robot=0)
    matches(connection.messages[0], fixture("joint_velocity"))


def test_tcp_pose_position_only(recorder):
    robot, connection = recorder
    robot.move_to_pose([0.45, 0.0, 0.35])
    matches(connection.messages[0], fixture("tcp_pose"))
    assert "orientation" not in connection.messages[0]


def test_tcp_pose_with_orientation(recorder):
    robot, connection = recorder
    robot.move_to_pose([0.45, 0.1, 0.35], [0.0, 0.7071068, 0.0, 0.7071068], robot=1)
    matches(connection.messages[0], fixture("tcp_pose_oriented"))


def test_gripper(recorder):
    robot, connection = recorder
    robot.gripper(0.25, robot=0)
    matches(connection.messages[0], fixture("gripper"))


def test_base_velocity(recorder):
    robot, connection = recorder
    robot.base_velocity(vx=0.4, vy=0.0, vz=0.0, omega=0.35, robot=0)
    matches(connection.messages[0], fixture("base_velocity"))


def test_reset_and_stop(recorder):
    robot, connection = recorder
    robot.reset(robot=0)
    robot.stop()
    matches(connection.messages[0], fixture("reset"))
    matches(connection.messages[1], fixture("stop"))


def test_joint_trajectory(recorder):
    robot, connection = recorder
    robot.move_trajectory([(0.0, Q_START), (1.25, Q_END)], robot=0, loop=False)
    robot.stop_trajectory(robot=0)
    matches(connection.messages[0], fixture("joint_trajectory"))
    matches(connection.messages[1], fixture("joint_trajectory_stop"))


def test_room_opening(recorder):
    robot, connection = recorder
    robot.set_room_opening("room_door_ab12cd34", 0.75)
    matches(connection.messages[0], fixture("room_opening"))


def test_markers(recorder):
    robot, connection = recorder
    robot.marker(
        "grasp_target",
        "arrow",
        from_=[0.3, 0.0, 0.4],
        to=[0.3, 0.0, 0.1],
        color=[1.0, 0.35, 0.0, 1.0],
        scale=0.02,
    )
    robot.marker(
        "depth_cloud",
        "points",
        points=[[0.1, 0.0, 0.05], [0.12, 0.01, 0.06], [0.14, -0.01, 0.05]],
        color=[0.2, 0.8, 1.0, 1.0],
        scale=0.004,
    )
    robot.delete_marker("grasp_target")
    robot.clear_markers()

    matches(connection.messages[0], fixture("marker"))
    matches(connection.messages[1], fixture("marker_points"))
    matches(connection.messages[2], fixture("marker_delete"))
    matches(connection.messages[3], fixture("marker_clear"))


def test_camera_subscribe_and_unsubscribe(recorder, monkeypatch):
    robot, connection = recorder
    # The video socket is the only thing a camera subscription needs beyond the
    # control message, and it is not what this test is about.
    monkeypatch.setattr(robot, "_ensure_video", lambda: None)

    stream = robot.camera("model:0", fps=30, width=256, height=256, quality=80, codec=0)
    stream.close()

    matches(connection.messages[0], fixture("camera_subscribe"))
    matches(connection.messages[1], fixture("camera_unsubscribe"))


def test_hello_carries_the_room():
    # A room pairs one script with one browser tab; the client stamps it on every
    # message, which is what makes `hello_room` look the way it does.
    robot = Robot(room="bench-a")
    connection = RecordingConnection()
    robot._send_lock = NullLock()
    robot._conn = connection

    robot._send({"type": "hello", "role": "controller"})
    matches(connection.messages[0], fixture("hello_room"))


# --- telemetry the client reads back ------------------------------------------


def test_telemetry_fixture_parses_into_robot_state():
    message = fixture("telemetry")
    state = RobotState.from_json(message)

    assert state.robot == message["robot"]
    assert state.id == message["id"]
    assert state.name == message["name"]
    assert state.dof == message["dof"]
    assert state.t == pytest.approx(message["t"])
    assert state.q == pytest.approx(message["q"])
    assert state.target == pytest.approx(message["target"])
    assert state.qd == pytest.approx(message["qd"])
    assert state.qdd == pytest.approx(message["qdd"])
    assert state.qddd == pytest.approx(message["qddd"])
    assert state.tcp == pytest.approx(message["tcp"])
    assert state.tcp_orientation == pytest.approx(message["tcpOrientation"])
    assert state.tcp_linear_velocity == pytest.approx(message["tcpLinearVelocity"])
    assert state.tcp_angular_velocity == pytest.approx(message["tcpAngularVelocity"])
    assert state.tcp_linear_acceleration == pytest.approx(message["tcpLinearAcceleration"])
    assert state.tcp_angular_acceleration == pytest.approx(message["tcpAngularAcceleration"])
    assert state.tcp_linear_jerk == pytest.approx(message["tcpLinearJerk"])
    assert state.tcp_angular_jerk == pytest.approx(message["tcpAngularJerk"])
    assert state.gripper == pytest.approx(message["gripper"])
