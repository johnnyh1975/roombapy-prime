# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
For the detailed, session-by-session reverse-engineering trail behind
any of this (what was tried, what's still uncertain, why), see
[`docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md).
This file only tracks what changed from a user's point of view.

## [Unreleased]

## [0.1.6a0] - 2026-07-14

### Fixed

- **`get_notifications()`'s `app_version` default corrected from `"1.0"` to `"2.2.4"`.** The
  previous placeholder value had zero evidentiary basis and was the suspected cause of this
  call's known HTTP 400 failure against a real account. The analyzed APK's own
  `BuildConfig.VERSION_NAME` and `AndroidManifest.xml`'s `versionName` both confirm "2.2.4" as
  the real app version used for this analysis -- a much stronger candidate for what this
  parameter is meant to carry. Not yet live-verified with the corrected value.
- **Stale documentation fixed**: a leftover comment in `models.py` and a matching test docstring
  still described the create/update favorite HTTP methods as "assumed", contradicted by a later
  session's bytecode confirmation already reflected everywhere else in the codebase. No behavior
  change -- documentation consistency only.
- **`get_cleaning_profiles()`'s query parameters corrected.** Directly bytecode-confirmed this
  time (`CleaningProfileRequest.getQueryParams()`): the robot-id key is `"robotId"` (camelCase,
  not `"asset_id"`), and a third, previously entirely missing parameter, `"includeSmart"`
  (`"true"`/`"false"`), is now sent. `p2map_id` is now optional to match the real branching
  logic. Not yet live-verified with the corrected shape.

### Changed

- **Mission control (`start`/`stop`/`pause`/`resume`/`dock`/etc.) no longer sent via the device
  shadow.** A live test confirmed every attempt via the previous `send_mission_command()`
  (shadow-update) path timed out with zero response. New `send_simple_command()` sends via a
  different, dedicated MQTT topic (`{irbt_topic_prefix}/things/{blid}/cmd`) with a simple
  `{"command", "time", "initiator"}` payload — confirmed both by this library's own native
  disassembly and independently by a third-party, unaffiliated implementation reporting this
  path working against a real device. `send_mission_command()` is kept for the region-based use
  case (still unconfirmed by any source), but is no longer the recommended path for basic
  commands. `verify_mission_commands.py` updated to use the new path. See
  `docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md` for the full evidence trail.

## [0.1.5a0] - 2026-07-14

### Changed

- **All user-facing output from `roombapy-prime-validate` and `roombapy-prime-verify-commands` is
  now in English** (report labels, status values, prompts, `--help` text). Previously this was in
  German. Internal code comments/docstrings (explaining implementation history) remain in German
  as before — this change only affects what a user actually sees when running the tools.

  *(Correction, added later: internal code comments/docstrings were subsequently also fully
  translated to English in a separate pass -- see the commit history for the full extent of that
  change, not tracked as its own version bump here.)*

## [0.1.4a0] - 2026-07-14

**Fixed a likely explanation for intermittent shadow request failures.** `get_shadow()`,
`update_shadow()`, the persistent `subscribe()` (used by `watch_state()`/`watch_live_map()`), and
subscription restoration after a token refresh all subscribed to response topics and acted
immediately, without waiting for the broker to confirm the subscription (SUBACK). If a response
or push arrived before the subscription was actually active, it was silently dropped, causing an
unpredictable timeout that had nothing to do with device tier or connectivity. This likely
explains the same-device, different-run inconsistency observed with `get_settings()` in earlier
testing. All four call sites now wait for subscription confirmation before proceeding.

### Added

- `roombapy-prime-verify-commands`: a separate, standalone script for manually verifying mission
  commands (start/stop/pause/resume/dock) against a real robot — deliberately never part of the
  automatic diagnostics script, since this is the one operation that actually moves your robot.
  Requires both a `--i-understand-this-will-move-my-robot` flag and an interactive confirmation
  before every individual command; declining any prompt skips that step. Runs a conservative
  start→stop test by default, with pause/resume and dock as separate opt-in steps. Also captures
  `get_state()` before/after each command — the first opportunity to see what the shadow reports
  during an actual active mission, which no prior real response has shown.
- `RobotSettings` model (from a confirmed real `get_settings()` response): child lock, volume,
  timezone, country, auto-evac frequency, language list, pad wash/dry cycle settings, and several
  permission flags — resolves a good portion of the previously-guessed settings vocabulary
- `PadWetnessParam.from_json()` (was missing despite `to_json()` existing)

## [0.1.3a0] - 2026-07-13

**Fixed a critical, silent bug affecting every user of mission history so far:** the detailed
timeline of a cleaning run (which room/zone was cleaned, pad washes, docking, relocalization
events) has returned an empty list for every mission since it was introduced, because the parser
was looking for a JSON key that doesn't exist in real server responses. No error was ever raised —
it silently returned nothing. Fixed and verified end-to-end against real mission history data.

### Fixed

- Mission timeline parsing (`parse_mission_timeline`) now reads the correct response key; all 20
  timeline sub-event types (`RoomEvent`, `TravelEvent`, `TraversalEvent`, `ZoneEvent`,
  `TentativeLocationEvent`, `PadWashEvent`, and more) had several wrong field names corrected
  against real data, plus two enum values (`TravelDestination`, `TraversalType`) that were
  wrong-cased
- Household lookup used internally by `get_schedules()`/`get_dnd_settings()` had the same class of
  bug as the earlier map-ID lookup (wrong field name silently blocking those checks) — fixed
- `get_active_map_versions()` field-name lookup fixed (was still using guessed names in one spot)
- `MissionCommandRecord` was missing a `params` field (separate from per-region params, sometimes
  carries the cleaning profile)
- `DoneCode` enum values were wrong-cased (lowercase in reality)
- `CommandParams.scrub`'s wire key corrected to `swScrub`; `RegionType` values corrected to
  lowercase
- Diagnostics script: `get_state()` device-info extraction now looks at the correct nested
  response path

### Added

- Typed models built from confirmed real responses: `P2MapVersion`/`RoomMetadataEntry` (map
  versions, including per-room cleaning presets), `RobotSerialInfo`, `RobotPart`/`RobotPartsInfo`,
  `Household`/`HouseholdRobot`/`HouseholdUser`
- `CommandParams.operating_mode`, `CommandParams.no_auto_passes`, `RoutineCommand.initiator`,
  `CommandParams.routine_type` (completed a previously incomplete field)

### Added

- Diagnostics script now also checks `get_live_map_stream()` and runs a short, bounded
  `watch_state()` sample (both read-only, previously omitted by oversight rather than by design)
- `--dump-config PATH` flag for the diagnostics script: saves the actual (lightly redacted) raw
  responses from every read endpoint as JSON, similar to a Home Assistant integration's "Download
  Diagnostics" feature — useful for pinning down exact field names, never auto-shared

## [0.1.2a0] - 2026-07-13

**First release with genuine live validation.** Up to and including `0.1.0.dev0`, nothing in this
library had ever been run against a real account. Between that point and this release, a
community member (@chairstacker, Roomba 405) ran `roombapy-prime-validate` against a real
Prime/V4 account, which:

- Confirmed the full login chain (Discovery → Gigya → iRobot auth), MQTT connection, and most
  REST reads (`get_state`, `get_favorites`, `get_mission_history`, `get_user_households`,
  `get_active_map_versions`) all work against a live server
- Confirmed the named `"rw-settings"` shadow responds on SMART-tier hardware, as predicted
- Surfaced a real bug in the diagnostics script itself (wrong field names — `p2mapId`/`id` instead
  of the documented `mapId`/`mapVersionId` — when looking up the active map version), now fixed,
  with more thorough debug output added so similar mismatches are self-diagnosing going forward
- Surfaced the same class of risk in the `get_user_households()` → `household_id` extraction path;
  fixed the same way with a new, reusable `_shallow_summary()` helper (reports response *structure*
  for debugging, never actual values, so a shared report can't leak account data)

### Added

- Account login (Gigya + AWS Custom Authorizer), MQTT shadow connection with automatic token refresh
- Live state: `get_state()`, `get_settings()`, `watch_state()` for continuous updates
- Mission control: `send_mission_command()` with the full command vocabulary (`MissionCommandType`, 30 values) and parameter surface (`CommandParams`, 37 fields covering suction, mop wetness, carpet boost, room confinement, timeboxing, drive speed, and more)
- Favorites: list/create/update/delete/reorder, backed by the fully-confirmed `FavoriteV1` model
- Maps: read metadata and active versions, edit rooms/zones/furniture/walls (`edit_map()`, 9 command types), watch the live map while cleaning, download and unpack the full map bundle (`download_map_bundle()` + `parse_map_bundle()`)
- Schedules: list/create/update/delete recurring cleaning schedules per household
- Mission history: `get_mission_history()` plus `parse_mission_history()` for typed results (duration, coverage, end reason via the 19-value `DoneCode` enum), including all 20 mission-timeline sub-event types (`MissionTimelineEvent` — room/zone/travel/plan/error events and more)
- Settings: Do Not Disturb windows, cleaning profiles (`DEEP`/`LIGHT`/`NORMAL`/`SMART`), per-map default routine suggestions
- Parts & device info: consumable part status (`get_robot_parts()`), reset after replacement (`reset_robot_parts()`), serial number data (`get_serial_number_data()`) — confirmed from the actual APK-bundled configuration file, not decompiled logic
- Find-my-robot echo/chirp (`poll_echo_value()`), time estimates (`get_time_estimates()`), full device reset (`reset_robot()`, destructive — see docstring), notification/timeline feed (`get_notifications()`) — same primary-source confirmation
- `roombapy_prime.diagnostics` — a live validation script (`roombapy-prime-validate`) that runs the library's read paths against a real account and reports what works; includes an opt-in reversible favorite create/verify/delete round trip, a credential-redaction pass, and a one-click pre-filled GitHub issue link for sharing results
- Full API reference (`docs/API_REFERENCE.md`) organized by feature area with per-item confidence markers
- MIT license, CI (test matrix across Python 3.11–3.13, lint, package build+install verification)

### Known limitations

See the README's "Confidence & known gaps" section for the current
list — the short version is: reading data rests on a solid,
source-confirmed wire format; anything that *sends* something to the
robot (mission commands, map edits) has the right shape on paper but
has never been confirmed against a real server. This library has never
been run against a real Prime/V4 account.
