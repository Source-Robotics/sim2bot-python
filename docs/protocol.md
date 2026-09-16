# Sim2Bot bridge protocol v1

The bridge protocol is JSON over WebSocket, TCP newline-delimited JSON, or UDP
datagrams. Binary camera frames use the separate `/video` WebSocket, framed as
described under [Camera frames](#camera-frames).

This document travels with the `sim2bot` package, and `fixtures/v1/` holds one
checked example of every message in it.

All controller commands may include:

- `robot`: non-negative robot index, default `0`.
- `room`: optional pairing/session id using letters, numbers, `.`, `_`, or `-`.

If `room` is omitted, the bridge uses the `default` room. The bridge relays
commands only inside one room, and within that room sends controller commands to
the newest connected simulator tab.

## Camera identity is independent of robot identity

Robot commands and telemetry use the optional `robot` index. Camera streaming
does **not**: `camera_subscribe` and `camera_unsubscribe` identify only a global
scene camera ID. This lets one overhead/world camera be used while controlling
two or more robot arms, and lets several controllers subscribe to that same feed.

The camera list returned by `describe` includes a `mount` field describing its
physical pose source:

- `{ "kind": "world" }` — fixed scene/overhead camera;
- `{ "kind": "robot", "robotId": "…", "robotName": "…" }` — model or custom camera mounted to a robot;
- `{ "kind": "object", "objectId": "…" }` — camera riding with a scene object;
- `{ "kind": "sensor", "anchor": "…" }` — authored camera sensor bound to a model anchor.

`mount` is discovery metadata only. It does not grant ownership, change stream
routing, or restrict control of any robot. The legacy optional `robot` display
field remains for compatibility; new clients should use `mount`.

## Checked examples

Every message in the tables below has a fixture in [`../fixtures/v1/`](../fixtures/v1/),
and each implementation checks itself against those files:

| Gate | Asserts |
|---|---|
| `tests/test_fixtures.py` (this repository) | The client really sends each fixture's message, really reads that telemetry back, and no message type the client sends is missing its example |
| the Sim2Bot application's own suite | Each fixture is accepted by the validator the simulator's socket handler actually gates on, and every command type it knows has a fixture |

The second gate is what stops this page drifting: adding a command type without
an example fails that build and names the type.

Adding a command type therefore means a fixture, a row below, client support
here, and a matching application release.

Timing summary:

- Robot telemetry packets are configurable up to 60 Hz per robot.
- Telemetry packets may include `samples`, a batch of MuJoCo substep states at
  the model timestep rate.
- Latest-state telemetry includes `q`/`qd`/`qdd`/`qddd` and full TCP pose plus
  linear/angular velocity, acceleration, and jerk. The legacy `tcp: [x,y,z]`
  field remains available.
- Camera subscriptions are clamped to 1–30 FPS.
- Commands are forwarded immediately, but the current browser simulator applies
  the newest command on the next visible-tab sim tick, normally around 60 Hz.
- The browser loop is not hard real time. A backgrounded tab is throttled by the
  browser, and rates fall accordingly. See
  [docs.sim2bot.com](https://docs.sim2bot.com/) for the full timing model.

## Every message, in full

Field names are exact. `robot` and `room` are omitted below — every command
accepts both, as described above. Where this table and a fixture disagree, the
fixture is right: it is the file both implementations are tested against.

### Session

| Message | Payload | Notes |
| --- | --- | --- |
| `hello` | `role: "simulator" \| "controller"` | First message on any connection |
| `describe` | — | Asks for the room's announced robots, cameras and room devices |
| `scene` | *(reply)* `robots[]`, `cameras[]`, `devices[]` | The bridge's answer to `describe`, served from its cached simulator hello |
| `bridge_status` | *(broadcast)* connected-controller count | Sent by the bridge; not something a controller sends |

### Driving a robot

| Message | Payload | Notes |
| --- | --- | --- |
| `joint_position` | `q: number[]` | Target angle per joint, up to the discovered robot DOF (runtime maximum 32). Overrides the local Hold/Sine/Step trajectory |
| `joint_velocity` | `qd: number[]` | Integrated each frame, up to the discovered robot DOF (runtime maximum 32). **500 ms watchdog** — re-send or the arm holds position |
| `joint_trajectory` | `points: {t, q}[]`, `loop?: boolean` | `t` is **seconds from trajectory start**, strictly increasing. Each joint array has the same 32-value safety ceiling. Holds the last point when finished |
| `joint_trajectory_stop` | — | Cancel playback, hold the current pose |
| `tcp_pose` | `position: [x,y,z]`, `orientation?: [x,y,z,w]` | Solved by in-browser IK. Without `orientation` it is position-only; with it, full 6-DOF |
| `gripper` | `fraction: number` | `0` closed … `1` open, mapped onto each gripper actuator's ctrlrange |
| `base_velocity` | `vx, vy, vz, omega` | Robot frame: forward, left, up, yaw. `vz` is aerial-only |
| `reset` | — | Release external targets; return to the local trajectory |
| `stop` | — | Freeze at the current pose |

### Cameras

| Message | Payload | Notes |
| --- | --- | --- |
| `camera_subscribe` | `camera: string`, `fps? width? height? quality? codec?` | `camera` is a global id: `model:<i>`, `custom:<id>` or `sensor:<id>`. **Re-send periodically** — subscriptions expire on a ~3 s TTL so a crashed controller stops the feed. Omitted params fall back to the per-camera GUI defaults |
| `camera_unsubscribe` | `camera: string` | |

Frames arrive on the separate binary `/video` socket, never inline, so a frame
backlog cannot delay a command.

#### Camera frames

Each frame is one binary WebSocket message: a 20-byte little-endian header, a
one-byte camera-ID length, the camera ID as UTF-8, then the encoded image.

| Offset | Type | Field |
| --- | --- | --- |
| 0 | `uint8` | framing version |
| 1 | `uint8` | codec: `0` JPEG, `1` H.264, `2` raw RGBA |
| 2 | `uint16` | flags; bit 0 marks a keyframe |
| 4 | `uint32` | sequence number |
| 8 | `float64` | capture timestamp, seconds since the Unix epoch |
| 16 | `uint16` | width in pixels |
| 18 | `uint16` | height in pixels |
| 20 | `uint8` | length of the camera ID |
| 21 | bytes | camera ID, UTF-8 |
| … | bytes | the frame itself |

Raw frames are `width × height × 4` bytes of RGBA. `sim2bot/client.py` has the
reference reader (`_parse_frame`).

### Debug markers

Purely visual, RViz-style, and **not per-robot**.

| Message | Payload | Notes |
| --- | --- | --- |
| `marker` | `marker: MarkerSpec` | Re-send the same `id` to update it in place |
| `marker_delete` | `id: string` | |
| `marker_clear` | — | Remove all |

`MarkerSpec` carries `id` and `shape` (`sphere`, `box`, `arrow`, `line`, `text`,
`axes`, `points`), plus optional `position`, `orientation`, `scale`, `color`, and
shape-specific `points` / `from` / `to` / `text`. `points` is a point cloud, with
`scale` as the point size.

### Architecture

| Message | Payload | Notes |
| --- | --- | --- |
| `room_opening` | `opening: string`, `fraction: number` | `0` closed … `1` fully open. Motorised openings follow it; passive ones are repositioned as a setup action |

The simulator hello and `scene` discovery response include `devices` entries for
room doors/windows (`id`, `name`, `kind`, `motion`, `wall`, `maxOpenDeg`).
Robot index 0 telemetry includes the scene-level `roomDevices` state array with
each opening's normalized requested `target`, measured normalized `position`,
and hinge `velocity` in rad/s.
