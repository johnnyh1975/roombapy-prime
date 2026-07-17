# Data privacy & data flow

What this library sends where, and what it stores — verified directly
against the current code, not written from memory. If anything here
ever stops matching the actual code, that's a documentation bug; please
open an issue.

## Where your data goes

**Directly to iRobot's own cloud infrastructure — never through any
third-party or intermediary server.** This library only ever talks to:

- iRobot's discovery/Gigya/Custom-Authorizer login chain
  (`auth.py`) — your username and password go directly to iRobot's own
  identity provider (Gigya) and then iRobot's own auth service, exactly
  as the official app does.
- AWS Cognito, for temporary cloud credentials (`CognitoId` and
  associated tokens) — again, iRobot's own AWS account, not a
  third-party service.
- AWS IoT (MQTT over WebSocket) for the live device connection
  (`mqtt_client.py`) and iRobot's own REST API (`rest_client.py`) for
  everything else (maps, schedules, favorites, mission history, etc.).

There is no analytics, telemetry, or crash-reporting service of any
kind in this library. Nothing is ever sent anywhere other than iRobot's
own infrastructure.

## What this library stores, and where

**Nothing is written to disk by the core library, ever.** Login
credentials, session tokens, and all fetched data (state, maps,
schedules, history, etc.) live only in memory, for as long as your
process keeps the `PrimeRobot`/`PrimeRestClient` object alive. Nothing
persists across restarts unless *your own application* explicitly
saves something itself (e.g. a Home Assistant integration built on top
of this library choosing to cache a value) — that's a decision for
whatever you build on top of this library, not something this library
does on your behalf.

The only exception, and it's fully opt-in: the diagnostic scripts
(`roombapy-prime-validate`, `roombapy-prime-verify-commands`,
`roombapy-prime-verify-map-edit`) will write a JSON file to disk, but
only if you explicitly pass `--dump-config PATH` — and only to the
exact path you name. See [`SECURITY.md`](../SECURITY.md) for what gets
redacted in that file before it's written.

## Logging

Username and password are used once during login and then discarded —
they are never logged. The only thing this library's own `_LOGGER`
calls record about your account is a bare count of robots found after
a successful login (e.g. `"authenticated, 1 robot(s) found"`); no
credential material, tokens, or identifiers appear in any log line
this library emits by default. See [`SECURITY.md`](../SECURITY.md) for
the fuller credential-handling picture (TLS, in-memory-only tokens,
the `repr()` redaction on credential-bearing objects).

## Cloud refresh cadence

This library does not poll on any fixed schedule by itself — it's a
client library, not a background service. `watch_state()` and
`watch_live_map()` use the device's own push mechanisms (MQTT shadow
deltas, the live-map topic) rather than periodic polling; any other
method call (`get_state()`, `get_mission_history()`, etc.) only talks
to iRobot's servers when *you* call it. How often data actually gets
fetched is entirely up to the application built on top of this
library.

## Third-party accounts

Two field testers (chairstacker, jadestar1864) have run diagnostics
against their own live iRobot accounts to confirm this library's
behavior — see
[`docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md)
for the details of what was captured and how it was redacted before
being shared. No account credentials, BLIDs, or other identifying
information from those sessions are stored anywhere in this
repository — diagnostic output shared during development is reviewed
and redacted before being used to fix real bugs, not archived verbatim.
