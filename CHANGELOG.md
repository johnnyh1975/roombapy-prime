# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
For the detailed, session-by-session reverse-engineering trail behind
any of this (what was tried, what's still uncertain, why), see
[`docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md).
This file only tracks what changed from a user's point of view.

## [Unreleased]

## [0.1.11a1] - 2026-07-17

### Added

- **`verify_mission_commands.py` gained an interactive mid-mission state capture**, inserted
  between Start and Stop in the core test flow. The script's existing before/after snapshots
  around each command are taken only ~3 seconds apart -- enough to prove a command was accepted,
  not enough to represent a genuinely active mission. This new step waits for explicit user
  confirmation ("robot is now visibly, actively cleaning") before calling `get_state()` again, no
  fixed sleep involved. Directly targets the long-open `RobotStatusV2` placement question: two
  independent real accounts (chairstacker, jadestar1864) have so far only ever produced
  idle-to-idle captures with identical top-level keys, which is consistent with either "wrong
  data source" or "these fields only populate during an active mission" -- neither has been
  distinguished yet because no capture has ever been taken while a robot was confirmed to be
  actually cleaning.
- **New `_diff_reported_keys()` helper**, printed immediately (not just written to
  `--dump-config`) so whoever runs the script sees the answer in the terminal: which top-level
  `reported` keys are new, missing, or changed in value versus the pre-mission baseline.
- 6 new tests (`test_diff_reported_keys_*`, `test_capture_mid_mission_state_*`), same
  fully-mocked style as the rest of this test file. 345/345 tests green, ruff clean.

### Changed

- Module docstring corrected: the previous claim that the existing before/after already captured
  "an active mission state" was inaccurate for the ~3-second Start window -- reworded to describe
  what that window actually establishes (command accepted) versus what the new mid-mission
  capture targets (a genuinely active state).

## [0.1.11a0] - 2026-07-16

### Fixed

- **Critical bug fixed: `get_default_routines()` would crash for any account with real
  `routine_builder_defaults` content.** `RoutineBuilderDefaults.regions` was modeled as a list
  (an unconfirmed guess, since bytecode alone couldn't distinguish List from Dict), but a real
  live response (chairstacker) confirms it's actually a dict keyed by region ID. Iterating a dict
  in a list comprehension yields its string keys, not values — the old code would raise
  `AttributeError: 'str' object has no attribute 'get'` the moment this field had real content.
  Fixed, along with two related corrections found in the same response: `RegionDefaults
  .operating_mode` is an int, not a str, and `OperatingModeProfile.params` is properly
  `CommandParams`-shaped (previously untyped `Any`) with a previously-missing sibling field,
  `updated_at`.
- **A second, separate real live crash fixed in the same area**: `get_default_routines()` also
  raised `AttributeError: 'str' object has no attribute 'get'` via `routines` itself (not just
  `routine_builder_defaults.regions`) — the confirmed bytecode said `routines` is a
  `List<Routine>`, but the real live value was very likely a JSON object keyed by routine
  ID/type (the same dict-not-list pattern as above, e.g. `RoomMetadataEntry
  .operating_mode_defaults`). `RoutinesDefaultsResponse.from_json()`/`parse_default_routines()`
  now handle both possible shapes defensively, and silently skip any individual malformed entry
  rather than letting one bad item crash the whole parse.
- **`ScheduleOptions.to_json()` was missing a required wrapper.** A real live `get_schedules()`
  response shows each `commands`/`end_commands` entry as `{"command": {...}}`, not a bare command
  dict as previously assumed — the old output would very likely have been rejected or
  misinterpreted by the real create/update schedule endpoints (never live-tested before now).
  Fixed.
- **`P2MapData` was missing several fields present in the real response**: `entity_type`,
  `robot_id`, `sku`, and a full `rooms_metadata` list — a real `get_map_metadata()` capture shows
  this endpoint's response is structurally almost identical to a single `P2MapVersion` entry
  (`get_active_map_versions()`'s own model), reusing `RoomMetadataEntry` for the room list.
  `BundleManifest.metadata` corrected from an assumed nested dict to `Any`, since a real bundle
  shows it's actually a bare string. The map bundle's own confirmed content-type set corrected:
  `dockPose` (singular), not `dockPoses` — and the manifest file's own filename within the
  archive is now confirmed to literally be `"manifest"`, closing a question open since the fifth
  session.

## [0.1.10a0] - 2026-07-16

### Security

- **URL path segments are now properly encoded.** Every identifier this library embeds into a URL
  path (BLIDs, map IDs, favorite IDs, household IDs, etc.) was previously interpolated directly
  via an f-string with no escaping — a value containing `/` or `..` could redirect a request to
  an unintended path on the same host. New `_path_segment()` helper (`urllib.parse.quote`) applied
  at all 22 URL-construction sites in `rest_client.py`. A no-op for any legitimate identifier this
  API actually uses (BLIDs/UUIDs are plain alphanumeric strings) — purely additive safety, no
  behavior change for well-formed input. Found during a dedicated security review; most relevant
  for any application built on top of this library (e.g. a Home Assistant integration) that might
  ever let untrusted or corrupted input reach these parameters.
- **`--dump-config`'s redaction now also covers `iot_token`/`iot_signature`/`user_cert`/
  `cognitoid`** — credential field names that exist elsewhere in this codebase
  (`ConnectionToken`/`RobotLoginEntry`/`CloudCredentials`) but were missing from the redaction
  helper's key list. No current call site actually captures these specific objects, so this was a
  latent gap rather than an active leak — fixed for defense in depth regardless, since the whole
  point of that function is to be a general-purpose safety net.
- **Credential-bearing fields no longer appear in default `repr()` output.** `CloudCredentials`
  (`secret_key`/`session_token`), `ConnectionToken` (`iot_token`/`iot_signature`), and the new
  `RobotLoginEntry` (`password`/`user_cert`) would previously print their raw secrets in plain
  text on any accidental `print()`/log/exception traceback involving these objects — a
  pre-existing gap, found and fixed together while adding the new model.

### Changed

- **`models.py` (4213 lines, 154 classes) split into a `models/` package**, organized by feature
  area (`geometry`, `mission_control`, `map_bundle`, `map_editing`, `favorites`, `schedules_dnd`,
  `mission_history`, `robot_info`, `livemap`, plus a small shared `enums_common`) instead of one
  session-ordered file. `roombapy_prime/models/__init__.py` re-exports everything, so this is a
  purely internal reorganization — every existing `from roombapy_prime.models import X` import
  across the library and test suite is completely unaffected. Verified with the full 332-test
  suite passing unchanged against a freshly built and installed wheel, plus a dedicated
  completeness check confirming every one of the 174 original public names is still importable.
  Largest resulting file is 787 lines, down from the original 4213.
- **All 12 map-bundle read models completely rebuilt with confirmed wire formats.** A systematic
  scan for `$$serializer` companion classes across the entire APK (226 found) revealed serializer
  classes for every single map-bundle content type, plus the bundle's own manifest structure —
  something no prior session had found. The real structure is a standard GeoJSON Feature
  (`{type, id, geometry, properties}`) with type-specific nested `properties`, not the flat
  objects previously guessed. `RoomInfo`/`BorderInfo`/`TrajectoryInfo`/`CoverageInfo`/`DockInfo`/
  `HazardInfo`/`FurnitureInfoRead`/`CleanZoneInfoRead`/`AdHocCleanZoneInfo` are replaced by
  `RoomFeature`/`BorderFeature`/`TrajectoryFeature`/`CoverageFeature`/`DockFeature`/
  `HazardFeature`/`FurnitureFeature`/`CleanZoneFeature`/`AdHocCleanZoneFeature` (each now with a
  proper `from_json()`, none did before). `NoMopZoneInfo`/`KeepOutZoneInfoRead`/`VirtualWallInfo`
  are replaced by a single `PolicyZoneFeature` — confirmed to be one unified type, not three
  separate ones. New: `FloorPlanFeature`, `FloorTypeFeature` (experimental), and
  `BundleManifest`/`ManifestFeature` — the bundle's own table-of-contents, which **definitively
  resolves the "exact file naming inside the tar.gz bundle" question** open since the fifth
  session: each `ManifestFeature` names the real filepath for its content type.
- **`get_map_metadata()` now returns a parsed `P2MapData`**, not raw JSON — its response shape
  (`p2map_id`, `active_p2mapv_id`, `create_time`, `last_p2mapv_ts`, `state`, `visible`, `name`,
  `user_orientation_rad`) is confirmed via bytecode, closing a placeholder open since the
  library's early sessions.
- **`get_schedules()` now returns a parsed `SchedulesResponse`** (→ list of `SchedulesList` → list
  of schedules), not raw JSON — the envelope shape is now confirmed via bytecode; only the class
  names had previously been found, not their fields.

### Fixed

- **Documentation staleness across `examples/`, `docs/API_REFERENCE.md`, and `README.md`,** found
  during a broader architecture review. Most significantly: `examples/mission_control.py` still
  used `send_mission_command()`/`RoutineCommand` — the transport confirmed **not working** since
  session 39 — instead of the confirmed-working `send_simple_command()`; anyone following that
  example would have hit the exact timeout bug this project spent many sessions resolving. Fixed,
  and rewritten to match the current, live-confirmed API.
  `docs/API_REFERENCE.md` had six method return types still showing `dict` after this session's
  own wiring fixes, an entire missing `get_map_geojson_link()` entry, references to map-bundle
  model names that no longer exist (renamed in session 47), a wrong `get_notifications()` default
  value (`"1.0"`, corrected to `"2.2.4"` back in session 36), and a mission-control section that
  never mentioned `send_simple_command()` at all. `README.md`'s "known unresolved gaps" list had
  two entries describing questions already resolved in later sessions (the map-edit envelope,
  map-bundle file naming). All corrected. 26 scattered `models.py`-in-docstring references across
  active code and current-state docs also corrected to point at the right submodule (e.g.
  `models.py::RobotStatusV2` -> `models/robot_info.py::RobotStatusV2`) — historical session-log
  entries in `CHANGELOG.md`/the gap analysis/`DEVELOPMENT_NOTES.md` deliberately left untouched,
  since those correctly describe what was true when they were written.
- **Architectural gap: several methods had confirmed response models that were never actually
  wired in.** `get_robot_parts()`, `get_serial_number_data()`, `get_dnd_settings()`, and
  `get_default_routines()` all had their own docstrings saying "response shape modeled" or
  pointing at a specific parser class/function — but the methods themselves still returned raw
  JSON, never calling that parser. Found during an architecture review, not new field-level
  research (the models themselves were already correct and tested). All four now return the
  parsed model directly (`RobotPartsInfo`, `RobotSerialInfo`, `DNDStatusResponse`,
  `RoutinesDefaultsResponse`), with `PrimeRobot`'s wrappers updated to match.
- **`set_map_name()` genuine bug fixed**: sent `{"type": name}`, confirmed via bytecode
  (`EditMapSettingsRequest$Command$SetName$$serializer`) to actually need `{"name": name}`. This
  was a real bug, not just an unconfirmed guess — the previous body would likely have been
  silently ignored or rejected by the real server.
- **`Routine`'s wire keys corrected**, confirmed via bytecode: `commanddefs` (all lowercase, no
  separator — an unusual one), `last_run`, `name_loc_key`, `name_loc_args`, `time_estimate`,
  `time_estimate_seconds` (snake_case) — not the previously-guessed camelCase equivalents.
- **V1 map-edit command envelope and 8 of 9 commands' field names, all corrected via bytecode.**
  The request envelope is now confirmed: `{"edit_cmd": {...}, "response_type": "..."}`, not the
  previously-assumed flat `{"command": "<Name>", ...fields}` shape. Individual command field
  names corrected: `RenameRoom` (`room_id`/`room_name`, not `id`/`name`), `SplitRoom` (`room_id`),
  `MergeRooms` (`room_ids`, not `ids`), `SetRoomType` (`room_id`/`type_id`, not `id`/`type`),
  `SetPermanentAreas` (`area_points`, not `areaPoints`), `DeletePermanentAreas` (`area_ids`, not
  `areaIDs`), `SetVirtualWalls` (`virwall`, not `walls`), `AdjustFurniture`
  (`furniture_list`/`package`, not `furnitureList`/`packageInfo`). `SetRoomMetadata` and the
  `VirtualWall` Linear/Rectangle/NoMopZone discriminator use hand-written custom serializers and
  remain at their previous, weaker confidence level.
- **`get_map_geojson_link()`'s response key confirmed**: `map_url`, previously entirely
  unconfirmed.
- **`create_favorite()`'s response key confirmed**: `favorite_id` (the existing fallback-chain
  guess happened to already have this first, now definitively confirmed rather than guessed).
- **`ScheduleOptions`'s wire keys corrected.** Directly confirmed via bytecode this time
  (`ScheduleOptions$$serializer`'s `<clinit>`, the same technique that resolved `RobotStatusV2`):
  real keys are `robot_id`, `end_commands`, `created_time`, `force_cloud` (snake_case) — not
  `assetId`, `endCommands`, `createdTime`, `forceCloud` (camelCase) as previously guessed. The
  other 13 fields were already correct. `HouseholdSchedule`/`HouseholdScheduleUpdate` similarly
  corrected: real key is `schedule_id`, not `scheduleId`.

### Added

- **New `RobotLoginEntry`/`RobotCapabilities`/`RobotDigitalCapabilities` models** — `LoginResult
  .robots`' per-device entries (previously a completely unmodeled raw dict) are now properly
  typed, confirmed via bytecode (`Robot$$serializer` and nested types) and cross-checked against
  real fixture data.
- **New `P2MapEditPartialSuccess`/`P2MapEditSuccessFallback`/`ResponseError` models** for
  `edit_map()`'s possible response/error shapes, confirmed via bytecode — not yet wired into
  automatic parsing, since which shape comes back for a given request isn't confirmed.
- **`get_default_routines()`'s full response envelope now modeled**: new
  `RoutinesDefaultsResponse`/`RoutineBuilderDefaults`/`RegionDefaults`/`OperatingModeProfile`,
  confirmed via bytecode — previously only the per-routine shape was modeled, and
  `routine_builder_defaults` (region-type-based default operating-mode settings) wasn't captured
  at all.
- **`RobotStatusV2`'s list fields now properly typed**: new `DockControl`/`RobotStatusButton`/
  `RobotStatusError` models (confirmed via bytecode) replace the previous `list[Any]` placeholders
  for `dock_controls`/`buttons`/`errors`/`conditional_errors`.
- **New `HouseholdSettingOptions` model**, replacing a long-standing "structure not investigated"
  placeholder — household demographic info (adult/kid/pet counts, opt-out flags).
- **New `DNDDailySchedule`/`DNDEndsAt` models**, with wire keys confirmed the same way
  (`dailyStart`/`dailyEnd`, `endsAt`) — the two variants used internally for building a DND PUT
  request. Not yet wired into `set_dnd_settings()` (the envelope/discriminator for combining them
  under `DNDSchedule` remains unconfirmed), but available for anyone experimenting further.
- **New, experimental `send_routine_command_via_cmd_topic()`** — a well-reasoned but unconfirmed
  hypothesis for region-aware mission commands (favorites, specific rooms/zones), which
  `send_simple_command()` can't express. Based on `RoutineCommand`'s own confirmed field mapping
  sharing two exact key names ("command", "initiator") with the confirmed-working simple command
  payload. Explicitly documented as higher-risk than the basic command confirmation and not yet
  live-tested — see the method's docstring before using it.
- **Documented, not resolved: a real tension in the live-map position-update format.** A
  bytecode scan found `PositionUpdate`'s confirmed fields (`point`/`orientation`/
  `operatingModes`) suspiciously close to this library's own `PositionSample` dataclass, raising
  a genuine, unresolved question about whether the "cur_path" flat-array parsing this library has
  used since early on is correct, or whether the real wire format is a structured object instead.
  Not changed without further evidence — see `PositionUpdateMessage.from_json()`'s docstring for
  the full, honest account.

## [0.1.9a0] - 2026-07-15

### Added

- **`roombapy-prime-validate --dump-config` now captures a type-only structure summary of every
  file in the downloaded map bundle**, not just their filenames. This is the only way to confirm
  the wire format of any of the 12 map-bundle read models in `models.py` (`RoomInfo`,
  `BorderInfo`, `TrajectoryInfo`, `CoverageInfo`, `DockInfo`, `HazardInfo`, and 6 more) — none of
  which have ever been checked against real data (none have a `from_json()` yet). Safe by
  construction: reuses the existing `_shallow_summary()` helper, which never reveals actual
  values (including geometry coordinates), only field names and generic type/length markers —
  verified with a dedicated regression test against realistic GeoJSON-shaped data specifically,
  not just the simpler flat-dict case the pre-existing leak test covered.
- **`roombapy-prime-verify-map-edit` now investigates the map bundle** when no named room is
  found via `get_active_map_versions()` (as happened on a real account whose rooms are named in
  the app, but not in that response). Downloads and unpacks the map bundle, looking at its
  separate "rooms" file for names instead — extracting only non-geometry fields (never
  coordinates/polygons; consistent with this project's standing rule that a floor plan is more
  personal than most other data captured here, and this is a report people might paste into a
  public issue). This is investigation, not a new confirmed model — `RoomInfo`'s wire format
  remains unconfirmed either way; the goal is to get real data to build that on next.

## [0.1.8a0] - 2026-07-15

### Confirmed

- **Mission control works.** `send_simple_command()` (`start`/`stop`/`pause`/`resume`/`dock`) was
  live-tested against a real robot for the first time and confirmed working end to end — the
  robot actually reacted to every single command, watched and confirmed by a real user, not just
  "no error was raised." This resolves the single most important open question this library has
  had since the project began. See `docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md` for the full
  evidence trail that led here.

### Fixed

- **`irbt_topic_prefix`/`iot_topic_prefix` definitively resolved.** A live account confirmed the
  real discovery-response field names are `irbtTopics`/`iotTopics` (not `irbtTopicPrefix`/
  `iotTopicPrefix` as guessed since introducing this field). `send_simple_command()` and
  `watch_live_map()` can now actually build their target topic on accounts where this previously
  came back empty. This was the fix that made the mission control confirmation above possible.

## [0.1.7a0] - 2026-07-15

### Added

- **New `roombapy-prime-verify-map-edit` script.** Map editing (`edit_map()`) has never been
  tested against a real device -- unlike mission commands, there's no external corroboration for
  its envelope format, so this script is deliberately narrower and more cautious than
  `roombapy-prime-verify-commands`: it only tests renaming an existing, already-named room to a
  clearly-marked test name and immediately back, never the riskier, less-reversible operations
  (split/merge rooms, delete permanent areas, virtual walls, furniture). Same safety design
  (explicit flag + per-step confirmation) as the mission-command script, plus explicit
  confirmation in the real app (not just an accepted HTTP response) before treating either step
  as successful.
- **`--dump-config` now also captures the raw discovery deployment object** (in
  `roombapy-prime-validate`, `roombapy-prime-verify-commands`, and the new
  `roombapy-prime-verify-map-edit`), redacted the same way as everything else -- needed to
  actually resolve `irbt_topic_prefix`'s real field name with real values, not just the key names
  the always-printed report already shows.
- **Diagnostic reporting for `irbt_topic_prefix`.** A live test confirmed the guessed discovery-
  response field names ("irbtTopicPrefix"/"iotTopicPrefix") don't match reality for at least one
  real account — `send_simple_command()` failed outright as a result. Rather than guess again,
  `LoginResult`/`PrimeRobot` now capture and expose the raw discovery deployment object, and both
  `roombapy-prime-validate` and `roombapy-prime-verify-commands` report its actual keys (structure
  only, never values) when the guess comes back empty — closing the loop with real evidence
  instead of another blind guess. `roombapy-prime-verify-commands` also now exits early with a
  clear explanation instead of repeating the same failure for every command.
- **New, optional `RobotStatusV2` model** (`models.py::parse_robot_status_v2()`) exposing
  `robot_state`, `battery_level`, `is_charging`, `is_robot_on_dock`, `current_p2map_id`/
  `current_p2map_version_id`, `dock_controls`, `errors`, `conditional_errors`, `buttons`, and
  `localization_args` -- all bytecode-confirmed wire keys, directly read from the real
  `@Serializable` class's serializer descriptor. **Important caveat**: it is NOT confirmed that
  this structure is part of `get_state()`'s response -- the one real capture available shows a
  completely different set of top-level keys. `verify_mission_commands.py` now attempts this
  parse before/after every command and includes the result in the diagnostic capture, so the
  next live run can help settle where (or whether) this structure actually appears.

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
  `docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md` for the full evidence trail.

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
