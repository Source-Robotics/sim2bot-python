"""Python client for the Sim2Bot browser robot simulator.

Starts (or connects to) the local bridge and exposes a robot endpoint:
control commands, telemetry, scene discovery, and camera feeds — the same way
you'd talk to a real robot.

    from sim2bot import Robot

    with Robot() as robot:
        info = robot.describe()          # what's loaded (dof, home, cameras, ...)
        robot.move_to(info[0].home)      # command joint positions
        print(robot.state().q)           # read telemetry
        for frame in robot.camera("model:0", fps=30):
            img = frame.image()          # numpy BGR (needs the [cv2] extra)
"""

from .client import (
    CameraFrame,
    CameraStream,
    Robot,
    RobotInfo,
    RobotState,
    RoomDeviceInfo,
)
from .bridge import BridgeInfo, ensure_bridge, is_bridge_running, shutdown_managed_bridges

__all__ = [
    "Robot",
    "RobotState",
    "RobotInfo",
    "RoomDeviceInfo",
    "CameraFrame",
    "CameraStream",
    "BridgeInfo",
    "ensure_bridge",
    "is_bridge_running",
    "shutdown_managed_bridges",
]
