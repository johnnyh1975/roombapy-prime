# Detailed audit: Prime app packages/analysis vs. roombapy-prime — status July 11, 2026

## CURRENT STATE (after ninth session) — read this summary first

The rest of the document is chronological (newest session on top), grown over 9 sessions.
This summary captures what applies NOW, without needing to search the history.

**Fully implemented and bytecode-/source-code-confirmed:**
Auth chain, MQTT shadow client, all 11 P2Map read endpoints + read models, V1 edit vocabulary
(9 commands, the actually active path), favorites (all 5 endpoints incl. HTTP
methods), schedules (`ScheduleOptions`/`HouseholdSchedule`, all 4 HTTP methods confirmed), DND
settings, cleaning profiles, default routines, mission history (request AND response top level),
tar.gz map bundles (download+unpacking), household listing (despite dead-code status on the app side).

**Mission control -- CONFIRMED LIVE (session 44), see that addendum for the full story:** basic
commands (`start`/`stop`/`pause`/`resume`/`dock`, via `send_simple_command()`, a dedicated
non-shadow MQTT topic) were live-tested against a real robot and confirmed working -- the robot
was watched and confirmed to actually react to every command, the biggest open question this
project has had since its first session. The old shadow-based approach
(`send_mission_command()`) was separately confirmed NOT working for this. The
`RoutineCommand`/`CommandParams` payload structure (37 fields, `Region`/`CommandPolygon`/
`PadWetnessParam`) remains believed correctly modeled for the region-based use case, which is
still unconfirmed by any source -- that part of `send_mission_command()` is kept only for that,
now-narrower purpose.

**Genuine, but NOT further resolvable gaps** (need a real device or are structurally
unreachable through analysis):
- Exact envelope format of the V1 edit commands (discriminator key unknown, custom
  serializer not decompilable)
- `irbt_topic_prefix`'s exact JSON field name -- RESOLVED (session 43): real keys are
  "irbtTopics"/"iotTopics", confirmed live from a real account (see the forty-third session)
- p2maps auth mechanism: the SigV4 assumption remains an analogy to Classic, Prime's own code
  provably delegates to a native `accountService.sendRequest()` -- in principle never
  confirmable from Kotlin/Java code, only through a real traffic capture
- File naming inside the tar.gz map bundle -- RESOLVED (session 47): the bundle's own
  `Manifest`/`ManifestFeature` structure names the real filepath per content type, see that
  session's addendum. The manifest file's OWN filename within the tar.gz is the one remaining
  open piece of this specific question.
