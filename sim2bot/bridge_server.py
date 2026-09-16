"""Packaged Sim2Bot local bridge server.

This is the same FastAPI relay used by the development bridge, but kept inside
the Python package so `sim2bot bridge` and `Robot(auto_bridge=True)` can start it
without relying on the repository layout.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import secrets
import string
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="Sim2Bot Simulated Robot Bridge")

simulators: set[WebSocket] = set()
controllers: set[WebSocket] = set()
websocket_rooms: dict[WebSocket, str] = {}
latest_simulator_by_room: dict[str, WebSocket] = {}

tcp_clients: dict[asyncio.StreamWriter, str] = {}
udp_clients: dict[tuple[str, int], str] = {}
udp_transport: asyncio.DatagramTransport | None = None

COMMAND_TYPES = {
    "joint_position",
    "joint_velocity",
    "tcp_pose",
    "gripper",
    "room_opening",
    "base_velocity",
    "camera_subscribe",
    "camera_unsubscribe",
    "marker",
    "marker_delete",
    "marker_clear",
    "reset",
    "stop",
    "joint_trajectory",
    "joint_trajectory_stop",
}

DEFAULT_ROOM = "default"
MAX_ROOM_LENGTH = 80
ROOM_CHARS = set(string.ascii_letters + string.digits + "._-")

scene_by_room: dict[str, dict[str, list]] = {}

video_simulators: set[WebSocket] = set()
video_controllers: set[WebSocket] = set()
video_rooms: dict[WebSocket, str] = {}


def normalize_room(value: object | None) -> str:
    if not isinstance(value, str):
        return DEFAULT_ROOM
    room = value.strip()
    if not room or len(room) > MAX_ROOM_LENGTH:
        return DEFAULT_ROOM
    if any(ch not in ROOM_CHARS for ch in room):
        return DEFAULT_ROOM
    return room


def message_room(message: dict, fallback: str = DEFAULT_ROOM) -> str:
    return normalize_room(message.get("room") or fallback)


def websocket_room(websocket: WebSocket, fallback: str = DEFAULT_ROOM) -> str:
    return normalize_room(websocket.query_params.get("room") or fallback)


def scene_message(room: str = DEFAULT_ROOM) -> dict:
    scene = scene_by_room.get(room, {"robots": [], "cameras": [], "devices": []})
    return {
        "type": "scene",
        "room": room,
        "robots": scene["robots"],
        "cameras": scene["cameras"],
        "devices": scene.get("devices", []),
    }


def tcp_port() -> int:
    return int(os.environ.get("BRIDGE_TCP_PORT", "8770"))


def udp_port() -> int:
    return int(os.environ.get("BRIDGE_UDP_PORT", "8771"))


def raw_host() -> str:
    return os.environ.get("BRIDGE_RAW_HOST") or os.environ.get("BRIDGE_HOST") or "127.0.0.1"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def bridge_token() -> str | None:
    return (
        os.environ.get("SIM2BOT_BRIDGE_TOKEN")
        or os.environ.get("BRIDGE_AUTH_TOKEN")
        or os.environ.get("SIM2BOT_API_KEY")
    )


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_client(websocket: WebSocket) -> bool:
    return is_loopback_host(websocket.client.host if websocket.client else None)


def allowed_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True

    allowed = {
        item.strip()
        for item in os.environ.get("BRIDGE_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if origin in allowed:
        return True

    parsed = urlparse(origin)
    return is_loopback_host(parsed.hostname)


def websocket_token(websocket: WebSocket) -> str | None:
    for key in ("token", "api_key", "authToken"):
        value = websocket.query_params.get(key)
        if value:
            return value
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return websocket.headers.get("x-sim2bot-token")


def token_matches(value: str | None) -> bool:
    expected = bridge_token()
    return bool(expected and value and secrets.compare_digest(value, expected))


async def authorize_websocket(websocket: WebSocket) -> bool:
    if not allowed_origin(websocket):
        await websocket.close(code=1008, reason="Origin is not allowed")
        return False
    if is_loopback_client(websocket):
        return True
    if token_matches(websocket_token(websocket)):
        return True
    await websocket.close(code=1008, reason="Remote bridge access requires a token")
    return False


def sanitize_message(message: dict) -> dict:
    if not any(key in message for key in ("authToken", "apiKey", "api_key", "token")):
        return message
    cleaned = dict(message)
    for key in ("authToken", "apiKey", "api_key", "token"):
        cleaned.pop(key, None)
    return cleaned


def message_token(message: dict) -> str | None:
    for key in ("authToken", "apiKey", "api_key", "token"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def raw_client_authorized(host: str | None, message: dict) -> bool:
    return is_loopback_host(host) or token_matches(message_token(message))


async def broadcast(targets, message: dict) -> None:
    dead: list[WebSocket] = []
    for ws in list(targets):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        targets.discard(ws)


def sockets_in_room(sockets: set[WebSocket], rooms: dict[WebSocket, str], room: str) -> set[WebSocket]:
    return {ws for ws in sockets if rooms.get(ws, DEFAULT_ROOM) == room}


def simulator_targets(room: str) -> set[WebSocket]:
    latest = latest_simulator_by_room.get(room)
    if latest in simulators and websocket_rooms.get(latest, DEFAULT_ROOM) == room:
        return {latest}
    return sockets_in_room(simulators, websocket_rooms, room)


def controller_targets(room: str) -> set[WebSocket]:
    return sockets_in_room(controllers, websocket_rooms, room)


def register_websocket(websocket: WebSocket, role: str, room: str) -> None:
    old_room = websocket_rooms.get(websocket)
    simulators.discard(websocket)
    controllers.discard(websocket)
    websocket_rooms[websocket] = room
    if role == "simulator":
        simulators.add(websocket)
        latest_simulator_by_room[room] = websocket
    else:
        controllers.add(websocket)
    if old_room and old_room != room and latest_simulator_by_room.get(old_room) is websocket:
        promote_latest_simulator(old_room)


def promote_latest_simulator(room: str) -> None:
    for ws in list(simulators):
        if websocket_rooms.get(ws, DEFAULT_ROOM) == room:
            latest_simulator_by_room[room] = ws
            return
    latest_simulator_by_room.pop(room, None)


def forward_to_simulators(message: dict, room: str | None = None) -> None:
    if message.get("type") in COMMAND_TYPES:
        asyncio.create_task(broadcast(simulator_targets(room or message_room(message)), message))


def forward_telemetry_to_raw(message: dict, room: str) -> None:
    line = (json.dumps(message) + "\n").encode()
    for writer, client_room in list(tcp_clients.items()):
        if client_room != room:
            continue
        try:
            writer.write(line)
        except Exception:
            tcp_clients.pop(writer, None)
    if udp_transport is not None:
        datagram = json.dumps(message).encode()
        for addr, client_room in list(udp_clients.items()):
            if client_room != room:
                continue
            try:
                udp_transport.sendto(datagram, addr)
            except Exception:
                udp_clients.pop(addr, None)


async def broadcast_status(room: str = DEFAULT_ROOM) -> None:
    await broadcast(
        sockets_in_room(simulators | controllers, websocket_rooms, room),
        {
            "type": "bridge_status",
            "room": room,
            "simulators": len(sockets_in_room(simulators, websocket_rooms, room)),
            "controllers": (
                len(sockets_in_room(controllers, websocket_rooms, room))
                + sum(1 for client_room in tcp_clients.values() if client_room == room)
                + sum(1 for client_room in udp_clients.values() if client_room == room)
            ),
        },
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if not await authorize_websocket(websocket):
        return
    await websocket.accept()
    role: str | None = None
    room = websocket_room(websocket)

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            message = sanitize_message(message)
            msg_type = message.get("type")

            if msg_type == "hello":
                role = "simulator" if message.get("role") == "simulator" else "controller"
                old_room = room
                room = message_room(message, room)
                register_websocket(websocket, role, room)
                if role == "simulator" and message.get("robots"):
                    scene_by_room[room] = {
                        "robots": message.get("robots") or [],
                        "cameras": message.get("cameras") or [],
                        "devices": message.get("devices") or [],
                    }
                if old_room != room:
                    await broadcast_status(old_room)
                await broadcast_status(room)
                continue

            if msg_type == "describe":
                await websocket.send_json(scene_message(message_room(message, room)))
                continue

            if role is None:
                role = "simulator" if msg_type == "telemetry" else "controller"
                room = message_room(message, room)
                register_websocket(websocket, role, room)
                await broadcast_status(room)

            if role == "controller" and msg_type in COMMAND_TYPES:
                await broadcast(simulator_targets(message_room(message, room)), message)
            elif role == "simulator" and msg_type == "telemetry":
                await broadcast(controller_targets(room), message)
                forward_telemetry_to_raw(message, room)
    except WebSocketDisconnect:
        pass
    finally:
        room = websocket_rooms.pop(websocket, room)
        simulators.discard(websocket)
        controllers.discard(websocket)
        if latest_simulator_by_room.get(room) is websocket:
            promote_latest_simulator(room)
        if not sockets_in_room(simulators, websocket_rooms, room):
            scene_by_room.pop(room, None)
        await broadcast_status(room)


@app.websocket("/video")
async def video_endpoint(websocket: WebSocket) -> None:
    if not await authorize_websocket(websocket):
        return
    await websocket.accept()
    role: str | None = None
    room = websocket_room(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            if text is not None:
                try:
                    data = json.loads(text)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "hello":
                    room = message_room(data, room)
                    role = (
                        "simulator-video"
                        if data.get("role") == "simulator-video"
                        else "controller-video"
                    )
                    (video_simulators if role == "simulator-video" else video_controllers).add(
                        websocket
                    )
                    video_rooms[websocket] = room
                continue

            frame = message.get("bytes")
            if frame is None:
                continue
            if role is None:
                role = "simulator-video"
                video_simulators.add(websocket)
                video_rooms[websocket] = room
            if role == "simulator-video":
                dead: list[WebSocket] = []
                for ws in list(video_controllers):
                    if video_rooms.get(ws, DEFAULT_ROOM) != room:
                        continue
                    try:
                        await ws.send_bytes(frame)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    video_controllers.discard(ws)
    except WebSocketDisconnect:
        pass
    finally:
        video_rooms.pop(websocket, None)
        video_simulators.discard(websocket)
        video_controllers.discard(websocket)


async def handle_tcp_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    peer = writer.get_extra_info("peername")
    peer_host = peer[0] if isinstance(peer, tuple) and peer else None
    authorized = is_loopback_host(peer_host)
    tcp_clients[writer] = DEFAULT_ROOM
    await broadcast_status(DEFAULT_ROOM)
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode())
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(message, dict):
                old_room = tcp_clients.get(writer, DEFAULT_ROOM)
                room = message_room(message, old_room)
                if room != old_room:
                    tcp_clients[writer] = room
                    await broadcast_status(old_room)
                    await broadcast_status(room)
                if not authorized:
                    authorized = token_matches(message_token(message))
                    if not authorized:
                        writer.write(b'{"type":"error","error":"unauthorized"}\n')
                        await writer.drain()
                        break
                message = sanitize_message(message)
                if message.get("type") == "describe":
                    writer.write((json.dumps(scene_message(room)) + "\n").encode())
                else:
                    forward_to_simulators(message, room)
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        room = tcp_clients.pop(writer, DEFAULT_ROOM)
        try:
            writer.close()
        except Exception:
            pass
        await broadcast_status(room)


class CommandUDPProtocol(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            message = json.loads(data.decode())
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(message, dict):
            return
        if not raw_client_authorized(addr[0], message):
            return
        room = message_room(message, udp_clients.get(addr, DEFAULT_ROOM))
        message = sanitize_message(message)
        udp_clients[addr] = room
        if message.get("type") == "describe":
            if udp_transport is not None:
                udp_transport.sendto(json.dumps(scene_message(room)).encode(), addr)
            return
        forward_to_simulators(message, room)


@app.on_event("startup")
async def start_raw_endpoints() -> None:
    global udp_transport
    loop = asyncio.get_running_loop()

    tcp_server = await asyncio.start_server(handle_tcp_client, raw_host(), tcp_port())
    app.state.tcp_server = tcp_server

    udp_transport, _ = await loop.create_datagram_endpoint(
        CommandUDPProtocol, local_addr=(raw_host(), udp_port())
    )


@app.on_event("shutdown")
async def stop_raw_endpoints() -> None:
    server = getattr(app.state, "tcp_server", None)
    if server is not None:
        server.close()
    if udp_transport is not None:
        udp_transport.close()
