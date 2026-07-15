# roombapy-prime

[![CI](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml/badge.svg)](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml)

An independent, async Python client library for iRobot's cloud-connected
**"Prime"/V4-generation** robots — the successor line to the Classic
protocol devices supported by [roombapy](https://github.com/pschmitt/roombapy).

> **Status: v0.1.7-alpha.** Runs, is tested (286+ unit tests), builds
> and installs cleanly. **Run twice against one real Prime/V4 account.**
> First run: login, MQTT connection, and most REST reads confirmed
> working; a reversible write (creating/deleting a favorite) also
> confirmed working. Second run additionally tried mission commands for
> the first time — and found the original approach (`send_mission_command()`,
> via the device shadow) doesn't work at all: every attempt timed out
> with zero response. The actual transport turned out to be a
> completely different MQTT topic, now implemented as
> `send_simple_command()` — corroborated both by this library's own
> native disassembly and independently by a third-party project that
> reports it working, but **not yet live-tested by this library
> itself**. Map editing is also still unverified against a live device,
> and only one robot model has been tested so far. See
> [Confidence & known gaps](#confidence--known-gaps) before relying on
> any of it, especially anything that sends a command to your robot.

## Features

- **Login & session** — account login (Gigya + AWS Custom Authorizer), automatic MQTT token refresh
- **Live state** — current robot status, one-shot (`get_state()`) or continuous (`watch_state()`); an optional, bytecode-confirmed `RobotStatusV2` parser for structured battery/charging/dock fields, though it's unconfirmed whether this structure actually appears in `get_state()`'s response yet (see the confidence table)
- **Mission control** — start/stop/pause/resume/dock via `send_simple_command()`, the corrected transport (see the status note above); the richer, region-aware `send_mission_command()` remains available but is now believed incorrect for basic use
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
await robot.send_simple_command("start")  # or "stop"/"pause"/"resume"/"dock"
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
| Login flow | High | Live-tested against real Classic-protocol accounts; Prime shares the same native auth core per binary analysis, and now live-tested against a real Prime account too |
| MQTT/shadow connection | High | Live-tested against a real Prime account (and previously against Classic devices) |
| Reading state/favorites/mission history | High (format), partially live-tested | Field names and types confirmed directly from decompiled source/bytecode; several read endpoints (state, favorites, mission history, active map versions, household listing) confirmed live against a real account |
| AWS SigV4 signing | High (algorithm), unverified (applied to this API) | Byte-identical to a separate, production-tested implementation |
| Sending mission commands (`send_simple_command()`) | Medium-high (transport), unverified (live) | The device-shadow approach (`send_mission_command()`) was live-tested and confirmed **not working** — every attempt timed out with zero response. The actual transport (a dedicated, non-shadow MQTT topic) is corroborated two independent ways: this library's own native disassembly of the APK, and separately a third-party, unaffiliated project reporting this exact path working against a real device. Strong circumstantial evidence, but not yet confirmed by this library's own live test. |
| Sending mission commands, region-based (`send_mission_command()`) | Low | Kept only for this use case; the simple payload has no known way to express regions/zones. Confirmed **not working** for basic commands (see above) — unconfirmed either way for the region-based case, since no source (including the third-party corroboration above) has verified it |
| `RobotStatusV2` (structured battery/charging/dock status) | Medium (fields), unresolved (placement) | The 11 fields are bytecode-confirmed wire keys from the real `@Serializable` class. Whether this structure actually appears in `get_state()`'s response is unresolved — the one real capture available shows unrelated top-level keys entirely |
| Map editing | Medium (structure), unverified (practice) | Confirmed from source, never sent to a real server. No independent corroboration for the envelope format exists anywhere, unlike mission commands -- a verification script exists (`roombapy-prime-verify-map-edit`), deliberately narrow in scope (room rename only), but hasn't been run against a real device yet |
| Deeply nested response fields (map bundle internals) | Low-medium | Modeled where it was cheap to; raw JSON is always available as a fallback where it wasn't. Mission history's 20 timeline sub-event types are now fully typed (`MissionTimelineEvent`), no longer in this category. |

**Known unresolved gaps:**
- Whether the exact JSON envelope for map-edit commands matches the server's expectations (the field names are confirmed; the wrapping shape around them is inferred by analogy)
- Whether `RobotStatusV2` (see table above) actually appears in `get_state()`'s response at all, and if so, where
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

286+ tests, all passing — structural checks against decompiled source,
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

Read-only by default (login, REST reads including parts/serial/
notifications, shadow state, a bounded `watch_state()` sample, live-map
stream request, map bundle download) — nothing here can change
anything on your account or robot. Also reports a best-effort
device/firmware summary and an explicit tier guess (SMART vs.
EPHEMERAL, inferred from whether the named settings shadow responds).
Pass `--allow-writes` to additionally run a self-cleaning favorite
create/verify/delete round trip. Mission commands and map edits are
never run automatically, with or without that flag — see the module
docstring in `roombapy_prime/diagnostics.py` for why.

Pass `--dump-config PATH` to additionally save the actual (lightly
redacted) raw responses from every read endpoint as JSON — similar to
a Home Assistant integration's "Download Diagnostics" feature. Useful
for pinning down exact field names when something doesn't parse
correctly; unlike the summary report, this file is never auto-included
in the issue link, since it contains real values, not just structure —
review it yourself before attaching it anywhere.

At the end of every run, the script prints a pre-filled GitHub
"new issue" link with the full report as the body — one click to share
what worked and what didn't. Credentials are redacted from the report
before that link is built, as a defense-in-depth measure. Pass
`--no-issue-link` to skip this, or `--open-browser` to have it open
automatically.

### Verifying mission commands (start/stop/pause/dock)

This is the one thing `roombapy-prime-validate` deliberately never does automatically — sending
mission commands means your robot actually moves. There's a separate, standalone script for this,
used only if and when you choose to run it, watching your robot the whole time:

```bash
roombapy-prime-verify-commands --username you@example.com --country-code US \
    --blid YOUR_ROBOT_BLID --i-understand-this-will-move-my-robot
```

Both the `--i-understand-...` flag *and* an interactive yes/no prompt before every individual
command are required — declining any prompt skips that step. Runs a conservative start→stop test
by default, with pause/resume and dock offered as separate, individually-opt-in steps, via
`send_simple_command()` (see the confidence table above for the transport correction this
implies). Before and after every command, it also attempts to parse the `RobotStatusV2` model out
of the reported state and shows the result — useful real-world data for settling whether/where
that structure actually appears. Produces the same kind of shareable report as
`roombapy-prime-validate`, including `--dump-config` support.

### Verifying map edits (rename a room)

Map editing has categorically weaker evidence than mission commands do — no independent
corroboration of the V1 envelope format exists anywhere, so this script is deliberately much
narrower and more cautious. It only tests one thing: renaming an existing, already-named room to
a clearly-marked test name, then immediately back.

```bash
roombapy-prime-verify-map-edit --username you@example.com --country-code US \
    --blid YOUR_ROBOT_BLID --i-understand-this-will-edit-my-map
```

Same doubly-secured safety design as the mission-command script. Unlike that script, it also asks
you to confirm the change in the real app before treating either step as successful — an accepted
HTTP response only proves the server didn't reject the request, not that anything actually
changed, which matters more here given the lack of outside confirmation for this command family.
Deliberately does **not** attempt splitting/merging rooms, deleting permanent areas, virtual
walls, or furniture — several of those aren't cleanly reversible even in principle.

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