- `HouseholdSettingOptions` structure. (Stale note removed here: "16 of 20 MissionTimelineEvent
  sub-event types" -- this was already resolved in the eighteenth session, all 20 are typed;
  this summary line simply hadn't been updated to reflect that until now.)
- Map-bundle read models (`RoomFeature` and 10 others) -- RESOLVED (session 47), see that
  session's addendum for the full story; this was previously the single largest block of
  unconfirmed models in the project.
- Teaming/multi-device coordination -- not investigated, needs multiple test devices in the household

**Known false positives from earlier sessions, since resolved:**
- "Furniture edit command missing 2 fields" (B2 below) -- was a comparison error
  read-model-vs-write-model; the real `EditMapV2Request.Furniture` only has 4 fields
  (geometry/id/type/userModified), exactly what the library already had
- "V1 is for older firmware" -- wrong, V1 is simply the only active path (all
  firmware generations), V2 is completely dead code
- Old C1 section below ("mission control not buildable") -- superseded by later sessions,
  mission control is implemented

**Test coverage:** 139/139 tests green, ruff clean.

## Addendum (eleventh session, same day): correction + live diagnostics script

**Correction:** The interpretation of `RoutineCommand.ordered` suggested as evidence in the
ninth session ("implies sequencing of multiple commandDefs entries") was rightly refuted by the
parallel chat: `ha_roomba_plus` (in production for years against real Classic devices) uses
`ordered` as a pure INTRA-command property alongside `regions` within the same command object --
whether the regions WITHIN this one command are visited in order or the robot is allowed to
optimize. Has nothing to do with the number of separately sent commands. Docstring in
`models.py` corrected accordingly. The original question (does the app iterate over multiple
`commandDefs` entries?) remains unresolved.

**New: `roombapy_prime/diagnostics.py`** -- a live validation script, arising directly from the
repeatedly-cited core weakness of the library (nothing was ever tested against a real account).
Read-only by default (login, REST reads, shadow state, map bundle download);
`--allow-writes` unlocks a reversible favorite create/verify/delete round trip, which would
live-confirm whether the three HTTP methods only confirmed via bytecode so far
(create/update/delete favorite) are actually accepted by the server. Mission commands and
map editing are deliberately NEVER run automatically (risk of a real action on the
physical device). CLI entry point `roombapy-prime-validate` registered in pyproject.toml.
Smoke test with invalid credentials confirms clean failure (no crash, clear
report, exit code 1).

Also closed a small gap along the way: `PrimeRobot.get_active_map_versions()` had been missing as
a wrapper, even though the rest_client.py version had already existed for a while.

145/145 tests green (6 new for the testable parts of diagnostics.py -- Report class and
helper functions; the actual live script by its nature can't be tested without a real account),
ruff clean.

**Addendum to the addendum (twelfth session):** Added on request -- the script now prints a
pre-filled GitHub "new issue" link at the end of every run (title + full report as
body, URL-encoded), so someone with a real account can share the results with one click.
Before that, the report goes through a redaction stage (`Report.redact()`), which replaces
every literal occurrence of username/password in error texts with "[REDACTED]" -- defense in
depth, even though credentials are normally never written into report entries anywhere.
`ISSUE_TRACKER_REPO` is a placeholder constant (`"OWNER/roombapy-prime"`), to be changed to the
real repo path once the repo exists -- the link works regardless
(pure URL construction, no API call), but points nowhere until then; the script itself points
this out. `--no-issue-link`/`--open-browser` as additional flags. 150/150 tests green.

## Addendum (twelfth session, same day): release prep -- LICENSE + CI

Given the question "are we release-ready for v0.1 Beta", gave an honest answer: NO, "Beta" would be
misleading -- not a single successful run against a real account exists, and the core question
(does login/mission control even work against the real server?) is completely
open, not just "still has rough edges". Two concrete blockers, now fixed regardless, were
handled anyway:

- **`LICENSE`** (MIT, consistent with `roombapy`'s own license choice) created.
  `pyproject.toml` switched to PEP 639 style (`license = "MIT"` + `license-files`), including
  classifiers -- deliberately `"Development Status :: 2 - Pre-Alpha"`, not Beta, matching the
  assessment above. Build verified locally: sdist+wheel build cleanly, the license ends up correctly in
  `dist-info/licenses/LICENSE`, metadata shows `License-Expression: MIT`.
- **CI tightened**: The lint job previously had `continue-on-error: true` (from when ruff
  wasn't yet consistently clean) -- removed, since ruff has been unfailingly clean for several
  sessions. New `build` job: builds sdist+wheel, installs the wheel in a fresh,
  isolated venv, imports the package -- validates that the library is actually
  installable, not just that the tests in the repo run. All three steps tried out
  locally before they went into the CI file.

README's license section updated from "TBD" to "MIT".

150/150 tests still green, ruff clean, build+install verification confirmed locally.

## Addendum (fourteenth session, same day): transport mechanism for mission commands -- two chains
## investigated, none confirmed (correction after a follow-up question)

At the parallel chat's request (MVP question: MQTT, REST, or shadow for starting a mission?),
`liblegacyCore.so` and `libcorebase.so` were fully analyzed with Ghidra. One promising
chain was found and initially treated as confirmed -- checked more closely on follow-up, finding a
genuine contradiction. Both chains documented here, neither viable:

**Chain A (classic shadow):** original state, concluded from a generic string finding
(`"$aws/things/%s/shadow/update"`). This string is generic (not mission-
specific), the choice "classic instead of named" was itself never independently substantiated.

**Chain B (NAMED "rw-settings" shadow, investigated and discarded again):**
```
CloudCapableMissionUIService::sendCommandJson(json)  [AWS branch]
  -> suspected: PMIAssetService::postCommand(type, json)   [vtable slot 0x38]
    -> getMqttTopic(type)
      -> ThingShadowConstants::supportedNamedShadowTopics()[5] == "rw-settings"
```
On closer inspection (argument-count matching, not just thematic plausibility), it turned out:
`sendCommandJson()`'s actual call site passes only ONE string argument, while
`PMIAssetServiceImpl::postCommand()` confirmedly needs TWO (`mov x21, x2` at its own
function entry, from the original disassembly). The connection sendCommandJson ->
postCommand was thus only thematically plausible (both "take a JSON string"), not confirmed by
argument matching -- presumably wrong. Additionally: the COMPLETE
`mapCommandsToNamedTopics()` table (which feeds postCommand, all 14 entries reviewed:
`SetBinPauseCommand`, `SetCarpetBoostCommand`, `SetEdgeCleanCommand`, `SetSuctionLevelCommand`,
`SetRobotPadWetnessCommand`, `SetAssetLanguageCommand`, `SetEchoCommand`, `AssetScheduleCommand`,
`AssetNameCommand`, `SetAssetPreferencesCommand`, `SetMapUploadAllowedCommand`,
`SetMultiPassCommand`, `SetRobotPadPlateWetnessCommand`, `SetRobotRankOverlapCommand`) covers
exclusively SETTINGS commands -- not a single mission start/clean/dock command was
among them, even if the connection had been correct.

**Consequence:** `send_mission_command()` was briefly switched to `"rw-settings"`, then reset
to the classic shadow after this check. The docstring now honestly documents both investigated,
neither confirmed chain. This remains the genuinely most uncertain part of the
entire library -- a definitive answer needs either a more complete native
trace (finding the actual caller of `sendCommandJson`'s correct counterpart) or a
real live test.

**Methodological lesson:** Resolving a vtable slot via reading RTTI type info (as shown here for
`AssemblerImpl`'s multiple inheritance from `Assembler`+`CoreInjector`) is a powerful
tool, but doesn't replace argument-count/signature matching at the actual call site
-- thematic plausibility ("both take a string") isn't enough as confirmation.

171/171 tests green, ruff clean.

## Addendum (eighteenth session): all 20 MissionTimelineEvent sub-event types typed

The effort limit drawn in the ninth session was lifted. While implementing, it turned out:
the 4 types documented as "already inspected in detail via bytecode" (PlanEvent,
PolygonEvent, TravelEvent, TraversalEvent), actually only existed as an analysis note in the
docstring -- never as real code. All 20 have now been newly implemented:

- **15 classes cleanly decompiled via jadx**: CommandEvent, DiscoveryEvent, ErrorEvent, EvacEvent,
  LiveViewEvent, PadDryEvent, PadWashEvent, PanoramaEvent, RefillEvent, RoomEvent, SubRoomEvent,
- **4 more via androguard** (jadx had silently skipped them as usual):
  PlanEvent, PolygonEvent, TravelEvent, TraversalEvent -- including 4 corresponding enums
  (PlanType, PlanUpcoming, TravelDestination, TraversalType)
- `MissionTimelineEvent` itself: androguard-confirmed EXACTLY 20 sub-event fields (not 19
  classes -- `relocalizing` and `tentativeLocation` share the same type
  `TentativeLocationEvent`, two fields, one class)
- `MissionHistoryEntry.timeline` switched from a raw dict to `list[MissionTimelineEvent]`,
  `parse_mission_timeline()` new

**Interesting side finding**: `PlanEvent.ordered` (Int) -- another instance of the `ordered` pattern,
this time clearly as an intra-event position indicator within the `upcoming` list, not as
command sequencing. Additional, independent evidence for the reading already corrected earlier
by ha_roomba_plus for `RoutineCommand.ordered` (see addendum, eleventh session).

205/205 tests still green (25 new), ruff clean.

## Addendum (twentieth session): FIRST successful live run ever

A user (johnnyh1975 themselves, real Prime account, BLID 80B2841450310780) ran
`roombapy-prime-validate` for the first time in this project's history against a real
server. Result: **7 OK, 1 failed, 4 skipped.**

**Confirmed live, for the first time ever:**
- The complete login chain (discovery -> Gigya -> iRobot auth) works against a real server
- MQTT connection (AWS IoT custom authorizer) works
- `get_state()` (classic shadow) works
- `get_favorites()`, `get_mission_history()`, `get_user_households()`, `get_active_map_versions()`
  all work REST-side

**One error message, but no new insight needed -- it confirms an already
documented prediction:** `get_settings()` (the named "rw-settings" shadow) timed
out. This method's docstring had already said: "only responds on SMART tier, times
out on EPHEMERAL (not a bug)". If the test user has an EPHEMERAL-tier device
(older Prime generation), this is the first LIVE confirmation of this structural
prediction, not a bug.

**A genuine, concrete bug found and fixed:** The user reported their robot had been cleaning on
schedule for months -- so the diagnosis "no active map version found" couldn't be right.
Cause found: `diagnostics.py` searched for the fields `p2mapId`/`id` in the
`get_active_map_versions()` response -- but `rest_client.py`'s OWN docstring for the same
method had already documented since the very first session that the response contains at least `mapId`
and `mapVersionId`. A pure bug in the diagnostics script itself, not in the REST client itself.
Fixed: `mapId` added as a primary field, additional debug output (shows the actual
keys of the response) in case no known field matches again in the future.

This is the first proof that the diagnostics script itself works as a tool -- it
immediately uncovered a real, concrete, fixable bug, exactly as intended.

## Addendum (twenty-first session): diagnostics script extended, on request

Right after the first live result, four things were added that should provide more
information on the next run:

1. **Three new, safe reads** (never added to the diagnostics script
   since their introduction): `get_robot_parts()`, `get_serial_number_data()`, `get_notifications()`.
2. **Automatic device info extraction** (`_report_device_info()`): tries to read model/SKU,
   firmware version, name, and capabilities field from `get_state()`'s response (candidate
   field names are guesses, never verified against a real response) -- ALWAYS additionally
   reports all actual top-level keys of the response, so a wrong
   candidate can be corrected on the next run, instead of silently finding nothing.
3. **Explicit tier guess** (`_report_tier_inference()`): makes the "SMART vs. EPHEMERAL"
   inference from `get_settings()`'s success/failure visible as its own, clearly readable report
   entry, instead of being only implicitly readable from a FAILED entry.

Direct trigger: the first live user had to be manually asked about their robot model,
to check the tier guess -- the script should figure this out on its own in the future.

210/210 tests green (16 new for the two new helper functions), ruff clean. Smoke test with
invalid credentials still clean (login gate fires before the new checks, no
regression risk).

## Addendum (twenty-second session): same gap found and fixed for household_id

On follow-up ("do we need further diagnostic details?"), systematically checked where else
the same kind of bug as the map bug could be lurking: guessed field names without a debug
fallback on failure. Found: `household_id = _extract_first_id(households, ["householdId", "id"])` for
the schedule/DND path has exactly the same risk -- `get_user_households()` is itself documented as
an analogy/unconfirmed, the field names are pure guesswork.

Fixed with a new, reusable `_shallow_summary()` helper function: summarizes an
unknown response structure for debug output (keys + value types, NEVER
actual values -- deliberately so, so that even with unexpected response shapes, no potentially
sensitive data like addresses or names ends up in a shared report). Both the
map ID and the household_id extraction now use the same mechanism, instead of
two slightly different ad-hoc solutions.

214/214 tests green (4 new for `_shallow_summary`, one of them explicitly against value
leakage), ruff clean. Smoke test still unremarkable.

## Addendum (twenty-third session): second live run -- tier guess confirmed live

chairstacker tested again after removing an old Roomba 675 from the account: **8 OK, 0
failed, 4 skipped** -- a clean run. Most important confirmation: `get_settings()`
(the named "rw-settings" shadow) responded this time -- the previous timeout was therefore
actually due to the wrong (old, retired) BLID, not a genuine tier limitation
of the current device. The previously walked-back tier guess for THIS specific device
(Roomba 405, SKU G185020, firmware p25-405+9.3.7+I4.6.150 -- confirmed via dorita980, not from
roombapy-prime itself) is thus moot -- it responds, so it's SMART-tier capable.

**The map version problem still persists, despite the field name fix from the last
session.** Most likely explanation: chairstacker presumably was still running the version FROM
BEFORE the fix (no `git pull`/reinstall between runs). To clarify this for the next run in
EVERY case (even if `get_active_map_versions()` genuinely returns an empty list,
which the field name fix wouldn't fix), the debug output was expanded: the
skip text for "no active map version found" now ALWAYS shows the actual
response structure (an empty list vs. data with unknown fields are now distinguishable).

214/214 tests still green, ruff clean.

## Addendum (twenty-fourth session): diagnostics script coverage checked, real gaps closed

On follow-up ("are we actually testing full functionality?"), systematically compared with `comm` against
`prime_robot.py`'s entire method catalog. Result: NO, not completely --
but the gaps were partly intentional (all write/destructive operations: schedule
CRUD, `set_dnd_settings`, `set_setting`, `reset_robot`, `edit_map`, `send_mission_command` --
deliberately left out, see safety principle) and partly pure oversight:
`get_live_map_stream()` and `watch_state()` are both read-only, but were never included.
Both now added -- `watch_state()` time-bounded to 3 seconds (getting no delta
counts as OK, not a failure, since the robot would need to actively change for that).

**Bigger addition: `--dump-config PATH`.** Implemented in response to the question "can't we also
report back a diagnostic config file like an integration does" -- directly inspired
by Home Assistant's "download diagnostics" feature. Unlike the normal report (which only
shows pass/fail and automatically flows into the issue link), this file saves the
ACTUAL raw responses of every read endpoint as JSON -- real field names AND real values,
exactly what was missing for the chairstacker map bug. Two-stage redaction (credentials +
obviously sensitive field names like address/GPS/WiFi credentials), but deliberately NOT as
thorough as the normal report -- this file is therefore NEVER automatically part of the
issue link, and must be deliberately attached individually. Map bundle contents are never
included (only filenames) -- a floor plan is more personal than most other
data captured here.

Implemented via a small extension of `_try()` (optional `capture` parameter, which
stores successful raw results in a separate dict, separate from the actual report) plus
a new `_redact_raw_capture()` function.

221/221 tests green (7 new), ruff clean. Smoke test with `--dump-config` confirmed: no crash,
correct (empty) JSON file on failed login.

## Addendum (twenty-fifth session): first --dump-config file evaluated -- several real bugs AND model corrections

chairstacker ran the updated script version including `--dump-config` against the same (now
confirmed correct) Roomba 405 and shared the complete, real raw JSON response
(via private message, not public, as recommended in `--dump-config`'s warning).
This is the most productive single data source since `base_roomba_config.json` itself.

**Definitively resolved: the map version bug.** The real `get_active_map_versions()` response
shows the actual field names: `p2map_id`, `entity_type`, `create_time`, `robot_id`, `sku`,
`active_p2mapv_id`, `last_p2mapv_ts`, `state`, `visible`, `name`, `rooms_metadata` -- NONE of it
was `mapId`/`mapVersionId`, the original (wrong) documentation assumption from the first
session. `diagnostics.py` and `rest_client.py`'s docstring corrected.

**Device info extraction was structurally wrong.** The real `get_state()` response shows: `sku`
lives under `payload["state"]["reported"]["sku"]`, not at the top level. `_report_device_info()`
corrected accordingly (now additionally reads `state.reported`, reports both
levels of keys on failure).

**Two genuine corrections to the core model, both from real mission history data:**
- `RegionType`'s values are LOWERCASE ("rid"/"zid"), not uppercase as originally read from
  bytecode ("RID"/"ZID") -- the constant names in the bytecode were correct, the actual
- `CommandParams.scrub`'s wire key is actually `"swScrub"`, not `"scrub"` --
  also corrected (Python attribute name stays "scrub" for backward compatibility).

**Two new fields added**, both from real data, previously unknown:
- `CommandParams.operating_mode` (wire: "operatingMode", observed values 2/32)
- `RoutineCommand.initiator` (wire: "initiator", observed values "cloud"/"rmtApp" -- who/what
  triggered the mission)

**One important walk-back: the "SMART tier live confirmed" claim from the twenty-third
session was premature.** Same user, same device (SKU G185020), two runs shortly
after each other -- once `get_settings()` succeeded, once it timed out. That's not a stable
tier signal. Docstrings and the automatic tier-guess output in `diagnostics.py`
worded more cautiously accordingly ("suggests" instead of "is", with an explicit note about the
observed inconsistency). An open hypothesis, not a code fix: perhaps the robot
itself needs to be actively connected to AWS IoT for a named shadow to respond, while
the classic shadow might be served from a cache -- unresolved.

**A new, genuine, unresolved bug:** `get_notifications()` fails live with HTTP 400. The
URL itself matches `base_roomba_config.json` -- presumably it's the
placeholder `app_version` value ("1.0") or a missing parameter not visible in the
configuration file. Docstring updated, marked as a known open bug, no fix
attempted without further data.

**Rich mission history raw data additionally confirms** (no code change needed,
just for reference): `RoutineCommand`'s existing field mappings (`command`, `p2map_id`,
`ordered`, `user_p2mapv_id`, `regions[].region_id`/`type`) as well as several `CommandParams` fields
(`twoPass`, `suctionLevel`, `carpetBoost`) match the documentation 1:1 -- a good
sign for the reliability of the original native analysis overall, despite the
individual corrections mentioned above.

228/228 tests green (12 new/updated), ruff clean.

## Addendum (twenty-sixth session): the rest of the --dump-config file -- complete models for two previously raw endpoints

chairstacker had shared the 38k-character file across two private messages; the first part
was evaluated in the twenty-fifth session, the second part (starting mid-
consumable-parts-list) here. Contained the complete, real responses for
`get_serial_number_data()` and `get_active_map_versions()` -- both previously only passed
through as raw JSON, now fully typed.

**`get_active_map_versions()`**: New models `P2MapVersion` and `RoomMetadataEntry` plus
`parse_active_map_versions()`. Confirmed: an account can have multiple maps (in the
observed case two: "Whole House" and "Master_Bathroom"). The most valuable single finding:
`rooms_metadata[].room_metadata.operating_mode_defaults` is a dict (keys = operating-
mode ID as a string, e.g. "512"/"32"/"2"), whose VALUES are directly CommandParams-shaped --
`CommandParams.from_json()` can be reused unchanged. Also confirms that
`region_type` is consistently lowercase ("rid"/"zid", matching the fix from the last
session) and that some rooms have a user-assigned name (e.g. "Bathroom"), others
don't.

**`get_serial_number_data()`**: New model `RobotSerialInfo`. Confirms, among other things, `family: "Roomba
Combo"` (a vacuum+mop combo device), `series: "G1"`, and the user-assigned robot name
("House_Bot").

**Also discovered and closed an incomplete wiring:** `CommandParams.routine_type`
already existed as a field (complete with a docstring that already referenced chairstacker's
data), but was never wired into `to_json()`/`from_json()` -- completed.

235/235 tests green (7 new), ruff clean.

## Addendum (twenty-seventh session): detailed review on request -- a large, previously overlooked finding

In response to the question "did you process everything, please review in detail again", `MissionHistoryEntry`
and `MissionCommandRecord` were systematically re-checked against the same real mission history that
had already been available in the twenty-fifth session -- there the focus had been on the NEW models
(P2MapVersion etc.), the library's own, long-standing field mappings were not re-checked at that time.
Result: **almost all field names in both classes had been wrongly guessed.**

**`MissionHistoryEntry`, corrected:**
- `robotId` -> `robot_id`
- `minutesRunning`/`minutesPaused`/`minutesCharging`/`minutesDone` -> `runM`/`pauseM`/`chrgM`/`doneM`
- `squareFeetCovered` -> `sqft`
- `numberOfEvacuations` -> `evacs`
- `endedOnDock` -> `eDock`
- `doneCode`/`doneRaw` -> `done`/`done_raw` (both seem to carry the same value twice)
- The mission command itself is under the key `cmd`, not `command`

**`MissionCommandRecord`, corrected:**
- `mapId` -> `p2map_id`, `mapVersionId` -> `user_p2mapv_id`
- `regions` switched from a raw list to `list[Region]` (structure now known)

**`Region.from_json()` was completely missing** (only `to_json()` existed, since originally only built
for sending) -- added. Real data shows the key `region_id` when reading, not `id`
as when sending via `to_json()` -- possibly two different wire forms for
the same purpose, both are now accepted.

**A second occurrence of the same case pattern as `RegionType`:**
`DoneCode.OK` had been confirmed as `"OK"` (androguard constant name), real data shows `"ok"`
(lowercase). All 19 values changed to lowercase -- only "ok" is directly confirmed,
the rest follows the same, now twice-observed pattern (consistent lowercasing
more likely than mixed casing within an enum). **Methodological consequence:**
all other "confirmed" enums via androguard in this library (CleaningMode,
VacuumPowerLevel, PadCategory, RankOverlap, CoverageStrategy, PlanType, PlanUpcoming,
TravelDestination, TraversalType) now carry the same risk of a casing
mismatch, until real data is available for them -- `_enum_or_none()` does catch this (no crash,
falls back to the raw string), but no one should currently rely on the exact casing
of these specific enum values.

**Two smaller additions from the same dataset:**
- `CommandParams.no_auto_passes` (wire: "noAutoPasses") -- found in an unusual
  place: embedded as a string-serialized (Python-repr-like, not direct JSON)
  `cmdStr` field in `get_state()`'s `cleanSchedule2` list.
- New models `RobotPart`/`RobotPartsInfo` for `get_robot_parts()` (previously raw JSON).

**Deliberately still NOT modeled**, for future sessions' reference:
- `get_state()`'s `cap` object (35 capability flags/levels like `carpetBoost: 3`, `suctionLvl: 4`,
  `maps: 6`) -- rich, but a dedicated model would be a substantial undertaking on its own
- `cleanSchedule2` itself as a whole (the schedule form embedded in the shadow, separate from the
  REST-based `get_schedules()`/`ScheduleOptions`) -- only the single `no_auto_passes` field
  from it was picked up
- Various MissionHistoryEntry fields with no recognizable value for a home automation
  library (`wlBars`, `startEndWlBars`, `oModeStats`, `saves`, `wifiChannel`, `flags`, `chrgs`,
  `pauseId`, `nMssn`) -- remain accessible via `.raw`

242/242 tests green (14 new), ruff clean.

## Addendum (twenty-eighth session): checked character by character once more, on explicit request

Two further, smaller but genuine findings while going through the
complete response once more, this time character by character:

**`get_state()` contains, contrary to my previous assumption, NO firmware field AT ALL.** The
complete `reported` structure has exactly eight keys (`digiCap`, `nsmip`, `cap`,
`cleanSchedule2`, `schedHold`, `sku`, `svcEndpoints`, `soldAsSku`) -- none of them is a
firmware/software version. `_report_device_info()`'s "firmware" candidate search will therefore
reliably stay empty here, not because something is wrong, but because the field simply isn't in
this response. Firmware instead comes from `get_serial_number_data()` or from
mission history entries (both carry `softwareVer`). Docstring clarified accordingly,
so an empty result here isn't misunderstood as a bug.

**One confirmed cross-connection, purely informational:** `get_state()`'s `svcEndpoints.svcDeplId`
("v007") matches exactly the prefix in `get_live_map_stream()`'s MQTT topic
("v007-irbthbu/things/.../livemap/update"). Confirms this prefix isn't a random value,
but comes from the account/device's "deployment ID" -- useful if the live-map topic
ever needs to be constructed for a different device/deployment, instead of copying it
literally.

**Also noticed, deliberately not changed:** The command structure embedded in
`cleanSchedule2[].cmdStr` uses `pmap_id`/`user_pmapv_id` (WITHOUT the "2"), while everywhere else
`p2map_id`/`user_p2mapv_id` is confirmed. Since `cleanSchedule2` remains unmodeled as a whole anyway
(see previous addendum), no code change is needed -- but if this structure is
later modeled after all, this is an important, distinct naming-convention difference, not a typo
mix-up.

**Honest assessment after two passes:** No further finding of similar size to the
field name corrections from the twenty-seventh session turned up. The remaining, deliberately
unmodeled areas (cap object, cleanSchedule2 as a whole, various mission history
side fields) are already named, not overlooked. Still, I can't be
100% certain -- the only method that has actually found bugs so far was comparison against
real data, not re-reading my own code; further real responses (other
endpoints, other devices) would presumably uncover further, similar errors, just as it
had been the case with every new data source so far.

242/242 tests still green, ruff clean.

## Addendum (twenty-ninth session): same bug type found for household_id -- overlooked despite the data having been available for a long time

On yet another follow-up ("can you still find something"), the household listing part of the
already long-available real response was checked again -- this time deliberately, not just superficially
dismissed as "stays raw". Result: **the same error type as with the map bug, this time for
`household_id`**, and it had been visible the whole time, but wasn't checked with the same
care as the mission history fields.

`diagnostics.py`'s `_extract_first_id(households, ["householdId", "id"])` searches for two
camelCase/generic candidates -- the actual, long-known response shows
`"household_id"` (snake_case), neither of the two candidates matches. This would have blocked the schedule/DND
check path in the diagnostics script just as silently as it did before with the maps.
Fixed: `"household_id"` added as the first candidate.

**While at it, built a complete model for `get_user_households()`**
(`Household`/`HouseholdRobot`/`HouseholdUser` + `parse_user_households()`), since the structure is now
completely known anyway. Also corrected the docstring assessment: the endpoint
had been documented as "dead code in the current app, HTTP method just convention" --
but works flawlessly live. "Unused in the app code" here actually only meant "this
app version doesn't need it", not "the server no longer supports it".

246/246 tests green (4 new), ruff clean.

## Addendum (thirtieth session): a missing field, not a wrong name this time

On yet another follow-up ("and what else"), found: `MissionCommandRecord` had no
top-level `params` field -- separate from `regions[].params`, sometimes
set in real mission history (e.g. `{"profile": "light"}`, observed on `initiator: "rmtApp"`
entries), sometimes explicitly `null` (on several `initiator: "cloud"` entries). Unlike
previous findings in this series of sessions, this wasn't a wrongly guessed field name, but a completely missing field
-- the data for it had been available the whole time, but was never individually extracted. Added,
uses `CommandParams.from_json()` like the analogous `regions[].params`.

247/247 tests green (1 new), ruff clean.

## Addendum (thirty-first session): programmatic full comparison instead of manual reading -- the biggest finding so far

In response to explicit criticism ("this is too iterative, did you really check the full information"),
the method was changed: instead of reading the data by eye again, ALL field names from
the complete `diagnose.json` (both messages, reconstructed as a real Python object) were
programmatically, recursively extracted and checked against every `.get()` call in the code. Result: the
most consequential finding of the entire investigation so far.

**The entire MissionTimelineEvent processing from the eighteenth session had been completely
ineffective up to this point.** `parse_mission_timeline()` searched for the key `"events"`
within `timeline` -- this key simply doesn't exist in real data. The
actual, rich sub-events are under `"finEvents"`; a separate, sparse
`"event"` list (just `type`+`ts`, no additional object) exists alongside it and contains no
additional information. Every single mission would have returned an empty
`.timeline` list for every previous user, with no error -- the bug was completely silent.

**Additionally, on almost every sub-event type: systematically wrong field names**, all following the same
pattern (wire format uses short `p2map`-prefixed forms, not the more verbose
camelCase guesses):
- `RoomEvent`: `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`, `regionId`->`rid`
- `TravelEvent`: `destination`->`dest`, `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`,
  `regionId`->`rid`, `zoneId`->`zid`
- `TraversalEvent`: `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`, `regionId`->`rid`,
  `zoneId`->`zid`
- `ZoneEvent`: `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`, `zoneId`->`zid`
- `TentativeLocationEvent`: `confirmedMapId`->`confp2mapId`, `confirmedMapVersion`->`confp2mapvId`,
  `mapId`->`p2mapId`, `mapVersion`->`p2mapvId`. Also: the MissionTimelineEvent key
  itself is `"reloc"`, not `"relocalizing"` or `"tentativeLocation"` as originally
  assumed -- added, without removing the two old (unconfirmed) field names.
- `PadWashEvent`: `fluidAmount`->`flAmt`, `padWashState`->`pwState`
- `MissionTimelineEvent.start_time`/`end_time` itself: `startTime`/`endTime` don't exist,
  the actual keys are `ts`/`ets`

**Two further enum misspellings discovered along the way** (the same case pattern
as RegionType/DoneCode before): `TravelDestination` and `TraversalType` were uppercase,
real data shows lowercase ("dock"/"zone"/"room", "region"). Both corrected.

**Verified end-to-end against the complete real mission history** (not just individual
unit tests): all three real missions now return correctly populated timelines (8/10/7
events), with correctly resolved map IDs, zone and room references -- previously
`0` everywhere.

**Honest framing:** This finding would likely not have been caught by the previous method
(field-by-field reading with occasional spot checks) -- it was several nesting
levels deep (timeline -> finEvents -> sub-event -> field) and involved a key whose
absence doesn't raise an error, only an empty list. The programmatic method (recursively
extracting all field names, checking against all `.get()` calls) is thus the only
approach found so far that reliably uncovers this kind of silent, deeply nested bug
-- for future live-data evaluations it should be the standard approach, not the
exception.

247/247 tests green (1 new, several corrected), ruff clean.

## Addendum (seventeenth session): prioritized roadmap -- what's next

Systematically answered the question "what do we still need to do to the library". Along
the way, another finding in the same configuration file: **47 settings commands
(`namedShadow: "rw-settings"`) total, ~25 of them completely unmodeled so far** (SetChildLock,
SetAudioVolumePattern, pad wash settings, PMapLearningAllowed, WifiDeviceLocalizationAllowed,
etc.) -- documented in `docs/API_REFERENCE.md`'s new "settings vocabulary" section, deliberately
NOT implemented (field names/wire format per setting not reverse engineered, would be its own,
larger undertaking).

**Priority 1 -- the one gatekeeper that qualifies everything else:**
At least one run of `roombapy_prime.diagnostics` against a real Prime/V4 account. Nothing
in this library has ever been live-tested. That's the difference between "thoroughly
analyzed" and "actually works" -- no amount of further analysis replaces that.

**Priority 2 -- concrete, known gaps (no new RE effort, just legwork):**
- Model the ~25 newly found settings commands as methods/fields, AS SOON AS their
  wire form is known (either through live traffic capture or targeted native tracing
  of individual commands)
- `HouseholdSettingOptions` structure (currently a raw dict)
- The 16 of 20 not-yet-typed `MissionTimelineEvent` sub-event types

**Priority 3 -- architecturally known, but deliberately deferred:**
- Teaming/multi-device coordination (9 REST endpoints confirmed, documented in
  API_REFERENCE.md, needs a real multi-robot household for meaningful testing)
- V1 edit command envelope format (discriminator key unknown)
- p2maps auth mechanism (SigV4 assumption, structurally never confirmable from Prime's own code,
  see C4 in an older section of this document)

**Deliberately not planned:**
- Account/app UX surface (survey system, notification management beyond reading,
  mission image approval) -- documented in API_REFERENCE.md, ranked as low priority for a
  home automation library
- Further native investigation of the commandDefs multi-entry question (issue #9, see its own
  section) -- four walked-back "definitive" conclusions in this investigation argue against
  investing further vtable work; only genuine field verification is planned going forward

## Addendum (fifteenth session, same day): DEFINITIVE resolution -- the actual configuration file found

In response to "keep searching", the configuration lookup (`PMIAssetServiceImpl::getProtocolConfig()`)
was pursued further. In the process, first found and corrected a GENUINE METHODOLOGICAL ERROR in my own
vtable reading: the "vtable for X" symbol address is the start of the ABI vtable *block*
(including offset-to-top + RTTI header), while objects themselves store a pointer shifted by +0x10
(confirmed from the constructor: `add x9, x8, #0x10`, and independently from the ELF relocation table
via `readelf -r`). The earlier reading was thus offset by 2 vtable slots -- after correction, it turned
out slot 0xA0 was `PMIAssetServiceImpl::getProtocolConfig()`, not `getNetworkInformation()`
(confirmed via a `readelf -r` relocation entry `R_AARCH64_GLOB_DAT` -> exactly the expected
vtable symbol name).

From there: `getProtocolConfig()` -> `core::ProtocolConfig::ProtocolConfig(string const&)` --
a CONSTRUCTOR that takes a raw string. Caller not statically resolvable
(data-driven call, as seen several times before) -- instead, searched for the underlying
CONFIGURATION FILE in the APK itself, no longer in the bytecode.

**Found: `res/raw/base_roomba_config.json`** (bundled in the APK, now saved as
[`docs/base_roomba_config_REFERENCE.json`](base_roomba_config_REFERENCE.json)) --
129 entries in `commandList`, each with `commandId`, `topic`, `namedShadow` (and sometimes
`httpMethod`/`urlPath` for REST commands). This is the **authoritative, actual
configuration source** for the transport mechanism of every single command -- no further
interpretation needed.

**Definitive finding for mission commands:**
```json
{"commandId": "AssetControlCommand", "topic": "cmd", "namedShadow": "", "networkList": ["lss", "awsIot"]}
```
`namedShadow` is EMPTY -- mission commands use the **classic (unnamed) shadow**, not a
named one. For comparison, in the same JSON:
```json
{"commandId": "SetBinPause", "topic": "delta", "namedShadow": "rw-settings", ...}
{"commandId": "AssetScheduleCommand,Set", "topic": "delta", "namedShadow": "rw-schedule", ...}
```
Settings and schedules actually use named shadows (rw-settings/rw-schedule) --
mission commands don't. This DEFINITIVELY confirms `send_mission_command()`'s classic-shadow
approach -- so walking back the "rw-settings" fix in the fourteenth session was correct,
now confirmed from a primary source instead of a discarded chain.

**Bonus finding in the same JSON:** `ResetRobotCommand` actually shows the REST path in action
(`"httpMethod": "POST", "urlPath": "/v1/%s/reset", "networkList": ["awsApiGateway", "lss"]`) --
confirms that `ProtocolAdapterRoombaApiGateway` (REST) is genuinely used for some commands,
while others (like `AssetControlCommand`) use MQTT instead -- both coexist, configured per
command in exactly this file.

**Methodological lesson:** The same lesson as before (thematic plausibility isn't enough), but
this time with an additional element: if a native chain repeatedly ends up at a CONSTRUCTOR
INPUT via a raw string, it's worth searching for the underlying RAW DATA FILE in
the APK itself, instead of continuing to trace the bytecode -- the actual "truth" had been
sitting in a bundled JSON file the whole time, not in the compiled code.

171/171 tests still green (docstrings updated, no behavior change needed -- the
implementation was already correct), ruff clean.

## Addendum (thirteenth session, same day): systematic review + documentation expansion

**Documentation added:** `docs/API_REFERENCE.md` (complete method/model overview with
confidence markers per entry), `CHANGELOG.md`, `SECURITY.md`, `examples/` (three runnable
scripts: `basic_usage.py`, `favorites_and_history.py`, `mission_control.py` with an explicit
safety prompt before every real command). All code examples verified against the real API.

**Systematic review, three concrete findings:**

1. **Missing PrimeRobot wrappers**: `delete_map()`, `get_map_geojson_link()`, `download_map_bundle()`
   existed in `rest_client.py`, but never as `PrimeRobot` wrappers -- the diagnostics script therefore
   had to access `robot._rest` (a private attribute). All three added, diagnostics script
   cleaned up accordingly. Found by simply comparing `grep`ed method names in rest_client.py
   against `self._rest.` calls in prime_robot.py.
2. **Test coverage check** (`pytest-cov`) uncovered two genuine gaps:
   - `prime_robot.py` at 81% -- almost all thin REST passthrough wrappers had no test at
     all. Fixed table-driven with `unittest.mock.create_autospec(PrimeRestClient)`
     (which automatically checks that call signatures match the real class) -- now 95%.
   - `auth.py` at 55% -- the complete `login()` orchestration chain (discovery -> Gigya ->
     iRobot) had NEVER been tested, even though that's the most critical entry point of the whole
     library. An earlier deliberate decision ("integration-shaped, not unit-shaped") was
     revised: a `_FakeSequentialSession` replays the three sequential HTTP calls,
     10 new tests cover the success path AND all "fail loudly" validation gates (missing
     credentials, missing individual credential key, missing MQTT endpoint, Gigya error,
     the known "mqtt slot" rate-limit special case). Now 94%.
   - `mqtt_client.py` (78%) and `diagnostics.py`'s `run()` (40%) deliberately NOT pursued further --
     genuine network/live-account internals, structurally as hard to meaningfully mock as
     `login()` had seemed before, but rightly so this time: paho client construction and the
     actual live script are integration-shaped, not unit-shaped.
3. No TODOs/FIXMEs found in the code, no return-type inconsistencies found, `examples/` correctly
   not tracked as package data.

**Overall coverage: 88% -> 91%. 171/171 tests green, ruff clean.**

---

## Update (sixth session, same day): full re-decompilation + six new REST areas + native dead end clarified

**Full re-decompilation** of the freshly uploaded APK (2.2.4) performed (24,983 classes,
only 56 errors -- all 56 in EXACTLY one class family, `EditMapV1Request`). This corrected two
earlier core assumptions and uncovered six completely new REST areas.

### Correction 1: V1, not V2, is the active edit path

`requestEditV2()` is called **not a single time** anywhere in the entire app code -- only `requestEditV1()`.
The 9 V1 commands (RenameRoom, SplitRoom, MergeRooms, SetRoomType, SetRoomMetadata,
SetPermanentAreas, DeletePermanentAreas, SetVirtualWalls, AdjustFurniture) are now
implemented in `models.py` (bytecode-confirmed via androguard, since jadx failed on exactly this class).
`rest_client.py::edit_map()` now uses V1; the old V2 path is preserved under `edit_map_v2()`,
with a warning that it's unused code.

### Correction 2: `FavoriteV1`/favorites endpoints complete, incl. bug fix

All 5 favorites endpoints implemented. `order_favorite()` had a genuine bug
(insert_at/insert_before/insert_after in the body instead of as query parameters) -- bytecode-confirmed
and corrected.

### Six new REST areas found and implemented

A systematic search for all `urlString`/`"/v1/"` patterns across the ENTIRE app code (not just
p2maps/favorites) turned up six previously completely unknown areas:

| Area | Endpoint | Status |
|---|---|---|
| Mission history | `GET /v1/{blid}/missionhistory` | **Fully implemented**, all query parameters confirmed |
| Schedules | `GET/DELETE /v1/households/{id}/settings/schedule[/{id}]` | Implemented, GET/DELETE confirmed, POST/PUT for create/update assumed |
| DND settings | `GET/PUT /v1/households/{id}/settings/dnd` | Implemented, both methods confirmed |
| Cleaning profiles | `GET /v1/profiles?assetId=...&p2mapId=...` | Implemented |
| Default routines | `GET /v1/p2maps/{id}/routines/defaults` | Implemented |
| `/v1/user/households` (household list) | -- | **Dead code** -- not called anywhere in the app code, not implemented |

`ScheduleOptions`/`HouseholdSchedule` (the body structure for create/update schedules) weren't
found under this name in the decompiled tree -- `create_schedules()`/`update_schedules()`
therefore accept raw JSON instead of prescribing a possibly wrong structure.

### Native dead end clarified (for the parallel chat, not blocking the library)

A multi-session-long Ghidra investigation (`FavoriteCommandType::ExecuteMission` -> iterates
`sendCommand` over `commandDefs`?) came to a clear, if negative, result:
`FavoritesDataUseCaseImpl::executeMissionForFavoriteId` only validates the favorite ID, but provably
sends no command (the JNI bridge shows exactly one virtual call, the called method
never accesses its own `FavoriteDataService` field). Not blocking for the library, since
the wire format (`RoutineCommand` -> shadow update) is already fully known independently of this.

### Test status after this session

123/123 tests green, ruff clean.

### Addendum (seventh session, same day): both open items closed after all

On follow-up, searched more thoroughly instead of giving up prematurely:

- **ScheduleOptions/HouseholdSchedule/HouseholdScheduleUpdate/ScheduleTime**: they do exist, jadx
  had silently skipped them like EditMapV1Request (not counted in the 56 errors).
  All fields pulled directly from the DEX via androguard and fully implemented in `models.py`
  (`ScheduleOptions`, `ScheduleTime`, `ScheduleDateEntry`, `ScheduleFrequency` enum,
  `HouseholdSchedule`, `HouseholdScheduleUpdate`). `create_schedules()`/`update_schedules()` now
  take the typed models instead of raw JSON.
- **`/v1/user/households` (household list)**: deliberately implemented despite dead-code status in the
  current app version -- an unused app-internal reference doesn't mean the endpoint doesn't
  exist server-side. HTTP method (GET) pure REST convention, not confirmed from a
  request class (unlike all other endpoints documented here).

124/124 tests green after this addendum.

## Addendum 2 (eighth session, same day): systematic full comparison DEX vs. jadx output
On follow-up, no longer searched individually for suspected gaps, but systematically
compared ALL ~11,325 `com.irobot.*` classes from the DEX against the jadx output tree. Result:
6755 are missing (after excluding R$ resource classes/BuildConfig) -- overwhelmingly UI layer
(Compose screens, navigation, fragments), irrelevant for a cloud client library. Two
subgroups, however, were highly relevant:

**`com/irobot/data/missioncommand/datamodels`** (31 missing classes): a complete, never-before-
seen preference/parameter system for mission commands. `CommandParams` (37 fields --
suction power, pad wetness, carpet boost, room confinement, timebox, drive speed for
steering commands, and much more), `Region`/`RegionType`, `CommandPolygon`/`CommandPolygonMetadata`,
`PadWetnessParam`, plus the `MissionPreference` family (CleaningMode, CleaningPasses,
ComboLiquidAmount, LiquidAmount, SoftwareScrub, VacuumPower as enums). All fully implemented in
`models.py`, replacing the previous raw dicts in `RoutineCommand.params/regions/
id_multipolys` (backward compatible -- raw dicts still work alongside).

**`com/irobot/data/restservices/*`** (57 missing classes, selection processed): found `CreateFavoriteRequest`/
`UpdateFavoriteRequest`/`CreateSchedulesRequest`/`UpdateSchedulesRequest` and read their
`httpMethod` construction directly from the bytecode (`const-string "POST"`/`"PUT"` in the
`<init>` method) -- so ALL four previously only "assumed" HTTP methods are now
bytecode-confirmed: create favorite=POST, update=PUT, create schedule=POST,
update=PUT. All affected docstrings updated.

**Not yet processed, but found** (for a future session): complete
mission history response models (`MissionHistory`, `MissionTimeline`, `MissionTimelineEvent`,
`PlanEvent`, `PolygonEvent`, `TravelEvent`, `TraversalEvent`, `MissionCommand`), `HouseholdSetting`
(response model for the DND/schedule container), `DNDStatusResponse`/`DNDSchedule.DailySchedule`/
`DNDSchedule.EndsAt`, `Routine`/`RoutineBuilderDefaults`/`RegionDefaults`/`OperatingModeProfile`
(response models for default routines), `CleaningProfile`/`CleaningProfile.ProfileType`. Currently
all affected `get_*` methods still return raw JSON -- works, but isn't
typed.

130/130 tests green, ruff clean after this second addendum.

## Addendum 3 (ninth session, same day): response models for mission history, DND, cleaning profiles, routines

The response models found but still open in the second addendum are now implemented:

- **`MissionHistoryEntry`/`MissionCommandRecord`** (models.py::parse_mission_history()):
  top-level fields of `MissionHistory` (times, `DoneCode` enum with 19 values, area coverage,
  error code, etc.) typed. `timeline` deliberately remains raw JSON -- `MissionTimelineEvent` has
  20 possible sub-event types (CommandEvent, DiscoveryEvent, ErrorEvent, ..., ZoneEvent), of
  which only 4 (PlanEvent, PolygonEvent, TravelEvent, TraversalEvent) were inspected in detail via bytecode
  -- fully typing all 20 was out of reasonable proportion to the benefit.
- **`CleaningProfile`** (with `CommandParams.from_json()` as a new inverse function to `to_json()`).
- **`DNDStatusResponse`** -- IMPORTANT finding: there are TWO different DND representations in the
  app code (the `DNDSchedule` sealed class with DailySchedule/EndsAt subtypes for building the PUT
  request, and the flat `DNDStatusResponse` for the GET response) -- both documented, only
  DNDStatusResponse implemented (the actual response shape).
- **`HouseholdSetting`** -- settingId/settingType typed, `options` remains a raw dict
  (HouseholdSettingOptions itself not further investigated, presumably polymorphic per settingType).
- **`Routine`/parse_default_routines()`** -- for get_default_routines(). `commandDefs` remains a
  list of raw dicts (by analogy to FavoriteV1.command_defs presumably List<RoutineCommand>, but
  not generically confirmable).

All four corresponding `get_*()` methods in rest_client.py still return raw JSON
(unchanged behavior) -- the new `parse_*()`/`Class.from_json()` functions are a
separate, optional step, exactly like `parse_map_bundle()`.

139/139 tests green, ruff clean after this third addendum.

## Update (fourth session, same day): "do we need more decompilation?"

**Short answer: no, no further decompilation needed -- but a
broader SEARCH within what's already decompiled certainly was.** The previous
classification of C2 ("not economically resolvable further") had been given up too
early. jadx/dex files no longer exist in this environment (only the already
decompiled Java sources from an earlier session) -- so a retry with different jadx
settings wasn't possible anyway. What helped instead: a systematic
search for ALL `urlString = "..."` assignments across the entire p2maps
source tree (`grep -rn 'urlString = "'`), instead of fixating on the one
failed coroutine method.

### tar.gz question fully resolved

`P2MapGeoJSONRequest.java` (previously overlooked) confirms:

    GET /v1/p2maps/{mapId}/versions/{mapVersion}/geojson?response_type=link
    Accept: application/json

returns (presumably) the presigned URL, from which
`fetchPersistentMap`/`fetchLatestPersistentMap`/`fetchMissionMap` load their
tar.gz map bundle (this had already been confirmed, see the previous
session). `response_type=binary` (Accept: application/gzip) loads the
archive directly, with no detour -- NOT implemented here (would need a
parametrizable Accept header in the SigV4 signer). Implemented as
`get_map_geojson_link()`. Only thing still open: which JSON key in
the "link" response carries the actual URL -- no dedicated response
class found in the source code, only the request itself.

### Two more, previously completely overlooked endpoints found

- **`delete_map()`** -- `DeleteMapRequest.java`: despite the name, NOT an
  HTTP DELETE, but `POST /v1/p2maps/{id}/settings
  ?trigger_fast_updates=true` with body `{"visible": false}` -- a
  "soft delete" via the same endpoint as `set_map_name()`. Small,
  implemented.
- **`EditMapV1Request`** -- an ENTIRE PARALLEL edit command
  vocabulary (RenameRoom, AdjustFurniture, SetPermanentAreas,
  DeletePermanentAreas, SplitRoom, MergeRooms, SetRoomType,
  SetVirtualWalls -- 8 commands), separate from the already
  implemented V2 vocabulary (10 commands, partly overlapping,
  partly named differently). `P2MapAPIEditRequestor` exposes both
  paths (`requestEditV1`/`requestEditV2`) as equal
  alternatives -- presumably V1 for older firmware with a
  more limited feature set, V2 for newer. The dispatch
  logic (who decides when V1 vs. V2) itself is again "not
  decompiled". **NOT implemented** -- a newly found, genuine gap,
  comparable in scope to the already-built V2 vocabulary,
  deliberately not included in this session either.

### What this says about the remaining work

This session shows: the remaining C2-like gaps aren't all equally hard. Some (like this one) are pure "not searched broadly
enough" gaps, others (mission control dispatch, the p2maps auth
mechanism) are genuine native limits. Before the next "this is
not resolvable" conclusion, it's worth doing a systematic grep across
the entire source tree for the pattern being searched for (here: URL fragments),
not just a targeted look at the one method that failed
first.

---
## Update (third session, same day): native disassembly + REST

**Tools:** installed `binutils-aarch64-linux-gnu` (apt),
so `aarch64-linux-gnu-objdump -d` provides real ARM64 disassembly
(the standard objdump on this x86-64 system couldn't do this).
No Ghidra/IDA available -- pure raw disassembly, strings
search and manual ADRP/ADD address tracing, no automatic
pseudocode.

### Breakthrough: mission control IS implementable (C1 half-revised)

The previous classification ("structurally hard native limit, not
closable") was only half right:

- **Transport confirmed** via a literal format string in
  `liblegacyCore.so`: `$aws/things/%s/shadow/update` (address
  0xde2a3a, found via cross-reference search of the ADRP/ADD
  instruction pairs that point to this address). Commands go
  through the already-implemented shadow update() path, not through
  a separate topic -- consistent with the old, never-confirmed
  assumption from CLOUD_SHADOW_PUSH_FINDINGS.md.
- **Payload shape confirmed** from Kotlin source code (not native!):
  `CommandWrapper` (@Serializable, one field `cmd` with
  @SerialName("cmd")) wraps `RoutineCommand` (@Serializable, all
  field names via @SerialName directly from the source code, not guessed).
  `CommandType` enum values also confirmed via @SerialName,
  including two surprising deviations from the Kotlin
  constant names (CLEAN_SPOT -> "point_clean", not "clean_spot").
- **Implemented**: `models.py` (MissionCommandType, RoutineCommand),
  `prime_robot.py::send_mission_command()`.
- **Still open**: the native `postCommand()` dispatch itself
  wasn't traced all the way to the actual MQTT publish --
  several levels of non-exported, symbol-less static functions,
  not economically resolvable further with the available tools. The
  envelope documented here combines two independently confirmed facts,
  never tested TOGETHER live.

### `irbt_topic_prefix`: existence doubly confirmed, content still open

Found: `core::ServiceDiscoveryImpl::kIrbtTopicPrefixFieldName` /
`kIotTopicPrefixFieldName` as real symbols (BSS section, `std::string`
objects with static initialization). Cross-reference search for the
initialization site was unsuccessful (presumably in a non-exported
function, not found via the available address ranges). The FIELD NAME
CONSTANTS thus provably exist -- the literal JSON key STRING
remains unconfirmed.

### Other changes this session

- **Concurrency protection**: `self._client_lock` (`threading.Lock`)
  in `mqtt_client.py` -- closes the previously documented gap
  between `replace_token()` and `get_shadow()`/`update_shadow()`.
  Verified with a real multi-thread test (including a counter-check: the test
  provably fails if the lock is replaced with a no-op).
- **Backpressure error visibility**: dropped exception entries
  are now logged as ERROR instead of WARNING (doesn't prevent the loss,
  but makes it more visible).
- **Household/multi-device (C5)**: briefly re-checked -- only native
  symbol names (`TeamingUIServiceImpl`), no Kotlin models relevant to p2maps found.
  Remains open unchanged, low
  priority.
- **Housekeeping**: `py.typed` marker, GitHub Actions CI (test matrix
  3.11-3.13 + ruff lint, found a genuine unused import),
  English user-facing README (convention: English for GitHub
  content) -- the previous German version now lives under
  `docs/DEVELOPMENT_NOTES.md`.
- **Ader update draft**: `docs/ADER_UPDATE_DRAFT_2026-07-11.md` --
  summarizes the three most important findings of this session (shadow
  transport for commands, tar.gz map bundles, livemap fixed topic).

---
## Update (later the same day): what's been worked on since

- **B1 (livemap topic)**: rebuilt. `watch_live_map()` now immediately
  subscribes to a fixed topic (`mqtt.livemap_topic()`), `get_live_map_stream()`
  continues running as a periodic background keep-alive. Needs
  `irbt_topic_prefix` from `LoginResult` (a new, uncertain field --
  discovery JSON field name guessed, not confirmed) -- if it's missing,
  `watch_live_map()` immediately raises a clear `RuntimeError` instead
  of silently waiting on the wrong topic.
- **B2 (furniture fields)**: WITHDRAWN, was a mistake on my part.
  I had compared the read model (P2MapFurnitureInfo) with the edit
  command. The actual edit class
  (EditMapV2Request.Furniture) really only has 4 fields -- no
  fix needed for the existing `Furniture` edit command.
  `orientation`/`cleaning_area` correctly belong in the new read model
  `FurnitureInfoRead` (see C3).
- **C2 (missing fetch endpoints)**: `fetchActiveVersions` now
  confirmed and implemented (`get_active_map_versions()` ->
  `GET /v1/p2maps?robotId={blid}&visible=true`) -- the INNER
  coroutine class (P2MapAPIFetching$fetchActiveVersions$2) decompiled
  cleanly, even though the outer wrapper method didn't. The
  other three (fetchPersistentMap/fetchLatestPersistentMap/
  fetchMissionMap) remain unconfirmed, but with new context: the
  map bundle is a **tar.gz archive**, not JSON -- downloaded
  from a presigned URL (`P2MapAPI.MapUnpacker.
  fetchMapBundleContentHolder(mapId, mapVersion)` resolves the URL,
  remains "not decompiled"; a second method with the same signature
  but a direct URL parameter then only shows "download + untar",
  no more URL construction).
- **C3 (read models)**: a large batch of new dataclasses added to `models.py`
  (RoomInfo, BorderInfo, TrajectoryInfo, CoverageInfo,
  DockInfo, HazardInfo, NoMopZoneInfo, AdHocCleanZoneInfo,
  KeepOutZoneInfoRead, VirtualWallInfo, CleanZoneInfoRead,
  FurnitureInfoRead) -- but still NO parser that breaks a complete
  response down into these types (the overall envelope format, now
  confirmed to be a tar.gz archive, wasn't further investigated).
- **C1 (mission control), C4 (auth mechanism), C5 (household)**:
  remains open unchanged, see below.

---

Systematic comparison of the Prime app sources decompiled today (and in previous
sessions) (`roomba_prime_decompiled.zip`,
`roomba_prime_native_libs.zip`) against the actual library code.
Three finding categories: **(A)** implemented and correct at its core,
**(B)** implemented, but with a concrete design flaw, **(C)**
not implemented at all — distinguishing whether it's a closable
knowledge gap or a genuine native limit.

---

## A. Implemented and correct at its core

- **Login flow** (discovery → Gigya → iRobot `/v2/login`) — field names,
  headers, payload shape confirmed 1:1, both against real Classic
  fixtures and against `ha_roomba_plus`'s production code.
- **AWS IoT custom authorizer connection** (WebSocket, three auth headers,
  shadow get/update) — live-tested against real Classic devices.
- **p2maps edit commands** (`POST /v2/p2maps/{id}/versions`, 10
  command types) — fully confirmed at the Java source code level.
- **Continuous dispatch loops, token refresh, backpressure** —
  own architectural decisions, not carried over from the app, but
  internally consistent.

---

## B. Implemented, but with a concrete design flaw

### B1. watch_live_map() / get_live_map_stream() — wrong model

**What I built:** a REST call returns an `mqtt_topic` field,
which is then subscribed to.

**What the app actually does** (`P2MapAPIFetching.observeLiveMap()`,
read in detail for the first time today):

1. Immediately subscribes to a fixed topic pattern:
   `mqttClient.subscribe(MQTTTopicPrefixType.irbt, "livemap/update", assetId)`
   resolved via `MQTTTopicResolverAdapter` into
   `{irbtTopicPrefix}/{identifier}` (the exact concatenation of assetId and
   "livemap/update" into identifier not conclusively confirmed, but
   the pattern "fixed, not from the REST response" is clear).
2. The REST call `GET /v1/p2maps/livemap` (my get_live_map_stream())
   is actually a periodic keep-alive ping
   (`LiveMapKeepAlivePublisher`, timer via refreshWindowMillis) — the
   response is never used anywhere to determine the topic. Specifically
   checked: LiveMapStreamResponse.topic (the mqtt_topic field) is read
   not a single time anywhere in the entire app code — only created
   when parsing, never consumed.

**Consequence:** get_live_map_stream() is presumably correct as a REST call
(matches LiveMapStreamRequest), but its purpose is
misunderstood — it's a keep-alive, not a "give me the topic"
call. watch_live_map() should instead immediately subscribe to a fixed
topic AND in parallel periodically send the keep-alive ping,
for as long as the watcher runs.

**New, genuine gap component:** irbtTopicPrefix/iotTopicPrefix are completely
missing from auth.py's discovery parsing — without them, the fixed
topic can't be assembled at all. Exact JSON field name in the
discovery response not confirmed (ServiceDiscoveryData is a
native/JNI class, field names there are C++ convention, not
necessarily identical to the wire JSON key).

**Recommendation:** Don't rebuild immediately — this is a fundamental
architecture change to already-tested code. First clarify: (a)
exact discovery field name for the topic prefixes, (b) exact
concatenation order in identifier. Until then: add this caveat to
watch_live_map()'s docstring.

### B2. Furniture edit command — two fields missing

The real read model P2MapFurnitureInfo has cleaningArea: Polygon and
orientation: double in addition to geometry/id/type/userEdited. My
Furniture dataclass (for set_furniture) only has furniture_type,
geometry, furniture_id, user_modified — no cleaning_area, no
orientation. Likely required fields when creating/changing furniture,
not just when reading.

---

## C. Not implemented

### C1. Mission control (CLEAN/START/STOP/PAUSE/DOCK/etc.) — biggest gap, but structurally hard

Complete command vocabulary found
(com.irobot.data.missioncommand.datamodels.CommandType, 30 values):
CLEAN, QUICK, SPOT, DOCK, START, PAUSE, RESUME, STOP, WAKE, RESET,
FIND, WIPE, IPDONE, PROVDONE, RECHRG, TRAIN, EVAC, STOPEVAC, QUERYDOCK,
TIDY, VIEWPOINT, STARTLOG, SKIP, FLREFILL, WASHPAD, DRYPAD, STOPPADDRY,
FLUSHSLUICE, CLEAN_SPOT, START_CLEAN. RoutineCommand structure (type,
assetId, mapId, ordered, idMultipolys, params, regions, pmapVersionId,
cleanAll, spotGeometry, favoriteId) also confirmed.

**Why this isn't (yet) buildable:** MissionRepositoryImpl (the
Kotlin code that calls startMission()) delegates to
MissionInitiation/ProductStatus/core::CommandTierAgentImpl::
postCommand() — all native JNI wrapper classes. The actual
transmission (MQTT topic? shadow desired state? REST?) happens in
liblegacyCore.so, invisible to Java/Kotlin analysis. This matches
the open question already noted in CLOUD_SHADOW_PUSH_FINDINGS.md
("no command topic found in the APK... presumably via the
shadow desired state, never tested") — confirmed again today, not
resolved.

**This is the most fundamental missing feature of the library** — without
it, the robot can't be started/stopped. But: not a knowledge gap
that could be closed by reading more Kotlin. The next step would be
either native disassembly (beyond symbol names) or a
real traffic capture against a Prime device.

### C2. p2maps read endpoints — status after the fourth session

The P2MapFetching interface has six methods:

| Method | Status |
|---|---|
| fetchMapMetadata | done: `get_map_metadata()` |
| fetchActiveVersions | done: `get_active_map_versions()` |
| observeLiveMap | partial: `watch_live_map()`, but see B1 |
| fetchPersistentMap / fetchLatestPersistentMap / fetchMissionMap | the endpoint for the presigned download link is now confirmed (`get_map_geojson_link()`, see the fourth session above) -- the actual downloading+unpacking of the tar.gz bundle from this URL is NOT implemented (would be a simple HTTP GET + tarfile unpacking, but not built yet) |

**Also found, not implemented:** `delete_map()` (small,
implemented) and the parallel V1 edit vocabulary (large, deliberately
not implemented -- see the fourth session above).

Earlier assessment ("Method not decompiled", "not economically
resolvable further") was too pessimistic -- a broader search within the
already-available source code (not re-decompilation) resolved
the core of the question.

### C3. p2maps read models (what's IN a map) — completely missing

models.py exclusively has edit command envelopes (what you SEND).
For what fetchPersistentMap/fetchMissionMap/get_map_metadata
actually RETURN, not a single data model exists.
Confirmed to exist, but not modeled, read types: P2MapRoomInfo,
P2MapBorderInfo, P2MapHazardInfo, P2MapTrajectoryInfo,
P2MapCoverageInfo, P2MapDockInfo, P2MapFloorPlanInfo,
P2MapNoMopZoneInfo, P2MapAdHocCleanZoneInfo, P2MapKeepOutZoneInfo,
P2MapVirtualWallInfo, P2MapThresholdInfo, P2MapFurnitureInfo
(read variant, see B2), P2MapRoomMetadata. get_map_metadata() currently
returns raw, unparsed JSON — honestly documented
("response shape not modeled yet"), but a big gap.

### C4. Auth mechanism for p2maps — today confirmed as structurally unconfirmable

AuthHTTPClientAdapter.perform() (the real HTTP client path of the
Prime app) delegates the entire request — including signing — to
accountService.sendRequest(), again a native method. The
SigV4 assumption in rest_client.py/aws_sigv4.py comes from
cross-referencing with ha_roomba_plus's cloud_api.py (Classic protocol,
its own reverse-engineering source) — it was and remains an
analogy assumption, not a fact confirmed from Prime's own code.
New today: the reason why it can never be confirmed from Prime's own code
is now clear (native delegation), not just "not
checked".

### C5. Household/multi-device concepts — not investigated

Teaming/capability_profiles were mentioned as concepts in earlier
sessions (shared native core), not pursued further today. A genuine gap,
but low priority without multiple test devices in the household.

---

## Prioritized proposal, non-binding

1. **B1 (livemap topic fix)** — affects already-built, tested
   functionality; should be corrected before anything new, once the
   discovery field name for irbtTopicPrefix is clarified.
2. **B2 (furniture fields)** — small, quick to add.
3. **C3 (read models)** — large, but well-scoped batch, directly
   derivable from the class names found today.
4. **C2 (missing fetch endpoints)** — first needs another,
   targeted decompilation attempt for the four "not decompiled"
   methods.
5. **C1 (mission control)** — the most important feature, but the hardest
   to close. A realistic next step: targeted native
   disassembly of CommandTierAgentImpl::postCommand() or
   waiting for real traffic data (Ader).

## Addendum (thirty-second session): get_settings() response content finally seen -- resolves a large part of the settings vocabulary list

chairstacker tested again after the corrected reinstall -- this time with actual
content for `get_settings()` in the `--dump-config` file (previously only "success/timeout" was
known, never the content itself). Built a new model `RobotSettings` that covers the complete
"rw-settings" shadow: child lock, volume, timezone, country, cloud environment, auto-evac
frequency, language list, various "*Allowed" permission flags, pad wash/dry cycle settings.

This resolves a substantial part of the ~25 settings commandIds previously listed in
`docs/API_REFERENCE.md` as "discovered, but unmodeled" -- SetChildLock, SetAudioVolumePattern,
SetAutoEvacFrequency, SetRobotLanguageV2, SetMapUploadAllowedCommand, SetPadWashReturn/
SetPadWashWetoutFrequency/SetPadDryDuration now correspond directly to confirmed fields.
The remaining ~12 (SetChargingLightRightPattern, SetDisplayLight, SetDemoMode, SetBinTypeDetect,
SetDetergentCleaningSolution, PMapLearningAllowed/PMapContinuousLearningAllowed,
SetNavStrategyCommand, WifiDeviceLocalizationAllowed/BleDeviceLocalizationAllowed,
TileScanModeAllowed, SetAQIScale, SetAssetSetting/SetSmartHomeSettings/SetPrecheck, ImgUpload)
didn't show up in this one response -- plausible, since not every device has every setting
active (e.g. SetDetergentCleaningSolution only relevant for detergent-capable models).

Also added `PadWetnessParam.from_json()` (was missing despite `to_json()` having existed for a long time)
-- confirmed against real values (`{"disposable": 3, "reusable": 1, "padPlate": 1}`).

Mission history/household/consumable parts/serial number/map versions in this run identical
to the previous data (just different JSON key order) -- no new insights there,
already fully processed.

250/250 tests green (4 new), ruff clean.

## Addendum (thirty-third session): likely cause found and fixed for "get_settings() sometimes yes, sometimes no"

In response to the question "why does get_settings() sometimes work and sometimes not", checked my own
code (not the server response) more closely -- and found a genuine, concrete candidate:
`get_shadow()`/`update_shadow()` subscribed to the response topics and published the
request IMMEDIATELY after, without waiting for the broker's SUBACK confirmation. `subscribe()` in
Paho is itself asynchronous (only queues the SUBSCRIBE packet). If the response came back BEFORE the
SUBACK was processed, it was lost -- at that point the client was technically
not yet subscribed, exactly the kind of non-deterministic, network-timing-dependent race that fits
"same device, different result" (chairstacker's repeatedly observed
`get_settings()` inconsistency on the same BLID).

Why did this mostly affect `get_settings()` so far and not the classic shadow
(`get_state()`, always successful so far)? Pure speculation, not confirmed: perhaps
the response latency differs slightly between the classic and named shadow,
causing the classic one to almost always "win" the race in practice while the named one more often
narrowly loses -- without access to timing measurements on a real device this remains speculation.

Fixed: new `_subscribe_and_wait()` helper method waits for the SUBACK confirmation (via a new
`on_subscribe` callback) for all affected topics, before publishing -- in both
methods. Deliberately short internal timeout (3s) for the waiting itself, since SUBACKs are typically
very fast, unlike the actual shadow response.

251/251 tests green (1 new, targeted regression test against the race ordering; several
existing tests needed a small adjustment to the fake MQTT client, which now needs to return
`(result, mid)` and trigger a simulated SUBACK, like the real Paho client). ruff clean.

Version bumped to 0.1.4a0 (lesson from the previous session: don't wait too long with the bump,
otherwise `pip install --upgrade` for git installs wrongly detects "nothing to do").

**Addendum to the addendum, same session:** In response to "what else", systematically searched for FURTHER
places with the same pattern (`grep` for all `.subscribe(` calls across the whole module) --
found two more: the persistent `subscribe()` method itself (used by
`watch_state()`/`watch_live_map()`) and the subscription restoration after a
token change in `replace_token()`. Milder risk there (missed first message instead of a missed
single expected response), but switched to `_subscribe_and_wait()` for consistency
anyway. After that: only ONE single, canonical `subscribe()` call site in the entire
module (in `_subscribe_and_wait()` itself) -- confirmed via `grep`, no other open spots.

252/252 tests green (1 further new test), ruff clean.

## Addendum (thirty-fourth session): built a manual mission command verification script

In response to the request to ask chairstacker whether he'd be willing to verify start/stop/pause --
and in anticipation of him agreeing -- built a separate, standalone script
(`verify_mission_commands.py`, entry point `roombapy-prime-verify-commands`). Deliberately
SEPARATE from `diagnostics.py`, for the same reason `diagnostics.py` itself never
automatically sends mission commands -- this script only exists for the moment when someone
wants to do that deliberately, once, and while watching.

**Safety design, twofold:**
1. `--i-understand-this-will-move-my-robot` must be set, or the script aborts before
   it even logs in.
2. A separate interactive confirmation before EVERY individual command (not just once at the start) --
   `_confirm()` only accepts unambiguous consent ("j"/"ja"/"y"/"yes"), an
   accidental Enter counts as a decline, not consent (secured by a test).

**Flow:** Start (clean_all=True) -> Stop, conservative as the default path. Pause/Resume and Dock
as separate, individually-asked additional steps. Before and after every command sent,
`get_state()` is additionally fetched and the raw state displayed/captured -- an active
mission state had never been captured before (every prior real response showed a
loaded but not running robot), so this would itself be new information, regardless of
the test result.

Uses the same `Report`/redaction/issue-link infrastructure as `diagnostics.py`
(reused, not duplicated) for a consistent, shareable final report.

268/268 tests green (16 new, all without a real network -- the actual purpose of the script is
by its nature not automatable to test). Smoke test confirmed: the safety gate correctly fires without
the required flag, `--blid` is required (no "first device found" like in diagnostics.py),
build/entry point verified.

Version stays at 0.1.4a0 -- everything from this session (race condition fix + new script) collected under the same, not yet externally distributed version.

## Addendum (thirty-fifth session): switched script runtime output to English

In response to a valid objection ("the complete library shouldn't respond in German, but in
English -- more international"), translated the complete runtime output of both scripts
(`diagnostics.py`, `verify_mission_commands.py`): report labels, status values (OK/
FAILED/SKIPPED instead of OK/FEHLGESCHLAGEN/UEBERSPRUNGEN), interactive prompts, `--help` texts, as well as
the name of the test favorite visible in the app. Internal code comments/docstrings (session
notes explaining why the code is the way it is) deliberately remain German -- this only affects
the actual user output.

Systematically checked via AST scan (not just `grep`), to make sure no German
strings remain in `print()`/`report.add()`/`_skip()`/`_confirm()`/`add_argument()` calls
-- including f-string components, which a plain text `grep` could easily have missed.

268/268 tests green (all affected expected values in the tests updated accordingly), ruff clean.
Smoke test of both scripts confirmed consistently English output.

Version bumped to 0.1.5a0 (a standalone, complete change, not an appendage to an
ongoing session).

## Addendum (thirty-sixth session): full English translation of internal code comments/docstrings, plus a concrete lead on the get_notifications() bug

**Translation completed.** The thirty-fifth session had deliberately left internal code
comments and docstrings in German (only user-facing runtime output was translated). On
follow-up, all of it -- every docstring and comment across the entire library and test
suite, plus this document itself -- was translated to English too, checked incrementally
against the full test suite and a fresh, isolated build+install after every batch, to catch
any accidental drift from a misplaced edit. 268/268 tests green throughout, ruff clean,
functional smoke tests re-run against the exact real-data shapes this project's bug fixes
target (finEvents timeline parsing, MissionHistoryEntry field names, Region send/read key
difference, RobotSettings) to make sure the translation pass changed no behavior, only
language.

**A concrete lead on the `get_notifications()` HTTP 400 bug (session 25).** The decompiled
APK's own `com.irobot.home.BuildConfig.VERSION_NAME` and `AndroidManifest.xml`'s
`android:versionName` both confirm `"2.2.4"` as the real app build version used throughout
this whole analysis -- a much stronger candidate for the `app_version` query parameter than
the old `"1.0"` placeholder, which had no evidentiary basis at all (just a generic guess).
No call site for this specific parameter could be found in the decompiled Kotlin/Java source
(the URL dispatch for config-driven commands like this one is native, same limitation as
`ResetRobotCommand` and others), so this remains the strongest available evidence, not a
certainty -- and the real Prime app in the field may since have moved past "2.2.4" to a newer
version. Default changed in `rest_client.py`/`prime_robot.py`; still needs a live re-test
against a real account to confirm whether this actually resolves the HTTP 400, or whether
the cause lies elsewhere (missing header/parameter not visible in `base_roomba_config.json`).

268/268 tests green (1 updated for the new default value), ruff clean. Version bumped to
0.1.6a0.

**A second, unrelated finding from the same "what else" pass:** while systematically checking
every documented uncertainty marker in the library for staleness (not just looking for new
native leads), found that `models.py`'s favorites section header comment still said the create/
update favorite HTTP methods were "ASSUMED... jadx silently didn't emit... no error reported for
it" -- a leftover from the fourth session, genuinely contradicted by the sixth session's later,
authoritative finding (`CreateFavoriteRequest`/`UpdateFavoriteRequest`'s `<init>` bytecode
directly confirms POST/PUT, already correctly reflected in `rest_client.py`'s own docstrings and
used as the CHANGELOG-worthy finding at the time). A pure documentation inconsistency between two
places in the same file describing the same fact differently, not a behavior bug -- fixed the
stale comment and a matching stale test docstring in `test_rest_client.py` to both say CONFIRMED,
consistent with the rest of the codebase.

**Also checked and left alone, genuinely still open (not stale):** `order_favorite()`'s
uncertainty about which insert_at/insert_before/insert_after combination(s) the server accepts,
`set_dnd_settings()`'s exact body field format, `get_map_geojson_link()`'s response JSON key name,
and `get_time_estimates()`'s request body shape -- searched again for a dedicated request class
for the latter specifically (none found in the decompiled sources), confirming the existing
"not investigated" docstring is accurate, not an oversight.

**A native investigation attempt on `irbt_topic_prefix` (the other genuinely open item from the
top summary) that didn't resolve the core question, but found new context worth recording.**
Traced the underlying native constants (`core::ServiceDiscoveryImpl::kIotTopicPrefixFieldName`/
`kIrbtTopicPrefixFieldName`) via disassembly of `liblegacyCore.so` far enough to find them used as
key arguments to a generic `AccountServiceImpl::sendUserRequest(key, callback)` call inside
`onAccountInfoRefreshed()`, alongside near-identical conditional checks for account country/
locale/notification-center/commercial-messages settings. This reads more like "sync this one
account attribute via its own request when a pending-change flag is set" than "read this key out
of the discovery response body" -- opening a competing hypothesis (a follow-up account-info fetch,
not the login discovery response) that hadn't previously been considered. The literal JSON key
string itself still couldn't be isolated (stored in a bss global filled in by a static initializer
not distinguishable from the many others in the same translation unit) -- the original conclusion
stands: needs either a real traffic capture or substantially deeper native work, not resolved by
this pass. Documented as new context in `auth.py`'s docstring, not as a resolution.

## Addendum (thirty-eighth session): second live run -- get_cleaning_profiles() query parameters corrected via direct bytecode read

chairstacker ran the updated script (0.1.5a0 at the time, before the app_version fix landed) all
the way through for the first time, including the previously-untested writing/mission-command
path. Two read-side findings and one enormous mission-control finding came out of this run.

**`get_notifications()` still failed with the old `"1.0"` placeholder** -- expected, since the
tester was still on 0.1.5a0, before the app_version fix from the thirty-sixth session had been
installed. Not a new finding, just confirms the fix hadn't been picked up yet.

**`get_cleaning_profiles()` failed live with HTTP 400** using the session-33 "informed guess"
query (`asset_id`/`p2map_id`, snake_case). This time, instead of guessing again, the actual
`CleaningProfileRequest.java` source was re-read in full (not just the URL path, which had already
been read before) -- its `getQueryParams()` method builds the query from two named constants:
`NotificationCenterConsts.IN_APP_NAV_QUERY_PARAM_ROBOT_ID` (resolves to the literal string
`"robotId"`, camelCase) and `PushNotificationConsts.PERSISTENT_MAP_ID` (resolves to `"p2map_id"` --
this one had actually been right all along). A completely missing THIRD parameter,
`"includeSmart"`, was also found: `"true"` whenever a non-blank p2mapId is present, `"false"`
otherwise -- and in the `"false"` branch, p2mapId itself is dropped from the query entirely, not
sent as an empty string. Fixed accordingly, `p2map_id` made optional to mirror this branching.
Unlike the app_version fix (an inferred candidate) or the session-33 guess (an informed but
wrong pattern-match), this one is a direct read of the real Kotlin query-building logic --
the strongest possible basis short of a live retest, which is still pending.

## Addendum (thirty-ninth session): mission control fails live via the shadow-update path -- corrected via independent native + third-party corroboration

The same run also attempted the full mission-control flow via `verify_mission_commands.py`:
Start, Start-for-pause-test, and Dock all failed identically -- `ShadowError: No response to
UPDATE on $aws/things/{blid}/shadow within 8.0s`. This is the first live test this library has
ever had of `send_mission_command()`/`update_shadow()` for actual mission control, and it failed
completely: not a rejection, not a malformed-payload error, but total silence from the shadow
service for every single attempt.

**Root cause found: mission commands were never meant to go through the device shadow at all.**
Re-examining `base_roomba_config.json` -- the same file that had already been used to confirm the
classic-vs-named shadow question back in the fifteenth session -- with fresh eyes turned up
something that had been misread the first time: cross-referencing the `"topic"` field across ALL
77 `commandList` entries (not just the one "Control" entry in isolation) shows it's a
discriminator with distinct categories, not an incidental label. `"shadow"` (2 commandIds,
including `GetThingShadow` -- confirmed live as `get_state()`'s classic shadow GET), `"delta"` (57
commandIds, all settings/schedule writes -- confirmed live as `update_shadow()`'s desired-state
mechanism for those), and `"cmd"` (exactly 4 commandIds: `Control`, `AssetControlCommand`,
`ResetRobotCommand`, `StartMatterCommissioning`). Mission commands fall into their own, third
category, entirely separate from both `"shadow"` and `"delta"` -- the original interpretation
("`namedShadow`: `""` means classic shadow, therefore send via `.../shadow/update`") had missed
this distinction, reading `"cmd"` as an incidental detail rather than a meaningful discriminator.

**Independent native corroboration, found via fresh disassembly of `libcorebase.so`:** a
targeted search for a "cmd"-specific topic string (as opposed to the already-known
`"$aws/things/%s/shadow/update"` in `liblegacyCore.so`) turned up a literal, distinct format
string: `"/things/%s/cmd"`, immediately preceded in `.rodata` by the string `"Processing command
<<%s>>"` -- strong contextual confirmation this is used for command dispatch, not shadow
operations. Cross-referencing its address in the disassembled `.text` section found exactly one
usage site.

**Independent, external corroboration:** a third-party, unaffiliated GitHub project
(`lvigilantecorreo-commits/roomba-v4`, MIT-licensed) documents this exact topic shape explicitly:
`"{irbt_topics}/things/{BLID}/cmd"`, with a simple payload `{"command": ..., "time": ...,
"initiator": ...}` -- its author reports this actually moved a real robot, reverse-engineered via
mitmproxy traffic capture, APK string analysis, and Ghidra, independently of this project. This is
an external, unverified-by-us source -- but its topic pattern independently matches this
library's own native string discovery, found completely separately. Two unrelated
reverse-engineering efforts converging on the identical topic pattern is about as strong a signal
as is available without a live test of our own against this exact path.

**Implemented:** `mqtt_client.py` gained `cmd_topic()` (builds
`"{irbt_topic_prefix}/things/{blid}/cmd"`) and `publish_cmd()` (publishes
`{"command", "time", "initiator"}`, fire-and-forget -- no known accepted/rejected acknowledgment
exists for this topic family, unlike the shadow system). `prime_robot.py` gained
`send_simple_command(command: str, initiator: str = "localApp")`, using this new path.
`send_mission_command()` (the old shadow-update path) is kept, but its docstring now documents
it as strongly suspected wrong for basic commands, retained only as a possible (equally
unconfirmed) fallback for the region-based use case (`RoutineCommand.regions`/`params`), which
no source -- including the third-party project, whose own status table lists room-cleaning as
unconfirmed too -- has verified either way. `livemap_topic()` was also updated to include a
`"things/"` segment, by analogy to the now much more strongly evidenced `cmd_topic()` pattern --
this one remains an analogy, not a direct confirmation, since neither corroborating source
speaks to the live-map topic specifically. `verify_mission_commands.py` updated to send via the
new path; the `clean_all`/regions option is gone from that script for now, since the simple
payload has no known way to express it.

**Honest framing:** this is the single most consequential change to the library's mission-control
path since the project began, but it remains UNCONFIRMED BY ROOMBAPY-PRIME ITSELF -- no live test
of this exact new path has been run yet. The evidence is unusually strong for a pre-live-test
finding (two independent reverse-engineering efforts agreeing on the same literal topic string),
but "unusually strong circumstantial evidence" is still not the same as "confirmed working." The
next live run against a real account is what would actually settle this.

273/273 tests green (7 new/updated across mqtt_client, prime_robot, verify_mission_commands),
ruff clean. Kept under 0.1.6a0 (bundled with the get_notifications/stale-comment fixes from the
thirty-sixth/thirty-seventh sessions) rather than bumping again, on request.

**Operational note:** partway through this session, the sandbox environment used for this
analysis became completely unresponsive for an extended period (all code-execution tools failing)
before recovering on its own, and on recovery had reverted to an earlier checkpoint (the last
downloaded package, from the end of the thirty-seventh session) -- losing the get_cleaning_profiles
fix and the entire mission-control topic correction described above from disk, though not from
this conversation's record. Both were fully reapplied from the conversation history after
recovery, re-verified against the same 273/273 green test suite, and re-packaged immediately
rather than continuing further before securing a checkpoint. No conclusions or evidence were
lost -- everything above reflects the work as originally done, not a reconstruction from memory.

## Addendum (fortieth session): RobotStatusV2 -- a real, bytecode-confirmed status model, but an unresolved question of where it actually lives

In response to a direct question ("doesn't roombapy-prime already have a robot status --
cleaning/charging/etc.?"), went looking for a structured mission/cleaning status model, since
`get_state()` currently only exposes raw JSON.

**Found: `com.irobot.data.maps.datamodels.RobotStatus.RobotStatusV2`**, fed by
`com.irobot.home.datarepository.RobotStatusV2Repository`/`RobotStatusV2RepositoryImpl` (which
explicitly holds a `kotlinx.serialization.json.Json` instance -- confirms this is genuinely
JSON-deserialized, not a native-only structure like several other things in this document).
Confirmed via androguard that this class has a companion `RobotStatusV2$$serializer` with its
own `<clinit>` -- and reading that `<clinit>`'s bytecode directly (extracting the literal string
arguments passed one by one to `PluginGeneratedSerialDescriptor.addElement()`) gave the actual,
confirmed wire keys, the same confidence level as `CleaningProfileRequest`'s query parameters:
`robot_state`, `buttons`, `dock_controls`, `errors`, `conditional_errors`, `localization_args`,
`p2mapId`, `p2mapvId` (these two camelCase, everything else here snake_case -- confirmed as-is,
not a typo), `battery_level`, `is_charging`, `is_robot_on_dock`.

**Critical, unresolved caveat, reported honestly rather than glossed over:** it is NOT confirmed
that this structure is what actually shows up in `get_state()`'s `reported` object. The one real
capture of that available (chairstacker, idle robot) has a completely different, unrelated set
of top-level keys (`digiCap`, `nsmip`, `cap`, `cleanSchedule2`, `schedHold`, `sku`,
`svcEndpoints`, `soldAsSku`) -- none of which match anything here. Two honest possibilities,
neither ruled out: this comes from an entirely different, not-yet-identified source, or these
fields only populate while a mission is actually active (which no prior capture has ever caught
-- every previous real response was from an idle robot).

**Also found, and deliberately NOT modeled:** the richer status concepts a person would actually
want (mission phase, a human-readable "cleaning"/"paused"/"returning to dock" status,
elapsed/remaining time, current cycle) live in a separate class,
`core::RobotStatusV2Constants` -- `PHASE`, `CYCLE`, `RESOLVED_MISSION_STATUS`, `REMAINING_TIME`,
`ELAPSED_TIME`, `PAUSE_TIME_REMAINING` among roughly 60 total field-name constants. This class,
however, has no `<clinit>` at all -- it's backed by a native djinni `$CppProxy` (same
fundamental limitation as `ServiceDiscoveryData`/`SettingsData` elsewhere in this document),
meaning its actual wire VALUES can't be extracted this way. A large, genuinely useful enum was
found alongside it, `com.irobot.data.maps.datamodels.mission.ResolvedMissionStatus` (~40 values
including `CLEANING`, `PAUSED`, `READY`, `SENDING_COMMAND_START`, `RETURN_TO_DOCK`,
`DOCK_EVACUATING`, `TIDYING_UP`) -- confirms the concept and roughly what the possible values
are, but not the literal wire key/value strings, so not modeled as an actual enum here -- would
need real data to do safely, consistent with this project's standing rule against building
models without concrete field evidence.

**Implemented:** `models.py` gained `RobotStatusV2` (frozen dataclass, the 11 confirmed fields)
and `parse_robot_status_v2()` (returns `None` if none of the confirmed keys are present in the
given dict, rather than a misleadingly "successful" all-`None` object).
`verify_mission_commands.py`'s `_show_state()` now attempts this parse on every before/after
`get_state()` call during the Start/Pause/Stop/Dock test flow, prints the result (or an explicit
"not found" message) alongside the raw reported dict, and includes both in the diagnostic
capture that flows into `--dump-config`-equivalent output -- so the next live run can help
settle the unresolved data-source question with real evidence, one way or the other.

277/277 tests green (7 new), ruff clean. Kept under 0.1.6a0, same consolidation as the rest of
this session's work.

## Addendum (forty-first session): third live run -- 23/23 reads green, and a genuine new bug found in mission control's prerequisite

chairstacker ran both scripts again. The `roombapy-prime-validate` result is the best this
project has ever had: **23 OK, 0 failed, 3 skipped** (the 3 skips are the deliberately-never-
automatic ones -- favorite write round-trip without `--allow-writes`, mission commands, map
editing). Both fixes from the thirty-eighth session are confirmed working live for the first
time (`get_notifications`, `get_cleaning_profiles`). Four endpoints that had never been reached
before now succeed: `get_map_metadata`, `get_schedules`, `get_dnd_settings`, and the full map
bundle download+unpack cycle (8 files found). Device info extraction shows the complete `cap`
object for the first time (35 capability fields: `carpetBoost: 3`, `suctionLvl: 4`, `maps: 6`,
`p2maps: 1`, etc. -- previously only known to exist, never seen in full). `state.reported`'s
8 top-level keys are IDENTICAL to the previous capture (same idle robot) -- consistent with,
though not proof of, the "`RobotStatusV2`-adjacent fields only appear during an active mission"
hypothesis from the fortieth session, rather than "wrong data source entirely."

**`roombapy-prime-verify-commands` found a new, genuine bug**, though: every single command
attempt (`Start`, `Start` again, `Dock`) failed identically with
`RuntimeError: send_simple_command() needs irbt_topic_prefix (from LoginResult) -- missing
here`. This is `irbt_topic_prefix` itself -- the long-standing, always-labeled-uncertain guess
at the discovery-response field name (`"irbtTopicPrefix"`/`"iotTopicPrefix"`) -- coming back
`None` for a real account, for the first time actually tested.

**Deliberately did NOT guess a third field name.** This project's own standing rule (never build
without concrete field evidence, revert speculative work when evidence is absent) applies just
as much to a third guess as it did to the first two. The real blocker turned out to be more basic
than "wrong field name": the raw discovery deployment object (where `irbt_topic_prefix` is meant
to come from) was never actually captured anywhere -- `login()` used it as a local variable and
discarded it, so even after this bug surfaced, there was no way to inspect what keys were
actually present.

**Fixed the capture gap, not the guess.** `LoginResult` gained a `deployment` field (the raw
discovery deployment dict), threaded through `PrimeRobot` (also gained a `deployment` attribute)
via `PrimeFactory.create_prime_robot()`. New `_report_topic_prefix_status()` in `diagnostics.py`
reports either the found prefix (if the guess ever turns out right for some account) or the
*actual* keys present in the deployment object via `_shallow_summary()` (structure/types only,
never values, consistent with this project's existing redaction discipline) -- called from both
`roombapy-prime-validate` (right after login) and `roombapy-prime-verify-commands` (same place,
plus an early exit with a clear explanation instead of repeating the identical failure for every
remaining command, which is what happened in this run).

**Next live run should finally reveal the real field name** -- something this project has been
guessing at since the fifteenth session's original discovery of the concept, now finally
positioned to be settled with actual evidence instead of another guess.

280/280 tests green (3 new for `_report_topic_prefix_status`), ruff clean. Kept under 0.1.6a0.

## Addendum (forty-second session): --dump-config now captures the deployment object, and a new, deliberately narrow map-edit verification script

Two follow-ups in response to direct questions.

**"Do we need the diagnostics file too?"** -- yes. `_report_topic_prefix_status()` from the
forty-first session only shows the deployment object's STRUCTURE (key names/types, via
`_shallow_summary()`) in the always-printed report -- deliberately conservative there, since that
report gets shared without a second thought. But actually confirming which key is
`irbt_topic_prefix` needs an actual VALUE (something shaped like `"v0NN-irbthbu"`) to distinguish
it from other candidate keys, not just a key name. `--dump-config`'s redaction (usernames/
passwords, not general values) is a different, already-accepted trust boundary than the
always-printed report -- so the raw deployment object is now captured there specifically, in all
three scripts (`roombapy-prime-validate`, `roombapy-prime-verify-commands`, and the new
`roombapy-prime-verify-map-edit` below).

**"Can we build a similar diagnostic for map editing?"** -- yes, but deliberately much narrower in
scope than `verify_mission_commands.py`. Map editing (`edit_map()`, the V1 command family) has
categorically weaker evidence than mission commands did before their own live test: mission
commands had two independently converging sources (this project's own native disassembly, and a
third-party project's live-tested implementation) before ever being tried live. Map editing's V1
envelope format has NO such corroboration from anywhere -- it's an analogy assumption from the V2
pattern, never independently confirmed. A wrong guess for a mission command was safely observable
as complete silence (confirmed, not dangerous). A wrong guess for a map edit command could, in
principle, be accepted by the server in a way that changes real map data unexpectedly -- lower
probability than a clean rejection, but nonzero, and unlike a mission command, a botched edit
could persist and need manual cleanup in the real app afterward.

For that reason, the new `roombapy-prime-verify-map-edit` script deliberately tests exactly ONE
operation: renaming an existing, already-named room to a clearly-marked test name
(`"{original} [roombapy-prime-test]"`), then immediately back. `_pick_test_room()` specifically
requires the room to already have a name -- a nameless room is never chosen, since
`RenameRoomV1.name` is a required string with no confirmed way to "clear" it back to nothing,
meaning a nameless room would have no safe revert path. Nothing else from the V1 vocabulary
(SplitRoom, MergeRooms, SetPermanentAreas, DeletePermanentAreas, SetVirtualWalls, AdjustFurniture)
is attempted -- several of those aren't cleanly reversible even in principle (a merge/split can't
be undone by calling some inverse operation, since the original boundary information is gone).

Also, unlike the mission-command script (which treats an error-free server response as success),
this script explicitly asks the user to confirm the name change in the real app before marking
either the rename or the revert as OK -- an accepted HTTP response only proves the server didn't
reject the request, not that anything actually changed, which matters more here given the
complete absence of independent corroboration for this envelope format.

286/286 tests green (6 new, for `_pick_test_room()`'s room-selection logic -- the script's `run()`
orchestration itself is intentionally not unit-tested, consistent with this project's existing
boundary for `diagnostics.py`/`verify_mission_commands.py`'s own `run()` functions). ruff clean.
New CLI entry point `roombapy-prime-verify-map-edit` registered. Kept under 0.1.6a0.

**Version note:** all of the thirty-ninth/fortieth/forty-first/forty-second sessions' work
(mission control transport correction, `RobotStatusV2`, `irbt_topic_prefix` diagnostics, the new
map-edit verification script) was deliberately consolidated under 0.1.6a0 rather than bumped
individually. After the forty-second session concluded, the version was bumped to **0.1.7a0**,
covering all of it as one release-worthy unit -- see `CHANGELOG.md` for the consolidated,
user-facing summary.

## Addendum (forty-third session): irbt_topic_prefix definitively resolved -- the diagnostics from the forty-first/forty-second sessions worked exactly as intended

chairstacker ran all three scripts with the new diagnostics from last session, and this time
shared the actual `--dump-config` content for the discovery deployment object. This is the
payoff of deliberately NOT guessing a third field name and instead fixing the underlying
capture gap first -- exactly the outcome that approach was for.

**The real deployment object, in full (chairstacker, live account):**
```json
{
  "awsRegion": "us-east-1", "discoveryTTL": 86400,
  "httpBase": "https://unauth3.prod.iot.irobotapi.com",
  "httpBaseAuth": "https://auth3.prod.iot.irobotapi.com",
  "httpProdSecBaseAuth": "https://certificatefactory.prod.security.irobotapi.com",
  "iotTopics": "$aws", "irbtTopics": "v011-irbthbu",
  "mqtt": "a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com",
  "mqttApp": "a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com",
  "svcDeplId": "v011", "userServicesBase": "prod.user-services.irobotapi.com",
  "vStream": "https://vstream.prod.user-services.irobotapi.com",
  "mqttAts": "a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com"
}
```

**The real keys are `irbtTopics` and `iotTopics`** -- plural "Topics", not "TopicPrefix" as
guessed since the fifteenth session. Close, but not exact; exactly the kind of near-miss that
static analysis alone (native getter names `getIrbtTopicPrefix()`/`getIotTopicPrefix()`) could
never have resolved, and real data settled immediately. `login()` in `auth.py` updated
accordingly, with a dedicated regression test using these exact real values.

**Three things this single capture confirms all at once:**
1. `iotTopics: "$aws"` -- confirms the classic shadow's hardcoded `"$aws"` prefix
   (`_shadow_base()`) IS the same concept as `iot_topic_prefix`, just already hardcoded directly
   rather than read from this field. No code change needed there, but resolves what this field
   conceptually represents.
2. `svcDeplId: "v011"` matches the `"v011"` in `irbtTopics: "v011-irbthbu"` -- confirms the
   `irbtTopics == f"{svcDeplId}-irbthbu"` pattern already suspected from session 28's "v007"
   observation on a different account, now confirmed as a general pattern across two different
   accounts/deployments. The field should still be read directly rather than reconstructed from
   `svcDeplId`, but this is a nice independent cross-check that the value is correctly understood.
3. `"v011-irbthbu"` is **byte-for-byte identical** to the example value shown in the third-party
   GitHub project (`lvigilantecorreo-commits/roomba-v4`) cited in the thirty-ninth session -- as
   strong a confirmation as this project could hope for that that project's corroboration was
   genuine and current, not a stale or coincidental match.

**The mission-command run itself skipped all 7 commands** (as designed -- the early-exit logic
from the forty-second session fired correctly, reporting the missing prefix once instead of
repeating the same failure seven times). With the field name now fixed, the NEXT run should be
the first one to actually attempt `send_simple_command()` with a real, correctly-built topic --
still not confirmed working end-to-end (whether the topic construction and payload shape
actually make a real robot react remains the open question), but the "can't even build the
topic" blocker is gone.

**The map-edit script also ran, safely, and found nothing to test on:** "no room with an
existing name was found across any active map version" -- `_pick_test_room()`'s safety
requirement (an already-named room, so there's something known-good to revert to) worked exactly
as designed, but this particular account doesn't currently have any named rooms. Not a bug --
this account would need a room manually named in the real app first before this specific test
could run at all. Low priority, entirely optional.

287/287 tests green (1 new regression test using the real confirmed values), ruff clean. Version
bumped to 0.1.8a0.

## Addendum (forty-fourth session): mission control CONFIRMED LIVE -- the biggest milestone of this project -- plus a room-naming mystery investigated

chairstacker ran the mission-command script again with the `irbt_topic_prefix` fix in place.
**Result: 10 OK, 0 failed, 0 skipped -- Start, Stop, Pause, Resume, and Dock all confirmed by the
user actually watching the robot react**, not just an error-free response. This is the single
most important open question this entire project has had, since the very first session's
classification of mission control as "structurally hard, not economically resolvable." It's
resolved now, live, on a real device.

This closes out the whole arc that started in the thirty-ninth session: the original shadow-
based approach was confirmed not working, two independent sources converged on a different
topic pattern, the missing `irbt_topic_prefix` value blocked testing that path at all, this
project deliberately fixed the underlying capture gap instead of guessing a third field name,
and the real value turned up byte-identical to the third-party project's example -- and now, at
the end of that whole chain, the actual command works. `docs/API_REFERENCE.md`/`README.md`
updated to reflect this as confirmed, not just strongly evidenced.

**The room rename test surfaced something worth investigating properly.** chairstacker's account
has named rooms in the real app (screenshot shown: named rooms inside two maps, "Whole House" and
"Master_Bathroom") -- but `_pick_test_room()` found none via `get_active_map_versions()`'s
`rooms_metadata`. chairstacker asked a sharp, specific question: could the space in "Whole House"
be the cause? Worth answering directly: no -- Python dict/string handling doesn't care about
spaces in values, and "Whole House"/"Master_Bathroom" are themselves MAP names (`P2MapVersion
.name`), not individual room names in the first place, so they were never going through the same
code path being questioned.

The more likely explanation: `RoomMetadataEntry.name` (from `get_active_map_versions()`) may
simply not be where the app-visible room names actually come from at all -- there's a
COMPLETELY SEPARATE model, `RoomInfo`, sourced from the downloaded map bundle's own "rooms"
file, which also has a `name` field. Critically, `RoomInfo` has no `from_json()` in this
library -- unlike `RoomMetadataEntry`, its wire format was never confirmed by real data, only
the underlying Kotlin class's field names were. Building a fallback on it would just be trading
one guess for another.

**Response: investigate, don't guess.** `verify_map_edit.py` now, when no named room is found via
metadata, downloads and unpacks the map bundle and looks at its "rooms" file instead --
extracting ONLY non-geometry fields (room_id/name/type-shaped keys), explicitly filtering out
anything geometry/polygon/coordinate-shaped. This isn't a new confirmed model, just a way to see
the real structure so one can be built correctly next time, consistent with this project's
whole methodology.

**Clarified on request, mid-session:** the geometry exclusion here is scoped specifically to
this diagnostic report (which people paste into public GitHub issues without necessarily
thinking about it) -- it is NOT a statement that the library shouldn't support geometry.
`models.py`'s `RoomInfo` already has a `geometry` field and will genuinely be needed once an
actual map/floor-plan feature gets built. The two concerns (what a shared debug report should
print vs. what the library's data model should support) are separate, and this investigation
function only touches the former.

291/291 tests green (4 new for `_room_names_from_bundle()`, including a dedicated regression
test against ever leaking a geometry-shaped key under any casing), ruff clean.

## Addendum (forty-fifth session): confirming ALL 12 map-bundle read models at once, not just rooms

On follow-up ("do we have the right API names for the other bundle content types -- can't we get
this from a bundle request"), the honest answer was checked directly: NONE of the 12 map-bundle
read models in `models.py` (`RoomInfo`, `BorderInfo`, `TrajectoryInfo`, `CoverageInfo`,
`DockInfo`, `HazardInfo`, `NoMopZoneInfo`, `AdHocCleanZoneInfo`, `KeepOutZoneInfoRead`,
`VirtualWallInfo`, `CleanZoneInfoRead`, `FurnitureInfoRead`) have a `from_json()` -- every single
one was confirmed from Kotlin class field names only, never checked against real bundle content.
The forty-fourth session's `_room_names_from_bundle()` addressed this for rooms specifically, but
the user's question was broader: can this be settled for everything at once, from data this
project can already fetch (`download_map_bundle()`/`parse_map_bundle()` already work, confirmed
live)?

**Realized `_shallow_summary()` is already safe for this, no new filtering needed.** Re-reading
its implementation: it recurses at most 2 levels deep and returns `type(data).__name__` (or
`"..."` once the depth limit is hit) for any leaf value -- it was ALREADY incapable of leaking an
actual coordinate number, string, or ID, by construction, since it never returns the value
itself, only its type or a truncation marker. This means the `_room_names_from_bundle()`
approach (explicitly filtering out geometry-shaped KEYS) was more restrictive than strictly
necessary for a general structure-confirmation purpose -- correct for that function's specific
job (showing actual room NAME text, which needed a different, narrower kind of care), but not
the right tool for confirming field names across all 12 models at once.

**Implemented:** `diagnostics.py`'s existing map-bundle handling (previously capturing ONLY
filenames into `raw_capture`, deliberately, per the twenty-fourth session's privacy reasoning)
now ALSO captures `{filename: _shallow_summary(content) for filename, content in parsed.items()}`
for every file. One `roombapy-prime-validate --dump-config` run should now surface the real
field names for all 12 currently-unconfirmed models simultaneously, rather than needing a
separate investigation per model.

**Added a dedicated regression test** (`test_shallow_summary_safe_for_geojson_geometry`) against
a realistic GeoJSON Polygon shape specifically -- the pre-existing leak test only covered a
simple flat dict (address/email strings), not nested coordinate arrays, and this new reliance on
the function's safety property for genuinely sensitive floor-plan data deserved its own direct
verification rather than assuming the simpler test generalizes.

292/292 tests green (1 new), ruff clean.

## Addendum (forty-sixth session): schedules/DND resolved via the RobotStatusV2 technique, plus a documented hypothesis for region-based mission commands

On request, researched two remaining open items: region-based mission commands, and
schedule/DND write confidence.

**Schedules/DND: the `$$serializer` companion-class technique (first used for `RobotStatusV2` in
session 40) generalizes cleanly, and found a real bug.** Checked whether `ScheduleOptions`,
`HouseholdSchedule`, `HouseholdScheduleUpdate`, `ScheduleDateEntry`, `ScheduleTime`, `DNDSchedule`,
`DNDSchedule.DailySchedule`, `DNDSchedule.EndsAt`, and `DNDStatusResponse` have the same kind of
serializer companion class -- all nine do. Reading each one's `<clinit>` (extracting the literal
strings passed to `PluginGeneratedSerialDescriptor.addElement()`) gave real, confirmed wire keys
for all of them at once:

- **`ScheduleOptions` had four wrong keys**, all in the same direction as prior bugs in this
  project (camelCase guessed, snake_case real): `robot_id` (not `assetId`), `end_commands` (not
  `endCommands`), `created_time` (not `createdTime`), `force_cloud` (not `forceCloud`). The
  other 13 of 17 fields were already correct. Fixed, with a new dedicated test covering all four.
- **`HouseholdSchedule`/`HouseholdScheduleUpdate` had one wrong key each**: `schedule_id` (not
  `scheduleId`). Fixed.
- **`ScheduleDateEntry`/`ScheduleTime`/`DNDStatusResponse` were already entirely correct** --
  confirmed, not fixed. `DNDStatusResponse` in particular had been marked "presumably" correct
  since its introduction; this closes that out definitively.
- **`DNDSchedule.DailySchedule`/`DNDSchedule.EndsAt`**: new models added (`DNDDailySchedule`/
  `DNDEndsAt`), confirmed fields `dailyStart`/`dailyEnd` and `endsAt` respectively. `DNDSchedule`
  itself (the sealed-class parent combining these two variants) uses a lazy `cachedSerializer`
  delegate pattern in its own `<clinit>` rather than a directly-readable discriminator string --
  resolving the actual envelope/discriminator would need deeper tracing than this session
  pursued, the same kind of limit as the V1 edit commands' envelope elsewhere in this document.
  `set_dnd_settings()` therefore still accepts a raw dict -- these two new dataclasses exist for
  their own confirmed fields, not yet wired into the request-building path.

**Region-based mission commands: a documented, reasoned hypothesis, deliberately not live-tested
given the risk profile.** Compared the confirmed-working simple command payload
(`{"command": str, "time": int, "initiator": str}`) against `RoutineCommand.to_json()`'s own,
independently-confirmed field mapping (via @SerialName, since the third session) and found they
share exactly two key names: `"command"` (from `RoutineCommand.type`) and `"initiator"`
(RoutineCommand's own confirmed field, added in session 25). This is unlikely to be coincidence --
it suggests `cmd_topic()` may accept `RoutineCommand`'s fuller structure (regions/params/
p2map_id/favorite_id), with `"time"` added on top, rather than being a fundamentally unrelated
schema that happens to share two names.

Deliberately did NOT build a live-test path for this the way `verify_mission_commands.py`/
`verify_map_edit.py` exist for their respective areas. The risk profile is different from the
original topic-discovery problem: a wrong topic guess there produced safe, confirmed silence.
Sending a plausible-looking but subtly wrong RICH payload to a topic that DOES work could mean
a real device accepts something malformed and behaves unpredictably -- not zero risk. Implemented
instead as `send_routine_command_via_cmd_topic()` (`prime_robot.py`) / `publish_cmd_payload()`
(`mqtt_client.py`), clearly labeled EXPERIMENTAL/UNCONFIRMED in its own docstring, with an explicit
recommendation that any future experimentation should prefer a `favorite_id`-referencing
`RoutineCommand` (an already-defined routine the person set up themselves in the real app) over
hand-built `regions` -- so the actual room/zone definitions come from something the app itself
already validated, not from this library's own unconfirmed region-construction logic.

299/299 tests green (7 new: 4 for the schedule/DND fixes, 4 for the new experimental cmd-topic
path -- one shared between mqtt_client.py/prime_robot.py test files), ruff clean.

## Addendum (forty-seventh session): the biggest single static-analysis finding of this project -- all 12 map-bundle read models resolved at once, plus the file-naming question closed for good

On follow-up to the forty-sixth session's `$$serializer`-companion-class technique, asked whether
this could be pushed further systematically: a scan for EVERY `$$serializer` class across the
entire APK (not just the ones already suspected relevant) found **226 of them**. This is a
fundamentally different kind of investigation than anything in this document before -- not
targeted at one known-uncertain model, but a complete map of every kotlinx.serialization-backed
class in the app, most of which are irrelevant (UI navigation destinations, Compose screen state,
etc.), but a significant cluster of which directly resolves this project's single largest
remaining block of unconfirmed models: the 12 map-bundle read models, none of which had ever had
a `from_json()`, all marked since their introduction as "the overall envelope format... wasn't
investigated."

**The real structure, definitively confirmed:** every map-bundle content type is a standard
GeoJSON Feature -- `{"type": "Feature" (presumed), "id": str, "geometry": {...}, "properties":
{...type-specific...}}` -- NOT the flat `{id, geometry, name, ...}` shape every one of the 12
previous models assumed. Confirmed via each type's own `Feature$$serializer` (top-level envelope,
identical across all 12: type/id/geometry/properties) and `Feature$Properties$$serializer`
(type-specific fields), read the same way as `RobotStatusV2`/`ScheduleOptions` before them.

**Properties confirmed per type:**
- `RoomFeature.Properties`: adjacentRoomIDs, name, type, simplifiedGeometry
- `BordersFeature.Properties`: confirmed EMPTY (no fields beyond the shared envelope)
- `CoverageFeature.Properties`: operatingModes
- `DockFeature.Properties`: orientation
- `FurnitureFeature.Properties`: type, source, orientation, cleaningArea
- `HazardFeature.Properties`: type
- `TrajectoryFeature.Properties`: index, operatingModes
- `FloorPlanFeature.Properties`: type, roomId (a content type not previously modeled at all)
- `PolicyZoneFeature.Properties`: type, threshold_type
- `CleanZoneFeature.Properties`: name
- `AdHocCleanZoneFeature.Properties`: confirmed EMPTY
- `experimental.FloorTypeFeature.Properties`: type (an "experimental"-packaged content type in
  the decompiled source, consistent with being newer/less stable)

**A genuine collapse of three previously-separate guesses into one confirmed type.** The earlier,
long-standing puzzle -- "keepOutZones"/"noMopZones"/"virtualWalls"/"thresholds" had no dedicated
`P2MapInfoType` field found, unlike the other 11 confirmed discriminators -- is now explained:
there is no separate `KeepOutZoneFeature`/`NoMopZoneFeature`/`VirtualWallFeature`. There is one
`PolicyZoneFeature`, discriminated by `properties.type`/`properties.threshold_type` (exact values
for these not confirmed, no enum found, left as raw strings). The three previously-modeled,
never-confirmed classes (`NoMopZoneInfo`, `KeepOutZoneInfoRead`, `VirtualWallInfo`) are removed;
`PolicyZoneFeature` replaces all three.

**The bundle's own manifest, previously never suspected to exist as a distinct, modelable thing:**
`Manifest` -- `{metadata, features: [Manifest.Feature...], experimentalFeatures: [...]}`, where
`Manifest.Feature` is `{type, filepath, schemaVersion}`. **This DEFINITIVELY resolves the "exact
file naming inside the tar.gz bundle" question that had been open since the fifth session** --
the manifest itself is the bundle's own table of contents, naming the real filepath for every
other content type. `Metadata`/`Metadata.PICEASourceMetadata` (bundle-level metadata, including
`missionStartTime`/`mapUploadTime`) also newly modeled.

**What's still genuinely open, honestly, not glossed over:**
- The manifest file's OWN filename within the tar.gz (not listed inside its own features list,
  for obvious reasons) -- still needs a real bundle capture to confirm, though its CONTENT
  structure is now fully known, which is most of the way there.
- Whether each per-type file (at the filepath a `Manifest.Feature` points to) contains a bare
  `list[Feature]` or a full GeoJSON `FeatureCollection` wrapper (`{type: "FeatureCollection",
  features: [...]}`) -- `FeatureCollection`'s own `$$serializer` uses a generic/lazy pattern this
  technique couldn't read directly (same kind of limit as `DNDSchedule`'s sealed-class
  discriminator in the forty-sixth session). Standard GeoJSON convention (the wrapper) is assumed,
  not independently confirmed.
- Which `Manifest.Feature.type` string maps to which Python class (e.g. is it "rooms" or "room"?)
  -- the confirmed data is each class's OWN field structure, not the discriminator value that
  selects it. Real manifest content would resolve this immediately.
- `RoomFeature.Properties.room_type`'s value space: a quick sanity check during this session found
  that reusing the edit-side `RoomType` `IntEnum` (2100-2120) breaks on a plausible read-side
  string value ("BEDROOM") -- the read side may use an entirely different value space (string
  enum vs. numeric codes) than the edit-side `SetRoomType` command. Left as a raw, unconverted
  value rather than forcing an incorrect enum mapping -- a small but important instance of this
  project's own methodology (don't force a confirmed field NAME into an unconfirmed VALUE
  assumption just because a plausible-looking enum already exists nearby).

**Implementation:** complete rebuild of `models.py`'s read-model section -- 12 old flat
dataclasses removed, replaced by 11 new/rebuilt `XFeature`/`XFeatureProperties` pairs
(`PolicyZoneFeature` covers what used to be 3), plus `BundleManifest`/`ManifestFeature`/
`Metadata`/`BundleMetadataSource`, all with real `from_json()` methods for the first time. Four
small GeoJSON-parsing helpers added (`_point_from_geojson`/`_linestring_from_geojson`/
`_polygon_from_geojson`/`_multipolygon_from_geojson`) since the confirmed geometry is always
nested one level deeper (`data["geometry"]`) than the existing `Polygon`/`Point`/etc. classes'
own `to_geojson()` output expects on the way back out. Comments in `diagnostics.py`/
`verify_map_edit.py` referencing the old class names updated to reflect the new, stronger
confirmation status.

308/308 tests green (9 new, plus 3 existing tests rewritten for the new nested structure), ruff
clean. This is very likely the single largest jump in confirmed model coverage in this project's
history, achieved entirely through static analysis -- no live test or new user-provided data was
needed for any of it.

## Addendum (forty-eighth session): the V1 edit envelope resolved -- open since the fourth session -- plus several more response keys, via the same systematic technique pushed further

Continuing the forty-seventh session's "what else can the `$$serializer` scan resolve" thread,
targeted the specific remaining high-value unknowns: the V1 map-edit envelope, `HouseholdSettingOptions`,
`get_map_geojson_link()`'s response key, `create_favorite()`'s response key, and the live-map
response models.

**The V1 edit envelope -- open since the fourth session, now resolved.**
`EditMapV1Request$Body$$serializer` confirms the real request body is `{"edit_cmd": {...}, 
"response_type": "..."}`, not the flat `{"command": "<Name>", ...fields}` shape assumed since
the command family was first modeled. **8 of the 9 individual V1 commands' field names were
also directly confirmed**, and most were wrong, in the same camelCase-guessed/snake_case-real
direction as bugs found elsewhere in this project: `RenameRoom` (`room_id`/`room_name`, not
`id`/`name`), `MergeRooms` (`room_ids`, not `ids`), `SetRoomType` (`room_id`/`type_id`, not
`id`/`type`), `SetPermanentAreas` (`area_points`, not `areaPoints`), `DeletePermanentAreas`
(`area_ids`, not `areaIDs`), `SetVirtualWalls` (`virwall` -- an unusual abbreviation neither
guessed nor obviously predictable, not `walls`), `AdjustFurniture` (`furniture_list`/`package`,
not `furnitureList`/`packageInfo`). `SplitRoom`'s `room_id` was the one field that needed
correcting there too (`split_points` was already right).

**Two commands' internals remain genuinely unconfirmed, for a specific, now-understood reason.**
`SetRoomMetadata` and the `VirtualWall` Linear/Rectangle/NoMopZone discriminator both use
HAND-WRITTEN CUSTOM serializer classes (`...$Serializer`, not the auto-generated
`...$$serializer` pattern this whole technique depends on) -- confirmed by name
(`EditMapV1Request$Command$SetRoomMetadata$Serializer`,
`EditMapV1Request$VirtualWall$VirtualWallSerializer`), found via a broader search once the
`$$serializer`-only search came up empty for these two specifically. Resolving them would need
disassembling actual serialize()/deserialize() method bodies, not just reading a `<clinit>`'s
literal strings -- the same harder category of problem as the outer discriminator question
itself, DNDSchedule's sealed-class serializer (forty-sixth session), and the p2maps auth
mechanism. Not pursued this session; these two commands remain at their previous, weaker
confidence level.

**The outer discriminator inside "edit_cmd" itself also remains unconfirmed**, for a related
reason: none of the 8 confirmed Command `$$serializer` classes show a "type"/"command" key
among their OWN fields -- the discriminator must be added by the sealed-class polymorphic
serialization mechanism itself, not declared by any individual command. `"type":
"<CommandName>"` is used here as kotlinx.serialization's standard default behavior (matching
each subclass's simple name), which is plausible but not independently confirmed the way the
field names themselves now are.

**Three more response-key confirmations, closing smaller but real gaps:**
- `get_map_geojson_link()`'s response key is `map_url`, confirmed via `P2MapURL$$serializer` --
  previously entirely unconfirmed ("no dedicated response class found in the source code" was
  simply an earlier search that hadn't found it). `diagnostics.py`/`verify_map_edit.py`'s
  heuristic "first http-looking value" URL extraction now tries this confirmed key first,
  keeping the heuristic as a fallback.
- `create_favorite()`'s response key is `favorite_id`, confirmed via
  `FavoriteIdResponse$$serializer` -- the existing extraction code's fallback chain
  (`favorite_id` tried first, then `favoriteId`, then `id`) had already happened to guess
  correctly; now definitively confirmed rather than lucky.
- `HouseholdSettingOptions` -- a long-standing "structure not investigated" placeholder --
  confirmed via its own `$$serializer`: household demographic info (`hh_adults`/`hh_kids`/
  `hh_pets` counts, opt-out flags, a `hh_location_factor` field of unexamined meaning). New
  model added, `HouseholdSetting.options` still kept as a flexible raw dict pending confirmation
  that ALL `settingType` values genuinely share this same shape (plausible, not certain, given
  "household settings" could in principle cover more than just demographics).

**A genuine, unresolved tension found and honestly documented, not silently overridden.** The
live-map position-update models (`LiveMapStreamInit`, `MapUpdateMessage`) were both already
correct -- their existing field names matched `LiveMapStreamResponse$$serializer`/
`LiveMapUpdateResponse$$serializer` exactly, now upgraded from "presumed" to "confirmed" in their
docstrings. But `PositionUpdates$PositionUpdate$$serializer` surfaced something more
complicated: it confirms fields `point`/`orientation`/`operatingModes` -- suspiciously close to
this library's own `PositionSample` dataclass (`point`/`orientation`/`operating_modes`), which
was built to match values derived from parsing a flat `"cur_path"` array, not copied from this
serializer. Two genuine possibilities, neither confirmed: the actual wire format might be a
structured object matching `PositionUpdate` directly (meaning the existing `cur_path`-parsing
logic, credited to tracing a CUSTOM `PositionUpdatesSerializer`'s deserialize() method in an
earlier session, might be based on a mistaken reading), or both might genuinely coexist (the
custom serializer packing/unpacking a list of these structured objects into the flat array as an
optimization, with `PositionUpdate` only ever existing Kotlin-side, never as its own JSON shape).
Deliberately NOT changed without stronger evidence either way -- documented in full in
`PositionUpdateMessage.from_json()`'s own docstring, flagged for a live traffic capture or a
proper disassembly of the custom serializer's actual method bodies to resolve.

**A process note, in the interest of complete honesty:** partway through this session, a
malformed tool call (a mistyped parameter name) accidentally deleted the entire body of
`DeletePermanentAreasV1` without replacing it, corrupting the file (confirmed via both a failed
runtime import and a cascading `dataclass` error in an unrelated neighboring class caused by a
duplicated decorator line). Caught immediately via an actual import test (not just a syntax-only
`py_compile` check, which had passed despite the corruption -- a good reminder of why this
project runs both), and repaired in the next step before any further work continued. Mentioned
here not because it changed any conclusion, but because this document's own standard is to
record what actually happened, mistakes included.

317/317 tests green (11 new: 8 for the V1 commands' corrected wire formats, 1 for
`HouseholdSettingOptions`, plus 1 existing test corrected for the new envelope), ruff clean.

## Addendum (forty-ninth session): finishing two more threads from the same 226-class scan -- RobotStatusV2's nested types, and get_default_routines()'s full envelope

Continued working through the remaining high-value classes from the forty-seventh session's
full `$$serializer` scan.

**`RobotStatusV2`'s four list/dict fields, previously left as `list[Any]`, now properly typed.**
`DockControl$$serializer` confirms `control`/`status`; `RobotStatusV2$Button$$serializer`
confirms `status`/`action`; `RobotStatusV2$RobotError$$serializer` AND
`RobotStatusV2$ConditionalRobotError$$serializer` both confirm the exact same fields
(`error_id`/`bucket`/`allowed_modes`) despite being two distinct Kotlin classes -- one shared
`RobotStatusError` dataclass models both here, since the two are structurally identical and no
difference beyond the class name itself was found.

**`get_default_routines()`'s response was only ever partially modeled -- the per-routine shape,
never the envelope around it.** `RoutinesDefaultsResponse$$serializer` confirms the top-level
shape is `{routines: [...], routine_builder_defaults: {...}}` -- the latter (region-type-based
default operating-mode settings, e.g. default suction level per room type) was never even
captured by the old `parse_default_routines()` helper, which only ever extracted the `routines`
list. New models added: `RoutinesDefaultsResponse`, `RoutineBuilderDefaults` (`regions: [...]`),
`RegionDefaults` (`type`/`operating_mode`/`by_operating_mode`), `OperatingModeProfile`
(`params`/`profile_type`).

**`Routine` itself had wrong keys, confirmed and fixed the same way as `ScheduleOptions` before
it.** Real keys: `last_run`, `name_loc_key`, `name_loc_args`, `time_estimate`,
`time_estimate_seconds` (snake_case, not the previously-guessed camelCase) -- and, more
unusually, `commanddefs`: all lowercase, no separator at all, neither camelCase nor snake_case.
`parse_default_routines()`'s old envelope-key guess (`"routines" or "defaults"`) is also resolved
-- `"routines"` was already right, the `"defaults"` fallback guess is dropped as no longer
needed.

319/319 tests green (7 new: 4 for `RoutinesDefaultsResponse`/`RegionDefaults`, plus corrections
to 2 existing tests for the new confirmed keys), ruff clean. This closes out the most valuable
remaining items from the forty-seventh session's 226-class scan -- what's left in that list is
mostly either UI-navigation-internal (not relevant to this library) or lower-value cross-checks
of fields already confirmed a different way (e.g. `CommandParams`/`RoutineCommand`'s own
`$$serializer`s, which would only re-confirm what @SerialName reading already established).

## Addendum (fiftieth session): independent re-verification of all 65 previously-confirmed classes -- everything held up, one stale docstring found

On request, re-verified ALL 65 classes covered across the forty-sixth through forty-ninth
sessions' `$$serializer` work, rather than trusting the earlier extractions at face value.
Method: re-ran the exact same bytecode extraction independently (not reusing cached output),
then wrote direct functional round-trip tests against the ACTUAL current code (not just
comparing extracted strings side-by-side) for every one of the 65 -- `RobotStatusV2` and its 4
nested types, the `ScheduleOptions`/`HouseholdSchedule`/`DND` family (10), `FavoriteIdResponse`,
the `HouseholdSettingOptions` family (3), the `Routine`/`RoutinesDefaultsResponse` family (5),
`P2MapURL`/`DeleteMapRequest.Body`/`P2MapGeoJSONRequest.Body` (3), all 9 V1 edit commands, all 5
LiveMap response models, and all 14 map-bundle Feature/Properties/Manifest/Metadata models (28
including the empty-Properties confirmations).

**Result: every single one of the 65 re-extracted exactly matches the original findings, and
every functional round-trip test against the actual code passed.** No regressions, no
transcription errors, no silent mismatches between what was claimed confirmed and what the code
actually does -- despite the rapid pace of the preceding four sessions' changes (including the
one session where a malformed tool call briefly corrupted a file, since repaired and itself
re-verified here again for good measure).

**One genuine, if minor, finding**: `delete_map()`'s docstring still described its `"visible"`
field as "found without a @SerialName... presumably serializes directly under the property name"
-- a weaker, pre-`$$serializer`-era characterization left over from an earlier session, even
though this session's own re-verification pass directly confirmed it via
`DeleteMapRequest$Body$$serializer`'s `<clinit>` (the field code itself needed no change, only
the docstring's confidence framing).

319/319 tests green (no new tests needed -- this was a verification pass using the existing
suite plus ad-hoc functional checks, not new coverage), ruff clean. This kind of
independent re-verification, done periodically rather than only when something seems wrong, is
worth repeating after any comparably large, fast-moving batch of bytecode-derived changes.

## Addendum (fifty-first session): the remaining high-value classes from the 226-class scan -- two long-standing placeholders resolved, one more genuine bug found

Continued through the classes identified as "further" targets after the fiftieth session's
re-verification pass: the `edit_map()` response shape, the schedule envelope, and the map
settings-edit commands.

**`get_map_metadata()`'s response shape, unmodeled since the library's early sessions, now
confirmed.** `P2MapData$$serializer` confirms `p2map_id`, `active_p2mapv_id`, `create_time`,
`last_p2mapv_ts`, `state`, `visible`, `name`, `user_orientation_rad` -- the last two matching
`set_map_name()`/`set_map_orientation()`'s own confirmed write-side keys exactly, a nice
cross-check that read and write sides agree on the same concept. `get_map_metadata()` now
returns this parsed directly instead of raw JSON.

**`get_schedules()`'s envelope, previously only half-found (the class NAMES had turned up in an
earlier session, never their fields), now confirmed.** `SchedulesResponse$$serializer`/
`SchedulesList$$serializer` confirm `{household_schedules: [{household_schedule_id, schedules:
[...]}]}`. Now parsed directly.

