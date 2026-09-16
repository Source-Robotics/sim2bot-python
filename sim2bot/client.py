"""Sim2Bot Python client.

A blocking, thread-based client over the local bridge. The control plane (commands
+ telemetry + discovery) runs on the JSON WebSocket; camera feeds run on a
separate binary WebSocket, mirroring the simulator's two-stream design.

Design notes:
  - "Always read the present": both telemetry and camera frames keep only the
    latest value; slow consumers drop intermediate frames instead of lagging.
  - Camera subscriptions are renewed on a timer (the simulator expires a feed a
    few seconds after the last renewal), so a crashed consumer stops the feed.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence, Union
from urllib.parse import urlparse, urlunparse

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

# Frame header: must match src/bridge/videoFrame.ts.
#   u8 version, u8 codec, u16 flags, u32 sequence, f64 captureTs, u16 width,
#   u16 height, then u8 cameraIdLen, cameraId (utf-8), frame bytes.
_HEADER = struct.Struct("<BBHIdHH")  # 20 bytes
_CODEC_JPEG = 0
_CODEC_H264 = 1
_CODEC_RAW = 2
_FLAG_KEYFRAME = 1
_CODEC_BY_NAME = {"jpeg": _CODEC_JPEG, "h264": _CODEC_H264, "raw": _CODEC_RAW}

# Re-send camera_subscribe at this interval to keep the feed alive (the sim's TTL
# is a few seconds — see CameraStreamManager).
_RENEW_INTERVAL_S = 1.0


@dataclass
class RobotInfo:
    """Model-derived metadata for one robot announced by the simulator.

    Attributes:
        index: Current scene command address. Pass it as ``robot=`` to commands.
        id: Stable identity while this scene instance is loaded.
        name: Human-readable model or instance name.
        dof: Number of controllable arm joints, excluding gripper joints.
        joint_names: Joint names in the exact order expected by commands.
        joint_limits: Per-joint ``[lower, upper]`` limits in radians, or ``None``
            for an unlimited joint.
        home: Model-defined home target in radians and command order.
        has_gripper: Whether the robot exposes a supported gripper.
        locomotion: Capability label such as ``"arm"``, mobile, or aerial.
        base_dof: Number of supported mobile or aerial base degrees of freedom.
        category: Sim2Bot catalog category.
        manufacturer: Authored manufacturer, when declared.
        license: SPDX licence identifier, when declared.
        source_url: Upstream model source, when declared.
        snapshot_digest: SHA-256 identity of the exact bundled model snapshot.
        capabilities: Verified Sim2Bot capability labels.
        arm_actuators: Authored arm actuator names in command order.
        gripper_actuators: Authored gripper actuator names.
        tcp_sites: Authored tool-centre-point site candidates.
        end_effector_bodies: Authored end-effector body candidates.
        keyframes: Named MJCF poses such as ``home`` or ``rest``.
        model_cameras: Cameras compiled as part of this robot model.
        model_sensors: Sensors compiled as part of this robot model.
    """

    index: int
    """Current scene command address passed as ``robot=`` to control methods."""
    id: str
    """Stable robot identity while the current scene instance remains loaded."""
    name: str
    """Human-readable robot model or scene-instance name."""
    dof: int
    """Number of controllable arm joints, excluding gripper joints."""
    joint_names: list[str] = field(default_factory=list)
    """Joint names in the exact order expected by commands and telemetry."""
    joint_limits: list[Optional[list[float]]] = field(default_factory=list)
    """Per-joint ``[lower, upper]`` limits in radians, or ``None`` if unlimited."""
    home: list[float] = field(default_factory=list)
    """Model-defined home joint target in radians and command order."""
    has_gripper: bool = False
    """Whether the robot exposes a gripper supported by `Robot.gripper`."""
    locomotion: str = "arm"
    """Capability label such as ``"arm"``, ``"mobile"``, or ``"aerial"``."""
    base_dof: int = 0
    """Number of supported mobile or aerial base degrees of freedom."""
    category: str = "custom"
    """Sim2Bot catalog category such as ``arm`` or ``mobile-base``."""
    manufacturer: str = ""
    """Authored robot manufacturer, if declared by the model package."""
    license: str = ""
    """SPDX licence identifier for the model package, if declared."""
    source_url: str = ""
    """Upstream source URL for the model package, if declared."""
    source_revision: str = ""
    """Pinned upstream source revision, if declared."""
    snapshot_digest: str = ""
    """SHA-256 identity of the exact bundled model snapshot, if available."""
    capabilities: list[str] = field(default_factory=list)
    """Verified Sim2Bot capability labels for this model."""
    arm_actuators: list[str] = field(default_factory=list)
    """Authored arm actuator names in command order."""
    gripper_actuators: list[str] = field(default_factory=list)
    """Authored gripper actuator names."""
    tcp_sites: list[str] = field(default_factory=list)
    """Authored TCP site candidates in preference order."""
    end_effector_bodies: list[str] = field(default_factory=list)
    """Authored end-effector body candidates in preference order."""
    keyframes: list[str] = field(default_factory=list)
    """Named MJCF keyframe poses exposed by the robot."""
    model_cameras: list[str] = field(default_factory=list)
    """Camera names compiled as part of this robot model."""
    model_sensors: list[str] = field(default_factory=list)
    """Sensor names compiled as part of this robot model."""
    variant: str = ""
    """Selected end-effector/model variant label, if applicable."""

    @classmethod
    def from_json(cls, data: dict) -> "RobotInfo":
        return cls(
            index=int(data.get("index", 0)),
            id=str(data.get("id") or ""),
            name=str(data.get("name", "")),
            dof=int(data.get("dof", 0)),
            joint_names=list(data.get("jointNames", []) or []),
            joint_limits=list(data.get("jointLimits", []) or []),
            home=list(data.get("home", []) or []),
            has_gripper=bool(data.get("hasGripper", False)),
            locomotion=str(data.get("locomotion", "arm")),
            base_dof=int(data.get("baseDof", 0)),
            category=str(data.get("category") or "custom"),
            manufacturer=str(data.get("manufacturer") or ""),
            license=str(data.get("license") or ""),
            source_url=str(data.get("sourceUrl") or ""),
            source_revision=str(data.get("sourceRevision") or ""),
            snapshot_digest=str(data.get("snapshotDigest") or ""),
            capabilities=list(data.get("capabilities", []) or []),
            arm_actuators=list(data.get("armActuators", []) or []),
            gripper_actuators=list(data.get("gripperActuators", []) or []),
            tcp_sites=list(data.get("tcpSites", []) or []),
            end_effector_bodies=list(data.get("endEffectorBodies", []) or []),
            keyframes=list(data.get("keyframes", []) or []),
            model_cameras=list(data.get("modelCameras", []) or []),
            model_sensors=list(data.get("modelSensors", []) or []),
            variant=str(data.get("variant") or ""),
        )


@dataclass
class RoomDeviceInfo:
    """Metadata for a door or window announced by the current scene.

    Room-device control is experimental while browser-side actuation is being
    revised. Discovery is safe to use, but do not build critical workflows around
    opening behavior yet.

    Attributes:
        id: Scene-unique device identity used by ``set_room_opening()``.
        name: Human-readable device name.
        kind: Authored type, normally ``"door"`` or ``"window"``.
        motion: Mechanism type, for example hinged, sliding, or fixed.
        wall: ID of the wall that owns the opening.
        max_open_deg: Maximum authored hinge travel in degrees when applicable.
    """

    id: str
    name: str
    kind: str
    motion: str
    wall: str
    max_open_deg: float

    @classmethod
    def from_json(cls, data: dict) -> "RoomDeviceInfo":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            kind=str(data.get("kind") or ""),
            motion=str(data.get("motion") or "fixed"),
            wall=str(data.get("wall") or ""),
            max_open_deg=float(data.get("maxOpenDeg", 0.0)),
        )


@dataclass
class RobotState:
    """Latest complete telemetry packet for one robot.

    Joint quantities use the command order announced by
    [`RobotInfo`][sim2bot.client.RobotInfo].
    TCP quantities are expressed in the world frame. Derivatives are unsmoothed;
    any smoothing selected in the GUI is display-only.

    Attributes:
        robot: Current scene robot index.
        id: Robot scene identity.
        name: Human-readable robot name.
        dof: Number of reported arm joints.
        t: MuJoCo simulation time in seconds.
        q: Joint positions in radians.
        target: Latest joint-position targets in radians.
        qd: Joint velocities in radians per second.
        qdd: Joint accelerations in radians per second squared.
        qddd: Joint jerk in radians per second cubed.
        tcp: TCP world position ``[x, y, z]`` in metres.
        tcp_orientation: TCP world quaternion ``[x, y, z, w]``.
        tcp_linear_velocity: World-frame linear velocity in metres per second.
        tcp_angular_velocity: World-frame angular velocity in radians per second.
        tcp_linear_acceleration: Linear acceleration in metres per second squared.
        tcp_angular_acceleration: Angular acceleration in radians per second squared.
        tcp_linear_jerk: Linear jerk in metres per second cubed.
        tcp_angular_jerk: Angular jerk in radians per second cubed.
        gripper: Commanded opening fraction from 0 closed to 1 open, if present.
        sensors: Authored sensor readings associated with this robot packet.
        room_devices: Scene door/window states, currently carried by robot 0.
        capture_ts: Unix capture timestamp for the newest packet sample.
        samples: Physics-substep ``{t, q, qd}`` samples since the prior packet.
    """

    robot: int
    """Current scene robot index used by addressed SDK commands."""
    id: str
    """Stable robot identity while the current scene instance remains loaded."""
    name: str
    """Human-readable robot model or scene-instance name."""
    dof: int
    """Number of reported controllable arm joints."""
    t: float
    """MuJoCo simulation time in seconds for the newest sample."""
    q: list[float]
    """Joint positions in radians and discovered joint order."""
    target: list[float]
    """Latest joint-position targets in radians and discovered joint order."""
    qd: list[float] = field(default_factory=list)
    """Joint velocities in radians per second."""
    qdd: list[float] = field(default_factory=list)
    """Unsmoothed joint accelerations in radians per second squared."""
    qddd: list[float] = field(default_factory=list)
    """Unsmoothed joint jerk in radians per second cubed."""
    tcp: list[float] = field(default_factory=list)
    """TCP world position ``[x, y, z]`` in metres."""
    tcp_orientation: list[float] = field(default_factory=list)
    """TCP world orientation quaternion in ``[x, y, z, w]`` order."""
    tcp_linear_velocity: list[float] = field(default_factory=list)
    """TCP world-frame linear velocity ``[x, y, z]`` in metres per second."""
    tcp_angular_velocity: list[float] = field(default_factory=list)
    """TCP world-frame angular velocity ``[x, y, z]`` in radians per second."""
    tcp_linear_acceleration: list[float] = field(default_factory=list)
    """TCP linear acceleration in metres per second squared."""
    tcp_angular_acceleration: list[float] = field(default_factory=list)
    """TCP angular acceleration in radians per second squared."""
    tcp_linear_jerk: list[float] = field(default_factory=list)
    """TCP linear jerk in metres per second cubed."""
    tcp_angular_jerk: list[float] = field(default_factory=list)
    """TCP angular jerk in radians per second cubed."""
    gripper: Optional[float] = None
    """Commanded opening fraction from 0 closed to 1 open, or ``None``."""
    sensors: list[dict] = field(default_factory=list)
    """Authored sensor readings associated with this robot telemetry packet."""
    # Scene-level door/window joint states. Present on robot 0 telemetry.
    room_devices: list[dict] = field(default_factory=list)
    """Scene door/window states, currently carried on robot 0 telemetry."""
    # Wall-clock capture time (unix s) of the newest sample, for latency.
    capture_ts: Optional[float] = None
    """Browser wall-clock capture timestamp in Unix seconds, if available."""
    # High-rate batch since the previous packet: [{t, q, qd}, ...] at physics rate.
    samples: list[dict] = field(default_factory=list)
    """Physics-substep ``{t, q, qd}`` samples since the previous packet."""

    @classmethod
    def from_json(cls, data: dict) -> "RobotState":
        return cls(
            robot=int(data.get("robot", 0)),
            id=str(data.get("id") or ""),
            name=str(data.get("name", "")),
            dof=int(data.get("dof", 0)),
            t=float(data.get("t", 0.0)),
            q=list(data.get("q", []) or []),
            target=list(data.get("target", []) or []),
            qd=list(data.get("qd", []) or []),
            qdd=list(data.get("qdd", []) or []),
            qddd=list(data.get("qddd", []) or []),
            tcp=list(data.get("tcp", []) or []),
            tcp_orientation=list(data.get("tcpOrientation", []) or []),
            tcp_linear_velocity=list(data.get("tcpLinearVelocity", []) or []),
            tcp_angular_velocity=list(data.get("tcpAngularVelocity", []) or []),
            tcp_linear_acceleration=list(data.get("tcpLinearAcceleration", []) or []),
            tcp_angular_acceleration=list(data.get("tcpAngularAcceleration", []) or []),
            tcp_linear_jerk=list(data.get("tcpLinearJerk", []) or []),
            tcp_angular_jerk=list(data.get("tcpAngularJerk", []) or []),
            gripper=data.get("gripper"),
            sensors=list(data.get("sensors", []) or []),
            room_devices=list(data.get("roomDevices", []) or []),
            capture_ts=data.get("captureTs"),
            samples=list(data.get("samples", []) or []),
        )

    @property
    def latency(self) -> Optional[float]:
        """Return the current telemetry age in seconds.

        Returns:
            Seconds since browser capture, or ``None`` when the packet did not
            include a capture timestamp.
        """
        return (time.time() - self.capture_ts) if self.capture_ts else None


@dataclass
class CameraFrame:
    """One encoded camera frame plus capture and stream metadata.

    Attributes:
        camera_id: Global scene camera ID used for the subscription.
        codec: Numeric wire codec: 0 JPEG, 1 reserved H.264, or 2 raw RGBA.
        keyframe: Whether the frame is marked as independently decodable.
        sequence: Monotonically increasing sequence number for this feed.
        capture_ts: Browser wall-clock capture timestamp in Unix seconds.
        width: Frame width in pixels.
        height: Frame height in pixels.
        data: Encoded image bytes, excluding the Sim2Bot frame header.
    """

    camera_id: str
    codec: int
    keyframe: bool
    sequence: int
    capture_ts: float
    width: int
    height: int
    data: bytes

    @property
    def age(self) -> float:
        """Return the current frame age in seconds.

        This is an approximate capture-to-consumer measurement based on wall
        clocks. It includes browser production, bridge relay, and Python delay.

        Returns:
            Elapsed wall-clock seconds since the browser captured this frame.
        """
        return time.time() - self.capture_ts

    def image(self) -> Any:
        """Decode the frame to a NumPy BGR image.

        JPEG and raw RGBA frames are supported. Raw WebGL frames are vertically
        flipped and converted from RGBA to BGR. H.264 decoding is reserved for a
        future implementation.

        Returns:
            A ``height × width × 3`` uint8 BGR NumPy array. A truncated raw frame
            returns ``None``.

        Raises:
            RuntimeError: If the optional OpenCV/NumPy dependencies are absent.
            NotImplementedError: If the frame uses the reserved H.264 codec.

        Examples:
            Install ``sim2bot[cv2]``, then decode a received frame::

                frame = stream.read(timeout=2.0)
                if frame is not None:
                    image_bgr = frame.image()
        """
        if self.codec == _CODEC_H264:
            raise NotImplementedError("H.264 decode needs the sim2bot[av] extra.")
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency hint
            raise RuntimeError(
                "Decoding camera frames needs OpenCV + numpy: pip install 'sim2bot[cv2]'"
            ) from exc
        if self.codec == _CODEC_RAW:
            # Uncompressed RGBA, bottom-up (WebGL order): reshape, flip, RGBA->BGR.
            need = self.width * self.height * 4
            if len(self.data) < need:
                return None
            arr = np.frombuffer(self.data, np.uint8, count=need)
            img = arr.reshape(self.height, self.width, 4)[::-1]
            return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        return cv2.imdecode(np.frombuffer(self.data, np.uint8), cv2.IMREAD_COLOR)


def _parse_frame(data: bytes) -> Optional[CameraFrame]:
    if len(data) < _HEADER.size + 1:
        return None
    version, codec, flags, sequence, capture_ts, width, height = _HEADER.unpack_from(data, 0)
    del version
    id_len = data[_HEADER.size]
    start = _HEADER.size + 1 + id_len
    if len(data) < start:
        return None
    camera_id = data[_HEADER.size + 1 : start].decode("utf-8", "replace")
    return CameraFrame(
        camera_id=camera_id,
        codec=codec,
        keyframe=bool(flags & _FLAG_KEYFRAME),
        sequence=sequence,
        capture_ts=capture_ts,
        width=width,
        height=height,
        data=bytes(data[start:]),
    )


def _video_url(control_url: str) -> str:
    parsed = urlparse(control_url)
    if parsed.scheme and parsed.netloc:
        path = parsed.path
        if path.endswith("/ws"):
            path = path[: -len("/ws")] + "/video"
        else:
            path = path.rstrip("/") + "/video"
        return urlunparse(parsed._replace(path=path))
    if control_url.endswith("/ws"):
        return control_url[: -len("/ws")] + "/video"
    return control_url.rstrip("/") + "/video"


class _LatestSlot:
    """Holds only the newest item; readers block until something newer arrives.

    This is the "always read the present" buffer — a slow reader drops the frames
    it missed instead of building a backlog.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._value: Any = None
        self._seq = 0

    def put(self, value: Any) -> None:
        with self._cond:
            self._value = value
            self._seq += 1
            self._cond.notify_all()

    def get(self, last_seq: int, timeout: float) -> tuple[Any, int]:
        with self._cond:
            if self._seq == last_seq:
                self._cond.wait(timeout)
            if self._seq == last_seq:
                return None, last_seq
            return self._value, self._seq


