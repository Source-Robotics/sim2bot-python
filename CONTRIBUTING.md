# Contributing

Thanks for taking the time. This repository is small on purpose, and the rules
below are the whole process.

## What lives here

| In this repository | Not here |
| --- | --- |
| the `sim2bot` Python package | the Sim2Bot application (browser, GUI, rendering, physics) |
| the local bridge (`sim2bot/bridge_server.py`) | the cloud services and the website |
| the protocol fixtures in `fixtures/v1/` | anything that needs the app's source |

The application is closed source. That is not a barrier to reporting bugs
against it — open an issue here and it gets routed — but please do not spend
time looking for its code.

## Setting up

```sh
git clone https://github.com/Source-Robotics/sim2bot-python
cd sim2bot-python
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The suite starts a real bridge on test ports (8795–8797) and drives it with real
clients. It needs no browser and no network.

To try a change by hand you do need the app: run your script, then open
[app.sim2bot.com](https://app.sim2bot.com/) and click **Tools → Bridge →
Connect**.

## Protocol changes

Every message type has a fixture in `fixtures/v1/`, and `tests/test_fixtures.py`
checks the SDK against them. A change to the wire format therefore means:

1. update or add the fixture;
2. update the SDK and `docs/protocol.md` in the same pull request;
3. say so in the description — the Sim2Bot app has to ship a matching change
   before the new message does anything, so these land together with a release.

Adding an **optional** field is the easy case. Renaming or removing one breaks
every installed client and needs a version bump and a note in `CHANGELOG.md`.

## Pull requests

- One change per pull request, with a test that fails without it.
- Keep the docstrings useful: parameters with units, what is returned, what is
  raised, and anything with a safety consequence.
- Follow the style already in the file rather than reformatting around your
  change.
- The CI run (Python 3.9 through 3.13) must be green.

## Sign your work — DCO

This project uses the [Developer Certificate of Origin](https://developercertificate.org/).
It is a statement that you wrote the patch, or otherwise have the right to
submit it under the MIT licence. There is no CLA.

Add a `Signed-off-by` line to each commit, which git writes for you:

```sh
git commit -s -m "Fix gripper fraction clamping"
```

The line must carry your real name and an email you can be reached at:

```text
Signed-off-by: Jane Developer <jane@example.com>
```

## Reporting a bug

Use the issue templates. The two things that make a report actionable are the
**Sim2Bot app version** (bottom of the app's Help menu) and the **bridge log**,
because most reports turn out to be about the app or the relay rather than the
Python code.

Security problems go to <info@source-robotics.com>, not to a public issue. See
[`SECURITY.md`](SECURITY.md).

## Code of conduct

Be decent to each other; see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