**A genuine bug found in `set_map_name()`, not just an unconfirmed guess.** It was sending
`{"type": name}` -- confirmed via `EditMapSettingsRequest$Command$SetName$$serializer` that the
real field is `name`. `set_map_orientation()`, checked at the same time, was already correct
(`user_orientation_rad`, matching its own confirmed field exactly).

**Three new models for `edit_map()`'s possible response/error shapes**, found alongside the map
settings-edit classes: `P2MapEditPartialSuccess` (`status`/`p2mapv_id`/`p2map_metadata`),
`P2MapEditSuccessFallback` (same plus `map_url`), and a generic `ResponseError` (`code`/`message`,
with two wrapper shapes -- `{"error": {...}}` and, distinctively, `{"Message": "..."}` with a
capital M, confirmed exactly as-is). `ResponseError`'s shape is used identically in two places in
the app (a generic `data.restservices.utils` version and a map-editing-specific `P2MapError`
version, field-for-field identical) -- modeled once here rather than duplicated. None of these
three are wired into automatic parsing yet -- which shape `edit_map()`'s response actually takes
for a given request isn't confirmed, so these exist as available building blocks for a caller
who wants to attempt parsing a specific expected shape, not an automatic pipeline.

**Checked and deliberately left unimplemented, lower priority:** `ScheduleCommand`
(`{command}`, single-field wrapper, purpose unclear without more context), `ScheduleListUpdate`
(`{schedules}`, another list wrapper, unclear how it differs from `SchedulesList`/
`SchedulesResponse` without more investigation), `ScheduleData` (in the `automations` package,
camelCase fields unlike the REST API's confirmed snake_case -- likely a different, UI/internal
representation, not the wire format this library's REST client needs), `StoredSpaceItem`
(`{assetId, mapId, mapVersion}`, also camelCase -- likely internal/UI-facing, not confirmed
relevant to any endpoint this library implements). Documented here rather than silently dropped,
in case a future session finds a reason to revisit them.

327/327 tests green (11 new, 4 tests updated for the two newly-parsed response types), ruff
clean.

## Addendum (fifty-second session): a fourth independent confirmation of irbt_topic_prefix, a previously-missed credential model, and a genuine pre-existing security gap fixed

On follow-up to "what else" after the fifty-first session, checked two more targets before
concluding the `$$serializer` scan has reached diminishing returns: `DiscoveryResponse.Deployment`
(a cross-check against the already live-confirmed `irbt_topic_prefix`) and the `foundation/models`
package's own `Robot` class (previously never swept, since prior sessions focused on
`missioncommand`/`maps` packages).

