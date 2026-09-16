"""Command line interface for the Sim2Bot Python package."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import socket
import sys
import webbrowser

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

from .bridge import (
    DEFAULT_CONTROL_URL,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    bridge_info,
    health_url,
    is_bridge_running,
    video_url,
)

# The hosted app is the right default for an installed package. Sim2Bot's own
# developers point this at their dev server with SIM2BOT_APP_URL.
DEFAULT_APP_URL = os.environ.get("SIM2BOT_APP_URL", "https://app.sim2bot.com")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sim2bot", description="Sim2Bot SDK tools")
    sub = parser.add_subparsers(dest="command")

    bridge = sub.add_parser("bridge", help="run the local Sim2Bot bridge")
    bridge.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "127.0.0.1"))
    bridge.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "8765")))
    bridge.add_argument(
        "--tcp-port", type=int, default=int(os.environ.get("BRIDGE_TCP_PORT", DEFAULT_TCP_PORT))
    )
    bridge.add_argument(
        "--udp-port", type=int, default=int(os.environ.get("BRIDGE_UDP_PORT", DEFAULT_UDP_PORT))
    )
    bridge.add_argument("--log-level", default="info")
    bridge.set_defaults(func=run_bridge)

    doctor = sub.add_parser("doctor", help="check bridge, browser, and SDK connectivity")
    doctor.add_argument("--url", default=DEFAULT_CONTROL_URL)
    doctor.add_argument("--room", default=os.environ.get("SIM2BOT_BRIDGE_ROOM"))
    doctor.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT)
    doctor.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    doctor.add_argument("--timeout", type=float, default=2.0)
    doctor.set_defaults(func=run_doctor)

    list_robots = sub.add_parser("list-robots", help="list robots announced by the browser sim")
    list_robots.add_argument("--url", default=DEFAULT_CONTROL_URL)
    list_robots.add_argument("--room", default=os.environ.get("SIM2BOT_BRIDGE_ROOM"))
    list_robots.add_argument("--timeout", type=float, default=30.0)
    list_robots.add_argument("--json", action="store_true", help="print machine-readable JSON")
    list_robots.set_defaults(func=run_list_robots)

    open_cmd = sub.add_parser("open", help="open the Sim2Bot web app")
    open_cmd.add_argument("--url", default=DEFAULT_APP_URL)
    open_cmd.set_defaults(func=run_open)

    return parser


def run_bridge(args: argparse.Namespace) -> int:
    os.environ["BRIDGE_TCP_PORT"] = str(args.tcp_port)
    os.environ["BRIDGE_UDP_PORT"] = str(args.udp_port)
    os.environ["BRIDGE_RAW_HOST"] = args.host

    import uvicorn

    url = f"ws://{_display_host(args.host)}:{args.port}/ws"
    print("Sim2Bot bridge running")
    print(f"Control WS: {url}")
    print(f"Video WS:   {video_url(url)}")
    print(f"TCP:        {_display_host(args.host)}:{args.tcp_port}")
    print(f"UDP:        {_display_host(args.host)}:{args.udp_port}")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (
        os.environ.get("SIM2BOT_BRIDGE_TOKEN")
        or os.environ.get("BRIDGE_AUTH_TOKEN")
        or os.environ.get("SIM2BOT_API_KEY")
    ):
        print("LAN clients need SIM2BOT_BRIDGE_TOKEN or BRIDGE_AUTH_TOKEN.")
    print("Waiting for browser simulator...")
    uvicorn.run(
        "sim2bot.bridge_server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    info = bridge_info(args.url, tcp_port=args.tcp_port, udp_port=args.udp_port)
    print("Sim2Bot doctor")
    print("")
    print(f"SDK:      {_version()}")
    print(f"Control:  {info.url}")
    print(f"Video:    {info.video_url}")
    print(f"Health:   {info.health_url}")

    if not is_bridge_running(args.url, timeout=args.timeout):
        print("Bridge:   not running")
        print("")
        print("Start it with `sim2bot bridge`, or use `Robot(auto_bridge=True)`.")
        return 1

    print("Bridge:   running")
    _check_scene(args)
    _check_video(args)
    _check_tcp(info, args.timeout, args.room)
    _check_udp(info, args.timeout, args.room)
    _check_cv2()
    return 0


def run_list_robots(args: argparse.Namespace) -> int:
    from .client import Robot

    try:
        with Robot(
            url=args.url,
            auto_bridge=True,
            wait_for_sim=True,
            wait_for_sim_timeout=args.timeout,
            room=args.room,
        ) as robot:
            robots = robot.describe()
    except TimeoutError:
        print("No robots found.")
        print("Open the Sim2Bot web app, then Tools -> Bridge -> Connect.")
        return 1

    if args.json:
        print(json.dumps([_robot_info_dict(robot) for robot in robots], indent=2))
        return 0

    print(f"Robots: {len(robots)}")
    for robot in robots:
        _print_robot_info(robot)
    return 0


def run_open(args: argparse.Namespace) -> int:
    print(f"Opening {args.url}")
    ok = webbrowser.open(args.url)
    if not ok:
        print("Could not open a browser automatically.")
        return 1
    return 0


def _check_scene(args: argparse.Namespace) -> None:
    try:
        with ws_connect(args.url, open_timeout=args.timeout) as ws:
            ws.send(json.dumps(_with_room({"type": "hello", "role": "controller"}, args.room)))
            ws.send(json.dumps(_with_room({"type": "describe"}, args.room)))
            scene = _recv_type(ws, "scene", args.timeout)
    except Exception as exc:
        print(f"Browser:  control WS failed ({exc})")
        return

    robots = scene.get("robots", [])
    cameras = scene.get("cameras", [])
    if robots:
        print(f"Browser:  connected ({len(robots)} robot(s))")
        for robot in robots:
            print(
                "          "
                f"[{robot.get('index', 0)}] {robot.get('name', '')}, "
                f"id={robot.get('id') or '(none)'}, "
                f"dof={robot.get('dof', '?')}, "
                f"gripper={'yes' if robot.get('hasGripper') else 'no'}"
            )
    else:
        print("Browser:  no simulator scene announced yet")
    print(f"Cameras:  {len(cameras)} available")
    for camera in cameras:
        print(f"          {camera.get('id')} ({camera.get('label', camera.get('kind', 'camera'))})")


def _check_video(args: argparse.Namespace) -> None:
    try:
        with ws_connect(video_url(args.url), open_timeout=args.timeout, max_size=None) as ws:
            ws.send(json.dumps(_with_room({"type": "hello", "role": "controller-video"}, args.room)))
        print("Video:    reachable")
    except Exception as exc:
        print(f"Video:    failed ({exc})")


def _check_tcp(info, timeout: float, room: str | None = None) -> None:
    try:
        with socket.create_connection((info.host, info.tcp_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(json.dumps(_with_room({"type": "describe"}, room)).encode() + b"\n")
            sock.recv(65535)
        print("TCP:      reachable")
    except OSError as exc:
        print(f"TCP:      failed ({exc})")


def _check_udp(info, timeout: float, room: str | None = None) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(json.dumps(_with_room({"type": "describe"}, room)).encode(), (info.host, info.udp_port))
        sock.recvfrom(65535)
        print("UDP:      reachable")
    except OSError as exc:
        print(f"UDP:      failed ({exc})")
    finally:
        sock.close()


def _check_cv2() -> None:
    try:
        import cv2  # type: ignore  # noqa: F401
        import numpy  # type: ignore  # noqa: F401
    except ImportError:
        print("OpenCV:   not installed (install sim2bot[cv2] for frame.image())")
        return
    print("OpenCV:   installed")


def _recv_type(ws, want_type: str, timeout: float) -> dict:
    while True:
        try:
            raw = ws.recv(timeout=timeout)
        except (TimeoutError, ConnectionClosed) as exc:
            raise RuntimeError(f"no {want_type!r} response") from exc
        message = json.loads(raw)
        if message.get("type") == want_type:
            return message


def _with_room(message: dict, room: str | None) -> dict:
    return {**message, "room": room} if room else message


def _display_host(host: str) -> str:
    return "localhost" if host in {"0.0.0.0", "::"} else host


def _print_robot_info(robot) -> None:
    print(f"[{robot.index}] {robot.name}")
    print(f"  id: {robot.id or '(not announced)'}")
    print(f"  dof: {robot.dof}")
    print(f"  joints: {robot.joint_names or '(not announced)'}")
    print(f"  home: {robot.home or '(not announced)'}")
    print(f"  gripper: {robot.has_gripper}")
    print(f"  locomotion: {robot.locomotion}")
    print(f"  base_dof: {robot.base_dof}")


def _robot_info_dict(robot) -> dict:
    return {
        "index": robot.index,
        "id": robot.id,
        "name": robot.name,
        "dof": robot.dof,
        "joint_names": robot.joint_names,
        "joint_limits": robot.joint_limits,
        "home": robot.home,
        "has_gripper": robot.has_gripper,
        "locomotion": robot.locomotion,
        "base_dof": robot.base_dof,
    }


def _version() -> str:
    try:
        return importlib.metadata.version("sim2bot")
    except importlib.metadata.PackageNotFoundError:
        return "editable/local"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