class CameraStream:
    """Latest-value iterator for a subscribed global scene camera.

    Use it as a context manager or call
    [`close`][sim2bot.client.CameraStream.close] to unsubscribe. The stream
    keeps only the newest frame, so a slow consumer drops intermediate images
    instead of accumulating latency. Iteration also skips frames older than
    ``max_age``.

    Args:
        robot: Owning client connection. The name is historical; cameras are
            global scene resources and are not owned by a robot command address.
        camera_id: ID returned by [`Robot.cameras`][sim2bot.client.Robot.cameras].
        max_age: Maximum frame age in seconds accepted by iterator mode.
    """

    def __init__(self, robot: "Robot", camera_id: str, max_age: float = 0.25) -> None:
        self._robot = robot
        self.camera_id = camera_id
        self._slot = _LatestSlot()
        self._last_seq = 0
        self._max_age = max_age
        self._closed = False

    def _deliver(self, frame: CameraFrame) -> None:
        self._slot.put(frame)

    def read(self, timeout: float = 5.0) -> Optional[CameraFrame]:
        """Wait for a frame newer than the previous read.

        Args:
            timeout: Maximum blocking time in seconds.

        Returns:
            The next [`CameraFrame`][sim2bot.client.CameraFrame], or ``None`` when
            the timeout expires.

        Notes:
            ``read()`` returns the next fresh slot value even if it is older than
            ``max_age``. Iterator mode performs the age filter automatically.
        """
        frame, self._last_seq = self._slot.get(self._last_seq, timeout)
        return frame

    def __iter__(self) -> Iterator[CameraFrame]:
        return self

    def __next__(self) -> CameraFrame:
        while not self._closed:
            frame = self.read()
            if frame is None:
                continue
            # Skip a frame that's already stale (a fresher one is on the way).
            if frame.age > self._max_age:
                continue
            return frame
        raise StopIteration

    def close(self) -> None:
        """Unsubscribe this camera and make the operation idempotently closed.

        Returns:
            Calling the method again after closure has no effect.
        """
        if self._closed:
            return
        self._closed = True
        self._robot._remove_camera(self.camera_id)

    def __enter__(self) -> "CameraStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class Robot:
    """A Sim2Bot robot endpoint over the local bridge.

    Args:
        url: Control WebSocket URL. Defaults to ``ws://localhost:8765/ws``.
        video_url: Binary camera WebSocket URL. Derived from ``url`` when omitted.
        connect_timeout: WebSocket connection timeout in seconds.
        transport: ``"ws"`` for reliable WebSocket control/telemetry or ``"udp"``
            for latest-value UDP. Camera frames always use the video WebSocket.
        udp_host: UDP bridge host. Derived from ``url`` when omitted.
        udp_port: UDP bridge port. Defaults to 8771.
        auto_bridge: Start a local bridge automatically when none is healthy.
        bridge_timeout: Seconds to wait for an automatically started bridge.
        wait_for_sim: During [`connect`][sim2bot.client.Robot.connect], wait for a
            browser scene.
        wait_for_sim_timeout: Maximum wait in seconds, or ``None`` to wait forever.
        wait_for_sim_poll_interval: Delay between scene discovery attempts.
        api_key: Optional bridge token for deliberately enabled LAN access. It is
            not needed for normal same-device loopback use.
        room: Optional pairing ID. Controllers communicate only with browser
            simulators in the same room.

    Notes:
        This client is blocking and thread-based. Telemetry and camera streams
        deliberately retain only the newest value. It is designed for simulation
        and is not a functional-safety interface for physical hardware.

    Examples:
        Use the client as a context manager so connections always close::

            from sim2bot import Robot

            with Robot(auto_bridge=True, wait_for_sim=True) as sim:
                arm = sim.describe()[0]
                sim.move_to(arm.home, robot=arm.index)
    """

    def __init__(
        self,
        url: str = "ws://localhost:8765/ws",
        video_url: Optional[str] = None,
        connect_timeout: float = 5.0,
        transport: str = "ws",
        udp_host: Optional[str] = None,
        udp_port: int = 8771,
        auto_bridge: bool = False,
        bridge_timeout: float = 10.0,
        wait_for_sim: bool = False,
        wait_for_sim_timeout: Optional[float] = None,
        wait_for_sim_poll_interval: float = 0.5,
        api_key: Optional[str] = None,
        room: Optional[str] = None,
    ) -> None:
        self.url = url
        self.video_url = video_url or _video_url(url)
        self._connect_timeout = connect_timeout
        self._auto_bridge = auto_bridge
        self._bridge_timeout = bridge_timeout
        self._wait_for_sim_on_connect = wait_for_sim
        self._wait_for_sim_timeout = wait_for_sim_timeout
        self._wait_for_sim_poll_interval = wait_for_sim_poll_interval
        self._api_key = api_key
        self._room = room or os.environ.get("SIM2BOT_BRIDGE_ROOM")
        # Control + telemetry transport: "ws" (WebSocket/TCP, default) or "udp"
        # (lower-latency, drop-don't-queue — best for tight control loops). The
        # camera feed always uses the binary video WebSocket regardless.
        self.transport = transport
        host = udp_host or urlparse(url).hostname or "127.0.0.1"
        self._udp_dest = (host, udp_port)

        self._conn = None  # WebSocket control connection (ws transport)
        self._udp_sock: Optional[socket.socket] = None  # UDP control socket
        self._video_conn = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()

        self._states: dict[int, RobotState] = {}
        self._states_lock = threading.Lock()
        self._on_telemetry: Optional[Callable[[RobotState], None]] = None

        self._scene: Optional[dict] = None
        self._scene_event = threading.Event()

        # camera_id -> (options dict, CameraStream)
        self._cameras: dict[str, tuple[dict, CameraStream]] = {}
        self._cameras_lock = threading.Lock()

        self._threads: list[threading.Thread] = []

    # -- lifecycle -----------------------------------------------------------

    def connect(self) -> "Robot":
        """Open the configured bridge connection and start reader threads.

        If ``auto_bridge=True``, this first reuses a healthy local bridge or starts
        one. If ``wait_for_sim=True``, the call does not return until a browser
        announces at least one robot or the configured timeout expires.

        Returns:
            This client instance, enabling ``Robot(...).connect()`` chaining.

        Raises:
            TimeoutError: If waiting for a browser simulator times out.
            RuntimeError: If an automatically started bridge cannot become healthy.
            ConnectionClosed: If the WebSocket closes while sending its initial hello.
            OSError: If the configured network endpoint cannot be opened.
        """
        if self._auto_bridge:
            from .bridge import ensure_bridge

            info = ensure_bridge(
                self.url,
                udp_port=self._udp_dest[1],
                timeout=self._bridge_timeout,
            )
            self.url = info.url
            self.video_url = self.video_url or info.video_url
        if self.transport == "udp":
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.settimeout(1.0)
            # A first datagram registers us with the bridge so telemetry flows
            # back to this socket (the bridge replies to the sender address).
            self._send({"type": "describe"})
            self._spawn(self._control_reader_udp, name="sim2bot-control-udp")
        else:
            self._conn = ws_connect(
                self.url,
                open_timeout=self._connect_timeout,
                additional_headers=self._auth_headers(),
            )
            self._send({"type": "hello", "role": "controller"})
            self._spawn(self._control_reader_ws, name="sim2bot-control")
        self._spawn(self._renew_loop, name="sim2bot-renew")
        if self._wait_for_sim_on_connect:
            self.wait_for_sim(
                timeout=self._wait_for_sim_timeout,
                poll_interval=self._wait_for_sim_poll_interval,
            )
        return self

    def close(self) -> None:
        """Close control, telemetry, camera, and UDP resources.

        Active camera feeds are unsubscribed first. Calling ``close()`` more than
        once is safe.

        Returns:
            The method is safe to call repeatedly.
        """
        self._stop.set()
        with self._cameras_lock:
            camera_ids = list(self._cameras.keys())
        for camera_id in camera_ids:
            self._unsubscribe(camera_id)
        for conn in (self._conn, self._video_conn, self._udp_sock):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        self._conn = None
        self._video_conn = None
        self._udp_sock = None

    def __enter__(self) -> "Robot":
        return self.connect()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _send(self, message: dict) -> None:
        if self._room:
            message = {**message, "room": self._room}
        if self._api_key and self.transport == "udp":
            message = {**message, "authToken": self._api_key}
        payload = json.dumps(message)
        if self.transport == "udp":
            if self._udp_sock is None:
                raise RuntimeError("not connected — call connect() first")
            self._udp_sock.sendto(payload.encode(), self._udp_dest)
            return
        if self._conn is None:
            raise RuntimeError("not connected — call connect() first")
        with self._send_lock:
            self._conn.send(payload)

    # -- control readers -----------------------------------------------------

    def _handle_control_message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "telemetry":
            state = RobotState.from_json(message)
            with self._states_lock:
                self._states[state.robot] = state
            if self._on_telemetry is not None:
                self._on_telemetry(state)  # one call per received packet
        elif kind == "scene":
            self._scene = message
            self._scene_event.set()

    def _control_reader_ws(self) -> None:
        conn = self._conn
        if conn is None:
            return
        while not self._stop.is_set():
            try:
                raw = conn.recv(timeout=1.0)
            except TimeoutError:
                continue
            except ConnectionClosed:
                break
            if isinstance(raw, bytes):
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            self._handle_control_message(message)

    def _control_reader_udp(self) -> None:
        sock = self._udp_sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                message = json.loads(data.decode())
            except ValueError:
                continue
            if isinstance(message, dict):
                self._handle_control_message(message)

    # -- discovery + telemetry ----------------------------------------------

    def describe(self, timeout: float = 2.0) -> list[RobotInfo]:
        """Request model-derived metadata for every robot in the current scene.

        Args:
            timeout: Maximum time in seconds to wait for a scene announcement.

        Returns:
            Robots in current scene order. The returned ``index`` is the command
            address to pass as ``robot=``. An empty list means no scene response
            arrived before the timeout or no robot is loaded.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending discovery.
            OSError: If the selected transport cannot send the request.

        Notes:
            Discover the scene instead of hard-coding degree of freedom, joint
            order, limits, or home targets.
        """
        self._scene_event.clear()
        self._send({"type": "describe"})
        self._scene_event.wait(timeout)
        robots = (self._scene or {}).get("robots", [])
        return [RobotInfo.from_json(item) for item in robots]

    def wait_for_sim(
        self,
        timeout: Optional[float] = None,
        poll_interval: float = 0.5,
    ) -> list[RobotInfo]:
        """Wait until a browser simulator connects and announces robots.

        Args:
            timeout: Maximum total wait in seconds, or ``None`` to wait forever.
            poll_interval: Delay in seconds between unsuccessful discovery calls.

        Returns:
            The non-empty list of discovered
            [`RobotInfo`][sim2bot.client.RobotInfo] objects.

        Raises:
            TimeoutError: If no browser scene appears before ``timeout``.
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes during discovery.
            OSError: If the selected transport cannot send a discovery request.

        Examples:
            This allows a controller to start before the browser::

                with Robot(auto_bridge=True, wait_for_sim=True) as sim:
                    robots = sim.describe()
        """
        deadline = None if timeout is None else time.time() + timeout
        while True:
            describe_timeout = 1.0
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for Sim2Bot browser simulator")
                describe_timeout = max(0.05, min(describe_timeout, remaining))

            robots = self.describe(timeout=describe_timeout)
            if robots:
                return robots

            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for Sim2Bot browser simulator")
                time.sleep(min(poll_interval, remaining))
            else:
                time.sleep(poll_interval)

    def cameras(self, timeout: float = 2.0) -> list[dict]:
        """Return the global scene cameras available for subscription.

        Args:
            timeout: Discovery timeout in seconds when the scene is not cached.

        Returns:
            Camera dictionaries containing an ``id``, label, kind, stream
            defaults, and mount metadata. Mount kind may be ``world``, ``robot``,
            ``object``, or ``sensor``.

        Raises:
            RuntimeError: If discovery is needed and the client is not connected.
            ConnectionClosed: If the WebSocket closes during discovery.
            OSError: If the selected transport cannot send a discovery request.

        Notes:
            Camera IDs are global scene resources. A world-mounted overhead camera
            can observe several robots and is not addressed with ``robot=``.
        """
        if self._scene is None:
            self.describe(timeout)
        return list((self._scene or {}).get("cameras", []))

    def room_devices(self, timeout: float = 2.0) -> list[RoomDeviceInfo]:
        """Return authored doors and windows available for scene-level control.

        Args:
            timeout: Discovery timeout in seconds when the scene is not cached.

        Returns:
            Discovered [`RoomDeviceInfo`][sim2bot.client.RoomDeviceInfo] entries.

        Raises:
            RuntimeError: If discovery is needed and the client is not connected.
            ConnectionClosed: If the WebSocket closes during discovery.
            OSError: If the selected transport cannot send a discovery request.

        Notes:
            Room actuation is experimental while browser-side mechanisms are
            being revised.
        """
        if self._scene is None:
            self.describe(timeout)
        return [
            RoomDeviceInfo.from_json(item)
            for item in (self._scene or {}).get("devices", [])
        ]

    def state(self, robot: int = 0) -> Optional[RobotState]:
        """Return the newest complete telemetry packet for one robot.

        Args:
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            Latest [`RobotState`][sim2bot.client.RobotState], or ``None`` before
            the first packet arrives.

        Notes:
            State is latest-value rather than queued. A slow consumer does not
            fall behind by reading old packets.
        """
        with self._states_lock:
            return self._states.get(robot)

    def on_telemetry(self, callback: Optional[Callable[[RobotState], None]]) -> None:
        """Register or clear a callback for every received robot-state packet.

        Args:
            callback: Function receiving one
                [`RobotState`][sim2bot.client.RobotState], or ``None`` to unregister
                the current callback.

        Returns:
            Registration takes effect immediately.

        Warning:
            The callback runs on the SDK reader thread. Keep it short and
            thread-safe; hand work to your own queue rather than blocking it.
        """
        self._on_telemetry = callback

    def states(self, robot: int = 0) -> list[dict]:
        """Return physics-substep samples carried by the newest telemetry packet.

        Args:
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            A list of ``{"t": seconds, "q": radians, "qd": radians_per_second}``
            dictionaries, or an empty list before telemetry arrives.

        Notes:
            The batch preserves model timestep resolution between lower-rate
            packets. It does not turn browser delivery into a deterministic
            wall-clock 500 Hz stream.
        """
        state = self.state(robot)
        return state.samples if state else []

    def wait_until_reached(
        self,
        q: Sequence[float],
        robot: int = 0,
        tol: float = 0.02,
        timeout: float = 10.0,
    ) -> bool:
        """Wait until every addressed joint is within a target tolerance.

        Args:
            q: Target joint positions in radians and discovered joint order.
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].
            tol: Absolute per-joint tolerance in radians.
            timeout: Maximum wait in seconds.

        Returns:
            ``True`` when all supplied joints reach tolerance; ``False`` on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.state(robot)
            if state and state.q and len(state.q) >= len(q):
                if all(abs(state.q[i] - q[i]) <= tol for i in range(len(q))):
                    return True
            time.sleep(0.02)
        return False

    # -- commands ------------------------------------------------------------

    def move_to(self, q: Sequence[float], robot: int = 0) -> None:
        """Send a joint-position target to one robot.

        Args:
            q: Joint positions in radians, ordered exactly like
                `RobotInfo.joint_names`. Normally supply ``dof`` values.
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            The target is sent asynchronously to the simulator.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Notes:
            The command is latest-writer-wins. In physics mode the robot's
            controller tracks the target; in kinematics-only mode it is applied
            directly. Sending a new joint, velocity, TCP, stop, reset, or
            trajectory command replaces the prior external motion mode.

        Warning:
            Use discovered limits and a model-appropriate trajectory. This method
            does not plan around collisions or guarantee a safe path.

        Examples:
            Move the first discovered robot to its model-defined home::

                arm = sim.describe()[0]
                sim.move_to(arm.home, robot=arm.index)
        """
        self._send({"type": "joint_position", "q": list(q), "robot": robot})

    def set_velocity(self, qd: Sequence[float], robot: int = 0) -> None:
        """Stream a joint-velocity target to one robot.

        Args:
            qd: Joint velocities in radians per second and discovered joint order.
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            The velocity target is sent asynchronously.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Notes:
            Velocity control has a 500 ms safety watchdog. Refresh the command
            faster than that while motion should continue; stale targets expire.
            Commands may be sent faster than the browser applies them, in which
            case the newest value wins.

        Warning:
            This command does not generate collision-free motion. Stop streaming
            or call [`stop`][sim2bot.client.Robot.stop] before leaving a control
            loop.

        Examples:
            Stream a small velocity for 250 ms, then hold::

                sim.set_velocity([0.1] + [0.0] * 6)
                time.sleep(0.25)
                sim.stop()
        """
        self._send({"type": "joint_velocity", "qd": list(qd), "robot": robot})

    def move_to_pose(
        self,
        position: Sequence[float],
        orientation: Optional[Sequence[float]] = None,
        robot: int = 0,
    ) -> None:
        """Command a Cartesian TCP target solved by in-browser inverse kinematics.

        Args:
            position: World-frame ``[x, y, z]`` in metres.
            orientation: Optional world-frame quaternion ``[x, y, z, w]``. Omit
                it for position-only IK.
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            The pose target is sent to the browser IK controller.

        Raises:
            TypeError: If position or orientation is not an iterable of numbers.
            ValueError: If a supplied component cannot be converted to ``float``.
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Notes:
            The IK solution drives the same external target path as joint control.
            Unreachable targets may settle at the closest configuration the solver
            finds; verify `RobotState.tcp` before continuing.

        Warning:
            IK does not imply a collision-free path and can choose a different
            joint configuration near singularities.

        Examples:
            Send a position-only target, then a full pose::

                sim.move_to_pose([0.45, 0.0, 0.35])
                sim.move_to_pose([0.45, 0.0, 0.35], [0.0, 0.0, 0.0, 1.0])
        """
        message: dict = {
            "type": "tcp_pose",
            "position": [float(v) for v in position],
            "robot": robot,
        }
        if orientation is not None:
            message["orientation"] = [float(v) for v in orientation]
        self._send(message)

    def gripper(self, fraction: float, robot: int = 0) -> None:
        """Set normalized gripper openness for one robot.

        Args:
            fraction: ``0.0`` fully closed through ``1.0`` fully open.
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            Unsupported grippers may ignore the command.

        Raises:
            TypeError: If ``fraction`` cannot be converted to ``float``.
            ValueError: If ``fraction`` is not a numeric value.
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Notes:
            Robots without a supported gripper ignore the command. Discover
            `RobotInfo.has_gripper` before relying on it.
        """
        self._send({"type": "gripper", "fraction": float(fraction), "robot": robot})

    def set_room_opening(self, opening: str, fraction: float) -> None:
        """Set the target opening fraction of a scene door or window.

        Args:
            opening: Device ID returned by
                [`room_devices`][sim2bot.client.Robot.room_devices].
            fraction: ``0.0`` closed through ``1.0`` fully open. Values are clamped.

        Returns:
            The clamped target is sent asynchronously.

        Raises:
            TypeError: If ``fraction`` cannot be converted to ``float``.
            ValueError: If ``fraction`` is not a numeric value.
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Warning:
            This API is experimental. Browser-side room actuation is currently
            under revision and may not reliably move every authored mechanism.
            Do not rely on it for automated tests until that work is completed.
        """
        value = max(0.0, min(1.0, float(fraction)))
        self._send(
            {
                "type": "room_opening",
                "opening": str(opening),
                "fraction": value,
            }
        )

    def base_velocity(
        self, vx: float = 0.0, vy: float = 0.0, vz: float = 0.0, omega: float = 0.0,
        robot: int = 0,
    ) -> None:
        """Command a supported mobile or aerial base velocity.

        Args:
            vx: Forward robot-frame linear velocity in metres per second.
            vy: Leftward robot-frame linear velocity in metres per second.
            vz: Vertical velocity in metres per second for aerial bases.
            omega: Yaw rate in radians per second.
            robot: Intended robot index.

        Returns:
            The latest velocity command replaces the previous one.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Warning:
            Multi-base addressing is not complete. The current browser runtime
            drives the scene's primary base even when another ``robot`` index is
            supplied. Treat this API as preview for multi-robot scenes.
        """
        self._send(
            {
                "type": "base_velocity",
                "vx": vx, "vy": vy, "vz": vz, "omega": omega,
                "robot": robot,
            }
        )

    def reset(self, robot: int = 0) -> None:
        """Reset simulation state and release an addressed external command.

        Args:
            robot: Robot whose external command ownership should be released.

        Returns:
            Reset is requested asynchronously.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Warning:
            The current MuJoCo reset is scene-wide, so other robots and dynamic
            objects are also reset even though command release is addressed.
        """
        self._send({"type": "reset", "robot": robot})

    def stop(self, robot: int = 0) -> None:
        """Hold one robot at its current joint pose.

        Args:
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            The simulator receives a hold-position command.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Notes:
            This replaces active velocity, TCP, or trajectory control for the
            addressed robot. It is a simulation hold command, not an emergency
            stop certified for real hardware.
        """
        self._send({"type": "stop", "robot": robot})

    def move_trajectory(
        self,
        points: Iterable[tuple[float, Sequence[float]]],
        robot: int = 0,
        loop: bool = False,
    ) -> None:
        """Play timestamped joint waypoints on one robot.

        Args:
            points: ``(t, q)`` pairs. ``t`` is seconds from trajectory start and
                must increase strictly; ``q`` contains joint positions in radians.
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].
            loop: Repeat from the first point after the last point.

        Returns:
            Playback is started asynchronously in the browser.

        Raises:
            TypeError: If a waypoint is not a ``(time, joints)`` pair.
            ValueError: If a waypoint time or joint value cannot convert to ``float``.
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Notes:
            The browser interpolates by simulation time. Physics mode servos
            through the normal controller; kinematics-only mode applies the
            interpolated target directly. The last point remains held until a new
            motion, stop, reset, or trajectory-stop command arrives.

            Motion-panel exports store ``t`` in milliseconds. Divide those values
            by 1000 before passing them here.

        Examples:
            Play a two-second out-and-back motion::

                home = sim.describe()[0].home
                bent = [value + 0.2 for value in home]
                sim.move_trajectory([(0.0, home), (1.0, bent), (2.0, home)])
        """
        self._send(
            {
                "type": "joint_trajectory",
                "points": [{"t": float(t), "q": [float(v) for v in q]} for t, q in points],
                "robot": robot,
                "loop": loop,
            }
        )

    def stop_trajectory(self, robot: int = 0) -> None:
        """Cancel trajectory playback and hold the current joint pose.

        Args:
            robot: Robot index returned by
                [`describe`][sim2bot.client.Robot.describe].

        Returns:
            Playback is cancelled and the current pose is held.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.
        """
        self._send({"type": "joint_trajectory_stop", "robot": robot})

    # --- Debug markers (RViz-style) --------------------------------------------

    def marker(
        self,
        marker_id: str,
        shape: str,
        position: Optional[Sequence[float]] = None,
        orientation: Optional[Sequence[float]] = None,
        scale: Optional[Union[float, Sequence[float]]] = None,
        color: Optional[Sequence[float]] = None,
        points: Optional[Sequence[Sequence[float]]] = None,
        from_: Optional[Sequence[float]] = None,
        to: Optional[Sequence[float]] = None,
        text: Optional[str] = None,
    ) -> None:
        """Create or update an RViz-style visual debug marker by ID.

        Args:
            marker_id: Scene-unique marker ID. Reuse it to update the same marker.
            shape: ``sphere``, ``box``, ``arrow``, ``line``, ``text``, ``axes``,
                or ``points``.
            position: World-frame ``[x, y, z]`` in metres.
            orientation: World-frame quaternion ``[x, y, z, w]``.
            scale: Uniform size or ``[x, y, z]`` dimensions in metres.
            color: RGBA components from 0 to 1.
            points: World-frame point list for a polyline or point cloud.
            from_: World-frame arrow start ``[x, y, z]``.
            to: World-frame arrow end ``[x, y, z]``.
            text: Label content for a text marker.

        Returns:
            Reusing ``marker_id`` updates the existing marker.

        Raises:
            TypeError: If a vector argument is not an iterable of numbers.
            ValueError: If a vector component cannot be converted to ``float``.
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.

        Notes:
            Markers are visual-only scene helpers. They do not alter physics and
            are intentionally excluded from simulated camera feeds.

        Examples:
            Draw a target position::

                sim.marker(
                    "goal",
                    "sphere",
                    position=[0.45, 0.0, 0.35],
                    scale=0.06,
                    color=[0.36, 0.54, 0.92, 1.0],
                )
        """
        spec: dict = {"id": marker_id, "shape": shape}
        if position is not None:
            spec["position"] = [float(v) for v in position]
        if orientation is not None:
            spec["orientation"] = [float(v) for v in orientation]
        if scale is not None:
            spec["scale"] = scale if isinstance(scale, (int, float)) else [float(v) for v in scale]
        if color is not None:
            spec["color"] = [float(v) for v in color]
        if points is not None:
            spec["points"] = [[float(v) for v in p] for p in points]
        if from_ is not None:
            spec["from"] = [float(v) for v in from_]
        if to is not None:
            spec["to"] = [float(v) for v in to]
        if text is not None:
            spec["text"] = str(text)
        self._send({"type": "marker", "marker": spec})

    def delete_marker(self, marker_id: str) -> None:
        """Remove one debug marker.

        Args:
            marker_id: ID previously supplied to
                [`marker`][sim2bot.client.Robot.marker].

        Returns:
            Deleting an unknown marker ID is harmless.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.
        """
        self._send({"type": "marker_delete", "id": marker_id})

    def clear_markers(self) -> None:
        """Remove every SDK-created debug marker from the current scene.

        Returns:
            Robots, objects, cameras, and room elements are unaffected.

        Raises:
            RuntimeError: If the client is not connected.
            ConnectionClosed: If the WebSocket closes while sending the command.
            OSError: If the selected transport cannot send the command.
        """
        self._send({"type": "marker_clear"})

    # -- cameras -------------------------------------------------------------

    def camera(
        self,
        camera_id: str,
        fps: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        quality: Optional[float] = None,
        codec: Any = None,
    ) -> CameraStream:
        """Subscribe to a global scene camera and return a latest-frame stream.

        Args:
            camera_id: Required ID returned by
                [`cameras`][sim2bot.client.Robot.cameras], such as
                ``model:0``, ``custom:<id>``, or ``sensor:<id>``.
            fps: Requested frame rate from 1 to 30. ``None`` inherits GUI/default
                stream settings.
            width: Requested width in pixels; currently clamped to 16–1920.
            height: Requested height in pixels; currently clamped to 16–1080.
            quality: JPEG quality from 0.1 to 1.0. Ignored for raw frames.
            codec: ``"jpeg"`` for compact frames or ``"raw"`` for uncompressed
                RGBA. H.264 is reserved for later.

        Returns:
            A [`CameraStream`][sim2bot.client.CameraStream]. Close it or use a
            context manager to stop the subscription.

        Raises:
            RuntimeError: If the control client is not connected.
            TimeoutError: If the video WebSocket cannot connect before its timeout.
            ConnectionClosed: If either WebSocket closes during subscription.
            OSError: If the video or control endpoint cannot be opened or written.

        Notes:
            Cameras are global scene resources and do not take ``robot=``. One
            overhead feed can observe several independently addressed robots.
            Unspecified stream options inherit the camera's GUI configuration.

        Examples:
            Read one world-mounted camera frame::

                overhead = next(
                    item for item in sim.cameras()
                    if item["mount"]["kind"] == "world"
                )
                with sim.camera(overhead["id"], fps=24, width=640, height=480) as stream:
                    frame = stream.read(timeout=2.0)
        """
        self._ensure_video()
        stream = CameraStream(self, camera_id)
        # Send only the params the caller specified, so unset ones fall back to
        # the per-camera GUI default on the sim side (controller overrides GUI).
        options: dict = {"type": "camera_subscribe", "camera": camera_id}
        if fps is not None:
            options["fps"] = fps
        if width is not None:
            options["width"] = width
        if height is not None:
            options["height"] = height
        if quality is not None:
            options["quality"] = quality
        if codec is not None:
            options["codec"] = (
                _CODEC_BY_NAME.get(codec, codec) if isinstance(codec, str) else codec
            )
        with self._cameras_lock:
            self._cameras[camera_id] = (options, stream)
        self._send(options)  # initial subscribe; renewed by _renew_loop
        return stream

    def _remove_camera(self, camera_id: str) -> None:
        self._unsubscribe(camera_id)

    def _unsubscribe(self, camera_id: str) -> None:
        with self._cameras_lock:
            self._cameras.pop(camera_id, None)
        try:
            self._send({"type": "camera_unsubscribe", "camera": camera_id})
        except Exception:
            pass

    def _ensure_video(self) -> None:
        if self._video_conn is not None:
            return
        # max_size=None: video frames can exceed the websockets 1 MB default — a
        # RAW 640x480 RGBA frame is ~1.2 MB, and larger resolutions more. Without
        # this the client rejects big frames and the feed silently stops.
        self._video_conn = ws_connect(
            self.video_url,
            open_timeout=self._connect_timeout,
            max_size=None,
            additional_headers=self._auth_headers(),
        )
        hello = {"type": "hello", "role": "controller-video"}
        if self._room:
            hello["room"] = self._room
        self._video_conn.send(json.dumps(hello))
        self._spawn(self._video_reader, name="sim2bot-video")

    def _auth_headers(self) -> Optional[dict[str, str]]:
        if not self._api_key:
            return None
        return {"Authorization": f"Bearer {self._api_key}"}

    def _video_reader(self) -> None:
        conn = self._video_conn
        if conn is None:
            return
        while not self._stop.is_set():
            try:
                raw = conn.recv(timeout=1.0)
            except TimeoutError:
                continue
            except ConnectionClosed:
                break
            if not isinstance(raw, (bytes, bytearray)):
                continue
            frame = _parse_frame(bytes(raw))
            if frame is None:
                continue
            with self._cameras_lock:
                entry = self._cameras.get(frame.camera_id)
            if entry is not None:
                entry[1]._deliver(frame)

    def _renew_loop(self) -> None:
        while not self._stop.wait(_RENEW_INTERVAL_S):
            with self._cameras_lock:
                options = [opts for opts, _ in self._cameras.values()]
            for opts in options:
                try:
                    self._send(opts)
                except Exception:
                    return