**`irbt_topic_prefix` now has a fourth independent confirmation.**
`DiscoveryResponse$Deployment$$serializer` directly confirms `iotTopics`/`irbtTopics` as field
names, bytecode-matching exactly what chairstacker's real account (session 43) and the
third-party GitHub project (session 39) had already independently agreed on. Live data, an
external project, and the app's own compiled source all now agree -- about as complete a
confirmation as this kind of question gets.

**`LoginResult.robots`, completely unmodeled since the field was introduced, now has a real
model.** `Robot$$serializer` (plus nested `Robot.Capabilities`/`Robot.DigitalCapabilities`)
confirms `id`, `password`, `sku`, `softwareVer`, `name`, `cap`, `digiCap`, `svcDeplId`,
`user_cert`. `cap`/`digiCap` matching the exact top-level keys already seen in real `get_state()`
capture data is a nice independent cross-check that this is genuinely the same concept as
elsewhere. New `RobotLoginEntry`/`RobotCapabilities`/`RobotDigitalCapabilities` models added,
cross-checked against real (anonymized) fixture data already present in the test suite
(`login_response_smart_tier.json`) -- every confirmed field matched the fixture's actual content
exactly.

**A genuine, pre-existing security gap found and fixed while adding this new model.**
`RobotLoginEntry.password`/`user_cert` are real per-device credentials -- adding them without
protection would have made a bad situation (secrets printable via default repr) worse by giving
it a new, more discoverable surface (a typed `.password` attribute, easier to stumble into via
autocomplete than digging through a raw dict). Checking the EXISTING credential-bearing classes
found the same gap already present and unaddressed: `CloudCredentials.secret_key`/
`session_token` and `ConnectionToken.iot_token`/`iot_signature` had no `repr=False` protection
either, meaning a default dataclass repr would print real secrets in plain text on any accidental
`print()`/log statement/exception traceback involving these objects -- something this project has
otherwise been careful about elsewhere (e.g. never logging full tokens), just missed for these
three classes until this session. All three fixed together, consistently.

