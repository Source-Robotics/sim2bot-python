# sim2bot — Python client for the Sim2Bot simulator

[![CI](https://github.com/Source-Robotics/sim2bot-python/actions/workflows/ci.yml/badge.svg)](https://github.com/Source-Robotics/sim2bot-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sim2bot.svg)](https://pypi.org/project/sim2bot/)
[![Python versions](https://img.shields.io/pypi/pyversions/sim2bot.svg)](https://pypi.org/project/sim2bot/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Control a robot in [Sim2Bot](https://sim2bot.com/) from Python, the same way you
would talk to a real one: send joint or Cartesian targets, read telemetry, open
and close a gripper, subscribe to camera feeds.

```python
from sim2bot import Robot

with Robot(auto_bridge=True, wait_for_sim=True) as robot:
    arm = robot.describe()[0]
    robot.move_to(arm.home)
    robot.wait_until_reached(arm.home)
    print(robot.state().q)
```

## How the pieces fit

Sim2Bot runs the physics (MuJoCo, compiled to WebAssembly) **in your browser**.
This package talks to that tab through a small local relay — the *bridge* — that
ships inside the package itself:

```
your Python script  ──►  sim2bot bridge (localhost)  ──►  Sim2Bot tab in your browser
      commands                 relay only                      MuJoCo physics
      telemetry  ◄──                                  ◄──      cameras, sensors
```

The bridge forwards messages and holds no robot state of its own. Nothing about
your scene leaves your machine.

## Install

```sh
pip install sim2bot                 # control + telemetry
pip install "sim2bot[cv2]"          # + camera frames decoded to numpy images
```

Python 3.9 or newer.

## First run

1. Run your script. `auto_bridge=True` starts the local bridge if it is not
   already running, and `wait_for_sim=True` waits for the browser.
2. Open [app.sim2bot.com](https://app.sim2bot.com/), load a robot, and click
   **Tools → Bridge → Connect**.

```python
from sim2bot import Robot

with Robot(auto_bridge=True, wait_for_sim=True) as robot:
    for info in robot.describe():
        print(f"[{info.index}] {info.name} id={info.id} dof={info.dof}")
```

`wait_for_sim=True` blocks until a simulator announces at least one robot; pass
`wait_for_sim_timeout=30` for a finite wait.

## What you can do

```python
from sim2bot import Robot

with Robot() as robot:                      # ws://localhost:8765/ws
    info = robot.describe()                 # dof, home, limits, gripper, cameras, ...
    print(info[0].index, info[0].id, info[0].name, info[0].dof)

    robot.move_to(info[0].home)             # joint position target
    robot.set_velocity([0.1] * info[0].dof) # joint velocity target
    robot.move_to_pose([0.45, 0.0, 0.35])   # Cartesian target, solved by browser IK
    robot.move_trajectory([(0.0, q0), (1.5, q1)])   # timed joint trajectory
    robot.stop()

    state = robot.state()
    print(state.q, state.qd, state.qdd, state.qddd)   # position ... jerk
    print(state.tcp, state.tcp_orientation)           # TCP position + quaternion
    print(state.tcp_linear_velocity, state.tcp_angular_velocity)

    if info[0].has_gripper:
        robot.gripper(1.0)                  # 0.0 closed .. 1.0 open

    robot.base_velocity(vx=0.4, omega=0.35) # mobile / aerial bases

    robot.marker("goal", "sphere", position=[0.45, 0, 0.35], scale=0.06)

    for device in robot.room_devices():     # doors and windows in the scene
        if device.motion == "actuated":
            robot.set_room_opening(device.id, 1.0)

    for cam in robot.cameras():
        print(cam["id"], cam["label"])

    with robot.camera("model:0", fps=30) as feed:     # codec="raw" for lowest latency
        frame = feed.read(timeout=2.0)
        image = frame.image()               # numpy BGR — needs the [cv2] extra
```

Telemetry and camera reads are **latest-value, drop-don't-queue**: they return
the present, never a backlog.

Robot discovery gives you both `index` and `id`. Address commands by `index`
(`robot.move_to(q, robot=info.index)`); use `id` to recognise the same scene
robot across the app and your script.

Camera parameters you leave unset (`fps`, `width`, `height`, `quality`, `codec`)
inherit that camera's setting in the app, so your script overrides the GUI only
where it says so.

Runnable versions of all of this are in [`examples/`](examples/).

## Command line

```sh
sim2bot bridge        # run the local bridge explicitly
sim2bot doctor        # check bridge, browser, TCP/UDP, video, OpenCV
sim2bot list-robots   # wait for the simulator and print index / id / name
sim2bot open          # open the app (override with SIM2BOT_APP_URL)
```

## Transport

WebSocket by default. For a lower-latency control path with no TCP
head-of-line blocking:

```python
Robot(transport="udp")                      # control + telemetry over UDP (8771)
```

Camera frames always use the binary video WebSocket, because one frame is far
larger than a datagram. On localhost the two transports perform about the same;
UDP's advantage shows over a real network.

## More than one simulator, and other machines

Use a room to pair one script with one browser tab:

```python
Robot(room="bench-a")                       # or SIM2BOT_BRIDGE_ROOM=bench-a
```

Without a room, commands go to the most recently connected tab.

Scripts on the same machine need no credentials. To drive the bridge from
another machine on the LAN, bind it to a LAN interface **with a token** — the
bridge refuses non-loopback clients otherwise:

```sh
SIM2BOT_BRIDGE_TOKEN=choose-a-secret sim2bot bridge --host 0.0.0.0
```

```python
Robot(url="ws://192.168.1.42:8765/ws", api_key="choose-a-secret")
```

## Protocol

The wire format is documented in [`docs/protocol.md`](docs/protocol.md), and
[`fixtures/v1/`](fixtures/v1/) holds one example message per type. Those
fixtures are the contract: the tests here check this SDK against them, and the
Sim2Bot app checks its own implementation against the same files. If you are
writing a client in another language, implement against the fixtures.

## Versions

The package version is the version to quote in a bug report. Each release says
which Sim2Bot app versions it is verified against in
[`CHANGELOG.md`](CHANGELOG.md).

## Contributing

Issues and pull requests are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).
This repository holds the Python client, the relay and the protocol fixtures.
The Sim2Bot application itself is closed source, so bugs in the simulator, the
GUI or rendering are reported here but fixed there.

## Licence

MIT — see [`LICENSE`](LICENSE).
