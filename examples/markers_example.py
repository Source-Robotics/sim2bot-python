"""Draw RViz-style debug markers in the scene from a controller (sim2bot SDK).

Markers let controller / policy code draw debug primitives into Sim2Bot — a
target sphere, a predicted-grasp pose (axes), a planned path (line), labels, etc.
Each marker has an id: re-send the same id to update it; delete by id or clear all.

Install the SDK:

    pip install sim2bot

Run, then connect the browser sim (Tools -> Bridge -> Connect):

    python examples/markers_example.py
"""

from __future__ import annotations

import math
import time

from sim2bot import Robot


def main() -> None:
    with Robot(auto_bridge=True, wait_for_sim=True, wait_for_sim_timeout=30) as robot:
        # A target sphere + its label.
        robot.marker("target", "sphere", position=[0.4, 0.0, 0.5], scale=0.08,
                     color=[1.0, 0.3, 0.3, 1.0])
        robot.marker("target_label", "text", position=[0.4, 0.0, 0.62], text="target",
                     color=[1.0, 1.0, 1.0, 1.0])

        # A predicted grasp pose drawn as a coordinate triad.
        robot.marker("grasp", "axes", position=[0.5, 0.2, 0.4], scale=0.15)

        # An arrow (approach direction) and a planned path (polyline).
        robot.marker("approach", "arrow", from_=[0.5, 0.2, 0.6], to=[0.5, 0.2, 0.42],
                     color=[0.2, 1.0, 0.4, 1.0])
        robot.marker("path", "line", color=[1.0, 0.8, 0.0, 1.0], points=[
            [0.3, -0.3, 0.2], [0.4, -0.1, 0.35], [0.5, 0.1, 0.45], [0.5, 0.2, 0.42],
        ])

        # A point cloud (e.g. a perceived/predicted cloud) — shape "points".
        import random
        cloud = [[0.45 + random.uniform(-0.1, 0.1), random.uniform(-0.1, 0.1),
                  0.3 + random.uniform(-0.1, 0.1)] for _ in range(800)]
        robot.marker("cloud", "points", points=cloud, scale=0.006,
                     color=[0.3, 0.9, 1.0, 1.0])

        print("Markers drawn. Animating the target for ~10 s...")
        t0 = time.time()
        while time.time() - t0 < 10.0:
            phase = time.time() - t0
            y = 0.25 * math.sin(phase)
            robot.marker("target", "sphere", position=[0.4, y, 0.5], scale=0.08,
                         color=[1.0, 0.3, 0.3, 1.0])
            time.sleep(0.05)

        robot.clear_markers()
        print("Cleared.")


if __name__ == "__main__":
    main()