**Assessment: the `$$serializer` scan has now reached genuinely diminishing returns.** Of the
226 classes originally found, roughly 90 have now been directly used to confirm or correct this
library's models; nearly all the rest are either UI-navigation/Compose-screen-state (irrelevant to
a REST/MQTT client library) or would only re-confirm fields already independently confirmed via
real data or `@SerialName` reading (`CommandParams`/`RoutineCommand`/the `MissionTimelineEvent`
family). Further progress from here needs live data again, not more static analysis of this
particular vein.

329/329 tests green (5 new: 1 for the new topic-prefix cross-confirmation context, 3 for
`RobotLoginEntry` incl. a dedicated repr-security regression test, 1 fixture-based), ruff clean.

## Addendum (fifty-third session): architecture review -- a real wiring gap found across five methods, models.py's size flagged

On request, stepped back from further bytecode-derived field research to review the library's
overall architecture: module sizes, API consistency, and whether recent rapid iteration had left
inconsistencies behind.

**The most significant finding: several methods had a confirmed response model sitting unused.**
Checking every `rest_client.py` method's return type found 24 still returning bare
`dict[str, Any]`, against only 4 returning a proper parsed model. Cross-referencing each of the
24 against `models.py` found that `get_robot_parts()`, `get_serial_number_data()`,
`get_dnd_settings()`, and `get_default_routines()` all had docstrings explicitly saying "response
shape modeled" or naming a specific parser (`RobotPartsInfo`, `RobotSerialInfo`,
`DNDStatusResponse`, `RoutinesDefaultsResponse`/`parse_default_routines()`) -- confirmed, tested
models that had simply never been called from the method itself. This is exactly the kind of gap
that's easy to miss turn-by-turn (each individual session's own docstring update looked complete
in isolation) but stands out once every method's actual behavior is checked against what its own
documentation claims. All four fixed to return the parsed model directly, `PrimeRobot`'s
wrappers updated to match, and each of the four existing tests (which had only ever checked
URL/method, never the return value) given a real assertion against the parsed result.

