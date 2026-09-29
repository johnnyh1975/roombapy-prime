# API Reference

This is a navigational reference, not a replacement for the source —
every method and model has a more detailed docstring in the code
itself, including the exact evidence behind each field (Kotlin source
line, bytecode inspection, or "analogy assumption, unconfirmed"). This
document tells you *what exists and roughly how sure we are*; the
docstring tells you *why*.

Confidence shorthand used throughout. **Two different questions are
being answered here**, and conflating them is the easiest way to
misjudge this library:

*How was the shape derived?*

- 🟢 **Confirmed** — field names/types/methods read directly from
  decompiled source or bytecode, not guessed
- 🟡 **Plausible** — right shape on paper, derived by analogy
- 🔴 **Best-guess** — genuine uncertainty flagged in the docstring;
  treat as a starting point, not a fact

*Has it actually been run against hardware?*

- **confirmed live** — a real person watched a real robot and reported
  back. This is the strong claim, and it is written out rather than
  given a colour precisely so it cannot be skimmed past.
- **known broken** — run against hardware and it failed. More useful
  than untested, and worth more than a colour.
- Anything without either label has **not been run by anyone**, however
  green its derivation. A perfectly decompiled request can still be
  rejected by a server for reasons no amount of reading reveals — that
  has happened here more than once.

For the reasoning behind any individual entry, including the
conclusions that turned out wrong, see
[`internal/EVIDENCE_TRAIL.md`](internal/EVIDENCE_TRAIL.md). For the
per-write-path testing status, see
[`internal/WRITE_PATH_TEST_STATUS.md`](internal/WRITE_PATH_TEST_STATUS.md).

## Contents

