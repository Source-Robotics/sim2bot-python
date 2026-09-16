"""Live camera viewer for Sim2Bot (opens an OpenCV window).

Discovers the cameras the simulator exposes, subscribes to one, and shows the
decoded frames in a window with a glass-to-glass latency overlay.

Setup:
    pip install "sim2bot[cv2]"          # SDK + OpenCV/numpy for decode+display

Then run this script, open the app (https://app.sim2bot.com), Tools -> Bridge ->
Connect, and make sure a camera exists (Tools -> Cameras -> add "World frame",
or load a robot with a built-in camera like SO-101's wrist_cam):

    python examples/camera_viewer.py                       # first camera, GUI defaults
    python examples/camera_viewer.py model:0 raw           # raw (lowest latency)
    python examples/camera_viewer.py model:0 jpeg 1280x720 30   # custom resolution + fps

Args: [camera_id] [codec=jpeg|raw] [WIDTHxHEIGHT] [fps]. Camera fps is capped at 30
(24 or 30 are the supported rates). Omitted params inherit the camera's GUI default
(set in the app's camera-window gear). Press q to quit.
"""

from __future__ import annotations

import sys
import time

import cv2  # from the [cv2] extra

from sim2bot import Robot


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
            print(
                "No cameras in the scene. In the app: Tools -> Cameras -> add "
                '"World frame" (or load SO-101 for a built-in wrist_cam), then '
                "Tools -> Bridge -> Connect."
            )
            return
        if camera_id is None:
            camera_id = cams[0]["id"]
        print("available cameras:", [c.get("id") for c in cams])
        size = f"{width}x{height}" if width else "gui-default"
        print(
            f"viewing {camera_id} (codec={codec or 'gui-default'}, "
            f"{size} @ {fps or 'gui-default'} fps) — press q to quit"
        )

        last = time.time()
        fps_ema = 0.0
        with robot.camera(camera_id, fps=fps, width=width, height=height, codec=codec) as feed:
            for frame in feed:
                img = frame.image()
                if img is None:
                    continue
                now = time.time()
                inst = 1.0 / max(now - last, 1e-3)
                last = now
                # Smooth the per-frame rate (instantaneous 1/dt is very noisy).
                fps_ema = inst if fps_ema == 0.0 else fps_ema * 0.9 + inst * 0.1
                cv2.putText(
                    img,
                    f"{frame.age * 1000:.0f} ms  {fps_ema:.0f} fps  #{frame.sequence}",
                    (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )
                cv2.imshow("Sim2Bot camera", img)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
        cv2.destroyAllWindows()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    # Omitted args stay None so they inherit the camera's GUI default on the sim.
    codec_arg = sys.argv[2] if len(sys.argv) > 2 else None
    w: int | None = None
    h: int | None = None
    if len(sys.argv) > 3 and "x" in sys.argv[3].lower():
        w, h = (int(v) for v in sys.argv[3].lower().split("x", 1))
    fps_arg = int(sys.argv[4]) if len(sys.argv) > 4 else None
    try:
        main(arg, codec_arg, w, h, fps_arg)
    except KeyboardInterrupt:
        pass