**`get_cleaning_profiles()` deliberately left as raw dict.** Unlike the four above, its response
ENVELOPE (bare list vs. some wrapped shape) was never actually confirmed, only the per-entry
`CleaningProfile.from_json()` shape -- wiring it up would mean guessing the envelope, which this
project's own standing rule argues against. Left alone, correctly, rather than "completing" the
architecture fix with an unconfirmed guess.

**A cross-cutting security gap, found while checking for other unprotected credential fields
after last session's `RobotLoginEntry` fix.** Searched the whole codebase for
password/secret/token/cert-like field names -- found nothing else unprotected beyond what was
already fixed in the fifty-second session (`CloudCredentials`/`ConnectionToken`/
`RobotLoginEntry`). Good confirmation that fix was complete, not partial.

**`models.py` at 4213 lines is genuinely large** -- flagged, not addressed this session. Splitting
it (e.g. by feature area: mission control, map bundle/editing, schedules/DND, favorites/routines,
shared geometry primitives) would be a real, worthwhile improvement, but is its own
non-trivial, higher-risk refactor (import cycles, re-export surface, updating every test file's
imports) better done as its own deliberate piece of work rather than folded into this review.

**Checked and found NOT to be a problem:** the three CLI scripts
(`diagnostics.py`/`verify_mission_commands.py`/`verify_map_edit.py`) share their common helpers
(`_shallow_summary`/`_redact_raw_capture`/`build_issue_url`/`_confirm`) via imports rather than
duplicating them -- no cleanup needed there.

