# roombapy-prime

[![CI](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml/badge.svg)](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml)

An independent, async Python client library for iRobot's cloud-connected
**"Prime"/V4-generation** robots — the successor line to the Classic
protocol devices supported by [roombapy](https://github.com/pschmitt/roombapy).

> **Status: v0.1.11-alpha.** Mission control, login, MQTT, and most REST
> reads are confirmed working against two independent real accounts.
> Map editing is unverified against a live device. See
> [Confidence & known gaps](#confidence--known-gaps) for the full,
> honest breakdown before relying on any of it.

## Contents

- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Testing](#testing)
- [Contributing / running diagnostics](#contributing--running-diagnostics)
- [Confidence & known gaps](#confidence--known-gaps)
- [Data privacy & security](#data-privacy--security)
- [Why not just extend roombapy?](#why-not-just-extend-roombapy)
- [Documentation](#documentation)
- [Credits](#credits)
- [License](#license)

## Features

- **Login & session** — account login (Gigya + AWS Custom Authorizer), automatic MQTT token refresh
- **Live state** — current robot status, one-shot (`get_state()`) or continuous (`watch_state()`); battery percentage and charging/dock state are confirmed live via the named shadow `ro-currentstate` (`CurrentStateShadow`) — a separate, older `RobotStatusV2` parser also exists but is unconfirmed to appear anywhere (see the confidence table)
- **Mission control** — start/stop/pause/resume/dock via `send_simple_command()`, confirmed working live against a real robot; the richer, region-aware `send_mission_command()` remains available but is now believed incorrect for basic use
- **Favorites** — list, create, update, delete, reorder saved cleaning routines
- **Maps** — read map metadata and active versions, edit rooms/zones/furniture/virtual walls, watch the live map while cleaning, download+unpack the full map bundle
- **Schedules** — recurring cleaning schedules per household (list, create, update, delete)
- **Mission history** — past cleaning runs with duration, coverage, and end reason
- **Parts & device info** — consumable part status, reset after replacement, serial number data, time estimates, notification feed. Find-my-robot: **confirmed working** via `send_simple_command("find")` (jayjay) — a genuine, audible chime with no robot movement; two other mechanisms (a REST endpoint, a shadow write) were tried first and confirmed **not working** — see the docstrings on `poll_echo_value()`/`trigger_echo_via_shadow()`/`send_simple_command()`
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

# Sends a real command to the robot — confirmed working live (see the
# status note above), but it still moves your actual robot.
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

## Testing

```bash
pip install -e ".[test]"
pytest roombapy_prime/tests/
```

498+ tests, all passing — structural checks against decompiled source,
a byte-for-byte regression pin for the SigV4 signer, genuine
multi-threading tests for the connection lock, and more. This validates
internal consistency (the library builds the requests it claims to
build); it does **not** validate that a real server accepts them — only
the diagnostics script below can do that. See
[`docs/internal/DEVELOPMENT_NOTES.md`](docs/internal/DEVELOPMENT_NOTES.md) for the
detailed breakdown (German; all code, comments, and this README are in
English per project convention).

## Contributing / running diagnostics

If you have a Prime/V4 account, the single most useful thing you can do
is run the built-in diagnostics script against it and share the
results — this is the only way any of the "unverified" items below get
resolved:

```bash
roombapy-prime-validate --username you@example.com --country-code US
# or without installing: python -m roombapy_prime.diagnostics --username you@example.com --country-code US
```

Every script in this project also accepts credentials and the target device via environment
variables, so you don't have to retype them for every run:

```bash
export ROOMBAPY_PRIME_USERNAME="you@example.com"
export ROOMBAPY_PRIME_PASSWORD="..."      # skips the interactive password prompt
export ROOMBAPY_PRIME_COUNTRY="US"
export ROOMBAPY_PRIME_BLID="YOUR_ROBOT_BLID"

roombapy-prime-verify-favorite-write --list-favorites   # no --username/--blid needed
```

Any of these can still be overridden per-run with the matching `--username`/`--country-code`/
`--blid` flag — the env var is only used when the flag is omitted. There's no environment
variable for password *replacement* of the interactive prompt beyond `ROOMBAPY_PRIME_PASSWORD` —
consider a local `.env` file (not committed) or your shell's own secret-handling if you're
concerned about it ending up in shell history.

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

### Verifying mission commands (start/stop/pause/dock/find)

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
`send_simple_command()` (see the confidence table below for the transport correction this
implies). Also offers an independent, separately opt-in **`"find"`** test at the end (doesn't
need an active mission) — **confirmed working** (jayjay): produces a genuine, audible chime with
no robot movement, resolving this project's own locate-mechanism search after two other
mechanisms (a REST endpoint, a shadow write) were tried first and confirmed not working. Before
and after every command, it also attempts to parse the `RobotStatusV2` model out of the reported
state and shows the result — useful real-world data for settling whether/where that structure
actually appears. Produces the same kind of shareable report as `roombapy-prime-validate`,
including `--dump-config` support.

### Verifying map edits (rename a room, or its category)

Map editing has categorically weaker evidence than mission commands do — no independent
corroboration of the V1 envelope format exists anywhere, so this script is deliberately much
narrower and more cautious. It only tests one command type (`SetRoomMetadataV1`), on either of
its two fields:

```bash
# Default: rename an existing, already-named room to a clearly-marked test name, then back
roombapy-prime-verify-map-edit --username you@example.com --country-code US \
    --blid YOUR_ROBOT_BLID --i-understand-this-will-edit-my-map

# --test-category: change an existing room's category to a different one, then back
roombapy-prime-verify-map-edit --blid YOUR_ROBOT_BLID \
    --i-understand-this-will-edit-my-map --test-category
```

Same doubly-secured safety design as the mission-command script. Unlike that script, it also asks
you to confirm the change in the real app before treating either step as successful — an accepted
HTTP response only proves the server didn't reject the request, not that anything actually
changed, which matters more here given the lack of outside confirmation for this command family.
Deliberately does **not** attempt splitting/merging rooms, deleting permanent areas, virtual
walls, or furniture — several of those aren't cleanly reversible even in principle. The
deprecated `SetRoomTypeV1`/`RenameRoomV1` pair also isn't tested — the current app doesn't use
either anymore, `SetRoomMetadataV1` replaces both.

### Watching for live mission status

`roombapy-prime-validate` and `get_state()` only ever show a static shadow — a live idle-vs-mid-mission
diff proved that specific comparison (two point-in-time GETs) is byte-identical whether the robot
is cleaning or not. This script watches the separate channel that actually carries live mission
status instead:

```bash
roombapy-prime-verify-mission-timeline --username you@example.com --country-code US \
    --blid YOUR_ROBOT_BLID
```

Purely passive by default — start a cleaning cycle any way you like (the robot's own button, the
app, or `roombapy-prime-verify-commands` in a separate terminal) and this script just watches.
Pass `--start-mission` to have it send start/stop/dock itself instead, in one terminal (same
`--i-understand-this-will-move-my-robot` gate as the other robot-moving scripts). Pass
`--watch-wildcard` to also capture everything else on the device's topic tree at the same time —
this is how live position data and the live map-streaming mechanism were both found.

One more flag, safe by design: `--watch-shadow-delta` runs `watch_state()` (the shadow's
`update/delta` push channel) for the same duration. **Tested live** (chairstacker, a real
mission): no delta messages arrived during that specific run — but the watch window ended
before the robot actually finished docking, likely before the moment a charging-related delta
would plausibly fire. Inconclusive, not a confirmed negative — a longer
`--post-dock-watch-seconds` (a few minutes) is more likely to catch it. Safe regardless of the
result: subscribes to exactly one specific, AWS-documented shadow topic (the same path used as
the example in AWS's own IAM policy documentation for this feature) — not a wildcard on the
reserved `$aws/` namespace.

**A `--watch-aws-tree` flag briefly existed here and has been removed.** It wildcard-subscribed
to the entire `$aws/things/{blid}/#` namespace. AWS IoT's own documentation explicitly warns
against this ("avoid wild card subscriptions to shadow topics... avoid subscribing to topic
filters like `$aws/things/thingName/shadow/#`", and "unsupported publish or subscribe operations
to reserved topics can result in a terminated connection"). A field tester hit exactly this: a
hung run, followed by a separate, later process failing every named-shadow request with
timeouts — consistent with AWS IoT degrading or terminating the connection in response, not just
a local hang. If you're on v0.1.11a8 and used this flag, update to v0.1.11a9 or later.

Same `--dump-config`/shareable-report support as the other scripts.

### Checking named shadows (battery/charging status — resolved)

Battery percentage and charging/docked state are now confirmed, live, with real values: the
named shadow `ro-currentstate` reports `batPct` (an int, 0-100), and charging state lives in
`clean_mission_status.phase` (e.g. `"charge"`). See `CurrentStateShadow` in
`models/robot_info.py` for the full, real-data-confirmed structure. This script checks all nine
known shadows (five `rw-`/classic, four `ro-`) in one pass:

```bash
roombapy-prime-verify-named-shadows --username you@example.com --country-code US \
    --blid YOUR_ROBOT_BLID --delay-seconds 2
```

**`--delay-seconds` is recommended, not just optional.** A real field report showed shadow
queries can fail in a striking pattern after a connection drop (some succeed, every one after
that fails) — `get_shadow()` itself now reconnects proactively when needed (fixed in
v0.1.11a14), which resolved most of this on its own, but a small delay between queries closed
the remaining gap in the same real test (9/11 without delay, 11/11 with `--delay-seconds 2`).

None of the nine shadows' content is guessed at — `rw-constatus` is MQTT/AWS-IoT connection
status, `rw-schedule` is the cleaning schedule, `rw-software` is OTA/firmware status, and the
four `ro-` shadows (`ro-currentstate`/`ro-stats`/`ro-services`/`ro-configinfo`) are all modeled
in `models/robot_info.py` from real captured payloads.

Purely read-only — no confirmation gate needed, unlike the scripts above; this one never sends
anything to the robot. These same shadows are now also checked automatically by
`roombapy-prime-validate` itself, so this standalone script is mainly useful if you want to
re-check them on their own (e.g. against a different device/account), or want a shorter run
than the full validation.

### Testing region-aware mission commands (staged, higher risk)

`send_routine_command_via_cmd_topic()` — cleaning specific rooms/zones/favorites — is the single
riskiest, least-confirmed write path this library has: a wrong guess could mean a real device
accepts something malformed and behaves unpredictably, not just silence. This script implements
a deliberately staged approach, each stage only worth attempting after the previous one is
confirmed working against your specific device — see the method's own docstring for the full
reasoning, and this script's own module docstring for the complete staged-risk explanation.

```bash
# Stage 0 -- pure reconnaissance, sends nothing:
roombapy-prime-verify-region-commands --list-favorites --blid YOUR_ROBOT_BLID

# Stage 1 -- resend one specific, unmodified command_def (moves the robot):
roombapy-prime-verify-region-commands --send FAVORITE_ID --command-index 0 \
    --blid YOUR_ROBOT_BLID \
    --i-understand-this-will-move-my-robot \
    --i-understand-this-is-experimental-and-unconfirmed

# Stage 1b -- identical, but adds initiator="localApp" if the stored command_def has none
# (CONFIRMED FINDING: a real stage-1 test's own favorite had no initiator at all, meaning
# the original hypothesis -- "command" AND "initiator" as shared keys -- was only partially
# exercised; this tests the missing half, purely additively):
roombapy-prime-verify-region-commands --send-with-initiator FAVORITE_ID --command-index 0 \
    --blid YOUR_ROBOT_BLID \
    --i-understand-this-will-move-my-robot \
    --i-understand-this-is-experimental-and-unconfirmed

# Stage 2 -- same favorite, one benign field changed (suction level):
roombapy-prime-verify-region-commands --send-modified FAVORITE_ID --suction-level 2 \
    --blid YOUR_ROBOT_BLID \
    --i-understand-this-will-move-my-robot \
    --i-understand-this-is-experimental-and-unconfirmed

# Stage 3 -- from-scratch command for a REAL room (no favorite at all):
roombapy-prime-verify-region-commands --list-rooms --p2map-id YOUR_MAP_ID --blid YOUR_ROBOT_BLID
roombapy-prime-verify-region-commands --send-region --p2map-id YOUR_MAP_ID \
    --room-id REAL_ROOM_ID --region-type rid \
    --blid YOUR_ROBOT_BLID \
    --i-understand-this-will-move-my-robot \
    --i-understand-this-is-experimental-and-unconfirmed

# Stage 4 (HIGHEST RISK) -- hand-built ad-hoc/TID region, needs a THIRD safety flag
# plus a real furniture_id and polygon coordinates YOU supply and have verified:
roombapy-prime-verify-region-commands --send-adhoc --p2map-id YOUR_MAP_ID \
    --furniture-id 42 --polygon-points "1.0,2.0 3.0,2.0 3.0,4.0 1.0,4.0" \
    --blid YOUR_ROBOT_BLID \
    --i-understand-this-will-move-my-robot \
    --i-understand-this-is-experimental-and-unconfirmed \
    --i-acknowledge-this-is-the-highest-risk-tier
```

Every sending stage shares the same two layered safety flags plus an interactive confirmation
showing the exact JSON payload immediately before it's sent. Stage 4 needs a third, its own
flag on top of those. `--list-favorites`/`--list-rooms` flag/list eligible targets before you
pick one — neither sends anything. **Not yet live-tested as of this writing, any stage** — a
reasoned, safety-checked hypothesis, same status as `watch_named_shadows_updates()` above. Stage
4 specifically also depends on two genuinely unconfirmed inputs (a real `furniture_id` and the
polygon's own coordinate format) that this script deliberately does not auto-generate or guess
at — see `send_stage_four()`'s own docstring for why.

### Testing schedule writes (staged, delayed-effect risk)

**Stages 1 and 2 CONFIRMED WORKING LIVE** (chairstacker). Unlike region commands, a bad schedule
write has a DELAYED effect (whenever the schedule next fires, possibly with nobody watching)
rather than an immediate one — this script's own staged approach is built around that
difference: stage 1 resends an existing household's own schedules completely unchanged; stage 2
(the only modification implemented) disables one specific schedule, chosen because it can only
*prevent* future unexpected activity, never cause it.

```bash
# Stage 0 -- pure reconnaissance, sends nothing:
roombapy-prime-verify-schedule-write --list-schedules --blid YOUR_ROBOT_BLID

# Stage 1 -- resend one household's schedules unchanged (CONFIRMED WORKING):
roombapy-prime-verify-schedule-write --update-unchanged HOUSEHOLD_SCHEDULE_ID \
    --blid YOUR_ROBOT_BLID --i-understand-this-changes-a-real-schedule

# Stage 2 -- disable one specific schedule, safest modification (CONFIRMED WORKING):
roombapy-prime-verify-schedule-write --disable HOUSEHOLD_SCHEDULE_ID --schedule-index 0 \
    --blid YOUR_ROBOT_BLID --i-understand-this-changes-a-real-schedule
```

Real-world note (chairstacker): the real app's own Automations screen doesn't always refresh in
real time after a write — later runs needed navigating away and back before the change appeared.
Not a sign the write failed; `--list-schedules` itself reflects the change immediately either
way.


### Testing favorite writes (staged)

**Stages 1 and 2 CONFIRMED WORKING LIVE** (chairstacker) — the first live confirmation across
any of this project's four new staged write-test scripts. `get_favorites()` already returns
fully-typed `FavoriteV1` objects (including properly reconstructed `RoutineCommand` entries), so
stage 1 needs no new parsing code.

```bash
# Stage 0 -- pure reconnaissance, sends nothing:
roombapy-prime-verify-favorite-write --list-favorites --blid YOUR_ROBOT_BLID

# Stage 1 -- resend one favorite's own data unchanged (CONFIRMED WORKING):
roombapy-prime-verify-favorite-write --update-unchanged FAVORITE_ID \
    --blid YOUR_ROBOT_BLID --i-understand-this-changes-a-real-favorite

# Stage 2 -- change only its color, purely cosmetic (CONFIRMED WORKING):
roombapy-prime-verify-favorite-write --update-color FAVORITE_ID --color "#FF0000" \
    --blid YOUR_ROBOT_BLID --i-understand-this-changes-a-real-favorite

# Stage 3 -- create a minimal test favorite, confirm it, then delete it again:
roombapy-prime-verify-favorite-write --create-and-delete-test \
    --blid YOUR_ROBOT_BLID --i-understand-this-changes-a-real-favorite

# Standalone cleanup -- delete by ID directly, no app visibility required:
roombapy-prime-verify-favorite-write --delete FAVORITE_ID \
    --blid YOUR_ROBOT_BLID --i-understand-this-changes-a-real-favorite
```

**Stage 3 has a confirmed caveat** (chairstacker): a favorite created with empty `command_defs`
is real and listable via `get_favorites()`, but was **not visible in the real app's own UI** —
meaning stage 3's own in-app confirmation can't be answered as written. Use `--delete
FAVORITE_ID` to clean it up instead of waiting on app visibility that won't come.

### Testing virtual walls / keep-out / no-mop zones (staged)

`SetVirtualWallsV1` ("set_virtual_wall") — never tested live before this script existed. A field
report initially suggested this command might work by add/delta semantics; direct confirmation
from the real app's own `deleteVirtualWall()` implementation settled this: it's REPLACE
semantics (read the current full list, resend the whole thing) — the exact same "resend
unchanged" stage-1 philosophy already used everywhere else in this project applies here too.

```bash
# Stage 0 -- pure reconnaissance, sends nothing:
roombapy-prime-verify-virtual-wall-write --list-walls \
    --blid YOUR_ROBOT_BLID --p2map-id YOUR_MAP_ID --p2mapv-id YOUR_MAP_VERSION_ID

# Stage 1 -- resend the current, complete list unchanged:
roombapy-prime-verify-virtual-wall-write --update-unchanged \
    --blid YOUR_ROBOT_BLID --p2map-id YOUR_MAP_ID --p2mapv-id YOUR_MAP_VERSION_ID \
    --i-understand-this-changes-real-map-zones
```

**Not yet live-tested as of this writing** — a reasoned, safety-checked hypothesis. Uses
`models/map_editing.py`'s `policy_zones_to_virtual_walls()`, which implements the complete,
confirmed categorization rule: there's no separate "VirtualWall" type string at all — a virtual
wall is a `"KeepOutZone"`-typed feature whose geometry happens to be a `LineString` instead of a
`Polygon`.

## Confidence & known gaps

**TL;DR:** reading data (state, favorites, mission history, maps) rests
on a solid, source-confirmed wire format. *Sending* something to the
robot — mission commands, map edits, anything that changes state — has
the right shape on paper but has never been sent to a real server.
Treat those as "should work" rather than "does work" until someone
confirms it. Mission control is the one confirmed exception — see
below.

| Area | Confidence | Why |
|---|---|---|
| Login flow | High | Live-tested against real Classic-protocol accounts; Prime shares the same native auth core per binary analysis, and now live-tested against a real Prime account too |
| MQTT/shadow connection | High | Live-tested against a real Prime account (and previously against Classic devices) |
| Reading state/favorites/mission history | High (format), partially live-tested | Field names and types confirmed directly from decompiled source/bytecode; several read endpoints (state, favorites, mission history, active map versions, household listing) confirmed live against a real account |
| AWS SigV4 signing | High (algorithm), unverified (applied to this API) | Byte-identical to a separate, production-tested implementation |
| Sending mission commands (`send_simple_command()`) | **High — confirmed live** | Live-tested against a real robot: `start`/`stop`/`pause`/`resume`/`dock` all confirmed by a real user watching the robot actually react, not just an error-free response. The old device-shadow approach (`send_mission_command()`) was separately confirmed **not working** for this — every attempt timed out with zero response. |
| Sending mission commands, region-based (`send_mission_command()`, `send_routine_command_via_cmd_topic()`) | Low | `send_mission_command()` (shadow-based) confirmed **not working** for basic commands, unconfirmed either way for regions. `send_routine_command_via_cmd_topic()` is reasoned-but-unconfirmed — a `favorite_id`-only command is NOT the safer option (an earlier recommendation here was reversed: the real app always sends `favorite_id` plus the favorite's own full resolved regions together, never one alone). `roombapy-prime-verify-region-commands` now implements a staged, safety-gated test package for this (4 stages, increasing risk) — see "Testing region-aware mission commands" above. Not yet live-tested at any stage. |
| Schedules/DND writes (`create_schedules()`, `update_schedules()`, DND models) | High for `update_schedules()` (CONFIRMED LIVE), medium-high for `create_schedules()`/DND (fields, unverified in practice) | Wire keys directly confirmed via bytecode (same technique as `RobotStatusV2`) — several were wrong camelCase guesses, now corrected to the real snake_case keys. A real bug in the request envelope (`commands`/`end_commands` entries need a `{"command": ...}` wrapper) was found and fixed by reading a real `get_schedules()` response. `update_schedules()` **CONFIRMED WORKING LIVE** (chairstacker) — both an unchanged resend and a real `enabled=False` toggle genuinely took effect. `create_schedules()`/DND writes remain unconfirmed against a real server. `roombapy-prime-verify-schedule-write` implements the staged, safety-gated test package that confirmed this — see "Testing schedule writes" above. |
| `RobotStatusV2` (structured battery/charging/dock status) | **RESOLVED — see `CurrentStateShadow`** | The original 11-field `RobotStatusV2` model itself remains unconfirmed as ever appearing anywhere (not in `get_state()`, not on any MQTT topic, not in any `rw-` named shadow) — but the underlying search is fully resolved: the named shadow `ro-currentstate` reports real, live-confirmed values (`batPct`: an int 0-100; charging state in `clean_mission_status.phase`, e.g. `"charge"`; `dock`/`bin`/`runtime_stats` as nested objects, not flat values). `CurrentStateShadow` (`models/robot_info.py`) models the full real structure, not placeholders — see `BinStatus`/`CleanMissionStatus`/`DockStatus`/`RuntimeStatsSummary`/`P2MapRef`. |
| Map editing | **High (envelope + 8/9 commands' fields), unverified (practice)** | The request envelope (`{"edit_cmd": ..., "response_type": ...}`) and 8 of 9 commands' field names are now bytecode-confirmed (several were wrong camelCase guesses, now corrected). `SetRoomMetadata`/`VirtualWall`'s internal discriminator use hand-written custom serializers and remain unconfirmed. Never sent to a real server -- a verification script exists (`roombapy-prime-verify-map-edit`), deliberately narrow in scope (room rename only), but hasn't been run against a real device yet |
| Deeply nested response fields (map bundle internals) | **High (fields), mostly resolved (envelope details)** | All 12 map-bundle content types (rooms, borders, hazards, trajectories, etc.) now have confirmed wire formats via bytecode (`RoomFeature` and 10 others) — each is a standard GeoJSON Feature with nested `properties`. The bundle's own manifest filename is now confirmed (it's literally `"manifest"`), and a real bundle confirms most content types use a `{type, features}` wrapper while at least one (`BorderFeature`) is a bare single Feature instead. Mission history's 20 timeline sub-event types are also fully typed (`MissionTimelineEvent`) — 10 of the 20 now confirmed against real data. |

**Known unresolved gaps:**
- The discriminator value inside a map-edit command's `"edit_cmd"` envelope (the envelope shape itself and 8/9 commands' own field names are now bytecode-confirmed — only which `"type"` string selects each command, and `SetRoomMetadata`/`VirtualWall`'s custom-serializer internals, remain unconfirmed)
- Multi-robot household / teaming concepts, beyond basic settings scoping

Full details, including what was tried and why some things remain
unconfirmed, are in
[`docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md).

## Data privacy & security

**In one sentence:** everything goes directly to iRobot's own cloud
infrastructure, nothing is sent to any third party, and nothing is
written to disk by this library unless you explicitly ask for it.

- [`docs/DATA_PRIVACY.md`](docs/DATA_PRIVACY.md) — what data goes
  where, and what this library does and doesn't store, verified
  directly against the code
- [`SECURITY.md`](SECURITY.md) — credential handling, TLS
  verification, and what's still unverified from a security standpoint

## Why not just extend roombapy?

Classic-protocol robots talk local MQTT with `ssl.CERT_NONE` and a
blid/password pair — no account, no internet round-trip. Prime/V4 robots
are cloud-only: AWS IoT Custom Authorizer sessions, request/response
"shadow" state instead of a local firehose, and a REST API for map
management that Classic doesn't have at all. Different trust model,
different protocol shape, not just a missing feature — see
[`docs/internal/ROOMBAPY_COMPARISON.md`](docs/internal/ROOMBAPY_COMPARISON.md) for the
full comparison (including a size/structure breakdown of both libraries).

## Documentation

**Start here:** [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) (every
method and model) and [`CHANGELOG.md`](CHANGELOG.md) (what's changed,
release by release).

Everything else — the session-by-session reverse-engineering trail
(`docs/internal/`) and a handful of superseded early drafts
(`docs/archive/`) — is background material, not needed to use the
library. See the comment at the top of each folder's files for what's
there and why.

## Credits

- **[roombapy](https://github.com/pschmitt/roombapy)** (pschmitt and
  contributors) — the Classic-protocol client this project doesn't
  extend (see [above](#why-not-just-extend-roombapy)), but whose
  design this project learned from throughout: `prime_robot.py`
  mirrors its public-class pattern, `prime_factory.py` mirrors its
  factory pattern, and the TLS-verification discussion in
  [`SECURITY.md`](SECURITY.md) directly contrasts with its
  local-network `ssl.CERT_NONE` approach (correct for its use case,
  not for this one).
- **[Ader](https://github.com/lvigilantecorreo-commits)** —
  maintainer of
  **[roomba-v4](https://github.com/lvigilantecorreo-commits/roomba-v4)**,
  the first public reverse-engineering work on the V4/Prime command
  path, and the project that triggered this library's development in
  the first place. Since then, an ongoing two-way exchange of
  cross-verification findings between the two independent projects —
  including confirming that room/zone-targeting is real, found
  directly in the app's own binary under the internal name `p2maps`,
  now the central concept this entire library is organized around.
- **chairstacker** — this project's primary field tester. Confirmed
  mission control working live against a real robot (the single
  biggest open question this library had for most of its life), and
  a detailed `--dump-config` capture from a real account surfaced
  three genuine crash bugs and a write-side bug that static analysis
  alone had missed. Most of what this library can say "confirmed
  live" about, it can say because of this testing.
- **jadestar1864** — a second, independent Prime account (same
  robot model as chairstacker's, different household) — the first
  confirmation that this library's behavior is consistent across
  more than one real account, not just one lucky match.

## License

MIT — see [`LICENSE`](LICENSE).
