# roombapy-prime

[![CI](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml/badge.svg)](https://github.com/johnnyh1975/roombapy-prime/actions/workflows/ci.yml)

An independent, async Python client library for iRobot's cloud-connected
**"Prime"/V4-generation** robots — the successor line to the Classic
protocol devices supported by [roombapy](https://github.com/pschmitt/roombapy).

> **Status: v0.3.0-beta.** (currently `b6`) Reading and writing both work
> against real hardware, confirmed on three independent accounts:
> login, MQTT, mission control, schedules, map edits, favorites, robot
> settings, and **region-based cleaning** — sending a robot to specific
> rooms, both from a saved favorite and built from scratch.
>
> Virtual wall and keep-out zone writes **work**, on two independent
> accounts, including the write / re-read / write round trip that
> separates "accepted" from "stored". The HTTP 500 this section used to
> describe was solved: `virwall` starts with a COUNT of the walls.
>
> One thing has still never been tried: a write carrying a **modified**
> list. Every confirmed write resent zones unchanged. See
> [Confidence & known gaps](#confidence--known-gaps).
>
> The diagnostic scripts live in a **separate distribution**
> ([`tools/`](tools/README.md)) so that installing this library never puts
> robot-moving commands on your PATH.

## Contents

- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Testing](#testing)
- [Contributing](#contributing)
- [Confidence & known gaps](#confidence--known-gaps)
- [What the vendor's own app told us](#what-the-vendors-own-app-told-us)
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
- **Diagnostics** — a companion distribution, [`roombapy-prime-tools`](tools/README.md), validates all of the above against a real account and reports what works. Deliberately separate: several of its commands move a real robot, and they have no business on the PATH of a Home Assistant installation that only consumes this library.

## Installation

Not yet published to PyPI — install from GitHub:

```bash
pip install "roombapy-prime@git+https://github.com/johnnyh1975/roombapy-prime.git@v0.3.0b11"
```

This gives you the **library only** — no console scripts at all. That is
deliberate.

**If you want the diagnostic tools** (to test your own robot, or to help
with the open questions below), install those instead — they pull this
library in as a dependency, so it stays one command:

```bash
pip install "roombapy-prime-tools@git+https://github.com/johnnyh1975/roombapy-prime.git@v0.3.0b11#subdirectory=tools"
```

### Upgrading, if you have the tools

**Upgrade the tools, not the library.** They are two distributions, and
upgrading the library on its own leaves the tools where they were:

```bash
# right -- brings the matching library with it
pip install --upgrade "roombapy-prime-tools@git+https://github.com/johnnyh1975/roombapy-prime.git@v0.3.0b11#subdirectory=tools"

# wrong, if you have the tools -- upgrades half of the pair
pip install --upgrade "roombapy-prime@git+https://github.com/johnnyh1975/roombapy-prime.git@v0.3.0b11"
```

This is not theoretical. @chairstacker upgraded the library to b6, ran
`verify-writes custom_initiator`, and was asked for a cleaning-history
entry — an instruction b6 had removed, because `find` creates no history
entry. He reported the missing entry as a finding. The tool was still b5.

`verify-writes` now says so at startup when the two disagree, and names
the command above. That is a backstop rather than a fix: the reason the
two can drift is that the tools are deliberately a separate
distribution, so several commands that move a real robot stay off the
PATH of a Home Assistant installation that only consumes the library.

See [`tools/README.md`](tools/README.md) for what they do and how to use
them safely.

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

1016+ tests for the library, plus 457 for the command-line tools —
structural checks against decompiled source,
a byte-for-byte regression pin for the SigV4 signer, genuine
multi-threading tests for the connection lock, and more. This validates
internal consistency (the library builds the requests it claims to
build); it does **not** validate that a real server accepts them — only
the diagnostics script below can do that. See
[`docs/internal/DEVELOPMENT_NOTES.md`](docs/internal/DEVELOPMENT_NOTES.md) for the
detailed breakdown (German; all code, comments, and this README are in
English per project convention).

## Contributing

If you own a Prime/V4 robot, running the diagnostics against your own
account is by far the most useful thing you can do. Every "confirmed"
entry above exists because somebody did exactly that.

Three findings that shaped this library came from testers pasting their
**full** terminal output rather than summarising it as "didn't work":
the live map turning out to be zlib-compressed (visible in the first two
bytes of a diagnostic line), `initiator` being mandatory for region
commands, and a robot's own capability list revealing five fields this
library was silently discarding. None of those would have surfaced from
a description of the symptom.

The most useful things right now:

- **Virtual wall writes with a CHANGED list** — never attempted. Writes
  themselves are confirmed on two accounts (@chairstacker resent four
  zones of two types; @jayjay13011 wrote, re-read the new map version
  and wrote again), but every one of them resent the existing zones
  unchanged. Adding, moving or removing a zone is untested, and
  `set_virtual_wall` **replaces the whole shared list** — a partial list
  deletes everything omitted.
- **Robot settings other than child lock** — they write and read back
  cleanly; whether they change anything is untested.
- **Anything at all on hardware not listed above.** The capability set
  genuinely differs between models, and each new device has so far
  turned up something.

The tools are a **separate distribution** — one command, and it pulls
this library in with it:

```bash
pip install "roombapy-prime-tools@git+https://github.com/johnnyh1975/roombapy-prime.git@v0.3.0b11#subdirectory=tools"
```

Start with `roombapy-prime-validate`: read-only, sends nothing, and its
output alone answers several open questions.

> If `roombapy-prime-validate` is not found, you have the library
> installed but not the tools — the two commands above are different
> packages. `python -m roombapy_prime.diagnostics` runs the same thing
> from the library alone. Full setup, the staged
safety model, and what each script does:
**[`tools/README.md`](tools/README.md)**.

Bug reports and findings are welcome even without a robot — the
[evidence trail](docs/internal/EVIDENCE_TRAIL.md) documents how each
conclusion was reached, including the ones that turned out wrong, and a
second pair of eyes on that reasoning is genuinely useful.

## Confidence & known gaps

The honest version. "Confirmed" below means a real person watched a real
robot and reported back — not that a request returned without an error.

**Summary:** reading works. Writing works, with one exception noted
below. Three independent accounts have exercised this, on a Roomba Plus
505 Combo, a Roomba Combo (G18-series) and a Y41-series machine.

### Confirmed on real hardware

| Area | How it was confirmed |
|---|---|
| Login (Gigya + AWS Custom Authorizer), token refresh | multiple accounts |
| MQTT connection, named-shadow reads | multiple accounts |
| Reading state, favorites, mission history, maps, schedules, parts | multiple accounts |
| Mission control — `start`/`stop`/`pause`/`resume`/`dock` | robot visibly reacted |
| `find` (audible locate, no movement) | robot chimed |
| **Region cleaning from a saved favorite** | robot cleaned the named rooms |
| **Region cleaning built from scratch** | robot travelled to room 12 and cleaned it |
| Schedule writes — unchanged resend and a real disable | change took effect |
| **Map editing (`edit_map`, room rename)** | renamed and reverted, both confirmed in the app |
| **Zone names in the map bundle** | read from a real robot's `cleanZones` layer |
| Map editing — room rename, with revert | twice, name changed in the app |
| Favorite writes — resend, colour change, delete | change visible in the app |
| Robot settings — child lock | appeared in the app, robot announced it audibly |
| Keep-out zone / no-mop zone **reads** | two real zones, both types correctly identified |

### Independently reconstructed

`samm-git/irobot-explore` rebuilt the same protocol from a different app
version (1.6.0) on different hardware, with no knowledge of this
project. Where two independent derivations agree, a thing is confirmed
rather than one derivation deep.

Agreeing without contradiction: service discovery, Gigya login,
`/v2/login`, the four IoT session attributes, the custom-authorizer
flow, shadow topics and envelope, the `cmd` topic with `p2map_id` and
`regions`, `rid`/`zid`/`tid`, settings under `desired`, SigV4 for map
data.

Where we differed, that reconstruction was right twice — see the
0.3.0b11 changelog entry.

### Region cleaning: what it took, and what it needs

This was the project's central unknown for months. Two things were
required, and neither is obvious:

- **`initiator` is mandatory.** A stored favorite does not carry one —
  the app adds it at send time. Resending a favorite unchanged is
  accepted, acknowledged, and silently ignored.
- **The wire keys are `start` and `region_id`**, not `clean` and `id`.
  The latter pair was an assumption recorded in this project's own code
  and never checked.

A map version is **not** required. The robot re-versions its map every
few seconds while cleaning (five values inside 37 seconds in one real
capture), so a stored favorite is stale within a minute of being saved —
and commands carrying versions hours out of date started missions
regardless.

### Known broken

- **A virtual wall write carrying a CHANGED list** — never attempted.
  The HTTP 500 that used to sit here was solved: `virwall` starts with a
  COUNT of the walls. Writes are confirmed on two accounts, including
  the write / re-read / write round trip that separates "accepted" from
  "stored" (#28, closed 30 July).
  But every confirmed write **resent the existing zones unchanged**.
  Adding, moving or removing one is untested, and `set_virtual_wall`
  replaces the whole shared list — a partial list deletes everything
  omitted. That hazard is guarded by a test, not by the server.

- **`schedHold`** writes succeed and read back correctly, and the
  schedule stays active in the app. Writing it to `rw-settings` is
  evidently not the mechanism the app uses.

  Worth knowing how that surfaced: this project's cross-check against the
  classic shadow flagged the divergence *before* the tester looked in the
  app. Two sources disagreeing turned out to mean "the write did not
  take", which makes that check a real signal.

### Untested

- **Robot settings other than child lock** — `ecoCharge`, `noAutoPasses`
  and `vacHigh` all write and read back cleanly; none has an easily
  observable effect, so their real-world behaviour is unknown.
- **Multi-robot household and teaming** concepts beyond basic settings
  scoping.
- The discriminator value inside a map-edit command's `edit_cmd`
  envelope. The envelope shape and 8 of 9 commands' fields are confirmed;
  `SetRoomMetadata` and `VirtualWall` use custom serializers whose
  internals are not.

### A warning if you search for help

Every public example of Roomba region cleaning you will find is for the
**Classic** protocol: `pmap_id`, `user_pmapv_id`, a flat payload, local
MQTT. Prime/V4 uses `p2map_id` and a different command structure
entirely. The names are close enough to look applicable and are not.

The full reasoning behind every entry above — including the conclusions
that turned out wrong and why — is in
[`docs/internal/EVIDENCE_TRAIL.md`](docs/internal/EVIDENCE_TRAIL.md).

## What the vendor's own app told us

`com.irobot.home.prime` 3.0.0 is a Flutter rewrite, and its data layer
ships as plain Kotlin serialisers rather than compiled constants. A
systematic comparison against it — 223 serialiser classes, 87 enums, 71
request classes, 25 locale files — corrected this library in places no
amount of field testing would have found, because **nothing was
failing**:

| Found | Why it mattered |
|---|---|
| `dirt`, `map_id`, `covStrat` | read here as `numberOfDirtDetects`, `staticMapId`, `coverageStrategy` — plausible names no robot has ever sent, so all three read `None` on every mission ever recorded |
| `cmd`, `disc`, `poly`, `tentativeLoc` | four timeline event types dropped from every real timeline, because this library read the long forms |
| `coverage` | per-room mission progress, declared beside fields already read. `RoomEvent`'s docstring spent fourteen lines reasoning about what `area` and `total_area` mean; the field that answers it was in the same object |
| 112 error codes | with iRobot's own title and explanation in 25 languages. Of 126 labels written here, **two** matched the vendor's |
| `schedule_id` inside options | 3.0.0 moved it; a schedule whose id cannot be found is one nobody can edit, and that reads as an empty calendar rather than an error |
| 24 writable settings | `audio.volume` with a dot, not `audio`; `padWetness.padPlate` addressed directly, retiring a read-modify-write recommendation |

The full comparison — what was checked, what was corrected, what was deliberately left alone, and
what the APK cannot answer — is in
**[docs/internal/APK_3_0_0_FINDINGS.md](docs/internal/APK_3_0_0_FINDINGS.md)**.

**And one thing it did not settle.** The app spells four commands in
camelCase (`washPad`, `dryPad`) where this library uses lowercase. The
lowercase forms are what a real robot recorded in its own shadow, with a
pad-wash counter to match, so they stay. A confirmed shape outranks a
plausible one — a rule this comparison had cause to apply three times.

## Does your robot still answer locally?

App 2.2.4 carried a complete local API — 46 local-socket serializers,
`irobotmcs` discovery, port 5678. App 3.0.0 has none of it.

Whether a given **robot** still listens is a question about its
firmware, not about anyone's app, and nobody has asked it:

```
roombapy-prime-verify-local-channel
```

Four stages — UDP discovery, TCP connect, TLS handshake, and
deliberately **no** MQTT CONNECT. No credentials, no cloud, nothing sent
to the robot beyond the nine-byte discovery broadcast.

The discovery reply carries **SKU and firmware version**, which is the
datapoint the whole question turns on. A run that gets that far and then
fails has already produced something useful.

A note on what this would and would not prove: the reference
implementation that does speak this channel still logs in to the cloud
once, to fetch the robot's local password. A local transport removes the
round trip, not the dependency.

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
- [Setting value sets](docs/internal/SETTING_VALUE_SETS.md) — why four of the six writable settings cannot have a picker
- [APK 3.0.0 findings](docs/internal/APK_3_0_0_FINDINGS.md) — what iRobot's own app corrected, and what was deliberately left alone

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