329/329 tests green (0 new -- this was a wiring/consistency fix, existing tests strengthened
with real assertions rather than net-new coverage), ruff clean.

## Addendum (fifty-third session, continued): documentation staleness -- examples/, API_REFERENCE.md, and README.md, checked systematically rather than assumed clean

Directly challenged on whether the architecture review above was actually systematic (fair --
it wasn't yet, at that point). Continued with concrete checks rather than moving on: actual
`pytest-cov` output (91%, matching the documented baseline, no regression), a real completeness
diff of every `rest_client.py` method against every `self._rest.` call in `prime_robot.py`
(complete, nothing missing), and then a pass through `examples/`, `docs/API_REFERENCE.md`, and
`README.md` against the CURRENT code -- not from memory of what they were supposed to say.

**The most consequential finding: `examples/mission_control.py` was actively wrong, not just
outdated.** It still used `send_mission_command()`/`RoutineCommand` -- the exact transport this
project spent from the thirty-ninth through forty-fourth sessions confirming does NOT work for
basic commands. Anyone running this example as written would hit the identical timeout bug that
took this project a long chain of sessions to diagnose and fix. Rewritten to use
`send_simple_command()`, the confirmed-live path, with an accurate warning about it being
fire-and-forget (watch the robot, don't expect a response). `basic_usage.py`/
`favorites_and_history.py` checked against current model field names and method signatures
directly (not just skimmed) -- both still accurate, no changes needed there.

**`docs/API_REFERENCE.md` had accumulated substantial staleness** across several dimensions:
- Six method return types (`get_map_metadata`, `get_schedules`, `get_dnd_settings`,
  `get_default_routines`, `get_robot_parts`, `get_serial_number_data`) still showed `-> dict`,
  the exact thing this session's own architecture-review fix had just corrected in the actual
  code -- the doc simply hadn't been updated alongside.
- The entire "Mission control" section presented `send_mission_command()` as the primary,
  "single largest confidence gap" method, with **no mention of `send_simple_command()` at all**
  -- arguably the single most consequential correction this whole project has made, missing
  from its own reference documentation. Rewritten with `send_simple_command()` first,
  `send_routine_command_via_cmd_topic()` second, and `send_mission_command()` correctly
  demoted to "confirmed not working for basic commands, kept only for the unconfirmed
  region-based case."
- The map-bundle read-model table listed `RoomInfo`, `BorderInfo`, `HazardInfo`,
  `FurnitureInfoRead`, `TrajectoryInfo`, `CoverageInfo` -- **none of which exist anymore**,
  renamed to `RoomFeature`/`BorderFeature`/etc. in the forty-seventh session. Anyone trying to
  `from roombapy_prime.models import RoomInfo` following this doc would get an `ImportError`.
- `get_notifications()`'s documented default was still `app_version="1.0"` -- the exact
  evidence-free placeholder corrected to `"2.2.4"` back in the thirty-sixth session, over fifteen
  sessions ago.
- `edit_map()`'s envelope was still described as "inferred by analogy, not confirmed" -- true
  before the forty-eighth session, false after it (the envelope itself is now confirmed; only
  the inner discriminator and two commands' custom serializers remain uncertain).
- `get_map_geojson_link()` had no entry in the reference at all, despite being a real, existing,
  now partially-confirmed (`map_url` key) method.
- The `models.py` class-count estimate said "~85 classes total" -- the actual count is 154,
  roughly 60% more classes have been added since that estimate was last checked.
- A self-caught mistake during this same pass: an edit intended to add a `HouseholdSettingOptions`
  mention accidentally created a duplicate `get_user_households()` table row with an incorrectly
  guessed return type (`list[HouseholdSetting]`, when the real, checked-against-the-actual-code
  type is still `dict`) -- caught immediately by checking the actual source rather than assuming
  the edit was correct, and fixed by merging into the original, correct row instead.

**`README.md` was in comparatively good shape** (the mission-control confidence table and
quick-start example were both already accurate, likely kept current during the sessions that
made those specific changes) -- but its "known unresolved gaps" list had two stale entries: the
map-edit envelope (same resolved-in-session-48 issue as above) and "exact file naming inside
downloaded map bundles" (resolved in session 47 via `BundleManifest` -- only the manifest's OWN
filename remains open, not every other file's).

**Honest assessment of what this confirms about the review process itself:** documentation and
examples drift out of sync with working code even in a project that has been unusually
disciplined about updating `CHANGELOG.md`/this gap-analysis document after every session --
because those two documents describe *what changed*, while `README.md`/`API_REFERENCE.md`/
`examples/` describe *the current state*, and it's the second kind that silently rots if not
periodically re-checked against the actual code rather than trusted from memory of having
written it correctly once. This kind of check is worth repeating on a regular cadence, not just
when directly asked "did you really check everything."

329/329 tests green (no test changes -- this was a documentation-only pass), ruff clean.

## Addendum (fifty-fourth session): a dedicated security review -- one real fix, one defense-in-depth fix, several areas checked and confirmed clean

On direct request, reviewed the core library specifically from a security/hardening angle,
distinct from the correctness-focused reviews of prior sessions.

**Checked and confirmed clean, no changes needed:**
- **Tar extraction** (`parse_map_bundle()`): reads archive members into memory via
  `tarfile.extractfile().read()`, never calls `extractall()` or writes to the filesystem using
  archive-embedded paths -- the classic tar/zip-slip path-traversal pattern (CVE-2007-4559 style)
  does not apply here, since `member.name` is only ever used to build an in-memory dict key.
- **Command/code injection**: no `subprocess`, `os.system`, `eval`, or `exec` calls anywhere in
  the library.
- **TLS/certificate verification** (`mqtt_client.py`): `client.tls_set()` does not set
  `cert_reqs`, meaning paho-mqtt's own default (`ssl.CERT_REQUIRED`) applies -- no `CERT_NONE`,
  no `tls_insecure_set()` anywhere. Verified this directly rather than trusting the module
  docstring's own claim ("real cert verification") at face value.
- **Signature comparison** (`aws_sigv4.py`): `hmac.new()` is only ever used to GENERATE an
  outgoing signature (client signing its own requests), never to compare/verify a received one
  against a stored secret -- no timing-attack-relevant string comparison exists here at all.
- **Password handling in the three CLI scripts**: credentials always come from an environment
  variable or an interactive `getpass.getpass()` prompt, never a `--password` command-line flag
  (which would be visible to any other process via `ps aux` on a shared system) -- already
  correctly implemented.
- **Logging**: every `_LOGGER` call across the library checked directly; none interpolate
  credential material.

**One genuine, real fix: URL path segments were never encoded.** Every identifier this library
embeds into a URL path (BLIDs, p2map IDs, favorite IDs, household IDs, and more -- 22 call sites
total) was interpolated via a raw f-string, with zero escaping. In this library's own typical
usage these values come from a trusted source (this library's own login/API responses, or values
a developer passes explicitly) -- but this library itself enforced nothing, and the explicit
long-term goal of integration into a Home Assistant custom component (`ha_roomba_plus`) means a
future config-entry value, however unlikely to be attacker-controlled in practice, could in
principle reach one of these parameters uninspected. A value containing `/` or `..` could
redirect a request to an unintended path on the same host. Fixed with a new `_path_segment()`
helper (`urllib.parse.quote(value, safe="")`) applied mechanically at all 22 sites -- a
deliberately low-risk, purely additive change: URL-encoding a legitimate BLID/UUID (plain
alphanumeric/hyphen characters) is a no-op, so no existing behavior changes for any well-formed
input, confirmed by the full test suite passing unchanged plus two new dedicated tests (one for
the helper directly, one proving the fix reaches a real call site end-to-end).

**One defense-in-depth fix, not an active leak:** `_redact_raw_capture()`'s key-based redaction
covered `password`/`accesskeyid`/`secretkey`/`sessiontoken` (the AWS Cognito credential field
names) but not `iot_token`/`iot_signature` (`ConnectionToken`'s actual MQTT connection
credentials) or `user_cert` (`RobotLoginEntry`'s per-device certificate, added just two sessions
ago) or `cognitoid`. Checked whether any CURRENT `raw_capture` call site actually captures one of
these objects -- none do, so this was a latent gap, not something an existing `--dump-config`
run could currently leak. Fixed anyway: this function's entire purpose is to be a
general-purpose safety net for whatever ends up captured, not just the specific fields anyone
happened to think of when writing a given capture site, and a future session adding a new
capture point (plausible, given how many already exist for other response types) could otherwise
silently reintroduce exactly this gap.

**Noted, not fixed -- lower priority or higher-effort tradeoffs, documented rather than silently
skipped:**
- **Decompression-bomb protection**: `parse_map_bundle()`'s `tarfile.open(mode="r:*")` has no
  size limit on the decompressed content -- a malicious or corrupted gzip payload could exhaust
  memory. The practical risk is limited (the archive comes from a presigned URL this library
  itself requested from an already-authenticated API response, not directly attacker-supplied),
  but adding an explicit size cap during extraction would be a reasonable, moderate-effort
  hardening step for a future session.
- **`download_map_bundle(url)` has no scheme/host allowlist**: it fetches whatever URL string is
  passed to it, with no validation that it's actually the expected S3/CDN host. Same "comes from
  an already-trusted response in normal use" caveat as above applies, but this is worth noting as
  an SSRF-adjacent gap if that trust assumption were ever violated (a compromised response, a bug
  elsewhere that constructs this URL differently than intended).
- **MQTT topic construction** (`cmd_topic()`/`livemap_topic()`) also interpolates `blid` without
  escaping, which could in principle allow MQTT wildcard character (`+`/`#`) injection into a
  topic string. Lower priority than the REST path-segment issue: `blid` is set once at
  `PrimeRobot` construction (from the login flow), not a per-call parameter varying with each
  request, so the practical injection surface is much narrower than the 22 REST call sites that
  take a fresh identifier on every call.
- **A dead code path in `mqtt_client.py`'s TLS setup**: `except ImportError: ca_certs = None`
  around the `import certifi` call can essentially never trigger, since `certifi` is a hard
  declared dependency in `pyproject.toml` -- worth simplifying (removing the try/except, since
  the import should never fail in a correctly installed environment) rather than leaving an
  unreachable branch that silently downgrades certificate validation behavior if it were ever
  hit in some unexpected environment state.

332/332 tests green (3 new: one for `_path_segment()` directly, one proving it reaches a real
call site, one for the expanded redaction key set), ruff clean.

## Addendum (fifty-fifth session): models.py split into a package, done in one pass and verified thoroughly

Following up on the architecture review's flagged-but-not-fixed item (`models.py` at 4213 lines,
154 classes), executed the split in full rather than incrementally, per direct instruction, with
correspondingly thorough verification given the scope.

**Method: AST-based extraction, not manual line-finding.** Rather than manually locating section
boundaries via `grep`, used Python's own `ast` module to parse the original file and get the
EXACT start/end line for every one of the 174 top-level definitions (classes, functions,
module-level assignments like `MapEditCommandV1 = ...`). This is inherently precise -- no risk of
an off-by-one error at a section boundary the way manual line-range guessing could produce.

**The split, by feature area rather than by session:**
`geometry` (Position/Point/Polygon/etc., no internal deps), `enums_common` (RoomType/
FurnitureType/`_enum_or_none`, small and shared), `livemap`, `map_bundle` (the 12 GeoJSON Feature
types), `map_editing` (V1+V2 edit commands), `mission_control` (RoutineCommand/CommandParams/
Region), `favorites`, `schedules_dnd`, `mission_history` (including all 20 `MissionTimelineEvent`
sub-events), `robot_info` (parts/serial/settings/status/cleaning-profiles/default-routines) --
plus `__init__.py` re-exporting everything.

**The re-export layer is what makes this safe.** Python doesn't distinguish between a module
(`models.py`) and a package (`models/` with `__init__.py`) from an importer's perspective --
`from roombapy_prime.models import X` behaves identically either way. `__init__.py` does
`from .submodule import *` for all ten submodules in dependency order, meaning all 132 import
lines across `rest_client.py`/`prime_robot.py`/`auth.py`/`diagnostics.py`/both `verify_*.py`
scripts/`test_models.py`/other test files needed ZERO changes.

**A genuinely useful discovery mid-process: the test suite alone wasn't sufficient verification.**
After generating all ten files and wiring a first-pass `__init__.py`, the full test suite went
from 0 to 305/332 passing with the remaining 27 all `NameError` -- straightforward to fix
(missing cross-module imports for names like `CommandParams`/`Region`/`_enum_or_none`/the
geometry `_xxx_from_geojson` helpers). But once all 332 tests passed, `ruff check` STILL found 9
more genuinely missing imports (`Position`, `Polygon`, `MultiPolygon`, `LineString`, `Point`,
`RoutineCommand` in various files) that the test suite's own runtime behavior had never
triggered -- because `from __future__ import annotations` defers every type annotation to a
string, meaning `class Foo: bar: SomeUndefinedType` never raises `NameError` at import OR at test
time unless something actually calls `typing.get_type_hints()` or similar. These missing imports
were real defects (would break any type checker, IDE, or future code path that evaluates
annotations) that a green test suite alone did not reveal -- `ruff check` was the tool that
caught them. This is a concrete illustration of why "the tests pass" and "the code is correct"
aren't quite the same claim, especially in a codebase using deferred annotations throughout.

**Full verification chain, not just "tests pass":**
1. Full test suite (332/332) after fixing all `NameError`s from missing cross-module imports.
2. `ruff check --fix` closed the remaining 9 `F821` (genuinely undefined names) plus 61 `F401`
   (unused stdlib imports left over from the mechanical per-file header generation).
3. A dedicated completeness check: parsed the ORIGINAL file's 174 top-level names via `ast`
   again, then confirmed every single one is still an attribute of the new `roombapy_prime.models`
   package -- only the 6 underscore-prefixed private helpers were "missing" from this check, which
   is correct and expected (`import *` deliberately excludes them by Python convention, and a
   separate `grep` confirmed no file outside `models/` itself ever imported these directly).
4. Every other library module (`rest_client`, `prime_robot`, `auth`, `diagnostics`,
   `verify_mission_commands`, `verify_map_edit`, `prime_factory`, `mqtt_client`) imported cleanly.
5. A fresh `python -m build` confirmed the wheel actually contains all 10 new submodule files
   (standard setuptools package discovery picked up the nested package with no `pyproject.toml`
   changes needed).
6. The full test suite was run AGAIN from a completely fresh virtualenv against the built-and
   -installed wheel (not just the working directory) -- this is the gold-standard check, since it
   rules out any working-directory-only artifact (stale `.pyc`, an import shadowing the real
   package, etc.) that a from-source test run could miss.
7. Coverage re-checked: 91% overall, identical to the pre-split baseline -- confirms no tested
   code path was accidentally dropped during extraction, only relocated.

**Result:** largest file is now 787 lines (`robot_info.py`), down from 4213 in one file. Total
line count actually dropped slightly (4213 -> 3931) due to consolidating repeated per-section
header comments into more compact per-module docstrings during generation, plus the unused-import
cleanup.

**Not done, deliberately:** did not rename the many scattered docstring references elsewhere in
the codebase that say "see models.py::RobotStatusV2" or similar (dozens of them, across
`rest_client.py`/`prime_robot.py`/`auth.py`/`models/` itself) to say "models/robot_info.py"
instead -- "models.py" remains an understandable, if slightly imprecise, shorthand for "the models
layer" in these comments, and an exhaustive rename pass across every such reference was judged
low-value relative to its size for this session.

332/332 tests green (0 new -- this was a pure reorganization, not a behavior or coverage change),
ruff clean.

**Follow-up, same session:** on direct question, reconsidered the earlier "not done, deliberately"
decision to leave scattered `models.py` docstring references unchanged. Separated the 57 total
references into two genuinely different categories: historical session-log entries
(`CHANGELOG.md`, this document, `docs/DEVELOPMENT_NOTES.md`'s dated confidence tables) that
correctly describe what was true AT THE TIME they were written -- these stay untouched, rewriting
them would be anachronistic, the same principle already applied elsewhere in this document -- versus
forward-looking navigational references in active code docstrings and current-state reference
docs (`API_REFERENCE.md`, `ROOMBAPY_COMPARISON.md`'s file-mapping table), which genuinely were
stale and misleading about the current file layout. Fixed all 22 references in
`__init__.py`/`diagnostics.py`/`prime_robot.py`/`rest_client.py`/both `verify_*.py` scripts plus 4
in the two current-state docs, pointing each at its correct submodule (e.g. `models.py::
RobotStatusV2` -> `models/robot_info.py::RobotStatusV2`). 332/332 tests still green, ruff clean --
this was a docstring-text-only pass, no behavior change.

## Addendum (fifty-sixth session): a genuine second account, second device owner -- jadestar1864, 28/28 non-skipped checks pass

First live validation from a SECOND real user against a SECOND real household -- everything prior
to this point came from a single tester (chairstacker) on a single account. jadestar1864 ran
`roombapy-prime-validate` (v0.1.9a0, so this predates the fifty-fourth/fifty-fifth sessions'
security hardening and the package split, but DOES include all the response-model wiring fixes
from the fifty-third session) against a Roomba Plus 405 -- same SKU (G185020) as chairstacker's
device, but a different BLID (`2FB160A17D4ECE04A0FD062EDF4CB51D` vs. chairstacker's
`6F55705AE0BF169D69BDBFC9D858B5D2`) and therefore a genuinely different account/household, not a
second run against the same device. **Result: 28 OK, 0 failed, 2 skipped (mission
commands/map editing, which never run automatically by design).**

**What this newly confirms, doubly, on a second account:**
- Every read endpoint this library implements, including all the ones wired to return typed
  models rather than raw JSON in the fifty-third session's architecture review
  (`get_map_metadata`, `get_schedules`, `get_dnd_settings`, `get_default_routines`,
  `get_robot_parts`, `get_serial_number_data`) -- none of these had EVER been live-tested against
  a second account before this run.
- `get_settings()` succeeded again -- now consistent across every post-SUBACK-fix live run this
  project has record of, on two separate accounts.
- The `create_favorite`/verify/`delete_favorite` round trip, previously confirmed only on
  chairstacker's account, now independently confirmed on a second one.
- The map bundle download+parse pipeline works end-to-end on a second account (7 files found here
  vs. 8 for chairstacker -- both are subsets of the 11 `KNOWN_BUNDLE_INFO_TYPES`, consistent with
  bundles being sparsely populated based on what's actually configured on a given map, e.g.
  hazard/ad-hoc-clean-zone entries only appearing if the user placed one -- not a discrepancy to
  chase further).

**A genuinely valuable new data point on the still-open `RobotStatusV2` question.** This is only
the SECOND real `get_state()` capture this project has ever had (the first was chairstacker's, a
long-standing "the one real capture available" caveat throughout this document). jadestar1864's
`state.reported` keys: `cap`, `digiCap`, `nsmip`, `schedHold`, `sku`, `svcEndpoints` -- a SUBSET of
chairstacker's own 8 keys (`digiCap`, `nsmip`, `cap`, `cleanSchedule2`, `schedHold`, `sku`,
`svcEndpoints`, `soldAsSku`), missing only `cleanSchedule2` and `soldAsSku`. Two accounts, two
idle-robot captures, and neither shows ANY of `RobotStatusV2`'s 11 bytecode-confirmed fields
(`robot_state`/`battery_level`/`is_charging`/etc.) -- this doesn't resolve the question (still no
capture during an ACTIVE mission, which remains the leading hypothesis for where these fields
might actually populate), but it's a second, independent data point consistent with the first,
meaningfully stronger than having only one account's word for it. The `cleanSchedule2`/
`soldAsSku` gap between the two accounts on an otherwise-identical SKU is itself mildly
interesting (conditionally-present fields, perhaps tied to whether a schedule is actively
configured or a retail-channel marker) but not concerning -- `RobotSettings.from_json()` already
handles missing keys defensively via `.get()`.

**Relevance to the Beta-readiness discussion earlier this session:** this is exactly the kind of
second-account evidence that discussion identified as still missing, independent of the
(retracted) EPHEMERAL-tier framing. One data point doesn't fully resolve "device/account
diversity" as a category -- same SKU, not a different hardware generation -- but it's the first
time this project has ever seen its own read-endpoint surface behave consistently on an account
other than the one it was originally developed against, which is genuine, meaningful progress.

No code changes this session -- this addendum is a live-test confirmation record, not a fix.

## Addendum (fifty-seventh session): a real live crash found by chairstacker, fixed defensively without the raw data to confirm the exact root cause

chairstacker ran the map-edit test again on v0.1.10a0 -- same result as v0.1.9a0 (no named room
found via `get_active_map_versions()` or the map bundle's rooms file, so the test correctly
skipped rather than sending anything; 4 OK, 0 failed, 1 skipped). But a NEW, real crash surfaced
separately: `get_default_routines()` raised `AttributeError: 'str' object has no attribute 'get'`.

**Root cause, reasoned but not directly confirmed (the raw JSON wasn't available for this specific
call -- chairstacker sent the v0.1.9a0 `--dump-config` output through a private channel, not
pasted here, and this crash is newer than that capture).** The confirmed bytecode
(`RoutinesDefaultsResponse$$serializer`) says `routines` is a `List<Routine>` -- but this exact
error shape (a bare string handed to a function expecting a dict) is EXACTLY what happens if the
real value is a JSON OBJECT keyed by routine ID/type instead of a JSON array: iterating a Python
dict walks its string keys, and each key would get passed straight into `Routine.from_json()`,
which immediately calls `.get()` on it. This is not a wild guess -- the exact same
dict-keyed-by-ID pattern already exists elsewhere in this project's own confirmed models (e.g.
`RoomMetadataEntry.operating_mode_defaults`, keyed by operating-mode ID strings like `"512"`), so
it's a well-precedented hypothesis for this specific field too, even without the raw bytes in
hand to prove it definitively.

**Fixed defensively for both possible shapes**, rather than committing to one guess: a new
`_parse_routines_list()` helper handles `routines` whether it's a dict (uses `.values()`) or a
list, and silently skips any individual entry that isn't a dict rather than crashing the whole
parse over one malformed item. Both `RoutinesDefaultsResponse.from_json()` and the older
`parse_default_routines()` convenience function now share this same helper. Verified against the
exact crash shape chairstacker hit (a list of bare strings) and the leading dict-keyed hypothesis,
both now handled without raising.

**Still open:** without the actual raw response for this specific call, the dict-keyed hypothesis
remains reasoned rather than confirmed -- if chairstacker (or anyone) can share the actual
`get_default_routines()` response content (via `--dump-config`, reviewed for anything sensitive
before sharing, same caution as always), that would let this be confirmed definitively rather than
inferred from precedent and the shape of the error alone.

3 new tests, 335/335 green, ruff clean.

## Addendum (fifty-seventh session): the first genuinely comprehensive real --dump-config capture -- one critical bug found and fixed, several fields corrected against evidence stronger than bytecode

chairstacker ran a full `roombapy-prime-validate --dump-config` against v0.1.9a0 and shared the
complete output -- by far the richest single piece of real evidence this project has had,
covering nearly every read endpoint's actual field-level content in one capture (not just
pass/fail). Went through it systematically rather than skimming for surprises.

