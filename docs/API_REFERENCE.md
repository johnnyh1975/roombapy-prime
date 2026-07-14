# API Reference

This is a navigational reference, not a replacement for the source —
every method and model has a more detailed docstring in the code
itself, including the exact evidence behind each field (Kotlin source
line, bytecode inspection, or "analogy assumption, unconfirmed"). This
document tells you *what exists and roughly how sure we are*; the
docstring tells you *why*.

Confidence shorthand used throughout:
- 🟢 **Confirmed** — field names/types/methods read directly from
  decompiled source or bytecode, not guessed
- 🟡 **Plausible** — right shape on paper, never sent to/confirmed
  against a real server
- 🔴 **Best-guess** — genuine uncertainty flagged in the docstring;
  treat as a starting point, not a fact

See [`PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](PRIME_APP_GAP_ANALYSIS_2026-07-11.md)
for the full evidence trail behind any of this.

## Contents

- [Setup & connection](#setup--connection)
- [Live state](#live-state)
- [Mission control](#mission-control)
- [Favorites](#favorites)
- [Maps](#maps)
- [Schedules](#schedules)
- [Settings (DND, cleaning profiles, default routines, households)](#settings)
- [Mission history](#mission-history)
- [Model index](#model-index)

Everything below is a method on `PrimeRobot` unless stated otherwise.
Get one via `PrimeFactory.create_prime_robot(...)` (see README) — all
methods are `async` and assume `await robot.connect()` has already run,
except where noted.

---

## Setup & connection

| Method | Confidence | Notes |
|---|---|---|
| `PrimeFactory.create_prime_robot(session, username, password, country_code, blid=None, *, auto_refresh=False)` | 🟢 | Logs in, picks a robot (first found if `blid` omitted), wires MQTT+REST. Returns a **not-yet-connected** `PrimeRobot` — call `.connect()` yourself. `auto_refresh=True` keeps credentials in a closure for automatic re-login before token expiry; see the module docstring in `prime_robot.py` for the credentials-in-memory tradeoff this implies. |
| `robot.connect(timeout=10.0)` | 🟢 | Blocking paho handshake run in a worker thread. |
| `robot.disconnect()` | 🟢 | |

```python
robot = await PrimeFactory.create_prime_robot(session, username, password, "US")
await robot.connect()
```

---

## Live state

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_state(timeout=8.0) -> ShadowResponse` | 🟢 | The classic/unnamed shadow — identity, capabilities, current mission status. |
| `robot.get_settings(timeout=8.0) -> ShadowResponse` | 🟢 | The named `"rw-settings"` shadow — only responds on SMART-tier devices, per binary analysis. |
| `robot.set_setting(key, value, timeout=8.0) -> ShadowResponse` | 🟡 | Writes into the `rw-settings` shadow. |
| `robot.watch_state(named=None, *, queue_maxsize=100) -> AsyncIterator[ShadowResponse]` | 🟢 | Yields every shadow delta as it arrives, until the generator is closed/cancelled. Bounded queue, drops oldest on overflow (logged). Pass `named="rw-settings"` to watch that shadow instead of the default. |

```python
async for delta in robot.watch_state():
    print(delta.payload)
```

---

## Mission control

The single largest confidence gap in the library — see the README's
confidence table before relying on this against a real robot.

| Method | Confidence | Notes |
|---|---|---|
| `robot.send_mission_command(command: RoutineCommand, timeout=8.0) -> ShadowResponse` | 🟢 (transport), 🟡 (payload never live-tested) | Publishes the command wrapped as `{"state":{"desired":{"cmd": ...}}}` to the **classic (unnamed) shadow**. Confirmed from the actual APK-bundled config file (`res/raw/base_roomba_config.json`) — the `"Control"`/`"AssetControlCommand"` entries both have `"namedShadow": ""`, unlike settings (`"rw-settings"`) or schedules (`"rw-schedule"`) in the same file. This is a primary-source confirmation, not decompiled-logic inference. Payload shape is separately source-confirmed. Never sent to a live server. |

**`RoutineCommand`** — the payload for the above. Key fields:

```python
from roombapy_prime.models import RoutineCommand, MissionCommandType, CommandParams

RoutineCommand(
    command_type=MissionCommandType.CLEAN,   # 🟢 30-value enum: CLEAN, START, STOP, PAUSE, RESUME, DOCK, SPOT, ...
    asset_id=robot.blid,                     # 🟢 -> wire key "robot_id"
    map_id=None,                             # 🟢 -> "p2map_id"
    clean_all=False,                         # 🟢 -> "select_all"
    favorite_id=None,                        # 🟢 -> "favorite_id", set this to run a saved favorite
    regions=None,                            # 🟢 list[Region] or raw dicts, both accepted
    params=None,                             # 🟢 CommandParams or raw dict, both accepted
)
```

`CommandParams` (🟢 all 37 fields confirmed from bytecode, all optional)
covers things like `suction_level`, `pad_wetness` (a `PadWetnessParam`),
`carpet_boost`, `room_confine`, `timebox_minutes`, and drive-command
fields (`velocity_left`/`velocity_right`). `Region` pairs a `RegionType`
(`RID`/`TID`/`ZID`) with its own `CommandParams`.

---

## Favorites

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_favorites() -> list[FavoriteV1]` | 🟢 | The only one of the five favorites endpoints with both HTTP method *and* response shape fully confirmed. |
| `robot.create_favorite(favorite: FavoriteV1) -> dict` | 🟢 | POST, confirmed from bytecode (`CreateFavoriteRequest.<init>`). |
| `robot.update_favorite(favorite_id, favorite: FavoriteV1) -> dict` | 🟢 | PUT, confirmed the same way. |
| `robot.delete_favorite(favorite_id) -> dict` | 🟢 | DELETE, confirmed. |
| `robot.order_favorite(favorite_id, *, insert_at=None, insert_before=None, insert_after=None) -> dict` | 🟢 | PUT; the three params are query parameters, not body fields (a real bug was caught and fixed here — see gap analysis). |

**`FavoriteV1`** fields include `name`, `command_defs: list[RoutineCommand]`
(the steps the favorite runs), `color`, `icon`, `is_hidden`, and
`time_estimates`. All 🟢 confirmed from cleanly decompiled source.

---

## Maps

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_active_map_versions() -> list[dict]` | 🟢 | Confirmed field names from a real response (26th session): `p2map_id`, `active_p2mapv_id` (the map version), `name`, `state`, `visible`, `rooms_metadata`. For a typed result use `models.py::parse_active_map_versions()` — includes per-room `operating_mode_defaults`, which reuse `CommandParams` directly. |
| `robot.get_map_metadata(p2map_id) -> dict` | 🟢 | |
| `robot.set_map_name(p2map_id, name) -> dict` | 🟢 | |
| `robot.set_map_orientation(p2map_id, orientation_rad) -> dict` | 🟢 | Clamped to (-π, π]. |
| `robot.edit_map(p2map_id, command: MapEditCommandV1) -> dict` | 🟡 | **The actually-used path** (V1) — every room/zone/furniture/wall edit in the app goes through this. Envelope discriminator format around the confirmed field names is inferred by analogy, not confirmed. |
| `robot.edit_map_v2(p2map_id, command: MapEditCommand) -> dict` | — | The app-side dead code path (confirmed never called by the app itself). Kept for completeness; prefer `edit_map()`. |
| `robot.get_live_map_stream() -> LiveMapStreamInit` | 🟢 | REST call that's actually a keep-alive ping, not a topic fetch — see `watch_live_map()`. |
| `robot.watch_live_map(*, queue_maxsize=100, keep_alive_interval=10.0) -> AsyncIterator[...]` | 🟢 (topic pattern), 🟡 (concatenation order) | Subscribes to the fixed livemap topic directly; the REST call above just keeps the stream alive in the background. |
| `robot._rest.download_map_bundle(url) -> bytes` + `models.parse_map_bundle(data) -> dict` | 🟢 (mechanism), 🔴 (file naming inside the archive) | Deliberately unsigned GET — the app opens the pre-signed URL directly, no auth headers. |

`edit_map()` takes one of 9 V1 command dataclasses: `RenameRoomV1`,
`SplitRoomV1`, `MergeRoomsV1`, `SetRoomTypeV1`, `SetRoomMetadataV1`,
`SetPermanentAreasV1`, `DeletePermanentAreasV1`, `SetVirtualWallsV1`,
`AdjustFurnitureV1`.

---

## Schedules

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_schedules(household_id) -> dict` | 🟢 | |
| `robot.create_schedules(household_id, schedules: list[ScheduleOptions]) -> dict` | 🟢 | POST, confirmed from bytecode. |
| `robot.update_schedules(household_id, household_schedule_id, schedules: list[HouseholdSchedule]) -> dict` | 🟢 | PUT, confirmed. |
| `robot.delete_schedule(household_id, household_schedule_id) -> dict` | 🟢 | DELETE. |

`ScheduleOptions` covers `frequency` (`ScheduleFrequency`: `ONCE`,
`WEEKLY`, `BI_WEEKLY`, `MONTHLY`), `start`/`end` (`ScheduleTime`),
`commands`/`end_commands` (🔴 assumed `list[RoutineCommand]` by strong
analogy to favorites, not generically confirmable from bytecode), and
`enabled`/`deleted`.

`household_id` isn't returned directly from login — try
`robot.get_user_households()` (below) or your account's app to find it.

---

## Settings

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_user_households() -> dict` | 🔴 | Implemented despite being dead code in the current app version — the endpoint likely still exists server-side even though nothing in the app calls it. HTTP method is REST convention, not confirmed from a request class like everything else here. |
| `robot.get_dnd_settings(household_id) -> dict` | 🟢 | Response form: see `DNDStatusResponse.from_json()`. |
| `robot.set_dnd_settings(household_id, settings: dict) -> dict` | 🟢 (method), 🔴 (body shape) | |
| `robot.get_cleaning_profiles(asset_id, p2map_id) -> dict` | 🟢 | See `CleaningProfile.from_json()` — `DEEP`/`LIGHT`/`NORMAL`/`SMART`, each with its own `CommandParams`. |
| `robot.get_default_routines(p2map_id) -> dict` | 🟢 | Auto-generated per-map cleaning suggestions. See `parse_default_routines()`. |
| `robot.get_robot_parts() -> dict` | 🟢 | Consumable part status (filter/brush/battery wear, unconfirmed which). Confirmed from `res/raw/base_roomba_config.json` (a primary-source config file bundled in the APK), not decompiled logic — see `docs/base_roomba_config_REFERENCE.json`. |
| `robot.reset_robot_parts() -> dict` | 🟢 (method), 🔴 (body shape) | Same source as above; presumably resets a part's wear counter after replacement. |
| `robot.get_serial_number_data() -> dict` | 🟢 | Confirmed structure (26th session): serial number, user-assigned robot name, `family` (e.g. `"Roomba Combo"`), `series`. For a typed result use `models.py::RobotSerialInfo.from_json()`. |
| `robot.poll_echo_value() -> dict` | 🟢 (method), 🔴 (body/response shape) | "Find my robot" — triggers the device's echo/chirp. Confirmed from the same config file (`"PollEchoValueCommand,Set"`); matches the `SetRoombaEchoAwsIotSerializer` found during native analysis. No body sent by default. |
| `robot.get_time_estimates(body: dict) -> dict` | 🟢 (method/URL), 🔴 (body shape) | `POST` despite being read-only in the config (`"read": true`) — the body presumably specifies which mission/rooms to estimate. Caller supplies the body directly; shape not reverse-engineered. |
| `robot.reset_robot() -> dict` | 🟢 (method/URL), ⚠️ | Confirmed from the config file, but the name and `"write": true` strongly suggest a real, consequential reset — treat as destructive until proven otherwise. |
| `robot.get_notifications(app_version="1.0") -> dict` | 🟢 | Timeline/notification feed (`event_type=HKC`, meaning not decoded — taken verbatim from the config). |

---

## Mission history

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_mission_history(blid, *, max_reports=None, max_age=None, filter_type=None, exclusive_start_timestamp=None, supported_done_codes=None) -> dict` | 🟢 | Query params all confirmed from source, including the comma-join for `supported_done_codes`. The app's own default call uses `filter_type="omit_quickly_canceled_not_scheduled"` and `supported_done_codes=["dndEnd", "returnHomeEnd"]` (from `base_roomba_config.json`) — not required, but a reasonable default if you want to match the app's own behavior. |

---

## Teaming (multi-robot) — documented, not implemented

Confirmed to exist as a real REST surface from `base_roomba_config.json`, but not wrapped in this
library — needs a multi-robot household to test meaningfully, which no one working on this library
has had access to. Listed here so a future contributor doesn't have to rediscover them:

| commandId | Method | Path |
|---|---|---|
| `GetTeamingConfig` | GET | `/v1/{blid}/team` |
| `SendTeamingConfig` | POST | `/v1/{blid}/team/config` |
| `EnableTeamingConfig` | POST | `/v1/{blid}/team/config/enable` |
| `CancelTeamingConfig` | POST | `/v1/{blid}/team/cancel` |
| `DeleteTeamingConfig` | POST | `/v1/{blid}/team/delete` |
| `GetTeamingReport` | GET | `/v1/{blid}/team/report` |
| `GetNonCurrentRobotThingShadow` | GET | `/v1/{blid}/team/status` |
| `SendNonCurrentRobotMissionCommand` | POST | `/v1/{blid}/team/command` |
| `StartTeamingDemo` | POST | `/v1/{blid}/team/configDemo` |

## Account & app-UX surface — documented, not implemented

Also confirmed from `base_roomba_config.json`, but judged low-value for a home-automation
library (app-UX-specific, or one-time setup rather than ongoing device control) and skipped —
listed here so the decision is visible and reversible:

| commandId | Method | Path | Why skipped |
|---|---|---|---|
| `Associate` | POST | `/v1/user/associations/robots/{blid}?change_ownership=0` | One-time robot pairing, not ongoing control |
| `SetNotificationDelete` | DELETE | `/v1/user/timeline/events/{id}` | Notification housekeeping; `get_notifications()` alone covers reading |
| `SetNotificationState` | PUT | `/v1/user/timeline/events/{id}` | Same |
| `SetNotificationResponse` | POST | `/v1/user/timeline/events/{id}/response` | Same |
| `GetSurveyData` | GET | `/v1/insights/surveys/{blid}?event_id={id}` | App feedback/survey system, not device control |
| `SetSurveyResponse` | POST | `/v1/robots/{blid}/timeline/surveys/{id}/response` | Same |
| `GetMissionImageMetadata` | GET | (no path in config) | Dirt-detection photo review — camera-equipped robots only, complex workflow |
| `ApproveMissionImages` | POST | `/v1/{blid}/imageupload/approval?mssnN={n}&approvals={list}` | Same |

```python
from roombapy_prime.models import parse_mission_history

raw = await robot.get_mission_history(robot.blid, max_reports=10)
entries = parse_mission_history(raw)  # -> list[MissionHistoryEntry]
for e in entries:
    print(e.mission_id, e.done_code, e.duration_m, e.square_feet_covered)
```

`MissionHistoryEntry.done_code` is a `DoneCode` enum (19 values: `OK`,
`STUCK`, `BATTERY`, `USER_END`, ...) — falls back to the raw string if
the server ever returns a value this library doesn't know about yet, so
it won't crash on new codes. `MissionHistoryEntry.timeline` is a
`list[MissionTimelineEvent]` — all 20 possible sub-event types are
typed (`RoomEvent`, `ZoneEvent`, `TravelEvent`, `PlanEvent`, `ErrorEvent`,
and 15 more; see the model index below). Only the field matching the
event's own `event_type` string is set on any given `MissionTimelineEvent`
— the rest are `None`. The full, unaltered server response for each
mission remains available via `MissionHistoryEntry.raw`.

---

## Model index

Everything above covers the models you're likely to construct or read
directly. The rest of `models.py` (~85 classes total) breaks down as:

| Category | Examples | Where to look |
|---|---|---|
| V1 map-edit commands | `RenameRoomV1`, `SplitRoomV1`, `SetVirtualWallsV1`, ... | "Maps" above |
| V2 map-edit commands (dead code, kept for completeness) | `SetRoomMetadata`, `MergeRooms`, `SetFurniture`, ... | `edit_map_v2()`'s docstring |
| Map read-models (what's *in* a downloaded map bundle) | `RoomInfo`, `BorderInfo`, `HazardInfo`, `FurnitureInfoRead`, `TrajectoryInfo`, `CoverageInfo`, ... | `parse_map_bundle()` in "Maps" above |
| Geometry primitives | `Point`, `Polygon`, `MultiPolygon`, `LineString` | used throughout |
| Live map streaming | `PositionUpdateMessage`, `MapUpdateMessage`, `LiveMapStreamInit` | "Maps" above |
| Mission preference vocabulary | `CleaningMode`, `VacuumPowerLevel`, `LiquidAmountLevel`, `CleaningPasses`, `SoftwareScrub` | referenced by `CommandParams`/`CleaningProfile` |

If you need one of these, its docstring in `models.py` documents
exactly where the field names came from (source line or bytecode
inspection) and what, if anything, is still uncertain about it.

---

## Settings vocabulary

`base_roomba_config.json` lists 47 `namedShadow: "rw-settings"` commands total. As of the 32nd
session, a real `get_settings()` response (chairstacker) confirmed the actual field names for
most of the settings below — `models.py::RobotSettings.from_json()` now covers them. Apply it to
`response.payload["state"]["reported"]` (same nesting as `get_state()`).

| commandId (write-side, still unconfirmed) | Confirmed field on `RobotSettings` |
|---|---|
| `SetChildLock` | `child_lock` (wire: `childLock`) |
| `SetAudioVolumePattern` | `audio_volume` (wire: `audio.volume`) |
| `SetAutoEvacFrequency` | `autoevac_freq` (wire: `autoevacFreq`) |
| `SetRobotLanguageV2` | `languages_raw` (wire: `langs2` — left as raw dict, nested language-list structure) |
| `SetMapUploadAllowedCommand` | `map_upload_allowed` (wire: `mapUploadAllowed`) |
| `SetPadWashReturn` / `SetPadWashWetoutFrequency` / `SetPadDryDuration` | `pad_wash_return`/`pad_wash_area_interval`/`pad_wash_time_interval`/`pad_dry_duration`/`pad_dry_allowed`/`pad_wash_allowed` (wire: `pwReturn`/`pwAreaInterval`/`pwTimeInterval`/`padDryDur`/`padDryAllowed`/`padWashAllowed`) |
| — (no matching commandId found, present anyway) | `timezone`, `country`, `cloud_env`, `sched_hold`, `evac_allowed`, `name` (the robot's own name), `svc_deployment_id` |

Read-side confirmed via `CommandParams` reuse (same wire keys as mission commands):
`carpet_boost`, `eco_charge`, `no_auto_passes`, `scrub` (wire `swScrub`), `suction_level`,
`two_pass`, `vac_high`, `pad_wetness` (via `PadWetnessParam.from_json()`, now implemented).

**Still genuinely unconfirmed** — these commandIds exist in the config file, but no field matching
them showed up in the one real settings response seen so far (a single device won't necessarily
have every setting active, e.g. `SetDetergentCleaningSolution` only applies to detergent-capable
models):

| commandId | Likely purpose |
|---|---|
| `SetChargingLightRightPattern` | Dock/charging light pattern |
| `SetDisplayLight` | Robot display brightness/behavior |
| `SetDemoMode` | In-store demo mode |
| `SetBinTypeDetect` | Bin-type auto-detection toggle |
| `SetDetergentCleaningSolution` | Mopping detergent/solution setting |
| `PMapLearningAllowed` / `PMapContinuousLearningAllowed` | Map-learning permission toggles |
| `SetNavStrategyCommand` | Navigation strategy selection |
| `WifiDeviceLocalizationAllowed` / `BleDeviceLocalizationAllowed` | "Find my robot" via phone permission toggles |
| `TileScanModeAllowed` | Related to floor-tile-based navigation, unconfirmed |
| `SetAQIScale` | Air quality index scale (air-purifying models) |
| `SetAssetSetting` / `SetSmartHomeSettings` / `SetPrecheck` | Generic/catch-all setting buckets, purpose unclear from name alone |
| `ImgUpload` | Image upload permission/trigger |

None of these have a known JSON field name or value type — only the `commandId` string and the
fact that they route through `rw-settings` are confirmed. Implementing any of these means finding
the actual `desired`-state field name each one writes, which wasn't part of this pass.
