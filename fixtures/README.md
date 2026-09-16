# Protocol fixtures

One example message per type, in the wire format described in
[`../docs/protocol.md`](../docs/protocol.md). `v1` is the protocol version, not
the package version.

These files are the contract between three implementations:

| Implementation | Checks itself against these files |
| --- | --- |
| this Python client | `tests/test_fixtures.py` |
| the Sim2Bot browser app | its own suite, against a copy of this directory |
| anything you write | your tests, against the same files |

So a change here is a change to the protocol. Adding an optional field is
backwards compatible; renaming or removing one is not, and needs a version bump
and a `CHANGELOG.md` entry.

Fields that appear in a fixture are required unless `docs/protocol.md` says
otherwise. A message may carry more than the fixture shows — the client always
addresses a robot index, for example, while `stop.json` leaves it out to show
that it is optional.
