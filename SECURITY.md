# Security policy

## Reporting a vulnerability

Email <info@source-robotics.com> with the details and, if you can, a way to
reproduce it. Please do not open a public issue for a security problem.

You can expect an acknowledgement within a few working days, and an assessment
with a fix or a plan after that. Tell us if you intend to publish, so the fix
and the disclosure can line up.

## What is in scope

- the `sim2bot` Python package;
- the local bridge in `sim2bot/bridge_server.py`.

Problems in the Sim2Bot application or its cloud services are handled at the
same address, so report them there too.

## What the bridge assumes

The bridge is a relay for the machine it runs on:

- bound to `127.0.0.1` by default, where any local process may use it, exactly
  like a local development server;
- a non-loopback bind requires `SIM2BOT_BRIDGE_TOKEN`, and clients that do not
  present it are rejected;
- it holds no robot state and stores nothing on disk.

A report that a LAN-bound bridge with a token you published can be used by
others, or that one local process can reach another local process's bridge, is
the documented behaviour rather than a vulnerability.