- [Setup & connection](#setup--connection)
- [Live state](#live-state)
- [Mission control](#mission-control)
- [Favorites](#favorites)
- [Maps](#maps)
- [Schedules](#schedules)
- [Error text](#error-text)
- [Settings (DND, cleaning profiles, default routines, households)](#settings)
- [Mission history](#mission-history)
- [Classic robots (REST)](#classic-robots-rest)
- [Teaming (multi-robot) — documented, not implemented](#teaming-multi-robot--documented-not-implemented)
- [Account & app-UX surface — documented, not implemented](#account--app-ux-surface--documented-not-implemented)
- [Model index](#model-index)
- [Settings vocabulary](#settings-vocabulary)

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

### One account, every robot — `CloudAccount` (0.4.0)

Login belongs to the account, not to a robot. `CloudAccount` logs in once
and hands out what each robot on the account needs:

| Member | Notes |
|---|---|
| `await CloudAccount.login(session, username, password, country_code, *, app_id="roombapy-prime", request_timeout=30.0)` | One login. Raises the `Auth*` errors. `request_timeout` applies to every login this account makes and every client it hands out. |
| `account.robots -> dict[str, RobotLoginEntry]` | Every robot on the account, by BLID. |
| `account.generation(blid) -> "prime" \| "classic" \| None` | From the SKU. `None` means **unknown** — the SKU tables are incomplete — and is not guessed. `KeyError` for a BLID not on the account. |
| `account.rest(blid)` | `ClassicRestClient` or `PrimeRestClient` for that robot's generation; `ValueError` when it is unknown. `account.classic_rest()` / `account.prime_rest()` when you know better. |
| `await account.prime_robot(blid, *, auto_refresh=False)` | A `PrimeRobot` from this login, no second one. Refuses a Classic robot. |
| `await account.relogin()` | A fresh login now. |

Every REST client the account hands out relogs **through the account** on
HTTP 403: clients that hit it together share one login, and a client whose
credentials were already replaced takes the new ones without logging in.
`prime_robot()` still goes through `PrimeFactory`, which keeps its own
relogin for the MQTT token.

There is deliberately no robot class for both generations. It would carry
methods that fail at runtime for one of them.

```python
account = await CloudAccount.login(session, username, password, "DE")
for blid in account.robots:
    if account.generation(blid) == "classic":
        maps = await account.rest(blid).get_pmaps(blid)
```

---

## Live state

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_state(timeout=8.0) -> ShadowResponse` | 🟢 | The classic/unnamed shadow — identity, capabilities, current mission status. `models.parse_robot_status_v2(state.payload...)` can attempt to extract a structured `RobotStatusV2` (bytecode-confirmed fields) from the reported state, but this specific structure is confirmed to NOT appear here — battery/charging/dock status actually lives in the named shadow `"ro-currentstate"` instead, see `get_named_shadow()`'s own row below and `CurrentStateShadow`. |
| `robot.get_settings(timeout=8.0) -> ShadowResponse` | 🟢 | The named `"rw-settings"` shadow — only responds on SMART-tier devices, per binary analysis. |
| `robot.get_named_shadow(name, timeout=8.0) -> ShadowResponse` | 🟢 (method) / 🟢 (content, now confirmed) | General form of the above two. Nine named shadows are now fully confirmed live: `"rw-constatus"`/`"rw-schedule"`/`"rw-software"` (chairstacker) — see `ConnectionStatusShadow`/`ScheduleShadow`/`SoftwareStatusShadow` in `models/robot_info.py` — plus four previously-unknown read-only shadows found via `MQTTTopics.java`: `"ro-currentstate"`/`"ro-stats"`/`"ro-services"`/`"ro-configinfo"`. **`"ro-currentstate"` is where battery/charging status actually lives** — `batPct` (int, 0-100), charging state in `cleanMissionStatus.phase` (e.g. `"charge"`) — see `CurrentStateShadow`/`StatsShadow`/`ServicesShadow`/`ConfigInfoShadow`, all with real, live-confirmed structure, not placeholders. See `roombapy-prime-verify-named-shadows` in the README (recommend pairing with `--delay-seconds 2` for reliability). |
| `robot.set_setting(key, value, timeout=8.0) -> ShadowResponse` | 🟢 **confirmed live** (one setting end-to-end) | Writes into the `rw-settings` shadow. `childLock` is confirmed all the way through — the change appeared in the iRobot app and the robot announced it audibly. `ecoCharge`, `noAutoPasses` and `vacHigh` write and read back cleanly but have no easily observable effect, so their real-world behaviour is untested. **`schedHold` is accepted but does nothing**, and APK analysis now explains why: the field appears exactly once in iRobot's Prime app, with no serialisation annotation and no consumer across 3801 classes. It is inherited plumbing. Prime pauses schedules by setting `enabled` on each one instead. **One key, one value is the vendor's own write shape** (`RobotServiceHandler.updateSetting`), and dotted keys such as `padWetness.padPlate` are addressed directly — the read-modify-write advice this project carried described the older app. Notably, this project's cross-check against the classic shadow flagged that divergence before anyone looked in the app, which makes it a usable signal rather than a curiosity. |

| `robot.watch_state(named=None, *, queue_maxsize=100) -> AsyncIterator[ShadowResponse]` | 🟢 | Yields every shadow delta as it arrives, until the generator is closed/cancelled. Bounded queue, drops oldest on overflow (logged). Pass `named="rw-settings"` to watch that shadow instead of the default. |

```python
async for delta in robot.watch_state():
    print(delta.payload)
```

---

## Mission control

| Method | Confidence | Notes |
|---|---|---|
| `RoutineCommand.to_json(legacy_map_keys=False)` | 🟢 (default), 🔴 (the switch) | `CommandDTO` carries `p2map_id`/`user_p2mapv_id` **and** `pmap_id`/`user_pmapv_id` as four separate nullable fields, and the app decides per device via `allowLegacyReportedValuesInCommand`. We cannot evaluate that flag. **Off by default** because @Echovictor37's confirmed region clean did not carry the old name — a confirmed shape outranks a plausible one. Turn it on if a legacy-SKU robot ignores a region command; a robot receiving no map it recognises cleans the whole house rather than erroring. |
| `MissionCommandType.STARTDONOTDISTURB` / `.STOPDONOTDISTURB` | 🟡 (shape confirmed, never sent) | New in app 3.0.0 and usable through `send_simple_command()`. `BasicCommandBuilder` lists both in its supported types and emits `command`, `initiator` and `time` with every other field null — **no window parameter**. These switch Do Not Disturb on and off *ad hoc*; the period itself is set separately through the household settings call. Spelled camelCase here, unlike the lowercase multi-word commands: nobody has sent these, so there is no confirmed form to prefer over the vendor's. |
| `MissionCommandType.RESET` | 🟢 (path confirmed in app 3.0.0) | **Reboots the robot.** `device_restart_page` -> `ControlSettingsRepo.restartDevice` -> `MissionCommandType.reset` (index 7), sent on the same channel as `start` via `send_simple_command("reset")`. The robot drops offline for about a minute. Independently found in app 1.6.0 by samm-git/irobot-explore, whose binary carries the string "Sending reset command to reboot robot" -- 3.0.0 logs `Restart device failed` instead, same command. |
| `robot.send_simple_command(command: str, initiator="localApp") -> None` | 🟢 **confirmed live** | `"start"`/`"stop"`/`"pause"`/`"resume"`/`"dock"`/`"find"` — all six live-tested against real robots, watched and confirmed by real users actually reacting, not just an error-free response. Publishes `{"command", "time", "initiator"}` to a dedicated non-shadow MQTT topic (`{irbt_topic_prefix}/things/{blid}/cmd`) — see `mqtt_client.py`'s `cmd_topic()`/`publish_cmd()` for the full evidence trail. Fire-and-forget: no response wait, since there's no known server acknowledgment for this topic — poll `get_state()` afterward if you want confirmation. This is now the recommended way to do basic mission control AND locate ("find my robot") — `"find"` produces a genuine, audible chime with no robot movement (jayjay); two OTHER find-my-robot mechanisms (a REST endpoint, a shadow write) were tried first and confirmed **not working** — this is the one that actually works. |
| `robot.send_routine_command_via_cmd_topic(command: RoutineCommand) -> None` | 🟢 **confirmed live** | Region-aware cleaning — specific rooms, from a saved favorite or built from scratch. Confirmed on real hardware: the robot travelled to the named room and cleaned it. **Two requirements, neither obvious.** (1) `initiator` is **mandatory** — a stored favorite does not carry one, the app adds it at send time, and without it the command is delivered, acknowledged with a PUBACK, and silently ignored. (2) The wire keys are `command="start"` and `region_id` — not `"clean"` and `id`, which was an assumption in this project's own code until field data settled it. A map version is **not** required: the robot re-versions its map every few seconds while cleaning, so any stored `user_p2mapv_id` is stale within a minute, and confirmed-working commands carried versions hours out of date or none at all. Fire-and-forget like `send_simple_command()`; watch `mission/timeline/report` for the robot's own echo of what it received. See EVIDENCE_TRAIL.md for why this took three field sessions to establish — the first two appeared to disprove it, both confounded by sends that never reached the broker. |
| `robot.send_mission_command(command: RoutineCommand, timeout=8.0) -> ShadowResponse` | 🔴 **confirmed NOT working for basic commands** | The original approach (device shadow) — live-tested and found to time out with zero response for `start`/`stop`/etc. Kept only for the region-based use case above, which no source has verified either way. Do not use this for basic mission control — use `send_simple_command()` instead. |

**`RoutineCommand`** — the payload for the two `RoutineCommand`-based methods above. Key fields:

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

`CommandParams` (🟢 all 39 fields confirmed via `$$serializer.<clinit>` inspection — 18 were
found to be wrong camelCase guesses in an earlier pass based on a weaker DEX-field-list reading,
now corrected to the real wire keys) covers things like `suction_level`, `pad_wetness` (a
`PadWetnessParam`), `carpet_boost`, `room_confine`, `timebox_minutes`, and drive-command fields
(`velocity_left`/`velocity_right`). `Region` pairs a `RegionType` (`RID`/`ZID` for real,
persistent rooms/zones; `TID` for ad-hoc zones, which have their own extra ID-range and
paired-`CommandPolygon` requirements — see `RegionType.TID`'s docstring) with its own
`CommandParams`.

---

## Favorites

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_favorites() -> list[FavoriteV1]` | 🟢 | The only one of the five favorites endpoints with both HTTP method *and* response shape fully confirmed. |
| `robot.create_favorite(favorite: FavoriteV1) -> dict` | 🟢 | POST, confirmed from bytecode (`CreateFavoriteRequest.<init>`). Response key confirmed via bytecode (session 48): `favorite_id`. **CONFIRMED LIVE** (chairstacker) — genuinely creates a real, listable favorite. Caveat: a favorite created with empty `command_defs` was NOT visible in the real app's own UI, though it's real server-side — use `--delete FAVORITE_ID` (not the app) to clean one up in that case. `roombapy-prime-verify-favorite-write --create-and-delete-test` implements a self-cleaning test for this. |
| `robot.update_favorite(favorite_id, favorite: FavoriteV1) -> dict` | 🟢 | PUT, confirmed the same way. **CONFIRMED LIVE** (chairstacker), both an unchanged resend and a real, cosmetic-only `color` change. `roombapy-prime-verify-favorite-write` implements a staged test package (see README) — stage 1 resends an existing favorite unchanged, stage 2 changes only its `color`. |
| `robot.delete_favorite(favorite_id) -> dict` | 🟢 | DELETE, confirmed. |
| `robot.order_favorite(favorite_id, *, insert_at=None, insert_before=None, insert_after=None) -> dict` | 🟢 | PUT; the three params are query parameters, not body fields (a real bug was caught and fixed here — see gap analysis). |

**`FavoriteV1`** fields include `name`, `command_defs: list[RoutineCommand]`
(the steps the favorite runs), `color`, `icon`, `is_hidden`, and
`time_estimates`. All 🟢 confirmed from cleanly decompiled source.

---

## Maps

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_active_map_versions() -> list[dict]` | 🟢 | Confirmed field names from a real response (26th session): `p2map_id`, `active_p2mapv_id` (the map version), `name`, `state`, `visible`, `rooms_metadata`. For a typed result use `models/robot_info.py::parse_active_map_versions()` — includes per-room `operating_mode_defaults`, which reuse `CommandParams` directly. |
| `robot.get_map_metadata(p2map_id) -> P2MapData` | 🟢 | Now returns a parsed `P2MapData` (`p2map_id`, `active_p2mapv_id`, `name`, `visible`, `user_orientation_rad`, etc.), confirmed via bytecode — previously raw JSON. |
| `robot.delete_map(p2map_id) -> dict` | 🟢 | Despite the name, NOT an HTTP DELETE — confirmed from `DeleteMapRequest.java` to be a "soft delete" via the same settings endpoint as `set_map_name()`/`set_map_orientation()`: `POST /v1/p2maps/{p2mapId}/settings`, body `{"visible": false}`. |
| `robot.download_map_bundle(url) -> bytes` | 🟢 | Downloads the raw `tar.gz` map bundle from a presigned URL (see `get_map_geojson_link()` below). Deliberately sent **without** SigV4 signing — confirmed from `P2MapAPI.MapUnpacker`, since presigned URLs carry their own auth in the query string. For parsing, see `models/map_bundle.py::parse_map_bundle()`. |
| `robot.set_map_name(p2map_id, name) -> dict` | 🟢 | Body `{"name": ...}` — CORRECTED (session 51): was sending `{"type": name}`, a genuine bug, confirmed and fixed via bytecode (`EditMapSettingsRequest.Command.SetName`). |
| `robot.set_map_orientation(p2map_id, orientation_rad) -> dict` | 🟢 | Clamped to (-π, π]. Body `{"user_orientation_rad": ...}`, confirmed via bytecode. |
| `robot.edit_map(p2map_id, command: MapEditCommandV1) -> dict` | 🟢 **CONFIRMED LIVE** (chairstacker, August 2026): a room rename and its revert, both verified in the app, 7 checks OK. Until then this path had never once been exercised against a robot -- the changelog said *confirmed via bytecode, not yet wired into* anything. It was never broken, just never run. Note app 3.0.0 has moved map editing to MQTT (`editv3_req`/`editv3_resp`, nine operations, documented in `models/map_editing.py`); this REST path is a second way that still works, not a dead one. | **The actually-used path** (V1) — every room/zone/furniture/wall edit in the app goes through this. The outer envelope (`{"edit_cmd": {...}, "response_type": ...}`) and 8 of 9 commands' field names are now bytecode-confirmed (session 48) — several were wrong camelCase guesses, now corrected. `SetRoomMetadata`/the `VirtualWall` Linear/Rectangle/NoMopZone discriminator use hand-written custom serializers and remain unconfirmed. The discriminator value inside `edit_cmd` itself (`"type": "<CommandName>"`) is a plausible default assumption, not independently confirmed. |
| `robot.edit_map_v2(p2map_id, command: MapEditCommand) -> dict` | — | The app-side dead code path (confirmed never called by the app itself). Kept for completeness; prefer `edit_map()`. |
| `robot.edit_map_checked(...) -> MapEditResult` / `robot.edit_map_v2_checked(...)` | 🟡 | Same request, parsed answer. Both edit paths returned raw JSON on the grounds that the response was "not modellable" — that was true of V3's opaque `data.value` and applied to V1/V2 by mistake. Four response shapes are in the serialiser extract, and `MapEditResult` discriminates between them. **`is_partial` is the reason to use it**: a new map version with no URL means the edit applied and the rendered map did not follow — indistinguishable from success in raw JSON. `error` resolves `code` against `MapEditingError`'s thirteen names. `edit_map()` itself is unchanged, so a first real response can still be inspected whole via `.raw`. |
| `robot.get_map_region_names(map_id, map_version) -> dict[str, str]` | 🟢 | `{region_id: name}` for named rooms on a map, from `geojson_details.regions` on `GET /v1/p2maps/{id}/versions/{vid}`. **Confirmed live August 2026**: returns room names on current firmware. This library previously marked the endpoint as appearing in no app version checked — it lives in ARM64 native blocks, which a DEX-and-Dart search cannot reach, so that was a limit of the search rather than of the protocol. The confirmed output is **rooms**; whether the same document carries **zones** is untested, and where zone names have been chased down they came from the command's `region_name` field, not from the map at all. The bundle's `cleanZones` layer was long cited as the source; the code reading it had a typo that returned nothing on every bundle, so that citation rested on a search that never ran (fixed in b15). |
| `robot.get_map_raw_link(map_id, map_version, response_type="link") -> dict` | 🟡 | The same map version in the vendor's raw format rather than GeoJSON — `get_map_geojson_link()`'s sibling, identical URL with `/raw`. Found by diffing the app's `_MapFetcherServiceChannel` against this client: it lists `fetchMapRawData` beside `fetchMapGeoJson` and only one was implemented. Nobody has fetched one, so the contents are unmodelled. Worth knowing if a field turns out to be missing from the GeoJSON bundle — the SDK's own map model carries several the bundle does not. |
| `robot.get_live_map_stream() -> LiveMapStreamInit` | 🟢 | REST call that's actually a keep-alive ping, not a topic fetch — see `watch_live_map()`. |
| `robot.watch_live_map(*, queue_maxsize=100, keep_alive_interval=10.0) -> AsyncIterator[...]` | 🟢 (topic pattern), 🟡 (concatenation order) | Subscribes to the fixed livemap topic directly; the REST call above just keeps the stream alive in the background. |
| `robot.get_map_geojson_link(map_id, map_version) -> dict` | 🟢 | Fetches the presigned download URL for the map bundle below. Response key confirmed via bytecode (session 48): `map_url` — read directly from the native Kotlin `P2MapURL` class's serializer (not a Python model in this library, just the evidence source — a single field wasn't worth its own dataclass; use `result["map_url"]` directly). Previously entirely unconfirmed. |
| `robot._rest.download_map_bundle(url) -> bytes` + `models.parse_map_bundle(data) -> dict` | 🟢 (mechanism + content structure, session 47), 🟡 (manifest's own filename in the archive) | Deliberately unsigned GET — the app opens the pre-signed URL directly, no auth headers. Every content type (rooms, borders, hazards, etc.) is a confirmed GeoJSON Feature — see the map read-models table below. The bundle's own `BundleManifest` names the real filepath for every OTHER content type; only the manifest's own filename within the archive is still unconfirmed. |

`edit_map()` takes one of 9 V1 command dataclasses: `RenameRoomV1`,
`SplitRoomV1`, `MergeRoomsV1`, `SetRoomTypeV1`, `SetRoomMetadataV1`,
`SetPermanentAreasV1`, `DeletePermanentAreasV1`, `SetVirtualWallsV1`,
`AdjustFurnitureV1`.

**Not all nine behave the same.** `RenameRoomV1` is confirmed live
(twice, with a revert). `SetVirtualWallsV1` is **known broken**: reading
zones works and both zone types are confirmed against real data
(`1 = KeepOutZone`, `6 = NoMopZone`), but the write returns HTTP 500 and
the cause is not yet found. Two candidates have been ruled out by field
testing — a duplicated closing coordinate in the polygon, and the
request envelope's `response_type`. That it returns 500 rather than 400
suggests a body that parses and then fails downstream. The remaining
suspect is the discriminator inside `edit_cmd`, which this project has
flagged as unverified since it was written.

The other seven commands have never been sent by anyone.

---

## Schedules

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_schedules(household_id) -> SchedulesResponse` | 🟢 | Now returns a parsed `SchedulesResponse` (→ list of `SchedulesList` → list of schedules), confirmed via bytecode — previously raw JSON. |
| `robot.create_schedules(household_id, schedules: list[ScheduleOptions]) -> dict` | 🟢 | POST, confirmed from bytecode. Never called against a real server -- `roombapy-prime-verify-schedule-write` doesn't attempt this specifically (only `update_schedules()`, see below), since creating a brand-new schedule risks causing new, unexpected future activity, a worse risk direction than that script's own two stages. |
| `robot.update_schedules(household_id, household_schedule_id, schedules: list[HouseholdSchedule]) -> dict` | 🟢 | PUT, confirmed. **CONFIRMED WORKING LIVE** (chairstacker) — both an unchanged resend and a real `enabled=False` toggle genuinely took effect (the real app's own Automations screen doesn't always refresh in real time, but `get_schedules()` itself reflects the change immediately). `roombapy-prime-verify-schedule-write` implements the staged test package that confirmed this (see README) -- stage 1 resends an existing household's own schedules unchanged, stage 2 disables one specific schedule. |
| `robot.delete_schedule(household_id, household_schedule_id) -> dict` | 🟢 | DELETE. |

`ScheduleOptions` covers `frequency` (`ScheduleFrequency`: `ONCE`,
`WEEKLY`, `BI_WEEKLY`, `MONTHLY`), `start`/`end` (`ScheduleTime`),
`commands`/`end_commands` (🔴 assumed `list[RoutineCommand]` by strong
analogy to favorites, not generically confirmable from bytecode), and
`enabled`/`deleted`.

`household_id` isn't returned directly from login — try
`robot.get_user_households()` (below) or your account's app to find it.

---

## Raw accessors and watchers

Two groups that this reference previously omitted entirely, which made them look private. They are
not — they are the escape hatches, and a caller reaching for one should know what they are getting.

### `*_raw()` — the unparsed response

| Method | What it gives you |
|---|---|
| `robot.get_favorites_raw()` | favourites, before `_favorite_from_json()` |
| `robot.get_schedules_raw(household_id)` | schedules, before the container parse |
| `robot.get_dnd_settings_raw(household_id)` | Do Not Disturb settings |
| `robot.get_automations_raw()` | automations; empty on every account seen so far |
| `robot.get_clean_score_raw(p2map_id)` | per-room dirtiness, and per-room preferences |

**Raw means raw.** These return whatever the server sent, and the typed accessors beside them exist
because the parse step is easy to forget: a downstream consumer read 0 of 46 missions for three
releases by iterating raw dictionaries and asking them for attributes.

Use them when you need a field this library does not model yet — which, after the 3.0.0 comparison,
is a shorter list than it was.

### `watch_*()` — long-lived subscriptions

| Method | Notes |
|---|---|
| `robot.watch_named_shadows_updates(...)` | every named shadow's `update/accepted` traffic |
| `robot.watch_mission_timeline(...)` | mission events as they happen. Pair with `request_mission_timeline()` if you want one now rather than when the robot offers it |
| `robot.watch_rejected_commands(...)` | commands the robot refused — **unused by this project so far**, and the obvious next place to look when a command is accepted and does nothing |
| `robot.watch_dock_reports(report_type=None, ...)` | the `dock/{reportType}/report` family. `dock/paddry/report` is confirmed live; with no argument this subscribes the whole family via a `+` wildcard, which is the only way to find out whether a `reportType` other than `paddry` exists. A `charge` or `battery` sibling would be the real find |
| `robot.watch_raw_topic(topic, ...)` | anything else, including wildcards |

A watcher that raises used to take down the whole MQTT client: a callback exception kills paho's
network loop thread, and the connection then looks alive while delivering nothing. That is guarded
now, but a watcher is still the wrong place to do slow work.

`robot.trigger_echo_via_shadow()` is **disproven as a locate mechanism** — writing `echo` to
`rw-constatus` does not make the robot chime (confirmed on real hardware). It is kept because it
does confirm that an arbitrary named-shadow write reaches the robot, which is a useful thing to be
able to test. Use `send_simple_command("find")` to actually locate a robot.

`robot.send_umi_get_request(...)` sits with these — a lower-level request path kept for the cases
the typed calls do not cover.

**The models are not listed individually here either.** There are 163 dataclasses, most of them
nested inside the ones this reference names — shadow blocks, capability flags, per-event payloads.
A table of all of them would go stale faster than it could be read, and their field docstrings carry
things a table cannot: which values are field-confirmed, which came from the vendor's app, and which
were guesses that turned out wrong. `dataclasses.fields()` and the source are the reference.

**`PrimeMqttClient` is deliberately not documented here.** It is the transport layer, and its
methods (`subscribe`, `update_shadow`, `shadow_topic`, `replace_token` and the rest) are reachable
but not part of the supported surface — read `mqtt_client.py` directly if you need them, and expect
its docstrings to be more current than any table.

## Two rules the parsers follow

**A `from_json` returns an empty instance rather than raising** when handed something that is not a
mapping — a truncated download, a server error body, a `None` where an object was expected. A
parser that raises turns a bad response into a crash in the caller's own code.

The exception is the GeoJSON map features, which have required fields and cannot construct an empty
instance. Those raise, which is the honest answer: a feature with no id and no geometry is not an
empty feature.

**A command refuses to travel under-addressed.** `Region.to_json()` raises when the region id is
empty — which happens when the server sends `null` and a `.get(key, "")` default fills in a blank.
The result would be a command that names a room and does not, and a robot given a command it cannot
target does something other than what was asked rather than erroring.

## Error text

`CleanMissionStatus.error` and `DockStatus.error` are integers. Both now carry `error_text` beside
them:

```python
status = state.clean_mission_status
status.error        # 46
status.error_text   # {"title": "Battery too low to clean", "content": "..."}
```

**Until v0.3.0b1 this library had no error table at all.** Codes went through as integers, so
`verify_region_commands` printed `ERROR value=46` and left the reader to look it up — which meant
asking the maintainers.

`vendor_error(code, language="en")` exposes the same catalogue directly: 112 codes with iRobot's own
title and explanation, in eight languages, taken from app 3.0.0's locale files. It returns `None`
for a code iRobot does not document, and that distinction is worth keeping — a caller can then say
"error 236, undocumented" rather than "no error".

`@val` is the robot's name in iRobot's own strings and is left in place, so a caller that knows the
name substitutes it. Four broken placeholders in the vendor's own text — one using a different form
in English, three run into the following word in Spanish and Polish — are repaired on the way out.

## Settings

| Method | Confidence | Notes |
|---|---|---|
| `robot.get_user_households() -> dict` | 🔴 | Implemented despite being dead code in the current app version — the endpoint likely still exists server-side even though nothing in the app calls it. HTTP method is REST convention, not confirmed from a request class like everything else here. Response entries can be parsed further via `HouseholdSetting.from_json()`/`HouseholdSettingOptions.from_json()` (the latter — household demographic info, adult/kid/pet counts — confirmed via bytecode, session 48). |
| `robot.get_household_id() -> str \| None` | 🟢 | Convenience wrapper around `get_user_households()` — finds the household_id of the household containing THIS robot (matched by `blid`), handling both possible response shapes (a single household dict per that method's own confirmed docstring, or a list per `parse_user_households()`'s own type hint — never reconciled against a real multi-household account). Returns `None` if no match, never raises for a simple "not found". |
| `robot.get_dnd_settings(household_id) -> DNDStatusResponse` | 🟢 | Now returns a parsed `DNDStatusResponse` directly (previously raw JSON despite the model existing since the ninth session). |
| `robot.set_dnd_settings(household_id, settings: dict) -> dict` | 🟢 (method), 🔴 (body shape) | |
| `robot.get_cleaning_profiles(asset_id, p2map_id=None) -> dict` | 🟢 (query params, session 38), 🔴 (response envelope) | Query params corrected via direct bytecode read: `robotId`/`includeSmart`/`p2map_id` (not the previously-guessed `asset_id`/`p2map_id`) — `p2map_id` now optional, matching real branching logic. Response envelope itself still unconfirmed (only the per-entry `CleaningProfile.from_json()` shape is) — `DEEP`/`LIGHT`/`NORMAL`/`SMART`, each with its own `CommandParams`. |
| `robot.get_default_routines(p2map_id) -> RoutinesDefaultsResponse` | 🟢 | Auto-generated per-map cleaning suggestions. Now returns a parsed `RoutinesDefaultsResponse` (also captures `routine_builder_defaults`, previously not exposed at all), confirmed via bytecode. |
| `robot.get_robot_parts() -> RobotPartsInfo` | 🟢 | Consumable part status (filter/brush/battery wear, unconfirmed which). Confirmed from `res/raw/base_roomba_config.json` (a primary-source config file bundled in the APK), not decompiled logic — see `docs/internal/base_roomba_config_REFERENCE.json`. Now returns a parsed `RobotPartsInfo` directly. |
| `robot.reset_robot_parts(part_ids=None, counters=None) -> dict` | 🟢 (method), 🟡 (body now known, never sent) | Resets a part's wear counter. **The body is two nested shapes, and the first fix supplied only the outer one** — `AssetHealthResetDto` declares `robot_id`, `num_parts` and `parts`, and `AssetPartResetDto` declares what belongs IN that list: `part_id` AND `counter`. A list of bare id strings is neither a rejection nor a reset. `counter` defaults to 0, which is an inference — the model names the field and does not say what a reset writes. Omitting `part_ids` still sends just `robot_id`, which may or may not mean "everything". |
| `robot.get_serial_number_data() -> RobotSerialInfo` | 🟢 | Confirmed structure (26th session): serial number, user-assigned robot name, `family` (e.g. `"Roomba Combo"`), `series`. Now returns a parsed `RobotSerialInfo` directly. |
| `robot.poll_echo_value() -> dict` | 🟢 (method), ❌ (does not work) | **Not the locate mechanism, despite the name.** Field-disproven on a real device: the call succeeds and the robot does not chime. `trigger_echo_via_shadow()` was tried second and also disproven. The working locate is `send_simple_command("find")`. Kept as historical record; the unknown body belongs to an endpoint that demonstrably does not do what it is named for. |
| `robot.get_time_estimates(smart_map_id=None, region_id=None, zone_id=None) -> dict` | 🟢 (method/URL), 🟢 (body) | `POST` despite being read-only in the config (`"read": true`). `TimeEstimatesRequestBody` declares `robot_id`, `smart_map_id`, `region_id` and `zone_id`; sending only the first asks for every estimate on every map, which is the shape confirmed on two accounts and stays the default. The three narrowing fields are optional here for the same reason they are nullable there. |
| `robot.reset_robot(robot_password=None, synchronous=None, send_wipe=None) -> dict` | 🟢 (method/URL), ⚠️ | Confirmed from the config file, but the name and `"write": true` strongly suggest a real, consequential reset — treat as destructive until proven otherwise. **`ResetRequest$Body` declares `robot_password`, `synchronous` and `send_wipe`**, and this sent no body at all — leaving the one field that decides how destructive a reset is to a server default nobody here knows. Default behaviour is unchanged (no arguments still sends no body); what is new is that a caller can be explicit, and `send_wipe=False` is the conservative choice. |
| `robot.get_notifications(app_version="2.2.4") -> dict` | 🟢 | Timeline/notification feed (`event_type=HKC`, meaning not decoded — taken verbatim from the config). `app_version` default CORRECTED (session 36) from the previous, evidence-free `"1.0"` placeholder to `"2.2.4"` (the app's own confirmed `BuildConfig.VERSION_NAME`) — the likely cause of an earlier live HTTP 400. |

---

## Mission history

| Method | Confidence | Notes |
|---|---|---|
| `robot.request_mission_timeline() -> int` | 🟡 (shape confirmed from source, never sent) | Asks the robot to publish its mission timeline now, rather than waiting for it to volunteer one. Publishes `{"timelineRequestId": <n>}` to `{irbt_prefix}/things/{blid}/mission/timeline/request`; the report arrives on the matching `report` topic carrying the same id, so a caller with a watcher running can match the answer to its question. The counter starts at 1 and increments, as the app's does — reusing an id would make two requests indistinguishable, which is the one thing the field exists to prevent. |
| `robot.get_firmware_raw(sku, software_ver, track=None, dock_fw_ver=None, dock_fw_ver_sec=None, dock_hw_rev=None) -> Any` | 🟢 (confirmed live) | Available firmware releases from `GET https://content-prod.iot.irobotapi.com/v2/firmware`, **with no authentication**. An earlier version called this against the SigV4 gateway and got a 403, which was recorded as a permission problem; it was the wrong host. `FirmwareRequest` in app 3.0.0 declares six parameters -- `sku` and `softwareVer` required, the other four sent only when set. `softwareVer` must be URL-encoded: an unencoded `+` becomes a space and the lookup silently misses. **Returns raw**: nothing describes the response envelope, and modelling one nobody has seen is how this library got a `time_estimates` shape it had to replace wholesale. |
| `robot.get_firmware(sku=None) -> list[FirmwareItem]` | 🟢 (confirmed live) | The same catalogue, parsed. The envelope is `{"firmware": [item, ...]}`, confirmed against a real response (SKU W155040). Returns the items; an **empty list means the catalogue had nothing for this sku**, not an error -- a Classic-generation sku returns exactly that, which is how the catalogue says it does not carry one. `FirmwareItem.fused` is an `int` (an eFuse level, observed as `3`), corrected from the `bool` the serializer-derived model had. |
| `robot.get_map_region_ids(map_id, map_version) -> list[str]` | 🟢 | Every region id the CURRENT map version carries, named or not. The p2map's own `rooms_metadata` is a snapshot and can lag zone edits -- a tester with twelve zones saw eight from it. Use this when the question is "which regions exist"; use `get_map_region_names()` when the question is what they are called. |
| `robot.get_mission_history(blid, *, max_reports=None, max_age=None, filter_type=None, exclusive_start_timestamp=None, supported_done_codes=None) -> Any` | 🟢 | **Returns a list of records**, not an object (annotated `dict` until 0.4.0; the Classic capture shows the list). Classic robots are asked with other parameter names — see [Classic robots (REST)](#classic-robots-rest). Query params all confirmed from source, including the comma-join for `supported_done_codes`. The app's own default call uses `filter_type="omit_quickly_canceled_not_scheduled"` and `supported_done_codes=["dndEnd", "returnHomeEnd"]` (from `base_roomba_config.json`) — not required, but a reasonable default if you want to match the app's own behavior. |

---

## Classic robots (REST)

The cloud REST side serves **both generations**, through three classes in
`roombapy_prime.rest_client` (all exported from the package):

| Class | What it is |
|---|---|
| `CloudRestClient` | The base: SigV4 signing, request handling, one relogin on HTTP 403, and the calls both generations make identically — `get_robot_parts(blid)` / `get_robot_parts_raw(blid)`, `get_favorites_raw(app_edition=…)`, `get_automations_raw()`. |
| `PrimeRestClient` | Prime/V4: everything above this section. Unchanged since 0.3.x — constructor, methods, import path. |
| `ClassicRestClient` | Classic (900 series, i/s/j series): the calls below. New in 0.4.0. |

Get them from a [`CloudAccount`](#one-account-every-robot--cloudaccount-040);
constructing one directly takes `(session, http_base_auth, credentials,
relogin=None, *, request_timeout=30.0)`, plus `app_id=None` for
`ClassicRestClient`. Classic robots
are **not** reached through `PrimeRobot` — controlling one stays local
(roombapy); only its cloud calls live here.

`ClassicRestClient` carries the calls ha_roomba_plus made for Classic robots
through its own `cloud_api.py` up to 4.2.12, moved **unchanged**. The tests
pin that literally: same query in the same order, same body bytes, same SigV4
signature as 4.2.12 sent (`tests/test_rest_client.py`, "Classic parity").

| Method | Confidence | Notes |
|---|---|---|
| `get_pmaps(blid) -> list[dict]` | 🟢 (confirmed on Classic) | `GET /v1/{blid}/pmaps?visible=true&activeDetails=2`. Classic's map system; Prime uses `p2maps`. Non-list answers come back as `[]`. |
| `get_pmap_umf(blid, pmap_id, version_id) -> dict` | 🟢 (confirmed on Classic) | `…/pmaps/{pmap_id}/versions/{version_id}/umf?activeDetails=2` — room polygons. **Raises `RestError` for anything but a non-empty object**, empty body included: an empty map would be drawn blank instead of reported unavailable. |
| `set_robot_part_counter(blid, part_id, counter) -> dict` | 🟢 (confirmed on Classic, `counter=0`) | Body `{"parts":[{"part_id":…,"counter":…}]}`, sent compact. `counter` is **percent used**; `0` means new, and `0` is the only value ever written in the field. The Prime client's `reset_robot_parts()` sends a different, unmeasured body. |
| `get_mission_history(blid, *, app_id=None, filter_type=…, supported_done_codes=…, count=100, before=None) -> Any` | 🟢 (confirmed on Classic) | Classic parameter names, in Classic's order. **The defaults are the Classic app's** — `omit_quickly_canceled_not_scheduled`, `dndEnd,returnHomeEnd`, 100 records, and the client's `app_id` — so `get_mission_history(blid)` is the request ha_roomba_plus sent. `None` leaves a key out. Returns a **list**. **`count` and `before` are ignored by the server** (measured on a 980 and an i7, below) — to page, use `get_mission_history_page()`. |
| `get_mission_history_page(blid, *, before=None, page_size=100, filter_type=…, supported_done_codes=…) -> Any` | 🟢 (confirmed on Classic, page size 10) | **New in 0.4.0b3.** The parameters that page on Classic robots (`PrimeRestClient.get_mission_history()`'s names): `page_size` → `maxReports`, `before` → `exclusiveStartTimestamp` (only missions that started earlier). No `app_id`. Same keys, order and signature as `PrimeRestClient.get_mission_history()` with the same values. A page size other than 10 is not measured; treat an error as "no further pages". Returns a **list**. |
| `get_favorites() -> list[dict]` | 🟢 (confirmed on Classic) | Only favourites: an answer without a list is `[]`, not one favourite made of the whole answer. `get_favorites_raw()` (default: no `app_edition`) shows what the server sent. |
| `get_automations() -> dict` | 🟢 (confirmed on Classic) | An object, or `{}`. The raw answer is `get_automations_raw()`. |
| `get_robot_parts_raw(blid)`, `get_robot_parts(blid)` | 🟢 (confirmed on Classic) | From the base. Raw keeps every key; the typed one parses the Classic capture completely. |

**Whether the Prime forms also work on Classic** — Prime parameter names
for mission history, `app_edition=1`, the Prime part-counter body — is what
`roombapy-prime-verify-classic-cloud` measures (tools package).

**First measurement, Roomba 980 (R980040), 26 Sep 2026:**

| Call | Classic form | Prime form |
|---|---|---|
| Mission history, page size | `count` **ignored**: 10 asked, all 33 missions returned | `maxReports` honoured: 10 asked, 10 returned |
| Mission history, paging | `before` **ignored**: the same 33 missions again | `exclusiveStartTimestamp` works: the next 10, all older, none repeated |
| Mission history, fields | — | the same fields as the Classic answer |
| Favorites | the account's favourite | **nothing** with `app_edition=1` — the Classic form stays |
| Part counters | parts 30, 31, 32 at `-1`, no minutes: the cloud does not track a 980's consumables | — |

So on this robot the Classic history parameters do nothing and the Prime
ones do what they say.

**Second measurement, Roomba i7 (i755840), 27 Sep 2026:**

| Call | Classic form | Prime form |
|---|---|---|
| Mission history, page size | `count` **ignored**: 10 asked, all 67 missions returned | `maxReports` honoured: 10 asked, 10 returned |
| Mission history, paging | `before` **ignored**: the same 67 missions again | `exclusiveStartTimestamp` works: the next 10, all older, none repeated |
| Mission history, fields | — | the same 31 fields as the Classic answer |
| Favorites | the account's 7 favourites | **nothing** with `app_edition=1` — the Classic form stays |
| Part counters | parts 6, 7, 8, 139, all tracked | — |

Two Classic generations, the oldest and an i-series, agree, so paging
now uses `maxReports`/`exclusiveStartTimestamp`
(`get_mission_history_page()`, 0.4.0b3).

"Prime form" in these tables means what `PrimeRestClient` sends. The
names come from `FetchMissionHistoryRequest`. The Roomba Home app 3.0.0
itself does not use them: its `MissionHistoryRequest` sends `app_id`,
`maxAge=10`, `filterType` and `supportedDoneCodes`, and does not page
(APK analysis, 27 Sep 2026). What the server does with the paging
parameters is what the two measurements show.
`get_mission_history()` keeps the Classic form: it is the request
ha_roomba_plus has always made for its regular refresh. The part-counter
body is still unmeasured on Classic: nobody has had a part to reset.

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

# THIS RETURNS RAW JSON. Conversion is a separate step, and forgetting it
# is not loud: `parse_mission_history()` produces typed entries, while the
# raw dicts silently answer None to every attribute a consumer asks for.
# A downstream importer read 0 of 46 missions this way for three releases.
raw = await robot.get_mission_history(robot.blid, max_reports=10)
entries = parse_mission_history(raw)
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

## Errors

Every exception below is exported from `roombapy_prime` directly. Three
hierarchies, one per transport, and since 0.4.0 one base above them:
**`CloudError`**. `except CloudError` catches anything from iRobot's cloud
— login, REST, MQTT. The three stay apart: a `RestError` is never an
`AuthError`, and every class kept its name and its subclasses.

**Every failure is one of these** since 0.4.0. Until then a login stage or
a REST call caught three aiohttp errors and let the rest escape: a
connection dropped mid-answer came through as `aiohttp.ServerDisconnectedError`,
a timeout as a bare `TimeoutError`, a login answer without an expected key
as a `KeyError`. Now every aiohttp error, every timeout and every
malformed login answer is mapped.

**Every HTTP request has a time limit**, 30 seconds by default, from
sending to having read the whole answer. `login()`, `CloudAccount.login()`
and every REST client take `request_timeout=` (`None` for no limit). A
403 relogin runs outside the limit; the retried request gets its own.

### Why — `reason` (0.4.0)

The class says which part failed. It does not say what to tell a person:
`AuthSSLError` alone covers a machine that cannot check certificates
(waiting will not help), an expired certificate on iRobot's side (waiting
will) and a failure that cannot be told apart. So every `CloudError`
carries **`reason`**, a member of `CloudErrorReason` — a `StrEnum`, so it
is also the plain string shown in the table.

The library does not translate. `reason` is the key an application
translates from; the English message stays, for logs. Anything a
translation needs rides along as an attribute, never inside the message:
`status` on REST errors, `retry_after` on `RestRateLimitedError`.

| `reason` | Meaning | Raised as |
|---|---|---|
| `connection_failed` | No connection at all: DNS, refused, unreachable | `AuthConnectionError`, `RestConnectionError`, `ShadowConnectionError`, `ShadowError` |
| `connection_broken` | Connected, then broke off before the answer was complete | `AuthConnectionError`, `RestConnectionError` |
| `timeout` | Sent, no complete answer in time | `AuthTimeoutError`, `RestTimeoutError`, `ShadowError` |
| `ssl_local_trust_store` | This machine cannot check iRobot's certificate — local setup, waiting will not fix it | `AuthSSLError`, `RestSSLError`, `ShadowSSLError` |
| `ssl_certificate_expired` | iRobot's certificate has expired — their side, usually fixed within hours | the three SSL errors |
| `ssl_unverified` | Not verified, cause unknown | the three SSL errors |
| `credentials_rejected` | Wrong username or password, or the login refused | `AuthCredentialsError` |
| `account_locked` | Locked after too many attempts; clears by itself, re-entering the password extends it | `AuthCredentialsError` |
| `too_many_sessions` | Too many active app sessions | `AuthRateLimitedError` |
| `no_robots` | The account has no robots | `AuthError` from `primary_blid()` |
| `robot_ambiguous` | Several robots and none named | `AuthError` from `primary_blid()` |
| `request_refused` | HTTP 4xx other than 429. `status` | `RestClientError`, `AuthError` |
| `rate_limited` | HTTP 429. `retry_after` on REST | `RestRateLimitedError`, `AuthError` |
| `server_error` | HTTP 5xx. `status` | `RestServerError`, `AuthError` |
| `response_malformed` | An answer arrived in a shape the call does not expect | `AuthError`, `RestError` |
| `not_connected` | An MQTT operation before `connect()` — the caller's mistake | `ShadowError` |
| `connect_refused` | The broker refused the MQTT connection after TLS succeeded | `ShadowError` |
| `publish_not_delivered` | A message refused locally or never confirmed sent | `ShadowError` |
| `subscription_not_sent` | A subscription failed before it reached the broker | `SubscriptionRejectedError` |
| `subscription_rejected` | The broker's policy denied a subscription | `SubscriptionRejectedError` |
| `shadow_rejected` | The device shadow refused a read or a write | `ShadowError` |
| `unknown` | Never raised by this library — what an error built elsewhere without a reason carries | — |

Two pairs share a class and need opposite advice, which is why `reason`
exists and not only the class: an account lockout is an
`AuthCredentialsError` (so Home Assistant stops retrying), but a person
told "credentials rejected" re-enters the password and extends the
lockout. `SubscriptionRejectedError` covers both a broker that refused and
a request that never reached it.

**The names are interface.** One is added, never renamed — a translation
keyed on it would break. A test holds every raise in the library to a
named reason, and `unknown` to never appearing.

```python
from roombapy_prime import CloudError, CloudErrorReason

try:
    await account.login()
except CloudError as err:
    key = f"cloud_{err.reason}"          # e.g. "cloud_account_locked"
    if err.reason is CloudErrorReason.RATE_LIMITED:
        wait = getattr(err, "retry_after", None)
```

`reason_for_status(status)` gives the reason for an HTTP status, the same
way for login and REST.

### Authentication — `AuthError`

| Exception | Meaning | Retry? |
|---|---|---|
| `AuthCredentialsError` | The login was rejected: wrong username or password | **No.** Retrying sends the same rejected credentials |
| `AuthRateLimitedError` | iRobot rejected the login for too many active app sessions | Later, and not immediately |
| `AuthTimeoutError` | Sent, no response in time | Yes |
| `AuthSSLError` | TLS or certificate verification failed | Only after investigating — a certificate failure is not transient |
| `AuthConnectionError` | No connection established at all (DNS, refused), or it broke off mid-answer | Yes |

The distinction that matters in practice is the first row against the
rest. A credentials failure will never succeed on retry; everything
else might.

### REST — `RestError`

| Exception | Meaning | Retry? |
|---|---|---|
| `RestConnectionError` | No connection, or it broke off mid-answer | Yes |
| `RestTimeoutError` | No complete answer within `request_timeout` | Yes |
| `RestSSLError` | TLS or certificate verification failed | Only after investigating |
| `RestHTTPError` | The server answered with an error status; `status` is set. Base of the three below (0.4.0) | — |
| `RestClientError` | 4xx other than 429: the request itself was refused (not found, not allowed, malformed). A 403 arrives here only after the one relogin has not cured it | **No**, not unchanged |
| `RestRateLimitedError` | 429. `retry_after` is the wait the server asked for, in seconds, or `None` | After `retry_after` |
| `RestServerError` | 5xx: a problem on iRobot's side | Later |

A plain `RestError` that is none of these means the answer arrived and was
not what the call expects — not JSON, or not the shape it documents
(`reason` `response_malformed`). One built elsewhere with an error `status`
takes its `reason` from the status.

**A certificate failure is diagnosed the same way as at login** since
0.4.0. REST and MQTT used to say "almost always a temporary problem on
iRobot's servers … not something wrong with your setup" for every
certificate failure — wrong for a machine without a usable trust store,
where waiting fixes nothing. Login had stopped saying it after a field
report.

### Shadow/MQTT — `ShadowError`

`ShadowConnectionError`, `ShadowSSLError`, and `SubscriptionRejectedError`
(a subscription the broker refused, or one that never reached it). A plain
`ShadowError` covers several causes and always says which in `reason`.
Since 0.4.0 the one path that raised a builtin `ConnectionError` raises a
`ShadowError` too.

### Catching them

```python
from roombapy_prime import (
    AuthCredentialsError,
    AuthError,
    AuthRateLimitedError,
)

try:
    result = await factory.login(username, password)
except AuthCredentialsError:
    # Ask the user for new credentials. Do not retry.
    raise
except AuthRateLimitedError:
    # Too many app sessions. Wait, then try again.
    await asyncio.sleep(60)
except AuthError:
    # Transport: timeout, TLS, connection. Usually worth one retry.
    ...
```

Ordering matters: `AuthError` is the base, so it must come last or it
swallows the specific cases. The same holds one level up — put
`except CloudError` after everything more specific.

## Identifier helpers

BLIDs and map ids arrive from several sources with inconsistent
formatting. Three functions, all exported from `roombapy_prime`:

| Function | Returns |
|---|---|
| `is_valid_id(value)` | `True` if the string is a well-formed identifier |
| `normalise_id(value)` | The canonical form, or raises on an unusable value |
| `id_problem(value)` | A description of what is wrong, or `None` |

`id_problem()` exists so a caller can tell a user *why* an id was
rejected instead of only that it was.

## Model index

Everything above covers the models you're likely to construct or read
directly. `models.py` was split into a `models/` package (session 55)
for navigability — `from roombapy_prime.models import X` still works
exactly the same either way, since `roombapy_prime/models/__init__.py`
re-exports everything. The package (~150 classes total, across ten
files organized by feature area — `geometry`, `mission_control`,
`map_bundle`, `map_editing`, `favorites`, `schedules_dnd`,
`mission_history`, `robot_info`, `livemap`, `enums_common`) breaks
down as:

| Category | Examples | Where to look |
|---|---|---|
| V1 map-edit commands | `RenameRoomV1`, `SplitRoomV1`, `SetVirtualWallsV1`, ... | "Maps" above |
| V2 map-edit commands (dead code, kept for completeness) | `SetRoomMetadata`, `MergeRooms`, `SetFurniture`, ... | `edit_map_v2()`'s docstring |
| Map bundle read-models (what's *in* a downloaded map bundle) — REBUILT session 47, renamed | `RoomFeature`, `BorderFeature`, `HazardFeature`, `FurnitureFeature`, `TrajectoryFeature`, `CoverageFeature`, `PolicyZoneFeature`, `CleanZoneFeature`, `AdHocCleanZoneFeature`, `FloorPlanFeature`, `FloorTypeFeature`, `BundleManifest` | `parse_map_bundle()` in "Maps" above |
| Geometry primitives | `Point`, `Polygon`, `MultiPolygon`, `LineString` | used throughout |
| Live map streaming | `PositionUpdateMessage`, `MapUpdateMessage`, `LiveMapStreamInit` | "Maps" above |
| Mission preference vocabulary | `CleaningMode`, `VacuumPowerLevel`, `LiquidAmountLevel`, `CleaningPasses`, `SoftwareScrub` | referenced by `CommandParams`/`CleaningProfile` |
| Structured robot status (older, unresolved model) | `RobotStatusV2`, `DockControl`, `RobotStatusButton`, `RobotStatusError` | `get_state()`'s docstring — confirmed NOT to appear in `get_state()`'s own response. The underlying question (where battery/charging/dock status actually lives) IS resolved — see the next row. |
| Named shadow content — battery/charging/dock status, connectivity, schedule, software, hardware info (all live-confirmed, real data) | `CurrentStateShadow`, `StatsShadow`, `ServicesShadow`, `ConfigInfoShadow`, `ConnectionStatusShadow`, `ScheduleShadow`, `SoftwareStatusShadow` | `get_named_shadow()` in "Live state" above — the actual, confirmed answer to the battery-status search |
| Default routines | `RoutinesDefaultsResponse`, `RoutineBuilderDefaults`, `RegionDefaults`, `OperatingModeProfile` | `get_default_routines()` above |
| Login-response models | `RobotLoginEntry`, `RobotCapabilities`, `RobotDigitalCapabilities` | `auth.py`'s `LoginResult.robots` |

If you need one of these, its docstring in `models/` (whichever
submodule it lives in — an IDE "go to definition" gets you there
regardless) documents exactly where the field names came from (source
line or bytecode inspection) and what, if anything, is still uncertain
about it.

---

## Settings vocabulary

`base_roomba_config.json` lists 47 `namedShadow: "rw-settings"` commands total. As of the 32nd
session, a real `get_settings()` response (chairstacker) confirmed the actual field names for
most of the settings below — `models/robot_info.py::RobotSettings.from_json()` now covers them. Apply it to
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