**Confirmed, with no changes needed, field-for-field:**
- `RobotSettings` -- every single field in the real `get_settings()` response (27 keys including
  `langs2`'s full nested structure) mapped correctly, nothing missing, nothing mistyped.
- The entire `MissionTimelineEvent` sub-event system -- all 8 real timeline events in one real
  mission (`padWash` x2, `evac`, `travel` x2 with both `TravelDestination.DOCK` and `.ZONE`
  correctly resolving, `zone`, `reloc`, `start`) parsed into exactly the right sub-event
  dataclass, first time this whole 20-type system has been checked against real data rather than
  synthetic examples.
- `RobotPart.last_updated_ts` and `CommandParams.gentle_mode` -- both already correctly present.

**One critical, genuinely serious bug found and fixed.** `RoutineBuilderDefaults.regions` was
modeled as a list (`session 49`'s own honest caveat: bytecode's generic-erasure limitation meant
this was a guess, not a confirmation). The real response shows it's a dict keyed by region ID
(`{"15": {...}, "100": {...}, ...}`) -- the exact same pattern as `RoomMetadataEntry
.operating_mode_defaults` elsewhere in this codebase. Iterating a dict in a list comprehension
yields its *keys* (strings), not its values, so the old code would call `.get()` on a plain
string the moment it ran -- `AttributeError: 'str' object has no attribute 'get'` -- for any
account with real `routine_builder_defaults` content. jadestar1864's clean 28/28 run in the
previous addendum evidently had no such content to trigger this path; chairstacker's account
did, and this is the first time this exact code path has ever executed against real data. Two
related corrections found in the same response: `RegionDefaults.operating_mode` is an int (not
str), and `OperatingModeProfile.params` is properly `CommandParams`-shaped with a
previously-entirely-missing sibling field, `updated_at`.

**A second, separate genuine bug found on the write side.** `ScheduleOptions.to_json()`'s
`commands`/`end_commands` serialization produced a bare command dict, but the real
`get_schedules()` response shows each entry wrapped as `{"command": {...}}`. Since
`ScheduleOptions`/`HouseholdSchedule`/`RoutineCommand` were all previously write-only models (no
`from_json()` existed for any of them -- confirmed by checking, not assumed), this asymmetry had
never been caught: the write path would have silently sent a shape different from what the read
path shows the server itself produces, for any actual `create_schedules()`/`update_schedules()`
call, none of which has ever been live-tested. Fixed. Also confirms `ScheduleTime.day` is
genuinely a list of plain ints (settling that field's own hedged docstring guess) --
`ScheduleTime.from_json()`/`ScheduleDateEntry.from_json()` added for completeness (neither had
existed before, despite their `to_json()` counterparts existing since early sessions).

**`P2MapData` extended with fields the real response has that the bytecode-derived model
lacked.** `entity_type`, `robot_id`, `sku`, and a full `rooms_metadata` list (reusing
`RoomMetadataEntry`, exactly as `get_active_map_versions()`'s own `P2MapVersion` already does) --
the real `get_map_metadata()` response is now confirmed to be structurally almost identical to a
single `P2MapVersion` entry. Kept as a separate class rather than merged, since
`user_orientation_rad` specifically has independent bytecode evidence tied to `P2MapData`'s own
serializer.

**Two small, low-risk map-bundle corrections**, both from real bundle content: the confirmed
content-type set had `dockPoses` (a guess), corrected to the real filename `dockPose` (singular)
-- this constant isn't used to gate any parsing logic elsewhere, so purely a documentation fix,
no functional impact. `BundleManifest.metadata` was typed as a nested dict (also a guess); the
real manifest.json shows it's a bare string, corrected to `Any` to honestly reflect that. And a
genuinely nice resolution: the manifest file's own filename within the tar.gz -- an explicitly
"UNCONFIRMED" caveat since the forty-seventh session -- is now settled: it's literally
`"manifest"`.

**Assessment:** this single real capture, examined carefully rather than treated as a pass/fail
signal, surfaced more genuine, confirmed corrections than several recent sessions of static
bytecode analysis combined -- a useful reminder that real data, even from areas that "already
work" (nothing here showed up as a failure in the validator's own summary), often contains
information a passing test suite alone won't surface, especially for fields whose generic/nested
shape bytecode analysis structurally cannot resolve.

335/335 tests green (2 new, 2 updated to match the newly-confirmed real shapes), ruff clean.

## Addendum (fifty-eighth session): four real-data verifications turned into permanent tests, and a genuine investigation into the one loose end from the previous session

**Part one: ad-hoc verification made permanent.** The previous session's follow-up checks
(4 additional `MissionTimelineEvent` sub-types against real data -- `room`, `pause`, `traversal`,
`charge` -- plus field-by-field checks of `RobotSerialInfo` and `parse_user_households()` against
chairstacker's real response, plus a structural confirmation that `BorderFeature` really is a
bare Feature with empty properties in practice, not just by bytecode inference) had been done in
a disposable Python script and only written up as prose. Correctly challenged on this: verification
that isn't preserved as a test provides no defense against future regression. All five turned into
proper test cases using the exact real values, added to `test_models.py`. This brings the running
total of `MissionTimelineEvent` sub-types confirmed against real (not synthetic) data to 10 of 20.

**Part two: were the `$$serializer`-derived corrections this project made across many sessions
actually reliable, given six real bugs just surfaced?** Traced each of the six fixes from the
previous addendum back to its actual evidentiary basis, rather than treating "bytecode-derived
model had a bug" as a single undifferentiated category:

- `RoutineBuilderDefaults.regions` (list -> dict), `OperatingModeProfile.params` (untyped `Any`):
  both traced to a docstring that ALREADY, explicitly disclosed the exact limitation responsible
  ("Java generics type erasure" -- the bytecode technique correctly reported it could not resolve
  this, and said so at the time).
- `ScheduleOptions.to_json()`'s missing `{"command": ...}` wrapper: traced to an area the
  `$$serializer` technique was never even applied to at all -- `RoutineCommand` has no
  `from_json()`, `ScheduleOptions`/`HouseholdSchedule` were write-only models, and the envelope
  shape was an explicitly-flagged analogy to `FavoriteV1`, not a bytecode claim.
- `dockPose`/`dockPoses`: a naming-convention guess for a reference-only constant, never
  bytecode-confirmed at all.
- `RegionDefaults.operating_mode` (str -> int), `BundleManifest.metadata` (dict -> str): in both
  cases the FIELD NAME was genuinely bytecode-confirmed and correct; what was wrong was an
  unstated TYPE assumption layered on top of a confirmed name, never independently checked.
- `P2MapData`'s missing fields (`entity_type`/`robot_id`/`sku`/`rooms_metadata`): the 8 fields
  THAT specific class's serializer confirmed were all accurate; what was incomplete was the
  assumption that this specific class is the entirety of what `get_map_metadata()` returns,
  rather than (as now appears likely) a near-duplicate of the richer `P2MapVersion`.

**Conclusion: in every one of the six cases, every field name the `$$serializer` technique
specifically claimed to confirm was later found to be accurate. Nothing the technique actually
asserted was ever wrong.** Every failure was in something adjacent to the technique's real,
narrow claim: filling a self-disclosed gap with a guess, reaching for analogy in an area never
scanned at all, assuming a type for a name without checking the type itself, or assuming a
scanned class's field list was the complete answer to what an endpoint returns.

**The one loose end explicitly flagged as concerning: `OperatingModeProfile`'s missing
`updated_at` field.** Its 2 confirmed fields (`params`, `profileType`) came from a `<clinit>`
scan explicitly claimed to be complete for that class -- if a real field were simply absent from
that enumeration, it would suggest an actual blind spot in the scanning technique itself (e.g.
inherited fields a simple string-literal scan might miss), not just a downstream guessing
problem. Investigated directly: read the actual decompiled `OperatingModeProfile.java` (not just
the serializer). **The class genuinely has only these two fields** -- no inheritance, no
composition, nothing hidden. The `<clinit>` scan was completely accurate.

**So where does `updated_at` in the real response actually come from?** The explanation is more
interesting than a scanning gap: `kotlinx.serialization` silently discards, by default, any JSON
key not declared in the target class. The real API server evidently includes `updated_at` in its
response; the app's own Kotlin class simply never declared a field for it, so the app's
deserializer drops it on the floor without complaint, and the app itself never uses it for
anything. This means: **analyzing the app's own bytecode can only ever reveal what the app
itself consumes -- never necessarily everything the server actually sends.** This is not a
one-off bug or a fixable gap in the scanning method; it's a structural, permanent ceiling on this
entire reverse-engineering methodology. Since this library deliberately wants full API fidelity
(unlike the app, which only needs what it displays), an "app-bytecode-confirmed" model can be
completely correct by that methodology's own standard and still legitimately be missing fields a
live capture reveals -- as this specific case demonstrates. Worth remembering as a category
distinct from every other kind of gap this project has catalogued (unconfirmed guesses,
never-checked areas, wrong type assumptions): this one has no bytecode-side fix, only live data
can ever close it.

339/339 tests green (4 new, real-data-backed), ruff clean. No functional code changes this
session beyond the new tests -- the fixes themselves were made in the fifty-seventh session; this
one is verification-hardening plus a methodological investigation.
