"""Measure the TRUE camera delivery rate — how many frames/sec your code receives,
with no window and no decode (i.e. what an AI/vision loop actually gets).

This differs from camera_viewer.py: that one decodes + draws each frame in an
OpenCV window, so its fps is the *display* rate (GUI overhead drags it down). Here
we just count frames off the stream, which reflects what the sim actually delivers.

Setup: pip install sim2bot
Run this script, then connect the browser (keep the tab VISIBLE — a backgrounded
tab is throttled by the browser):

    python examples/camera_fps.py                      # first camera, GUI defaults
    python examples/camera_fps.py model:0 raw             # raw codec (skips JPEG encode)
    python examples/camera_fps.py model:0 jpeg 320x240    # lighter encode
    python examples/camera_fps.py model:0 jpeg 640x480 24 # request 24 fps

Args: [camera_id] [codec=jpeg|raw] [WIDTHxHEIGHT] [fps]. fps is capped at 30 (24 or
30). Omitted params inherit the camera's GUI default. Compare codecs/resolutions/fps
to find this machine's best rate. NOTE: keep the Sim2Bot browser window VISIBLE —
a backgrounded/occluded tab is throttled by the browser and the rate collapses.
"""

from __future__ import annotations

import sys
import time

from sim2bot import Robot

DURATION_S = 5.0


def main(
    camera_id: str | None,
    codec: str | None,
    width: int | None,
    height: int | None,
    fps: int | None,
) -> None:
    with Robot(auto_bridge=True, wait_for_sim=True, wait_for_sim_timeout=30) as robot:
        cams = robot.cameras()
        if not cams:
            print("No cameras — connect the browser to the bridge and use a camera.")
            return
        camera_id = camera_id or cams[0]["id"]
        size = f"{width}x{height}" if width else "gui-default"
        print(
            f"measuring {camera_id} delivery for {DURATION_S:.0f}s "
            f"(codec={codec or 'gui-default'}, {size}, "
            f"{fps or 'gui-default'} fps, no decode/window)..."
        )

        n = 0
        first = None
        with robot.camera(camera_id, codec=codec, width=width, height=height, fps=fps) as feed:
            deadline = time.time() + DURATION_S
            # read(timeout) returns None on timeout, so we exit on the wall-clock
            # deadline even if NO frames arrive (no hang).
            while time.time() < deadline:
                frame = feed.read(timeout=0.5)
                if frame is None:
                    continue
                n += 1
                if first is None:
                    first = frame.age

        print(f"  delivered: {n / DURATION_S:.1f} frames/sec")
        if first is not None:
            print(f"  first-frame age: {first * 1000:.0f} ms")
        if n == 0:
            print("  (no frames — browser not connected/visible, or sim paused.)")


if __name__ == "__main__":
    cam = sys.argv[1] if len(sys.argv) > 1 else None
    codec_arg = sys.argv[2] if len(sys.argv) > 2 else None
    w: int | None = None
    h: int | None = None
    if len(sys.argv) > 3 and "x" in sys.argv[3].lower():
        w, h = (int(v) for v in sys.argv[3].lower().split("x", 1))
    fps_arg = int(sys.argv[4]) if len(sys.argv) > 4 else None
    try:
        main(cam, codec_arg, w, h, fps_arg)
    except KeyboardInterrupt:
        pass
