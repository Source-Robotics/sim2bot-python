"""Measure Sim2Bot telemetry rate — shows the high-rate batching (A2).

Packets arrive at ~30 Hz, but each carries a batch of per-physics-substep samples,
so the *effective* sample rate a controller can reconstruct is much higher
(hundreds of Hz to ~1 kHz). This prints both so you can see the difference.

Setup:
    pip install sim2bot

Run this script, then open the app (https://app.sim2bot.com) and click
Tools -> Bridge -> Connect:

    python examples/telemetry_rate.py          # WebSocket (TCP)
    python examples/telemetry_rate.py udp      # UDP control/telemetry transport
"""

from __future__ import annotations

import sys
import threading
import time

from sim2bot import Robot

DURATION_S = 5.0


def main(transport: str) -> None:
    packets = 0
    samples = 0
    last_latency = None
    lock = threading.Lock()

    def on_packet(state) -> None:
        nonlocal packets, samples, last_latency
        if state.robot != 0:
            return
        with lock:
            packets += 1
            samples += len(state.samples)
            last_latency = state.latency

    with Robot(transport=transport, auto_bridge=True, wait_for_sim=True, wait_for_sim_timeout=30) as robot:
        info = robot.describe()
        print(f"transport: {transport}")
        print(f"robot: [{info[0].index}] {info[0].name} id={info[0].id or '(none)'} (dof {info[0].dof})")
        robot.on_telemetry(on_packet)

        print(f"measuring for {DURATION_S:.0f}s ...")
        time.sleep(DURATION_S)

        with lock:
            p, s, lat = packets, samples, last_latency

    pkt_hz = p / DURATION_S
    eff_hz = s / DURATION_S
    per_pkt = s / p if p else 0
    print(f"  packets/s:            {pkt_hz:.0f}")
    print(f"  samples/packet:       {per_pkt:.1f}")
    print(f"  effective sample rate: {eff_hz:.0f} Hz   <- high-rate batch")
    if lat is not None:
        print(f"  telemetry latency:    {lat * 1000:.0f} ms")
    if s == 0:
        print("  (no batch samples — is the sim paused? unpause it in the toolbar.)")


if __name__ == "__main__":
    # Optional transport arg: "ws" (default) or "udp".
    transport_arg = sys.argv[1] if len(sys.argv) > 1 else "ws"
    try:
        main(transport_arg)
    except KeyboardInterrupt:
        pass
