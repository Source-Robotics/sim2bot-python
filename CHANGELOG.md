# Changelog

All notable changes to the `sim2bot` package are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/).

Each release states the Sim2Bot application versions it was verified against,
because the protocol is what the two share.

## [Unreleased]

## [0.1.0] — 2026-09-16

First public release: the client, the local bridge and the protocol fixtures,
extracted from the Sim2Bot monorepo.

### Added

- `Robot` client: joint position and velocity targets, Cartesian targets via the
  browser's IK, timed joint trajectories, gripper, mobile and aerial base
  velocity, reset and stop.
- Telemetry with joint position, velocity, acceleration and jerk, TCP pose,
  twist, acceleration and jerk, gripper state and room devices, read
  latest-value.
- Camera subscriptions with JPEG and raw codecs, decoded to numpy images with
  the `[cv2]` extra.
- Scene discovery (`describe`, `cameras`, `room_devices`) with stable robot IDs.
- Debug markers: spheres, boxes, arrows, lines, text, axes and point clouds.
- Rooms, so one script pairs with one browser tab.
- WebSocket, raw TCP and UDP control transports.
- The local bridge, runnable as `sim2bot bridge` or started automatically with
  `Robot(auto_bridge=True)`.
- CLI: `bridge`, `doctor`, `list-robots`, `open`.
- `fixtures/v1/`, the published wire-format contract.

### Changed

- `sim2bot open` now opens <https://app.sim2bot.com> by default. Set
  `SIM2BOT_APP_URL` to point it elsewhere, such as a development server.

### Verified against

**Protocol v1.** Sim2Bot is a hosted browser application, so everyone runs the
current build and there is no application version to pin. What is pinned is the
protocol, and `fixtures/v1/` is checked from both sides:

- this package's `tests/test_fixtures.py` asserts the client puts exactly those
  messages on the wire and reads that telemetry back;
- the application's own suite asserts that the validator its socket handler
  gates on accepts every one of them.

Both sides were run against these fixtures on 16 September 2026.

If the wire format ever changes incompatibly, this package gets a matching
release and the fixtures gain a `v2` directory. `v1` stays where it is.

[Unreleased]: https://github.com/Source-Robotics/sim2bot-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Source-Robotics/sim2bot-python/releases/tag/v0.1.0
