"""Helpers for starting and checking the local Sim2Bot bridge."""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

DEFAULT_CONTROL_URL = "ws://localhost:8765/ws"
DEFAULT_TCP_PORT = 8770
DEFAULT_UDP_PORT = 8771


@dataclass(frozen=True)
class BridgeInfo:
    """Connection details for a local Sim2Bot bridge.

    Attributes:
        url: JSON control and telemetry WebSocket URL.
        video_url: Binary camera-frame WebSocket URL.
        health_url: HTTP health-check URL.
        host: Parsed bridge host.
        port: WebSocket/HTTP port.
        tcp_port: Newline-delimited JSON TCP port.
        udp_port: Datagram control/telemetry port.
        started_by_sdk: Whether [`ensure_bridge`][sim2bot.bridge.ensure_bridge]
            started this process.
        pid: Managed bridge process ID when available.
    """

    url: str
    video_url: str
    health_url: str
    host: str
    port: int
    tcp_port: int
    udp_port: int
    started_by_sdk: bool = False
    pid: Optional[int] = None


_managed_processes: list[subprocess.Popen] = []


def video_url(control_url: str = DEFAULT_CONTROL_URL) -> str:
    """Return the binary video WebSocket URL for a control WebSocket URL."""
    if control_url.endswith("/ws"):
        return control_url[: -len("/ws")] + "/video"
    return control_url.rstrip("/") + "/video"


def health_url(control_url: str = DEFAULT_CONTROL_URL) -> str:
    parsed = urlparse(control_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    return f"{scheme}://{host}:{port}/health"


def bridge_info(
    url: str = DEFAULT_CONTROL_URL,
    tcp_port: int = DEFAULT_TCP_PORT,
    udp_port: int = DEFAULT_UDP_PORT,
    started_by_sdk: bool = False,
    pid: Optional[int] = None,
) -> BridgeInfo:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    return BridgeInfo(
        url=url,
        video_url=video_url(url),
        health_url=health_url(url),
        host=host,
        port=port,
        tcp_port=tcp_port,
        udp_port=udp_port,
        started_by_sdk=started_by_sdk,
        pid=pid,
    )


def is_bridge_running(url: str = DEFAULT_CONTROL_URL, timeout: float = 0.5) -> bool:
    """Check whether the bridge health endpoint responds successfully.

    Args:
        url: Bridge control WebSocket URL used to derive the health URL.
        timeout: HTTP timeout in seconds.

    Returns:
        ``True`` only when the endpoint responds with HTTP 200.
    """
    try:
        with urllib.request.urlopen(health_url(url), timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_for_bridge(url: str = DEFAULT_CONTROL_URL, timeout: float = 10.0) -> bool:
    """Poll until the bridge becomes healthy or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_bridge_running(url, timeout=0.3):
            return True
        time.sleep(0.1)
    return is_bridge_running(url, timeout=0.3)


def ensure_bridge(
    url: str = DEFAULT_CONTROL_URL,
    *,
    host: Optional[str] = None,
    tcp_port: int = DEFAULT_TCP_PORT,
    udp_port: int = DEFAULT_UDP_PORT,
    timeout: float = 10.0,
) -> BridgeInfo:
    """Reuse a healthy local bridge or start one in the background.

    Args:
        url: Desired control WebSocket URL.
        host: Bind host for a new process. Defaults to loopback unless configured
            with ``BRIDGE_HOST``.
        tcp_port: Newline-delimited JSON TCP port.
        udp_port: Datagram control/telemetry port.
        timeout: Seconds to wait for a new bridge to become healthy.

    Returns:
        Connection and ownership details as
        [`BridgeInfo`][sim2bot.bridge.BridgeInfo].

    Raises:
        RuntimeError: If a new bridge exits early or does not become healthy.

    Notes:
        The subprocess uses the current Python interpreter and is terminated at
        interpreter exit. Use the ``sim2bot bridge`` CLI for a manually managed,
        long-running process. The default loopback bind is deliberate; LAN access
        requires explicit host and authentication configuration.
    """
    if is_bridge_running(url):
        return bridge_info(url, tcp_port=tcp_port, udp_port=udp_port)

    parsed = urlparse(url)
    bind_host = host or os.environ.get("BRIDGE_HOST") or "127.0.0.1"
    port = parsed.port or 8765
    env = os.environ.copy()
    env["BRIDGE_TCP_PORT"] = str(tcp_port)
    env["BRIDGE_UDP_PORT"] = str(udp_port)
    env["BRIDGE_RAW_HOST"] = bind_host
    env.setdefault("PYTHONUNBUFFERED", "1")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "sim2bot.bridge_server:app",
        "--host",
        bind_host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _managed_processes.append(process)
    _register_cleanup()

    if wait_for_bridge(url, timeout=timeout):
        return bridge_info(
            url,
            tcp_port=tcp_port,
            udp_port=udp_port,
            started_by_sdk=True,
            pid=process.pid,
        )

    exit_code = process.poll()
    if is_bridge_running(url):
        return bridge_info(url, tcp_port=tcp_port, udp_port=udp_port)
    if exit_code is None:
        _terminate_process(process)
        raise RuntimeError(f"Sim2Bot bridge did not become healthy within {timeout:.1f}s")
    raise RuntimeError(
        f"Sim2Bot bridge failed to start (exit code {exit_code}); "
        "run `sim2bot bridge` to see server logs"
    )


def shutdown_managed_bridges() -> None:
    """Terminate bridge processes started by
    [`ensure_bridge`][sim2bot.bridge.ensure_bridge] in this process.

    Bridges that were already running and merely discovered are not stopped.

    Returns:
        Already-running bridges not owned by this process remain active.
    """
    for process in list(_managed_processes):
        _terminate_process(process)
    _managed_processes.clear()


def _register_cleanup() -> None:
    if getattr(_register_cleanup, "_registered", False):
        return
    atexit.register(shutdown_managed_bridges)
    setattr(_register_cleanup, "_registered", True)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)
