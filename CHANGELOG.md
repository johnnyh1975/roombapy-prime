# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
For the detailed, session-by-session reverse-engineering trail behind
any of this (what was tried, what's still uncertain, why), see
[`docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md).
This file only tracks what changed from a user's point of view.

## [Unreleased]

## [0.1.11a14] - 2026-07-21

### Fixed

- **`get_shadow()`/`update_shadow()` now reconnect first if the connection is known to be
  down**, prompted by a real field report of shadow GETs failing in a specific pattern (some
  succeed, every one after that fails, the exact number varying between runs) and a matching,
  documented AWS IoT MQTT SDK behavior (see
  [aws/aws-iot-device-sdk-js-v2#117](https://github.com/aws/aws-iot-device-sdk-js-v2/issues/117)
  — a field report there, on an unrelated project, describes this exact symptom for shadow
  topics after a connection drop). Callers doing a plain sequential series of shadow queries with
  no reconnect logic of their own (e.g. `verify_named_shadows.py`, unlike `watch_state()`'s own
  hardened reconnect loop) previously had no way to recover from a single silent mid-run
  disconnect — every subsequent call would keep trying to subscribe/publish on a dead
  connection and time out. Cheap when already connected — only pays the reconnect cost when
  actually needed.
- **`verify_named_shadows.py` gained a `--delay-seconds` option**, prompted by the same field
  report — genuinely unresolved which factor(s) contribute how much, so this is offered as a
  cheap, no-downside option to try, not a confirmed fix on its own.

## [0.1.11a13] - 2026-07-21

### Added

- **`CurrentStateShadow` rebuilt with real, live-confirmed values** (chairstacker) — battery at
  72%, robot idle/charging. Most fields turned out to be nested objects, not flat values: new
  `BinStatus`/`CleanMissionStatus`/`DockStatus`/`DockCapabilities`/`RuntimeStatsSummary`/
  `P2MapRef` classes. Charging state lives in `clean_mission_status.phase` (`"charge"`), not a
  separate boolean. `dock.state`/`pw_state`/`pd_state` (301/601/701) plausibly align with
  `DockState`'s four subsystem categories — a pattern worth watching, not a confirmed mapping.

### Fixed

- **Self-correction to the reconnect fix from v0.1.11a12**: that fix relogged in on *every*
  reconnect attempt whenever a `relogin` callback was configured, even for an ordinary transient
  disconnect with a perfectly valid token — trading a fast, simple MQTT reconnect for a full
  Gigya+iRobot auth round-trip unconditionally. Narrowed: a relogin now only happens when the
  token is actually at/near expiry, matching the same check `_refresh_loop()` itself already
  uses. An ordinary reconnect with a still-valid token uses the fast, same-token path exactly as
  it always did before either fix existed.
- **CI failure**: `decode_rawmap_to_png()`'s new test needs Pillow, which is an optional
  dependency — CI's own install step didn't include the `[map]` extra, so the import failed
  there even though it works fine locally. Fixed both directions: CI now installs `[map]` too,
  and the test itself skips cleanly (rather than failing) if Pillow genuinely isn't present,
  matching the library's own "not a hard dependency" design.

### Added

- **`decode_rawmap_to_png()`** in `models/livemap.py` — promotes the confirmed rawmap-decoding
  logic (previously a standalone diagnostic script) into a proper library function. Takes raw
  `rawmap` bytes, returns PNG bytes, already oriented to match the real app's own view. Optional
  `Pillow` dependency (`pip install "roombapy-prime[map]"`), not required for the rest of the
  library.

## [0.1.11a12] - 2026-07-21

### Fixed — prompted by a real field report of a permanently stuck connection

- **A real reconnect weakness that could leave a long-running connection stuck permanently**,
  found while investigating a field report (chairstacker) of an integration that lost
  connectivity and stayed lost across multiple full application restarts. Two related issues in
  `_watch_topic()`'s (the shared engine behind `watch_state()`/`watch_mission_timeline()`/
  `watch_raw_topic()`) reconnect-after-drop handling:
  1. `reconnect()` is same-token by design (see its own docstring) — it never checks whether that
     token is still valid. If a disconnect happens to land after the token has already expired
     (or the proactive refresh task died for any reason — see next point), every subsequent
     reconnect attempt would keep reusing the same now-permanently-invalid token, retrying
     forever at an ever-increasing backoff but never able to succeed. Fixed: when a `relogin`
     callback is configured, every reconnect attempt now fetches a fresh token first
     (`relogin()` + `replace_token()`) instead of blindly reusing the existing one. Falls back to
     the previous same-token `reconnect()` unchanged when no `relogin` was configured.
  2. `_refresh_loop()` (the proactive background task that normally keeps the token fresh well
     before expiry) had no error handling at all — a single failed `relogin()`/`replace_token()`
     call (a transient network blip at exactly the wrong moment, for instance) would propagate
     out of this fire-and-forget background task and kill it silently. No further proactive
     refresh would ever happen again for that `PrimeRobot`'s lifetime, with no log line anywhere
     pointing at it. Fixed: a failed refresh attempt is now logged and retried after a short,
     fixed delay, rather than ending the loop permanently.
  Together, these two fixes close the specific failure mode of "stuck forever on a stale
  credential, no error visible anywhere" — whether or not this turns out to be the exact
  mechanism behind any specific report, both are real, independently-justified hardening.

2 new tests (one per fix, each directly exercising the new relogin/retry behavior), 468/468
total green, ruff clean.

## [0.1.11a11] - 2026-07-21

### Added

- **`core::MissionData`'s getter return types confirmed via bytecode signature reading — no live
  device needed for this part.** `getBatteryLevelPercentage()` → `short`, `getTankLevel()` →
  nullable boxed `Short` (a numeric level, genuinely different from `CurrentStateShadow`'s own
  `tank_present`, correcting an earlier guess that conflated the two), `getIsCharging()`/
  `getIsFullyCharged()` → plain `boolean` (neither appears in `ro-currentstate`'s own key list,
  plausibly folded into `clean_mission_status` instead), `getDockState()` → a genuinely composite
  86-value enum spanning four dock subsystems (evac dock, fluid replenishment, pad wash, pad
  dry), `getResolvedMissionStatus()` → a 49-value enum. New, deliberately partial
  `ResolvedMissionStatus` `IntEnum` added with the values actually transcribed so far — NOT the
  full 49, extend incrementally rather than guessing at the gaps.
- **A significant correction to an earlier assumption this session**: `core::MissionData`
  actually has 27 getters, not the 7 originally listed — it does NOT map 1:1 onto
  `CurrentStateShadow`'s 12 keys, and is confirmed to be a larger, aggregated object (one of four
  combined input streams), not a direct shadow-serialization source. The confirmed TYPES above
  remain directly useful regardless — but which getter (if any) feeds which specific shadow key
  remains a hypothesis, not a settled mapping. Documented honestly in `CurrentStateShadow`'s own
  docstring rather than left implying a 1:1 correspondence.

### Changed

- **`CurrentStateShadow`/`StatsShadow` enriched with a cross-reference this project already had
  but hadn't checked**: `ha_roomba_plus`'s own Classic-tier field registry
  (`MISSIONSTORE_FIELD_REGISTRY.md`) confirms `batPct`/`detectedPad`/`tankPresent` as real,
  already-live-verified top-level Classic robot fields (including a real capture showing
  `batPct` moving 100→100→79 across one mission), and `bbchg3`/`bbrstinfo`'s confirmed Classic
  sub-field structure (`estCap`/`nAvail`/`hOnDock`/`avgMin`; `nNavRst`/`nMobRst`/`nSafRst`/
  `safCauses`). Same company, same field vocabulary, different product line — not proof Prime
  behaves identically, but meaningfully stronger supporting evidence than a bare guess for both
  models' docstrings.

### Fixed

- **`PolygonEvent` and `CleaningProfile` — the two remaining candidates from the systematic audit
  for wire-key confidence gaps, both resolved and both wrong.** `PolygonEvent`: 4 of 7 wire keys
  corrected (`mapId`→`p2mapId`, `mapVersion`→`p2mapvId`, `polyId`→`polyid`, `regionId`→`rid`) —
  `polyid`/`rid` specifically are not derivable from the property name by any casing
  transformation, exactly why the earlier DEX-field-list reading couldn't have caught this.
  `CleaningProfile`: `commandParams`→`params`, doubly confirmed — both by `$$serializer`
  inspection AND against chairstacker's own real `get_cleaning_profiles()` response from an
  earlier session, which had shown the correct key the whole time without anyone cross-checking
  the model against it. **Practical consequence, more significant than the `PolygonEvent`
  fields**: `command_params` stayed silently `None` against every real response — the actual
  cleaning-profile parameters (feeding into region-aware commands) were never being read at all.
  Both existing tests corrected to the real keys.

### Added

- **The battery-status search is resolved — `ro-currentstate` reports `batPct`.** Live-confirmed
  (chairstacker): the named shadow `"ro-currentstate"` — one of four previously-unknown
  read-only shadows found earlier this session via `MQTTTopics.java` — reports keys including
  `batPct` (battery percentage), `dock` (plausibly docked/charging state), and
  `cleanMissionStatus` (matching, independently, the exact event name this project's own native
  decompilation found on `AssetIotTopicFactory` months earlier). New `CurrentStateShadow` model
  captures all 11 confirmed keys. **Only the key names are confirmed so far** — every field is
  deliberately typed `Any` rather than guessed at, pending a follow-up with the actual reported
  values (not just the key list) to type this properly.
- **Models added for the other three "ro-" shadows too** (`StatsShadow`/`ro-stats`,
  `ServicesShadow`/`ro-services`, `ConfigInfoShadow`/`ro-configinfo`), all confirmed live in the
  same capture. Same caveat as `CurrentStateShadow` — key names only, values still unconfirmed.

### Fixed

- **A real, currently-existing redaction gap**, found directly by checking this project's own
  redaction coverage against `ro-configinfo`'s actual field name: `"passwordHash"` would NOT have
  matched the exact-match `"password"` entry in `diagnostics.py`'s sensitive-key set
  (`"passwordhash" != "password"` after lowercasing) — meaning a `--dump-config` capture of this
  shadow would have leaked it unredacted. Added as its own entry.

### Changed

- **Systematic audit for the same wire-key confidence gap that caused the `CommandParams` bug**,
  across every model in this package: checked every class whose docstring says only "Confirmed
  (androguard)" without a `$$serializer`/real-live-data cross-reference. Most turned out fine —
  single-word field names (`CommandPolygon`, `Region`, `ScheduleTime`) carry much lower risk than
  compound camelCase ones, and several (`PadWetnessParam`, `ScheduleTime`, parts of `Region`) were
  already independently confirmed against real captured data. Two genuine, unresolved candidates
  found and downgraded honestly rather than left overclaiming "Confirmed": `PolygonEvent`
  (`areaCleaned`/`mapId`/`mapVersion`/`polyId`/`regionId`) and `CleaningProfile`
  (`commandParams`) — both read-side models where a wrong key would silently produce `None` for
  real data rather than breaking an outgoing command, a different consequence than the
  `CommandParams` bug but still not confirmed correct. Flagged for the same `$$serializer`-table
  verification technique that resolved `CommandParams`, not guessed at.

### Fixed

- **A real, significant wire-format bug in `CommandParams` — 18 of 39 fields were using wrong
  keys, not just differently-cased ones.** Found by a separate native-analysis track
  investigating region-aware cleaning, via actual `$$serializer.<clinit>` inspection — the
  stronger evidence than this project's own earlier "DEX field list" reading, which had read
  Kotlin PROPERTY names, not the `@SerialName` wire keys kotlinx.serialization actually uses.
  Critically, kotlinx.serialization silently **drops undeclared keys** rather than erroring — a
  `RoutineCommand` sent with the old keys would have had these 18 parameters vanish entirely on
  arrival (cleaning strength, mop mode, pass count, and more), not just look slightly different.
  Corrected in both `to_json()`/`from_json()` (`roomConfine`→`room_confine`,
  `manualUpdate`→`manUpd`, `timeboxMinutes`→`timebox`, `velocityLeft`/`velocityRight`→`vleft`/
  `vright`, and 14 more — see `CommandParams.to_json()`'s own docstring for the complete list).
  `CommandPolygonMetadata`'s single field corrected the same way (`furnitureId`→`furniture_id`).
  One deliberate exception: `no_auto_passes` (wire key `noAutoPasses`) is NOT in the confirmed
  serializer list at all — kept exactly as-is because it's independently confirmed from real
  live data, a genuinely different field from `no_persistent_pass`, not a naming variant of it.
  New `test_command_params_wire_keys_match_confirmed_serializer_list()` checks every single
  output key against the confirmed list, not just a couple of examples. **This meaningfully
  reduces the risk profile of the whole region-aware-cleaning investigation** — `CommandParams`
  sits inside every region of a region-aware command, so this was a real, silent
  parameter-loss bug waiting for the first live test to hit, not a cosmetic naming issue.
- **A real, previously-unnoticed crash risk in `get_favorites()`**, found by a separate
  native-analysis track investigating region-aware cleaning: `Favorite`'s own Kotlin/Java field
  for command definitions is typed `List<String>`, not a list of already-structured objects —
  meaning each entry may arrive as a JSON-encoded string rather than a dict directly. The
  existing parser assumed dicts unconditionally; a real string-shaped response would have
  crashed outright. Now defensively handles both shapes. Follow-up analysis confirmed each
  string entry deserializes to a full `RoutineCommand` object (`check-cast` in the bytecode) —
  a favorite genuinely carries complete command definitions, not just a reference.

### Changed

- **A significant, well-reasoned recommendation in `send_routine_command_via_cmd_topic()`'s own
  docstring has been reversed**, following a native-analysis track tracing the real app's
  `RoutineCommandBuilder`: the earlier advice favored a `favorite_id`-only `RoutineCommand` over
  hand-built regions, reasoning that referencing something already app-defined would be safer.
  That's now known to be backwards — `setFromFavorite()` sends the favorite_id AND its full
  resolved command definitions (regions/params/id_multipolys/map_id) together, never favorite_id
  alone. A favorite_id-only command isn't a safer subset of real app behavior; it's something
  the app itself never actually sends.
- **`routine_modified` confirmed to be a COMPUTED comparison value**, not a free-form field —
  the real app derives it by comparing the command being built against the original favorite on
  three axes (region count, region order/IDs, and each region's specifically *user-modifiable*
  params). Exactly 7 `CommandParams` fields are confirmed non-user-modifiable (`routine_type`,
  `clean_score_id`, `smart_clean_id`, `replay_of`, `routine_modified`, `adaptive_cleaning`,
  `cleaning_profile`) — every other field factors into the comparison. **Practical consequence**:
  the safest possible test design is to resend an *existing* favorite's own command_def
  completely unchanged (sidestepping the modified-flag computation question entirely) rather
  than hand-constructing anything from scratch.
- **`RegionType.TID` (ad-hoc/temporary zones) fully explained**: ad-hoc regions get IDs from a
  reserved, hardcoded range (160–199) via a dedicated counter, and each one is created alongside
  a `CommandPolygon` sharing the exact same ID (the region↔geometry linking mechanism), with
  polygon metadata referencing a real furniture ID. A further, separate risk on top of the
  favorite-replay guidance above — the safest test design also avoids ad-hoc (TID) regions
  entirely, sticking to ordinary RID/ZID regions from real map data.
- **`OperatingModeBitmask` and `RoutineTypeParam` added** — `operating_mode`'s previously
  unexplained int values (2, 32, and the `cap.oMode` value 550 seen in every `get_state()`
  shadow response) are now a confirmed, independently-validated bitmask (`cap.oMode` turns out
  to be the device's advertised *set of supported modes*, not one active mode).
  `routine_type`'s full enum (`FIRST_RUN`/`CLEAN_ALL`/`CLEAN_DIRTY`/`REPLAY`/`SPOT_CLEAN`/
  `UNKNOWN`) is now modeled, wire format confirmed to be the constant name itself.
- **`RoutineCommand`/`CommandParams`'s own docstrings corrected** — both had gone stale
  (`RoutineCommand` claimed `params`/`regions`/`id_multipolys` "wasn't modeled in detail",
  `CommandParams` claimed 37 fields instead of its actual 39) after later sessions added detail
  without updating the summary. The naming discrepancy flagged here against that track's own
  field list is now resolved -- see the wire-format bug fix above, which corrected exactly the
  18 fields this discrepancy had flagged.

Final tally for this release: the region-aware-cleaning wire-key audit fixed 21 wrong keys
across `CommandParams` (18), `PolygonEvent` (4), `CleaningProfile` (1), and
`CommandPolygonMetadata` (1); the battery-status search resolved with 4 new "ro-" shadow models;
`core::MissionData`'s types confirmed via bytecode signature reading. 466/466 total tests green,
ruff clean.

## [0.1.11a10] - 2026-07-21

### Changed

- **Four new named-shadow candidates added to the battery investigation** (`roombapy-prime-verify-named-shadows`,
  `roombapy-prime-validate`), prompted by a separate native-analysis track: `MQTTTopics.java`
  builds topics for shadows this project never knew existed — `ro-currentstate`, `ro-stats`,
  `ro-services`, `ro-configinfo` (`ro-` = read-only, unlike the five `rw-`/classic shadows
  already confirmed and checked). These never appeared in the app's own command config for an
  identifiable reason: that config only lists commands, and nothing writes to a read-only shadow
  — the same reasoning gap that originally caused `rw-constatus` to be wrongly written off, now
  recognized as a systematic blind spot in how shadows were enumerated, not a one-off mistake.
  `ro-currentstate` is the strongest lead this investigation has had: the name itself describes
  exactly the kind of data being searched for (live, device-reported, read-only state). Not yet
  tested against a real device.

- **`send_simple_command()`'s docstring updated with a new, genuinely different "find my robot"
  candidate**, prompted by a separate native-analysis track: `MissionCommandType.FIND` (already
  in this library's own confirmed `CommandType` enum, wire value `"find"`) traces back to the
  real app's own locate button via `MissionUIServiceCommand.FindLocateRobotRunAction`. Distinct
  from the two already-disproven attempts (a REST endpoint, a shadow write) — this is a third,
  different transport (`send_simple_command()`'s own cmd-topic channel). `"find"` itself was
  never part of the confirmed-live verb subset (only start/pause/stop/resume/dock are) — untested
  against a real device as of this writing. A second candidate from the same analysis, `"FBEEP"`,
  is flagged with lower confidence — it isn't part of this project's own confirmed `CommandType`
  enum, and was found specifically in `liblegacyCore.so`, raising an open question about whether
  it even applies to Prime's command channel rather than being Classic-specific.

- **`PrimeRobot.trigger_echo_via_shadow()`, DISPROVEN against a real device** (chairstacker):
  writing `True` to `rw-constatus`'s `"echo"` field produced a genuine, accepted shadow write (a
  real `update/delta` response came back), but the robot did not chime, and "locate" from the
  real app worked fine on the same device immediately afterward. This was this project's second
  best-reasoned guess for the "find my robot" mechanism (the first, a REST endpoint, was also
  confirmed not working). Both docstrings (`trigger_echo_via_shadow()`,
  `ConnectionStatusShadow`) updated to reflect this — the method is kept (the underlying
  shadow-write mechanism itself works correctly and may be useful for other purposes), but not as
  a working locate trigger. The actual mechanism remains unresolved.

2 existing tests updated to the new named-shadow candidates (mechanics only — the underlying
capability, querying an arbitrary named shadow, was already fully covered), 450/450 total green,
ruff clean. Both the `ro-` shadows and the `"find"` command are genuinely promising leads, not
yet confirmed against a real device.

## [0.1.11a9] - 2026-07-20

### Fixed — urgent, prompted by a real field incident, read this first

- **`--watch-aws-tree`, added in v0.1.11a8, has been REMOVED entirely.** It wildcard-subscribed
  to the entire reserved `$aws/things/{blid}/#` namespace on `roombapy-prime-verify-mission-timeline`.
  AWS IoT's own "Reserved topics" documentation states topics starting with `$` are reserved and
  "unsupported publish or subscribe operations to reserved topics can result in a terminated
  connection"; the Device Shadow MQTT topics page explicitly recommends against wildcard
  subscriptions to shadow topics, naming `$aws/things/thingName/shadow/#` as the exact pattern to
  avoid. A field tester (chairstacker) hit exactly this: a `--start-mission --watch-wildcard
  --watch-shadow-delta --watch-aws-tree` run hung after sending the mission-start command
  (needed Ctrl+C to exit), and a separate, later process (`roombapy-prime-verify-named-shadows`,
  previously reliable against the same account) then failed all four named-shadow GETs with
  timeouts — consistent with AWS IoT terminating the connection or otherwise degrading service
  in response to the unsupported wildcard subscription, not just a local client-side hang.
  **If you installed v0.1.11a8 and used `--watch-aws-tree`, update to this version and avoid that
  flag going forward** (it no longer exists as of this release — passing it now raises a clear
  error instead of silently doing something risky).
  `--watch-shadow-delta` is unaffected by this and remains safe to use: it subscribes to exactly
  one specific, AWS-documented shadow topic (the same path used as the example in AWS's own IAM
  policy documentation for this exact feature), never a wildcard on the reserved namespace.

### Added

- **`PrimeRobot.trigger_echo_via_shadow()`** — a new, experimental hypothesis for the "find my
  robot" (audible chime) feature, prompted directly by a real bug report: a field tester
  (chairstacker) found `ha_roomba_plus`'s existing locate action — `poll_echo_value()`, a REST
  POST to `/v1/robots/{blid}/echo` — does not actually make the robot chime, even though the
  same action works from the real app. Separately noted but not connected until now:
  `ConnectionStatusShadow`'s `"echo"` field (in the named `"rw-constatus"` shadow) plausibly
  corresponds to the app's own `"SetEchoCommand"` — the exact command name the "find my robot"
  feature is built on, per the app's own command config. That command is a shadow WRITE, not a
  REST POST — meaning the existing implementation may simply be hitting the wrong mechanism
  entirely. Genuinely uncertain what value actually triggers the chime (one capture showed
  `echo=0` in an idle state); `value` defaults to `True` as the simplest guess, not a confirmed
  answer. **Still experimental, never confirmed against a real device** — a request has gone out
  to chairstacker to try it.

### Performance

- **Discovery response caching, prompted by a real "onboarding is slow" field report**
  (chairstacker): `ha_roomba_plus`'s Prime/V4 onboarding runs the full login chain
  (discovery → Gigya → iRobot cloud login) TWICE in immediate succession — once in the config
  flow to validate credentials and list robots, then again right after in `async_setup_entry` to
  establish the real, persistent connection. Investigated caching the login credentials
  themselves to avoid the second full chain, but concluded the risk/benefit wasn't there
  (adds a real security surface for a benefit that only applies once, to the very first setup).
  The discovery step specifically is a much better candidate: it depends ONLY on `country_code`
  — no username, password, or other per-user data goes in, and the response is static service
  infrastructure (deployment endpoints, Gigya app config), not anything session-specific. Now
  cached in-memory, keyed by `country_code`, with a conservative 1-hour TTL (not indefinite — a
  real infrastructure change should be picked up within an hour, not require a process restart).
  This removes one of the two redundant discovery round-trips during onboarding automatically —
  no changes needed in `ha_roomba_plus` itself, since both logins go through the same `login()`
  function — and also benefits every subsequent login this process makes, not just the one-time
  onboarding case. A real test-pollution bug was found and fixed while adding this: several
  `test_auth.py` tests share `country_code="US"` and would have silently seen an earlier test's
  cached discovery response instead of their own; a new `autouse` fixture clears the cache
  before every test.
- **`PrimeFactory.create_prime_robot()` gained an optional `login_result=` parameter**, letting a
  caller supply an already-obtained `LoginResult` to skip the internal `login()` call entirely.
  Built for `ha_roomba_plus`'s own onboarding handoff (config flow's validation login reused for
  the immediate first setup, single-use and short-lived — see that project's `_prime_login_bridge`
  module). Every existing caller is unaffected (parameter defaults to `None`, behavior unchanged).

450/450 tests green, ruff clean.

## [0.1.11a8] - 2026-07-20

### Added

- **`PrimeRobot.get_named_shadow(name)`** — a general, public form of the capability
  `get_state()`/`get_settings()` were always thin wrappers around (`mqtt_client.py`'s
  `get_shadow(named=...)`, which already accepted any string). Prompted by a person's own
  native-binary symbol analysis, not this library's own investigation: the real app subscribes
  to a wildcard covering every named shadow, and five are known to exist, but this library had
  only ever queried two (classic + `rw-settings`). The other three — `rw-constatus`,
  `rw-schedule`, `rw-software` — had never been queried before this session. A specific earlier
  mistake this corrects: `rw-constatus` had been written off because the app's command config
  only lists a write-side `SetEchoCommand` (`read: false`) for it — but that config describes
  commands, not subscriptions.
- **Result (chairstacker, all three checked live): the `rw-constatus` battery/charging
  hypothesis is DISPROVEN.** Its content is MQTT/AWS-IoT connection status (`{"connected",
  "connectedv2", "echo", "svcEndpoints"}`), not battery — the name's surface resemblance to
  "connection status" was accurate, but pointed at the wrong KIND of connection. The other two
  also confirmed content, neither battery-related: `rw-schedule` is the cleaning schedule,
  `rw-software` is OTA/firmware update status. New `ConnectionStatusShadow`/`ScheduleShadow`/
  `SoftwareStatusShadow` models (`models/robot_info.py`) capture all three. All five named
  shadows this wildcard-subscription pattern covers are now fully enumerated — none contain
  battery/charging/dock data.
- **A genuinely new, structurally-grounded lead found in the same capture: a whole new topic
  family, `dock/{reportType}/report`.** One `reportType` observed so far (`"padDry"`, on
  `dock/paddry/report`), fired essentially immediately after a mission's `"start"` command. New
  `DockPadDryReport` model (`models/robot_info.py`) captures it — lifetime dock/pad-dry counters
  (`numDocks`, `totalPadDry`, `totalPadDryTime`), not battery data itself, but the topic name's
  shape strongly suggests sibling `reportType` values could exist (a `"charge"` or `"battery"`
  one would be the obvious hope) — not confirmed, no other `reportType` has been seen yet in any
  capture, but a more concrete lead than anywhere else has pointed so far.
- **`mission/timeline/request` confirmed live, not just from native symbols.** A bare
  `{"timelineRequestId": <int>}` message was captured on it directly — the standalone
  confirmation of the same field `MissionTimelineReport.timeline_request_id` (added in
  v0.1.11a6) already carries when embedded in a report. Confirms the two topics are a genuine,
  now-observed request/response pair. `mission_timeline_topic()`'s docstring updated
  accordingly.
- **A real overreach in our own prior claims, corrected (parallel reverse-engineering track):**
  "live mission status does NOT flow through get_state()/watch_state() at all" was based on a
  snapshot DIFF of `get_state()` (two point-in-time GETs compared) — `watch_state()`'s own
  persistent `update/delta` push subscription was never actually run live during an active
  mission, only assumed by extension. That assumption may be wrong: AWS IoT's standard shadow
  push-on-change semantics could see intermediate changes a before/after snapshot comparison
  would never surface. Corrected in three places (`mqtt_client.py`, `prime_robot.py`'s
  `watch_state()`/`watch_mission_timeline()`, `verify_mission_timeline.py`'s module docstring).
- **One new flag on `roombapy-prime-verify-mission-timeline`** to actually test the above:
  `--watch-shadow-delta` (runs `watch_state()` for the same duration as everything else). Safe
  by design: subscribes to exactly one specific, AWS-documented shadow topic (the same path used
  as the example in AWS's own IAM policy documentation for this feature).

### Fixed (real field incident)

- **`--watch-aws-tree`, briefly added this same session, has been REMOVED entirely** after a
  field tester (chairstacker) hit exactly the failure mode AWS's own documentation warns
  against. The flag wildcard-subscribed to the entire reserved `$aws/things/{blid}/#` namespace.
  AWS IoT's "Reserved topics" documentation states topics starting with `$` are reserved and
  "unsupported publish or subscribe operations to reserved topics can result in a terminated
  connection"; the Device Shadow MQTT topics page explicitly recommends against wildcard
  subscriptions to shadow topics, naming `$aws/things/thingName/shadow/#` as the exact pattern to
  avoid. The real-world symptom matched: the run hung after sending the mission-start command
  (needed Ctrl+C), and a separate, later process (`roombapy-prime-verify-named-shadows`,
  previously reliable) then failed all four named-shadow GETs with timeouts — consistent with
  AWS IoT terminating the connection or otherwise degrading service in response to the
  unsupported wildcard, not just a local hang. `--watch-shadow-delta` (above) is unaffected —
  it was never a wildcard, only ever one specific, documented topic.
- **`RobotStatusV2`'s docstring expanded with a fuller field list** from `RobotStatusV2Constants.java`
  directly — meaningfully larger than the 11 fields currently modeled (adds `allowed_modes`,
  `dock_info`, `command_readiness`, `cycle`, `asset_connection_state`, `dock_state_*`). Not yet
  added as dataclass fields; documented so a future capture that finds this structure is
  recognized against the fuller list.
- **New script: `roombapy-prime-verify-named-shadows`** — checks all five known named shadows
  (the two already-confirmed ones as a baseline, plus the three candidates) in one pass.
  Purely read-only, no confirmation gate needed (unlike the mission-command scripts, this one
  never moves the robot). Reports the reported-keys of every shadow that responds.
- **The three candidate shadows are now also checked automatically by the main
  `roombapy-prime-validate` script** (`diagnostics.py`) — the one every new tester runs first.
  Factored into its own `_check_candidate_shadows()` function specifically so it's unit-tested
  on its own (`run()` as a whole has no dedicated test of its own; this way the new behavior
  still does). Considered also adding this to `verify_mission_commands.py`'s post-dock capture,
  but that script fires "dock" without waiting for the robot to physically arrive and start
  charging (same timing gap already known from `"fin"`'s own behavior) — an immediate
  post-command shadow check there wouldn't reliably catch a charging state anyway. The new
  dedicated script is the more deliberate way to check that specific moment, since a person can
  simply confirm the robot is already charging before running it.

### Fixed

- **A real secret leak, found directly (not hypothetically) from testers pasting raw terminal
  output**: presigned S3 URLs (from live-map/file-transfer messages) contain
  `X-Amz-Signature`/`X-Amz-Security-Token`/`X-Amz-Credential` query parameters — genuine, if
  short-lived (~1h expiry), access credentials to the underlying S3 objects. Neither
  `Report.redact()` nor `_redact_raw_capture()`'s existing key-name masking caught these (they're
  ordinary string values under keys like `"livemap_url"`, not literal username/password, and
  blanking the whole URL would also lose the base path that's useful for reverse engineering).
  New `redact_aws_url_secrets()` strips just the secret-bearing query parameters, keeping the
  rest of the URL intact. Applied as a third redaction stage inside `_redact_raw_capture()`
  (`--dump-config` output) **and**, more importantly, directly at print time in every script that
  prints a raw payload to the terminal (`verify_mission_timeline.py`'s `_watch_one()`,
  `verify_named_shadows.py`, `verify_mission_commands.py`'s `_show_state()`) — the actual leak
  happened via raw terminal output pasted directly, which never went through `--dump-config`'s
  redaction path at all.

- **`livemap_url_raw` ("rawmap") partially resolved (chairstacker, from a saved file):**
  confirmed to be zlib-compressed data that decompresses (13KB → ~207KB) to something `file`
  reports as plain "data" — NOT a recognized image container. Rules out the simple case (a
  ready-made image) for any future live-map rendering feature. Leading hypothesis, not yet
  confirmed: a raw occupancy grid (one byte per cell). Investigated via byte-level statistics
  and locally-rendered candidate images the tester checked themselves, without ever sharing the
  actual map content (their real home layout) — see `models/livemap.py`'s docstring for the
  full reasoning.

12 new tests (4 for the security fix, 6 for the three new shadow-content models, 2 for
DockPadDryReport), plus 5 more for `_build_watch_specs()` (the two new script flags, factored out
of `run()` for testability, matching `diagnostics.py`'s own `_check_candidate_shadows()`
pattern), 443/443 total green, ruff clean. Every named-shadow lead is now exhausted, but two new
ones opened in the same session: `dock/{reportType}/report` (from a live capture) and the
never-actually-tested `watch_state()`/`$aws/` gap (from the parallel reverse-engineering track).
The battery/charging question remains open — this release doesn't resolve it, but genuinely
advances the investigation rather than closing it out.

## [0.1.11a7] - 2026-07-20

### Added

- **The exact MQTT topics for live position/map data are now confirmed** (jayjay13011,
  the first capture with `verify_mission_timeline.py`'s topic-tracking fix from a6):
  `{prefix}/things/{blid}/livemap/update` carries BOTH position updates (`pos_update`) and
  map-ready notifications (`map_update`), discriminated by which key is present. This was
  already the exact pattern `livemap_topic()`/`watch_live_map()` used — previously an
  untested analogy to `cmd_topic()`'s pattern, now directly, independently confirmed. Both
  methods' docstrings updated accordingly; no code change was needed, only confirmation.
  7 distinct topics identified in total from the same capture's full topic-frequency
  breakdown: `livemap/update`, `filexfer_req`, `filexfer_resp`, `livemap/cmd`,
  `mission/timeline/report`, `cmd`, `service_event`.
- **`operating_modes` confirmed to genuinely vary, not a fixed constant**: 0 for the first
  ~5s of a cleaning mission, switching to 5 for the remainder of the observed period —
  resolves (in favor of the flat-array reading) a tension flagged in `PositionUpdateMessage`'s
  own docstring between two competing hypotheses about `cur_path`'s wire format.
- **`MapUpdateMessage` gained two previously-unmodeled fields**, confirmed present on every
  real message: `livemap_url_raw` and an outer `timestamp`.
- **`xferId` precision caveat, checked more rigorously**: an earlier note claimed
  `xferId = int(unix_timestamp)` matches its message's own `p2mapv_id` timestamp exactly, based
  on a small sample. Checked against 17 examples this time (jayjay13011) instead of a handful:
  16 matched exactly, one was off by exactly one second. "Almost always exact, occasionally off
  by one second" is the more honest characterization — not an unconditional exact match.
- **Stronger negative evidence for `RobotStatusV2`/battery status**: the same capture watched
  300s after stop+dock, with fully topic-tracked wildcard coverage (all 7 topics identified
  by name) — none carried anything battery/charging-related. Doesn't prove it's unreachable
  via MQTT, but rules out "wasn't watching long enough" and "missed it mixed into another
  topic" as explanations. Documented directly in `RobotStatusV2`'s own docstring.

### Removed

- **Redundant `models.LiveMapUpdate`** (added in a6, before this session realized
  `models.livemap.MapUpdateMessage`/`PositionUpdateMessage`/`parse_livemap_message_data()`
  already existed, fully built, just never live-confirmed). Removed in favor of enhancing the
  pre-existing, better-evidenced models instead of maintaining two overlapping ones. Anyone
  who adopted `LiveMapUpdate` in the brief window it existed should switch to
  `MapUpdateMessage` — same data, now with the two additional fields above.

423/423 tests green, ruff clean.

## [0.1.11a6] - 2026-07-19

### Added

- **New `"pos_update"` messages found live** — a second, longer live capture (chairstacker,
  `verify_mission_timeline.py --start-mission --watch-wildcard --try-pose-request
  --post-dock-watch-seconds 60`) showed live position/path data (`{"pos_update":
  {"cur_path": [...]}, "timestamp": ..., "update_expire_ts": ...}`) arriving repeatedly and
  unprompted throughout the mission — the open "does position data flow over MQTT" question
  from the previous session is answered: yes, and no request is needed to get it. The exact
  topic this arrives on isn't confirmed yet (see the "Fixed" section below for why); documented
  in `mqtt_client.py`'s existing position-investigation notes, with `cur_path`'s shape treated
  as a hypothesis, not confirmed against any decompiled source. `update_expire_ts` stays fixed
  across multiple consecutive messages rather than being a per-message TTL, consistent with a
  renewable ~60s streaming-session window (matching separately-observed
  `{"operation": "start", "start": {"duration": 60}}` messages on the same channel).
- **`MissionTimelineReport.timeline_request_id`** — a new optional field, confirmed present on
  some (not all) live report messages, tied to an explicit client-side request for a fresh
  update.
- **`RoomEvent.area`/`total_area` and `.status`, refined understanding (hypothesis, not
  confirmed)**: `area` looks like the room's total/target size (unchanged across visits),
  `total_area` how much was actually covered THIS visit — observed as `0` on a room interrupted
  immediately by `send_simple_command("stop")` before real coverage happened. `status=0` was seen
  on a normally-superseded travel event, `status=5` on the same interrupted room event.
- **New `models.LiveMapUpdate`, and a genuinely actionable connection**: push notifications
  (`{"timestamp": ..., "map_update": {"livemap_url": ..., "livemap_url_raw": ...}}`) arrived
  repeatedly throughout the same mission, roughly every 5-15s. `livemap_url` is a presigned URL
  ending in `p2mapv_geojson.tgz` — the exact same format `download_map_bundle()`/
  `parse_map_bundle()` already handle for REST-fetched bundles. No new download/parsing code is
  needed to consume a live-updating map feed; only a way to obtain the URL live (topic still
  unknown, see above). The robot's own matching upload side
  (`uploadP2MapLive`/`uploadP2MapMission`, their `reqParams`/`status: success` responses, and a
  one-time `NEW_P2MAP_AVAILABLE` notification after mission end) is documented but not modeled as
  dataclasses — this project only ever observes it, never constructs it.
- **`"fin"` and `"pause"` confirmed as real, LIVE `mission/timeline/report` event types**
  (previously only confirmed via the historical `get_mission_history()` endpoint). `"fin"` marks
  the mission as concluded; `"pause"` is what `send_simple_command("stop")` itself produces in
  the timeline (there is still no confirmed `"pause"`-distinct-from-"stop"` event type at all).
- **New `RoomFeatureProperties.visibility` field** — a real key confirmed from a live map bundle's
  `rooms.geojson` structure (chairstacker, field names only, no values shared). Not in the
  original bytecode-confirmed field list — genuinely new, not a correction. Left as a raw,
  unconfirmed value (only the field name is confirmed, not its value space).
- **Full structural cross-check of a second live map bundle**: `ManifestFeature`, `PolicyZoneFeature`,
  and `BorderFeature` all matched their existing models exactly, zero corrections needed — a clean
  independent confirmation of prior work.

### Fixed

- **Real diagnostic-tooling bug found and fixed**: `verify_mission_timeline.py`'s `_watch_one()`
  printed/stored the static watch *label* for every message, not `response.topic` (the actual
  concrete topic a message arrived on). Invisible for a specific-topic watch (label and topic
  are identical there), but a wildcard watch (`--watch-wildcard`) silently discarded exactly the
  information that would show which distinct topics were actually active — all 81 messages in
  the capture above printed under one identical bracketed label, with no way to tell them apart
  by topic after the fact. Now prints/stores `response.topic` instead.
- **Tooling improvements prompted by reviewing that same 81-message capture by hand**:
  `_watch_one()` now also prints a distinct-topic frequency summary once a watch ends, so a large
  wildcard capture doesn't require scanning every message by eye just to see which topics were
  active. `--post-dock-watch-seconds`'s help text now says explicitly that `"fin"` (mission
  concluded) fires within the same second as the stop command, not after the robot physically
  reaches its dock — the 30s default is unlikely to be long enough to catch battery/charging
  status specifically, and a much longer value is now explicitly recommended for that
  investigation. `--dump-config`'s saved JSON now also gets a topic-grouped sibling view for every
  watch entry (new `_add_topic_grouped_views()`), matching the terminal summary — previously only
  the terminal output was grouped, the saved file stayed a flat list.
- 4 tests updated in `test_verify_mission_timeline.py` for the topic-tracking fix, 2 more added for
  `timeline_request_id`, 2 more for `LiveMapUpdate`, 3 more for `_add_topic_grouped_views()`.
  423/423 tests green, ruff clean. Also
  corrected an earlier, now-disproven note of this project's own: `nMssn` going 255→256 between
  two live captures rules out "a saturating counter capped at the max value of an unsigned 8-bit
  integer" as an explanation.

## [0.1.11a5] - 2026-07-19

### Fixed

- **Real bug found and fixed: persistent wildcard subscriptions (`watch_raw_topic()` with a
  pattern like `"{prefix}/things/{blid}/#"`) could never receive anything, in any test run, ever.**
  Found via a live capture (chairstacker) that came back empty despite matching traffic
  demonstrably existing. `_on_message()` dispatched persistent subscribers via an exact dict-key
  lookup on `msg.topic` — but a wildcard registration's key is the literal pattern string, which
  `msg.topic` (always the concrete topic a message actually arrived on) can never equal. Fixed by
  matching every registered pattern against `msg.topic` via `paho.mqtt.client.topic_matches_sub()`
  instead of an exact lookup. `_pending` (one-shot request/response waits) is unaffected — it's
  never used with wildcards. 3 new regression tests.
- **`verify_mission_timeline.py --start-mission` real user friction, found and fixed**: cleanup
  only sent `"stop"`, leaving the robot stranded wherever it was when the watch window ended
  (chairstacker: "I had to physically push the button on the device"). Now sends `"stop"` then
  `"dock"`, matching the exact sequence `verify_mission_commands.py`'s own test already validated
  together.
- **A second, related bug found while designing a way to actually test for docking-related
  events**: the watch tasks were cancelled BEFORE stop/dock were sent, meaning any events resulting
  from docking could never be captured even if they exist. Restructured so watching continues
  through the whole stop → dock → post-dock window; new `--post-dock-watch-seconds` (default 30)
  controls how long that extra window lasts.
- **`SetRoomMetadataV1` (room rename/re-categorize) is now LIVE-CONFIRMED, not just
  decompilation-confirmed**: chairstacker successfully renamed a real room ("Master Bathroom" ->
  "Master Bathroom [roombapy-prime-test]") via `verify_map_edit.py`, confirmed in the real app,
  then reverted it back, also confirmed in the app.
- New `"policyZones"` confirmed as a real map-bundle content type (`policyZones.geojson`, from a
  second live bundle, chairstacker) — added to `KNOWN_BUNDLE_INFO_TYPES`, not previously known.

### Added

- **`models.MissionTimelineReport`** — the confirmed message shape for `mission/timeline/report`,
  built from a real, live, active-mission capture (chairstacker, `verify_mission_timeline.py
  --start-mission`). A valuable independent cross-confirmation: this wraps the SAME
  `MissionTimelineEvent`/`RoomEvent`/`TravelEvent`/`TentativeLocationEvent` models already confirmed
  (session 18/31, static analysis) for `get_mission_history()`'s HISTORICAL timeline — those models
  needed ZERO corrections to parse this live data, meaning the live push channel and the historical
  pull endpoint evidently share one underlying event schema. 2 new tests, using the actual captured
  data verbatim (redacted IDs only).
- 5 new tests total (3 for the wildcard-dispatch fix, 2 for `MissionTimelineReport`). 417/417 tests
  green, ruff clean.

## [0.1.11a4] - 2026-07-19

### Added

- **`watch_mission_timeline()` — a genuinely new channel, found via native decompilation, prompted
  by a live finding that ruled out where live mission status does NOT live.** A live idle-vs-mid-
  mission diff of `get_state()` (chairstacker) proved the classic shadow's reported state is
  byte-identical whether the robot is idle or actively cleaning — live mission status does not flow
  through `get_state()`/`watch_state()` at all. A separate investigation (native decompilation of
  `libcorebase.so`) found the actual channel this project believes carries it instead:
  - New `mqtt_client.py::mission_timeline_topic()`: builds
    `{irbt_topic_prefix}/things/{blid}/mission/timeline/report` (or `.../request`), found from
    `core::protocol::AssetIotTopicFactory::createMissionTimelineTopic()` — the same factory/
    constructor as the already-live-confirmed command topic (`createCommandPublishTopic()`, behind
    `cmd_topic()`), giving strong (not independently live-confirmed) reason to believe the same
    `irbt_topic_prefix` applies here too.
  - New `prime_robot.py::watch_mission_timeline()`: subscribes to the report topic, same
    reconnect-with-backoff behavior as `watch_state()`. Genuinely exploratory — the payload SHAPE on
    this topic is completely unknown; this method exists to capture a live sample, not to parse one.
  - New `mqtt_client.py::rejected_report_topic()` / `prime_robot.py::watch_rejected_commands()`:
    found in the same decompilation pass (`AssetIotTopicFactory`'s third method,
    `createCommandRejectedTopic()`) — directly complements the already-live-confirmed
    `send_simple_command()`: if a command call appears to succeed but the robot doesn't react, this
    is where a rejection reason (if reported at all) would be expected to arrive.
  - **Two related investigations, documented for future contributors rather than re-explored later**:
    `AssetIotTopicFactory`'s fourth method, `createRobotPositionTopic()`, builds its topic
    dynamically at runtime rather than from a static literal (unlike the other three) — pure string
    analysis is exhausted here; a live wildcard capture (`--watch-wildcard`, see below) is the
    practical way forward instead. Separately, `GetAssetMissionStatusCommand` — a read command
    mentioned in an earlier investigation — is confirmed a dead end for this library: its
    serializer routes through local HTTPS polling (the legacy "UMI" protocol family), not any cloud
    channel.
  - **New, genuinely testable hypothesis**: a follow-up decompilation pass found the exact request
    payload literal for a position/pose query: `{"do": "get", "args": ["pose"], "id": <n>}` — a
    generic `do`/`args`/`id` protocol (explaining why no dedicated topic literal exists at all: the
    intent lives in the payload, not the topic). New `prime_robot.py::send_umi_get_request()`
    (EXPERIMENTAL, UNCONFIRMED, elevated-risk caveat same as `send_routine_command_via_cmd_topic()`)
    sends this on the already-confirmed `cmd` topic. New `verify_mission_timeline.py --try-pose-request`
    flag to try this live, with its own explicit interactive confirmation regardless of the flag.
  - New `prime_robot.py::watch_raw_topic()`: a thin public wrapper for ad-hoc diagnostic
    subscriptions to any topic this library has no dedicated method for yet (e.g. a wildcard
    subscription to see what else is active on an account).
  - **Refactored**: `watch_state()`'s reconnect-hardened core is now shared, extracted into
    `_watch_topic()`, used by all three `watch_*()` methods above instead of being duplicated.
  - **A real bug found and fixed during the refactor**: a bare `async for x in inner_gen(): yield x`
    does NOT guarantee `inner_gen`'s `.aclose()` runs when the outer generator is closed — the
    `unsubscribe()` call in `_watch_topic()`'s own `finally` block silently never fired on
    `agen.aclose()`, only on natural exhaustion. Fixed with `contextlib.aclosing()`; caught by the
    existing `watch_state()` test suite immediately after the refactor, not shipped.
- **New script: `roombapy-prime-verify-mission-timeline`** — a diagnostic tool that subscribes to
  the new mission-timeline and rejected-command topics and logs whatever arrives during a real,
  actively-running mission. Purely passive by default (never sends anything, no
  `--i-understand-this-will-move-my-robot` flag needed) — optionally, `--start-mission` has it send
  the actual start/stop itself (via the same already-live-confirmed `send_simple_command()` path),
  so a tester can run one script in one terminal instead of coordinating two.
- 16 new tests (11 in `test_prime_robot.py`/`test_mqtt_client.py` covering the new topic/watch/send
  methods and the `aclosing()` regression, 4 in a new `test_verify_mission_timeline.py`, 1 for
  `rejected_report_topic()`). 412/412 tests green, ruff clean.

## [0.1.11a3] - 2026-07-18

### Added

- **SSL/certificate error clarity, moved here from `ha_roomba_plus`'s `cloud_api.py`, and extended
  to every network layer in this library.** Ported from `cloud_api.py`'s `_raise_clear_ssl_error()`
  (a v3.5.0 bug-hunt fix from a real-world report) — belongs here rather than only in the
  integration, since every consumer of this library hits the exact same endpoints, including the
  standalone `verify-*` scripts chairstacker and jadestar1864 run directly, not just through
  Roomba+. Found while preparing `ha_roomba_plus`'s login consolidation onto this library's
  `login()` — that consolidation would otherwise have silently lost this already-shipped fix.
  - `auth.py`: all three HTTP calls in the login chain (discovery GET, Gigya POST, iRobot POST)
    now catch `aiohttp.ClientSSLError` and re-raise a clear `AuthError`.
  - `rest_client.py`: `_request()` — the single chokepoint nearly every endpoint method in this
    file goes through (p2maps, favorites, schedules, DND, mission history, map editing) — plus
    `download_map_bundle()` (which deliberately bypasses `_request()`, different unsigned host)
    both now catch `aiohttp.ClientSSLError` and re-raise a clear `RestError`.
  - `mqtt_client.py`: a genuinely different mechanism, not a copy-paste — this module uses
    paho-mqtt directly (synchronous `connect()`, not aiohttp), so a TLS handshake failure here
    would never surface as `aiohttp.ClientSSLError`. `connect()` now catches `ssl.SSLError`
    (paho-mqtt's own documented behavior for a TLS handshake failure) and re-raises a clear
    `ShadowError`. Unlike the aiohttp-based fixes, this one is reasoned-through from paho-mqtt's
    documented behavior, not from a real captured failure in this project — flagged as such in
    its own docstring.
- **Typed exception subclass hierarchy, extended coverage for `ClientConnectorError`/
  `ServerTimeoutError`, and translation-key prep for `ha_roomba_plus`.** Previously every failure in
  a given module raised the same single exception type (`AuthError`/`RestError`/`ShadowError`),
  which meant a consumer could only distinguish failure categories (bad credentials vs. temporary
  SSL/network issue) by string-matching the message — fragile, and exactly what HA's own
  `errors["base"] = "translation_key"` convention avoids. Every subclass IS-A its base, so existing
  `except AuthError`-style callers keep working unchanged.
  - `auth.py`: `AuthError` (base) → `AuthCredentialsError` (Gigya/iRobot login rejected — wrong
    username/password), `AuthRateLimitedError` (the real, confirmed "mqtt slot" case — distinct
    from credentials, since the fix is "close the iRobot app", not "check your password"),
    `AuthSSLError`, `AuthConnectionError` (`aiohttp.ClientConnectorError` — DNS failure, connection
    refused, network unreachable), `AuthTimeoutError` (`aiohttp.ServerTimeoutError`).
  - `rest_client.py`: `RestError` (base) → `RestSSLError`, `RestConnectionError`, `RestTimeoutError`
    — same three network categories, no credentials/rate-limit equivalent needed here (post-login
    REST calls, not the login itself).
  - `mqtt_client.py`: `ShadowError` (base) → `ShadowSSLError`, `ShadowConnectionError` (covers
    DNS/connection-refused/connect-timeout in one bucket, since paho-mqtt's synchronous `connect()`
    raises all three as plain `OSError` subclasses with no way to distinguish them meaningfully,
    unlike the separate `ClientConnectorError`/`ServerTimeoutError` types on the aiohttp side).
  - **Important asymmetry, deliberate:** `AuthSSLError`'s message confidently states "not your
    fault, temporary" — justified, since a cert failure is unambiguous. `AuthConnectionError`/
    `RestConnectionError`/`ShadowConnectionError` do NOT make that claim — a connection failure
    genuinely could be either iRobot's servers or the caller's own network, and overclaiming
    certainty there would be misleading.
  - All new exception classes exported from the top level (`roombapy_prime.AuthConnectionError`,
    etc.) — the intended way for a consumer like `ha_roomba_plus` to map onto its own translation
    keys without ever parsing message text.
- **Reconnect-with-backoff hardening — the biggest reliability gap this library had.** Previously,
  a dropped MQTT connection (network blip, broker restart, token expiry) left `watch_state()`'s
  generator hung on an empty queue forever, with zero signal anything was wrong — `mqtt_client.py`
  had no `on_disconnect` handling at all, and paho-mqtt's own auto-reconnect was deliberately
  disabled to avoid a different failure mode (infinite reconnect loop on bad setup). Both gaps are
  closed now:
  - `mqtt_client.py`: `on_disconnect` wired up; new `wait_for_disconnect()` (async, awaitable) lets
    a caller detect a drop instead of polling; new `reconnect()` (extracted from `replace_token()`,
    same "disconnect, connect, restore all persistent subscriptions" sequence, minus the token
    swap) reconnects with the *same* token.
  - `prime_robot.py`: `watch_state()` now races `queue.get()` against `wait_for_disconnect()`. A
    drop triggers automatic reconnection with exponential backoff (1s → 2s → 4s → ... capped at
    60s, configurable via `max_reconnect_backoff`), unbounded retries — appropriate for a
    long-running background consumer (e.g. a Home Assistant coordinator) that should keep trying
    rather than give up permanently. The caller's `async for` loop never sees any of this happen;
    it just resumes receiving deltas once reconnected.
  - Found and fixed a real bug while building this: if the generator itself is cancelled while both
    race tasks are still pending, the "loser" was left running as an orphaned task. Fixed with an
    unconditional `try`/`finally` cleanup, not just conditional cleanup in the normal-completion
    path.

### Fixed

- **Major structural correction to all nine V1 map-edit commands, prompted by a live HTTP 500 on a
  room rename (chairstacker) and resolved via live APK decompilation of the full
  `EditMapV1Request.java` source, down to the actual serializer calls.** Every V1 command's inner
  body was assumed to be a flat `{"type": "<PascalCase>", ...fields...}` object; the confirmed real
  shape is `{"command": "<snake_case>", "params": {...}}` for all nine, with several discriminator
  strings turning out to differ from what the class names would suggest (`MergeRooms` →
  `arrange_room`, `SetVirtualWalls`/`SetPermanentAreas` → singular `set_virtual_wall`/
  `set_permanent_area`, `DeletePermanentAreas` → abbreviated `del_permanent_area`).
  - `RenameRoomV1`, `SplitRoomV1`, `MergeRoomsV1`, `SetRoomTypeV1`, `SetPermanentAreasV1`,
    `DeletePermanentAreasV1`, `SetVirtualWallsV1`, `AdjustFurnitureV1`: envelope corrected
    (`command`/`params`), most inner field names were already correct from prior sessions.
  - `SplitRoomV1.split_points`: corrected from a list of `[x,y]` pairs to a single flat list of
    doubles.
  - `PermanentAreaV1`, `VirtualWallLinearV1`/`VirtualWallRectangleV1`/`VirtualWallNoMopZoneV1`,
    `FurnitureItemV1`: all three turned out to have their own custom serializers emitting
    **positional arrays**, not JSON objects at all. `VirtualWall`'s Linear/Rectangle/NoMopZone
    discriminator (previously an open question -- "custom serializer, unconfirmed") is a positional
    int at array index 1 (1/2/6), not a `"type"` string; a Linear wall degenerates to a 4-point
    polygon on the wire by repeating each endpoint (from, to, to, from).
  - `AdjustFurnitureV1.package_info`: confirmed to be a fixed `[1, 1]` default (a Kotlin default
    parameter value), not an arbitrarily-shaped, per-call-computed structure as previously assumed.
  - **`SetRoomMetadataV1`: complete rewrite, fully resolved down to `room_metadata`'s own two
    possible keys.** `room_metadata` contains exactly `"name"` and `"type"`, each written only when
    not `None`; `room_id` sits alongside `room_metadata` at the `params` level, not nested inside
    it. New `RoomCategory` enum (`models/enums_common.py`) for `"type"`'s value — a completely
    separate enum from the existing `RoomType` (used by the app-deprecated `SetRoomTypeV1`), with
    its own wire representation: snake_case strings (`"dining_room"`, `"living_room"`), confirmed
    via the actual serializer call (`type.name().toLowerCase()`), NOT the underlying Kotlin enum's
    own `raw` field (camelCase: `"diningRoom"`, `"livingRoom"`) that would have been the more
    natural-looking assumption — two of nine values would have been wrong had that been assumed
    instead. Confirmed constraint enforced: at least one of `name`/`room_type` must be set (the
    underlying API has no way to express "change nothing") — `__post_init__` now raises a clear
    `ValueError` instead of allowing a request the server would have to reject.
  - **A real mistake caught and fixed before ever going out**: an intermediate draft of
    `SetRoomMetadataV1.to_v1_command_body()` wrote a `RoomType` value into a key named
    `region_type`, conflating it with `RoomMetadataEntry`'s own `region_type` field — which is
    actually `RegionType` (`mission_control.py`), an unrelated enum for region-identifier-kind
    (`rid`/`tid`/`zid`), not room category. Caught by checking the enum's actual definition before
    shipping, not via a live failure.
  - **`RenameRoomV1` is deprecated app-side** (Kotlin `@Deprecated("Use SetRoomMetadata(mapId,
    metadata) instead")`) -- the current app build renames rooms via `SetRoomMetadataV1`
    exclusively. Kept available (deprecation is a statement about the app, not confirmed evidence
    the server has stopped accepting it), but documented as the non-primary path; prefer
    `SetRoomMetadataV1`.
  - `verify_map_edit.py` switched from `RenameRoomV1` to `SetRoomMetadataV1` for its live rename
    test, matching the app's actual current behavior.

387/387 tests green, ruff clean.



### Added

- **`roombapy_prime/__init__.py` now exports a real public API**: `PrimeFactory`, `PrimeRobot`,
  `login`, `LoginResult`, `RobotLoginEntry`, `AuthError`, `ShadowResponse`, plus a matching
  `__all__`. Previously the package exported nothing at all -- every consumer had to reach into
  internal submodules directly (e.g. `from roombapy_prime.auth import login`), coupling callers
  to internal module layout rather than a stable contract. This is the intended integration
  surface for external consumers (e.g. ha_roomba_plus's planned V4/Prime support).
- 3 new tests confirming the top-level exports stay importable and `__all__` stays in sync.

### Changed

- **Two stale "never tested against a real account" status claims corrected.** Both
  `roombapy_prime/__init__.py`'s and `auth.py`'s module docstrings still said login/MQTT/mission
  control had never been live-verified against a real Prime/V4 account -- true when originally
  written, but contradicted by this project's own CHANGELOG since v0.1.2a0 (chairstacker) and
  reinforced by the fifty-sixth session's second account (jadestar1864). Both docstrings now
  describe the actual, current confirmation status, with pointers to the CHANGELOG entries that
  established it.

### Fixed

- **`roombapy-prime-verify-map-edit` could never find a named room to test on, even when one
  existed.** `_pick_test_room()` used `getattr()` to read `p2map_id`/`rooms_metadata`/`name`/
  `room_id`, but `robot.get_active_map_versions()` returns raw `list[dict]` (see `prime_robot.py`'s
  own type hint) — `getattr()` on a plain dict silently returns the default for every field, at
  every level, always. Confirmed via a real capture from jadestar1864: their
  `get_active_map_versions()` response genuinely contained named rooms
  (`rooms_metadata: [{"room_id": "10", "room_metadata": {"name": "Living Room", ...}}, ...]`) that
  the script reported as absent. The already-correct `parse_active_map_versions()` /
  `RoomMetadataEntry.from_json()` (session 26/51) already does the right flattening — `run()` now
  calls it before handing data to `_pick_test_room()`, whose own logic was otherwise already
  correct. The same bug also silently broke the map-bundle fallback path (same raw-dict-via-getattr
  pattern), so it never got as far as actually attempting a bundle download either.
- The existing unit tests for `_pick_test_room()` didn't catch this because their `SimpleNamespace`
  helpers built an idealized, flat shape that never matched the real API response — same class of
  problem as a `MagicMock` hiding a real attribute mismatch. 2 new regression tests added, one
  running the exact real-shaped raw dict through the actual parsing pipeline end-to-end.

350/350 tests green, ruff clean.


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
