# roombapy-prime

[![CI](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml/badge.svg)](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml)

An independent, async Python client library for iRobot's cloud-connected
**"Prime"/V4-generation** robots — the successor line to the Classic
protocol devices supported by [roombapy](https://github.com/pschmitt/roombapy).

> **Status: draft, Pre-Alpha.** Runs, is tested (200+ unit tests), builds
> and installs cleanly — but has **never been run against a real
> Prime/V4 account or device.** Everything here comes from APK
> decompilation and native library analysis, not live testing. See
> [Confidence & known gaps](#confidence--known-gaps) before relying on
> any of it, especially anything that sends a command to your robot.

## Features

- **Login & session** — account login (Gigya + AWS Custom Authorizer), automatic MQTT token refresh
- **Live state** — current robot status, one-shot (`get_state()`) or continuous (`watch_state()`)
- **Mission control** — start/stop/pause/resume/dock and the rest of the 30-command vocabulary (`send_mission_command()`)
- **Favorites** — list, create, update, delete, reorder saved cleaning routines
- **Maps** — read map metadata and active versions, edit rooms/zones/furniture/virtual walls, watch the live map while cleaning, download+unpack the full map bundle
- **Schedules** — recurring cleaning schedules per household (list, create, update, delete)
- **Mission history** — past cleaning runs with duration, coverage, and end reason
- **Parts & device info** — consumable part status, reset after replacement, serial number data, find-my-robot echo, time estimates, notification feed
- **Settings** — Do Not Disturb windows, cleaning profiles, per-map default routine suggestions
- **Diagnostics** — a built-in script to validate all of the above against a real account and report back what works (see [Contributing](#contributing--running-diagnostics))

## Installation

Not yet published to PyPI — install from source:

```bash
git clone https://github.com/johnnyh1975/roombapy-prime.git
cd roombapy-prime
pip install -e .
```

Requires Python 3.11+. Dependencies: `aiohttp`, `paho-mqtt`, `certifi`.

## Quick start

```python
import asyncio
import aiohttp
from roombapy_prime.prime_factory import PrimeFactory

async def main():
    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(
            session=session,
            username="you@example.com",
            password="hunter2",
            country_code="US",
            # blid="BLID123",  # optional — first robot on the account is used otherwise
        )
        await robot.connect()

        state = await robot.get_state()
        print(state.payload)

        async for delta in robot.watch_state():  # runs until cancelled
            print(delta.payload)

asyncio.run(main())
```

A few other things you can do with the same `robot` object, once connected:

```python
favorites = await robot.get_favorites()
history = await robot.get_mission_history(robot.blid, max_reports=10)
maps = await robot.get_active_map_versions()

# Sends a real command to the robot — see the status warning above
# and the confidence table below before trying this against a real device.
from roombapy_prime.models import RoutineCommand, MissionCommandType
await robot.send_mission_command(
    RoutineCommand(command_type=MissionCommandType.CLEAN, asset_id=robot.blid)
)
```

There's more — schedules, DND settings, map editing, live map streaming.
See [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) for every method
and model organized by feature area, with confidence markers per item —
or the module docstrings in `roombapy_prime/` directly for the full
evidence behind each one.

Runnable versions of the above (plus mission control and a favorites/
mission-history example) are in [`examples/`](examples/) — each reads
credentials from environment variables, none hardcode a password.

## Confidence & known gaps

**TL;DR:** reading data (state, favorites, mission history, maps) rests
on a solid, source-confirmed wire format. *Sending* something to the
robot — mission commands, map edits, anything that changes state — has
the right shape on paper but has never been sent to a real server.
Treat those as "should work" rather than "does work" until someone
confirms it.

| Area | Confidence | Why |
|---|---|---|
| Login flow | High | Live-tested against real Classic-protocol accounts; Prime shares the same native auth core per binary analysis |
| MQTT/shadow connection | High | Live-tested (Classic devices via cloud shadow) |
| Reading state/favorites/mission history | High (format), unverified (live) | Field names and types confirmed directly from decompiled source/bytecode, not guessed |
| AWS SigV4 signing | High (algorithm), unverified (applied to this API) | Byte-identical to a separate, production-tested implementation |
| Sending mission commands | High | Transport confirmed from the actual APK-bundled configuration file (`res/raw/base_roomba_config.json` — see `docs/base_roomba_config_REFERENCE.json`), not just decompiled logic: the `"Control"`/`"AssetControlCommand"` entry has `"namedShadow": ""` (classic shadow), distinct from settings (`"rw-settings"`) and schedules (`"rw-schedule"`) in the same file. Payload shape is source-confirmed separately. Still never sent to a live server. |
| Map editing | Medium (structure), unverified (practice) | Confirmed from source, never sent to a real server |
| Deeply nested response fields (map bundle internals) | Low-medium | Modeled where it was cheap to; raw JSON is always available as a fallback where it wasn't. Mission history's 20 timeline sub-event types are now fully typed (`MissionTimelineEvent`), no longer in this category. |

**Known unresolved gaps:**
- Whether the exact JSON envelope for map-edit commands matches the server's expectations (the field names are confirmed; the wrapping shape around them is inferred by analogy)
- Multi-robot household / teaming concepts, beyond basic settings scoping
- Exact file naming inside downloaded map bundles

Full details, including what was tried and why some things remain
unconfirmed, are in
[`docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md).

## Why not just extend roombapy?

Classic-protocol robots talk local MQTT with `ssl.CERT_NONE` and a
blid/password pair — no account, no internet round-trip. Prime/V4 robots
are cloud-only: AWS IoT Custom Authorizer sessions, request/response
"shadow" state instead of a local firehose, and a REST API for map
management that Classic doesn't have at all. Different trust model,
different protocol shape, not just a missing feature — see
[`docs/ROOMBAPY_COMPARISON.md`](docs/ROOMBAPY_COMPARISON.md) for the
full comparison (including a size/structure breakdown of both libraries).

## Testing

```bash
pip install -e ".[test]"
pytest roombapy_prime/tests/
```

200+ tests, all passing — structural checks against decompiled source,
a byte-for-byte regression pin for the SigV4 signer, genuine
multi-threading tests for the connection lock, and more. This validates
internal consistency (the library builds the requests it claims to
build); it does **not** validate that a real server accepts them — only
the diagnostics script below can do that. See
[`docs/DEVELOPMENT_NOTES.md`](docs/DEVELOPMENT_NOTES.md) for the
detailed breakdown (German; all code, comments, and this README are in
English per project convention).

## Contributing / running diagnostics

If you have a Prime/V4 account, the single most useful thing you can do
is run the built-in diagnostics script against it and share the
results — this is the only way any of the "unverified" items above get
resolved:

```bash
roombapy-prime-validate --username you@example.com --country-code US
# or without installing: python -m roombapy_prime.diagnostics --username you@example.com --country-code US
```

Read-only by default (login, REST reads, shadow state, map bundle
download) — nothing here can change anything on your account or robot.
Pass `--allow-writes` to additionally run a self-cleaning favorite
create/verify/delete round trip. Mission commands and map edits are
never run automatically, with or without that flag — see the module
docstring in `roombapy_prime/diagnostics.py` for why.

At the end of every run, the script prints a pre-filled GitHub
"new issue" link with the full report as the body — one click to share
what worked and what didn't. Credentials are redacted from the report
before that link is built, as a defense-in-depth measure. Pass
`--no-issue-link` to skip this, or `--open-browser` to have it open
automatically.

## Documentation

- [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) — every `PrimeRobot` method and the key models, organized by feature area, with per-item confidence markers
- [`CHANGELOG.md`](CHANGELOG.md) — what's implemented, from a user's point of view
- [`SECURITY.md`](SECURITY.md) — credential handling, TLS, and what's still unverified
- [`docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md) — detailed, running audit of what's implemented vs. what's missing and why
- [`docs/ROOMBAPY_COMPARISON.md`](docs/ROOMBAPY_COMPARISON.md) — why this isn't built on `roombapy`
- [`docs/HA_ROOMBA_PLUS_CROSSREF.md`](docs/HA_ROOMBA_PLUS_CROSSREF.md) — cross-reference against a production Classic-protocol integration
- [`docs/FINDINGS_2026-07-11.md`](docs/FINDINGS_2026-07-11.md) — raw findings from APK decompilation
- [`docs/DEVELOPMENT_NOTES.md`](docs/DEVELOPMENT_NOTES.md) — detailed maintainer notes (German)

## License

MIT — see [`LICENSE`](LICENSE).
