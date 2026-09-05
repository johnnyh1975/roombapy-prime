# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
For the detailed, session-by-session reverse-engineering trail behind
any of this (what was tried, what's still uncertain, why), see
[`docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md).
This file only tracks what changed from a user's point of view.

## [0.3.2]

### Added

- **`verify-virtual-wall-write --drop-one-wall` and `--move-one-wall`** —
  the first writes on this path that CHANGE the list rather than
  restating it. Both preserve existing coordinates byte-for-byte, so the
  unconfirmed CommandPolygon coordinate system is never touched; stage 2
  had been deferred on the assumption that it needed new geometry, which
  is true of adding and not of removing or moving.

  Both capture the original before sending, print the restore payload
  before the change, verify by re-reading rather than trusting the
  response, and restore unconditionally. `--drop-one-wall` refuses a map
  with fewer than two walls, because removing the only entry sends an
  empty list and asks a different question.

  `--move-one-wall` also **measures the coordinate system**: geometry
  reaches the wire untransformed, so a known delta and a look at the app
  give the scale. Metres or millimetres has been open on the edit path
  since it was first modelled.

- **Python 3.14 in the CI matrix.** The suite already passed there and
  `requires-python = ">=3.11"` already promised it.

- **PyPI badges** in the README: version, supported Pythons, monthly
  downloads, license.

### Fixed

- **Four blocks of documentation were unreachable.** A second
  triple-quoted string after a docstring is a discarded expression, not
  documentation — `send_simple_command()`, `mission_control.py`,
  `schedules_dnd.py` and `mqtt_client.py` each had one, including the
  evidence trail for the corrected mission-control path and a correction
  of four wire keys. All merged into their docstrings, with a test that
  fails on the pattern and deliberately allows PEP 258 attribute
  docstrings.

### Documentation

- `irbtTopics` / `iotTopics` confirmed independently by the app's own
  service-discovery response, so the "best-guess field names" note is
  gone. They remain optional reads because the VALUE is legitimately
  absent sometimes — deployment-dependent, with vendor error causes for
  both being empty. What its absence costs is now written down: two
  subscriptions die silently and back off to five minutes.

- `WRITE_PATH_TEST_STATUS.md` rewritten. Its header had said
  `v0.1.11a29` for roughly twenty releases while section 6 still called
  virtual-wall writes broken — solved before 0.2.0b1. A tester planned a
  field test against it and asked first. The file now says what is
  actually open and what is answered.

## [0.3.3]

### Fixed

- **`watch_live_map()` never re-subscribed after a reconnect.** It
  subscribed once; the client clears its subscriptions on disconnect by
  design, leaving that to the caller, and every other stream gets it
  from `_watch_topic()`. After the first reconnect the map was
  permanently dead -- empty queue, no exception, and a keep-alive still
  reporting success because the REST ping is a different transport. One
  reconnect is enough, and one tester's instance reconnects hourly. Also
  explains @chairstacker's zeroed live-map counters without needing the
  missing-prefix theory.

- **The keep-alive retried a rate limit at a fixed ten seconds.** On a
  failure no message arrives, so the expiry is never set and the delay
  falls back to the interval -- against the endpoint that just returned
  429. @jpatchMC's two robots produced twelve requests a minute between
  them. Now exponential with a five-minute cap, and a one-minute floor
  for 429 specifically, since a rate limit is about the account rather
  than one robot.

- **A Gigya lockout no longer reads as a bad password.** The message
  leads with not re-entering anything and to wait. The CLASSIFICATION is
  unchanged on purpose: a rate-limit error would reach Home Assistant as
  ConfigEntryNotReady and retry 11 times per entry in ten minutes
  against an account locked for too many attempts, where the credentials
  path retries zero times. Guarded by a test.

- **`--drop-one-wall` verified the pre-edit map version**, so its
  "ACCEPTED BUT NOT STORED" warning fired for an edit that had worked
  (@chairstacker, issue #89). It reads the version the edit returned.

- **`CommandPolygonMetadata.from_json()` raised `TypeError` on
  malformed input** -- the fallback was `return cls()` and
  `furniture_id` has no default. Returns None, which is also correct for
  a robot that creates no furniture.

### Added

- **Vendor firmware schemas** (`docs/internal/vendor_schemas_ruby_0_7_12.json`)
  and `scripts/check_vendor_schema_enums.py`, which compares this
  library's enums against them and fails on an undecided divergence. It
  resolved `wid` and surfaced `tag`. The file's `_channel` note is not
  optional reading: these are Classic local-channel schemas.

- **`check_vendor_value_sets.py` in CI**, where it had never run.

## [Unreleased]

### Packaging

- **Removed the direct-URL dependencies that made this unpublishable.**
  Both pyprojects pointed at each other with
  `@ git+https://github.com/...`, which was right while the project
  lived only on GitHub and fatal the moment it did not: PyPI rejects
  any distribution carrying one, and rejects the whole upload with it.

  Now `roombapy-prime-tools>=0.3.1` and `roombapy-prime==0.3.1`. A test
  fails the build if a URL dependency comes back.

  Found by a release run that got as far as the upload and stopped
  there. Nothing was uploaded, so the version survived — a partial
  success would have burned it.

- **Published to PyPI.** `pip install roombapy-prime` and
  `pip install roombapy-prime-tools`; the GitHub install instructions
  are gone from the README.
- The release workflow publishes both distributions via **Trusted
  Publishing** — PyPI verifies the workflow's identity directly, so no
  API token is stored in the repository. The library goes first: the
  tools declare it as a dependency.
- **Development Status raised to Production/Stable** for both
  distributions. The tools were left at Alpha on the reasoning that
  they move real robots — but that describes their effect, not their
  maturity. 486 tests, 17 console commands, and every field finding in
  this project was made with them. The warning belongs in the
  description and the safety notes, which is where it is.
- GitHub releases are no longer marked pre-release.

### Documentation

- **Every exception is now in the API reference.** All 13 were exported
  and none documented, so a caller could not tell
  `AuthCredentialsError` from `AuthRateLimitedError` without reading the
  source — and those two need opposite handling. Three independent
  hierarchies, one per transport, with a retry column.
- **`is_valid_id`, `normalise_id` and `id_problem`** documented. They
  are exported and deal with the identifiers every caller handles.
- **Four new examples**, covering areas that had none: `maps.py`
  (versions, region names, bundle), `maintenance.py` (part counters and
  resetting them), `do_not_disturb.py` (the two mutually exclusive
  schedule shapes) and `watching.py` (live streams rather than polling).
  Sixteen of 64 robot methods appeared in an example before; the gaps
  included every map read and every write to DND.
- **New example: `error_handling.py`.** The other six show the happy
  path. This one shows the distinction that matters: a credentials
  failure will never succeed on retry, and a naive retry loop turns it
  into `AuthRateLimitedError`.
- **Import line straightened.** README and all examples used
  `from roombapy_prime.prime_factory import PrimeFactory`, teaching the
  submodule path when the package exports it directly.

### Internal

- A test now parses every example and checks that every imported name,
  every `roombapy_prime.*` model import and every `robot.<method>()`
  call actually exists. A first draft of the
  new example invented `factory.login()` and `factory.create_robot()`;
  neither exists. An example that does not run is worse than none — it
  is the first thing a new user copies, and it fails in a way that looks
  like their mistake.


## [0.3.1] - 2026-08-27

### Fixed

- **Every zone was marked as possibly deleted.** The "not in the current
  map version" marker added in 0.3.0 compares against
  `geojson_details.regions`, which lists rooms and not zones — so no
  zone is ever in that set and all ten of @chairstacker's were flagged.
  A marker that always fires is not information.

  It applies to rooms only now. For a zone there is no list of what
  currently exists, so a deleted one and an unnamed one still look
  alike; saying nothing is better than a confident wrong answer.

- **A zone listed from the bundle showed `region_type=None`.** The
  snapshot has no entry to read a type from, but a name found in the
  bundle's zone layers is itself the answer. It now reads
  `'zid (from bundle)'`.


## [0.3.0] - 2026-08-27

First stable release of the 0.3 line. Eighteen betas, and the last six
were about one question: where zone names live.

### The zone-name question, answered

Four releases of `--list-rooms` reported "no zone names found" after a
search that had not run. In order:

- **b13–b14** widened a search that was not executing — `zone_layers`
  was read with `getattr` on a dict, so the function returned `{}` for
  every bundle ever passed to it
- **b15** fixed that reader, which the call never reached
- **b16** fixed the call — the geojson link is a dict, not a URL string
- **b17** fixed what the call then hit: `parse_map_bundle` is a module
  function, not a robot method

The answer, once the search ran, is that it depends on the layer:

- **`cleanZones` carries names.** Confirmed on a G185020 with nine
  names read (@chairstacker).
- **`policyZones` has no name field at all.** Confirmed from a raw dump
  on a Y351020 (@utkjmitch): a FeatureCollection whose properties carry
  only `{"type": "NoMopZone"}`. The app does not offer naming for
  keep-out and no-mop zones and the bundle agrees.

### Fixed since b18

- **The room listing missed regions the snapshot did not know.**
  `rooms_metadata` lags map edits in both directions, and the listing
  compared against the map version in one direction only. A zone
  created in the app had its name read from the bundle and was never
  printed — nine names read, eight shown. A zone deleted in the app
  stayed in the listing as an unnamed one. Both are now visible, the
  second marked as absent from the current map version.

### Internal

- `_regions_not_in_snapshot()` extracted so the comparison can be
  tested. It used to be four lines inside a function that logs in,
  connects over MQTT and downloads a bundle.


## [0.3.0b18] - 2026-08-26

### Fixed

- **`active_p2mapv_id` is not the only spelling.** @chairstacker's
  G185020 reports `p2mapv_id` and `user_p2mapv_id` and no `active_`
  field at all — the one field callers read. So "the current version"
  came back None, the bundle request went out without one, and the
  server returned its default: a zone created that morning was missing
  from a bundle read that afternoon while the version carrying it sat
  in the same response.

  `P2MapData.current_map_version` resolves whichever the robot uses,
  `user_p2mapv_id` first — that is the version the user's own edits
  produced, which is what "the map as it is now" means.

### Documented

- **A docstring contradicted itself about `clean_all`.** One paragraph
  recorded @Echovictor37 firing it on hardware — `regions` omitted and
  `regions` empty, PUBACK both times, no effect — while another said
  "STILL UNTESTED". Same subject, opposite claims, and the wrong one
  got read: it sent @BryznNguyen offering a hardware run on a question
  already answered.

  What remains open is narrower and now says so.


## [0.3.0b17] - 2026-08-26

### Fixed

- **`parse_map_bundle` is a module function, not a robot method.**
  `robot.parse_map_bundle(blob)` raised `AttributeError` — and the
  dict-link bug fixed in b16 had masked it completely: that line had
  never executed, so the error only became reachable one release ago.
  @utkjmitch and @chairstacker both hit it the same day.

- **The tool concluded absence after a read that threw.**
  `_zone_names_from_bundle` returned `{}` for both "the read failed"
  and "the read found no names", so the caller printed its no-names
  finding either way. It returns `None` on failure now, and the caller
  says the question is unanswered by that run.

  Three releases in a row reported on a search that had not happened:
  b14 widened a search that was not running, b15 fixed the reader the
  call never reached, b16 made the call work and still printed the
  conclusion after a throw. @utkjmitch proposed the split.

- **The blid leaked through composite ids in diagnostics.** Substring
  redaction already existed, built after an earlier report where
  `p2map_id` carried `<BLID>-<epoch>` past a key-name list — and was
  wired into the shadow section only. `map_id` inside a `lastCommand`
  repr went through the outer pass, which checks key names and did not
  know the blid. Both passes now cover the whole payload.

- **A test read a source file through a cwd-relative path**, so the
  suite passed from the repo root and failed from inside `tools/`.
  Anchored to `__file__`.

### Internal

- **Bundle tests build a real tar.gz and use the real parser.** Every
  one of them mocked `parse_map_bundle` as a robot method — an
  attribute `PrimeRobot` does not have — so the mocks agreed with a
  call site that could only ever raise. Fourteen failed when they were
  switched over. b15's own notes named this failure mode for the
  `zone_layers` fixture; this was the same seam one function up.


## [0.3.0b16] - 2026-08-25

### Fixed

- **`--list-rooms` never reached the bundle.** `get_map_geojson_link`
  returns the whole response dict; `download_map_bundle` wants the URL
  string out of it. Passing the dict raised `Constructor parameter
  should be str` from yarl — which reads like a type bug in the library
  rather than a mistake at the call site (@chairstacker).

  `verify_map_edit.py` has extracted the URL correctly all along. Two
  implementations of the same three lines, one of them wrong, and the
  wrong one was the one the zone-name question depended on.

- **The bundle-contents line printed only on success.** Added in b15 so
  a reader could see where the tool had looked, it sat *after* the
  `except` — so the run that fails, which is when it matters, never
  showed it. The failure message now names the exception type as well.


## [0.3.0b15] - 2026-08-24

### Fixed

- **The zone-name search never ran.** `_zone_names_from_bundle` read
  `getattr(bundle, "zone_layers", None)`. `parse_map_bundle` returns a
  **dict** — `{filename_without_extension: content}` — and has no such
  attribute, so that expression was `None` on every bundle and the
  function returned `{}` regardless of the map's contents.

  Every "no zone names in the map bundle" message, across every
  version, was a statement about a typo.

  b14's widening from one layer to three did nothing, because the loop
  was iterating an empty dict. Same mistake one level up: concluding
  absence from a search that never happened.

- **Three tests agreed it worked.** Each built its fixture with a
  `zone_layers` attribute, matching what the reading code reached for —
  proving the code agreed with itself and nothing else. They now
  construct bundles from the field names confirmed in `map_bundle.py`:
  a `CleanZoneFeature` carries `id` on the feature and `name` inside
  `properties`.

### Added

- **`--list-rooms` prints what the bundle contains.** Reporting
  "nothing found" without naming where it looked is unfalsifiable from
  outside, which is precisely how the typo above survived as a claim
  about a tester's data. Bundle contents also vary per map, so the file
  list is the first thing worth knowing when the answer is empty.


## [0.3.0b14] - 2026-08-23

### Fixed

- **`--list-rooms` read one zone layer of three.** It looked only at
  `cleanZones`, so a map without that layer reported "no zone names in
  the map bundle" — true about the search, wrong about the data.
  @chairstacker's bundle held five files (borders, manifest, metadata,
  **policyZones**, rooms) and no `cleanZones` at all; bundle contents
  vary per map. `adHocCleanZones` and `policyZones` are read now.

  A keep-out or no-mop zone is marked with its `zone_type`, because
  sending a cleaning command at one is the mistake worth preventing. A
  clean zone keeps its plain name — an earlier version of this fix
  appended the layer to every entry, which changed the data rather than
  adding to it, and an existing test caught it.

- **The "no zone names" message claimed more than the tool knows.** It
  asserted the names were "stored nowhere we can read". It now names
  the layers it searched and asks for a report if the app shows names
  anyway. @chairstacker then found the same names in calendar entries,
  which is how the original claim came apart.

### Documented

- **`condNotReady` codes are named.** A comment claimed their meanings
  were unknown and would need control-flow disassembly;
  `RobotReadinessState` names them in the next file over. Values stay
  plain ints so an unrecognised code reaches the caller rather than
  raising.


## [0.3.0b13] - 2026-08-23

### Fixed

- **`regions: []` is no longer sent.** An empty region list produced
  `regions: []` in the command payload, because `[] is not None`. The
  vendor app omits the key entirely for both null and empty — verified
  from `MissionCommand::toPayload` (Prime 3.0.0) — and scope is decided
  downstream by whether region data is present. An empty array is a
  shape the vendor client never emits, in the one place being creative
  is unaffordable.
- **Race condition in MQTT dispatch.** `_on_message` runs on paho's
  network thread and iterated the persistent-subscription dict live
  while `subscribe()`/`unsubscribe()` wrote it from the caller's
  thread. The resulting `RuntimeError` escaped into paho's dispatch
  loop, after which **every watcher on that client stopped receiving,
  silently** — the same failure mode the wildcard fix was written to
  cure. Reproduced deliberately before fixing.
- **`request_mission_timeline()` could raise `AttributeError`** when
  called before `connect()`: it published without the None check its
  neighbour had.
- **Two verification tools read the map bundle after the session had
  closed** (`verify-region-commands --list-rooms`,
  `name-clean-zone`), and called `get_map_geojson_link()` with one
  argument where it needs two. Both surfaced to a tester as "map
  bundle unreadable" / "Session is closed".

### Added

- **`get_firmware()`** — the firmware catalogue, parsed. The endpoint
  is confirmed live; `FirmwareItem.fused` was corrected from `bool` to
  `int` (a real response carries `3`, an eFuse level).
- **`ids` module** — validation for mission and deployment IDs
  (26-char Crockford base32). Validates without rejecting: the robot
  is the authority on its own IDs. Scoped to mission/deployment IDs
  only — map ids and map-version ids are different formats, and
  applying this to them would flag working data as broken.
- **`parse_map_version_region_ids()`** — every region in a map
  version, named or not. The name lookup drops unnamed regions, which
  is right for a name lookup and wrong for a listing: a tester with
  twelve zones saw eight, because a newly added zone was absent from
  the p2map snapshot and unnamed in the version.
- **`get_map_region_ids()`**, **`watch_dock_reports()`**,
  **`dock_report_topic()`**.

### Changed

- **17 Prime mission phases** added to the phase tables, tagged
  OBSERVED vs ASSUMED. Three were seen in real data; the rest come
  from a firmware enum and no robot here has been observed reporting
  them. Both are mapped; only one kind is evidence.
- **`condNotReady` is now `list[int]`** rather than `list[Any]`, and
  the parser filters rather than casting. The individual code
  meanings stay unnamed — naming a code nobody has seen is how this
  project has been wrong before.
- **The dock report family is closed**: exactly `{evac, refill,
  padwash, paddry}`. No `charge`, no `battery`. This was an open
  question no field tester could have answered, because a tester's
  silence is ambiguous.
- **mypy is clean and the CI gate is now hard.** The 34 errors it had
  been running past included one real crash path and two missing
  guards; none were resolved with a `cast`.

### Documented (no behaviour change)

- **Five module docstrings had outdated status banners removed.**
  `prime_robot`, `rest_client` and `prime_factory` each opened with
  "STATUS: Draft" and a claim of never having been tested against a real
  account; `rest_client` went further with "not a single one of these
  calls has actually been executed yet". A dozen testers had been using
  all three for months. `mqtt_client` still said the shadow topics were
  unverified for V4 — the inference it described had since been
  confirmed — and `__init__` called map editing and region commands
  unverified after both were confirmed on hardware.

  The specific caveats stay exactly where they were: `clean_all`, the
  p2map upload, the services write path. A blanket disclaimer at the top
  of a file invites either ignoring it or distrusting everything, and
  helps nobody deciding whether one particular call is safe.

- **`command_type=START` is not a scope limiter.** Disassembly shows
  the command dispatcher validates the operating mode and calls the
  handler; scope is decided downstream by whether `regions` is
  present. A field-confirmed `START` + region run worked *because a
  region was named*.
- **`SplitRoomV1` / `MergeRoomsV1` are field-confirmed** on hardware
  (response level; geometry not audited). `MergeRoomsV1`'s wire
  command is `arrange_room` — now attested in app bytecode, on a live
  robot, and in firmware.
- **Region cleaning confirmed on an x05**: 234 sq ft against two
  whole-house runs at 644. A PUBACK proves delivery; the area proves
  intent.
- **`clean_all` / `select_all` remain untested**, and the firmware
  read did not change that. No such field exists in firmware at all.


## [0.3.0b12] - 2026-08-21

The local channel answered. A field run on current firmware settled a
question this project had been guessing at, disproved one of its
hypotheses, and exposed three ways the discovery tool could have
reported silence from a robot that was talking.

### Fixed

- **Discovery could silently drop a robot that answered.** Three cases,
  all sharing one failure mode: the robot replies, we fail to hear it,
  and the run reports nothing found — which reads as *"the firmware
  dropped the local channel"*, the exact question the tool exists to
  ask. Learned by comparing against an independent implementation.
  - Some robots prefix the discovery JSON with a **2-byte big-endian
    length**. A plain `json.loads()` throws on those, and the reply was
    discarded.
  - **BLID now falls back to the hostname** (`iRobot-<blid>` /
    `Roomba-<blid>`) when `robotid` is absent, instead of leaving the
    robot unidentifiable with its BLID one field away.
  - The discovery packet now goes to **both the subnet-directed
    broadcast and 255.255.255.255**. Some routers and interface
    configurations drop the global one.

### Added

- **`dock/{reportType}/report` is a real topic family, not a dead end.**
  `dock_report_topic()` builds it and `PrimeRobot.watch_dock_reports()`
  subscribes it — with no argument, via a `+` wildcard, which is the
  only way to discover whether a `reportType` other than `paddry`
  exists. A `charge` or `battery` sibling would be the real find; none
  has been seen. `DockReport` aliases `DockPadDryReport`, whose model
  already keyed off `reportType` and so always fitted the whole family.
- **A third TLS attempt** in the local-channel check: a **static RSA**
  suite, which carries no server signature at all. Untested against a
  robot, and the one remaining case where standard Python might
  complete the handshake.

### Changed

- **`geojson_details` is confirmed live.** Marked in this library as
  existing in no app version checked; it returns room names on current
  firmware. It lives in ARM64 native blocks, which a DEX-and-Dart
  search cannot reach — a limit of the search, not of the protocol.
  `parse_map_version_regions()` now has tests, mirroring a real
  response.

### Corrected

- **"The local channel was removed" was wrong**, and wrong in a way
  worth naming: the **app** stopped using it, the **robots** did not.
  What an app ships says nothing about what firmware still serves —
  a distinction this library built a tool around and then failed to
  apply to its own notes.
- **"Cap it at TLS 1.2 and the bad signature goes away" was wrong.** A
  field run failed with `BAD_SIGNATURE` on both attempts. The robot
  signs with a key that does not match the certificate it presents;
  TLS 1.3 carries that signature in `CertificateVerify`, TLS 1.2 with
  ECDHE carries it in `ServerKeyExchange`. Capping the version changes
  which message holds the bad signature, not whether one is sent.
- **A native helper is the only route that has been made to work** —
  not, as previously implied, the only route that could exist.
- **A silent local-channel run proves less than the tool claimed.** The
  channel is closed until something opens it and closes again on
  reboot, so silence means "not recently provisioned", not "firmware
  dropped it". The old wording would have misled every tester who ran
  it.

## [0.3.0b11] - 2026-08-20

Cross-checked against an independent reconstruction of the same
protocol (`samm-git/irobot-explore`, built from app 1.6.0) and against
app versions 2.2.4 and 3.0.0. Two things here were wrong.

### Fixed

- **The firmware catalogue was on the wrong host.** `get_firmware_raw()`
  called the SigV4 gateway and got a 403, which we read as "the consumer
  role has no invoke rights" and recorded as settled. The catalogue is
  on `content-prod.iot.irobotapi.com` and needs **no authentication at
  all** — the reading was right and the conclusion was wrong.
- **It also took the wrong parameters.** `FirmwareRequest` in app 3.0.0
  declares six, not four: `sku` and `softwareVer` are required;
  `track`, `dockFwVer`, `dockFwVerSec` and `dockHwRev` are optional and
  sent only when set. The last two are missing from the reference
  implementation as well. Sending `track=prod&dockFwVer=`
  unconditionally was a guess about defaults.
- **`max_inflight` was paho's default of 20; the app sets 1000.** This
  matters for `_subscribe_and_wait`, which subscribes to every
  persistent topic in one loop — beyond twenty, the rest queue behind
  the window and can look like a missing SUBACK.

### Added

- **`get_map_version()`, `parse_map_version_regions()`,
  `get_map_region_names()`** — a third possible source of region names.
  Marked as unverified: app 3.0.0 does not call this path, 2.2.4 has it
  only under the older `pmaps` naming, and `geojson_details` appears in
  **no** app version checked. Kept because "not in the app" is not
  "does not exist".
- **`roombapy-prime-verify-local-channel`** — checks whether a robot
  still answers on the local channel. Four stages (UDP discovery, TCP
  connect, TLS handshake, no MQTT CONNECT), no credentials, nothing sent
  to the robot beyond the nine-byte discovery broadcast. The discovery
  reply carries SKU and firmware, which is the datapoint the whole
  question turns on.
- **`na-irbtfeatures` in the shadow validation run.** Present in no app
  version we have; a shadow `get` either answers or does not, and twelve
  testers settle it as a side effect of runs they were doing anyway.

### Internal

- **mypy now runs in CI**, against production code at default settings.
  Informative rather than gating while the count comes down: the full
  package under `--strict` reports 1399 errors, production code alone
  reports 33, and gating on the larger number would mean gating on
  nothing.

  It paid for itself on the first run: **four dataclass fields in
  `map_bundle.py` were declared twice**, the second declaration
  silently winning while the first block's documentation described a
  field nobody read. `no-redef` sees that; no test can.

### Documented

- **`reset` is the reboot command.** Confirmed in 3.0.0:
  `device_restart_page` → `ControlSettingsRepo.restartDevice` →
  `MissionCommandType.reset` (index 7), same `send` channel as `start`.
- **`editv3` map editing over MQTT.** App 3.0.0 uses
  `things/{assetId}/editv3_req` with nine operations. Note
  `delPermanentAreaRes` — eight replies end `Rsp` and one ends `Res`.
  Not implemented: our REST path is now field-confirmed working, so
  this would be a second transport for the same nine operations.
- **The local channel: the APP dropped it, the ROBOTS did not.** App
  2.2.4 carried 46 local-socket serializers, `irobotmcs` discovery and
  port 5678; 3.0.0 has none of it. An earlier entry concluded from that
  the channel "was removed" — wrong, and the wrong test: what an app
  ships says nothing about what firmware still serves. Confirmed twice
  on `p25-705+9.3.6+I3.8.149`, current: a field tester's discovery run
  answered, port 8883 open. TLS then fails with `BAD_SIGNATURE`, the
  robot signing with a key that does not match its certificate.
  It would not have answered the `async-dependency`
  question anyway: the reference implementation still logs in to the
  cloud once to fetch the robot's local password, so a local transport
  removes the round trip, not the dependency.
- **Region names were probably never in a separate document.** App
  2.2.4's `fetchRegionName(assetId, mapId, mapVersion, regionId,
  regionType)` resolves them one at a time via `fetchMapMetadata` — the
  same source we already read. Confirmed in the field: a tester's
  bundle carries them in the standard `cleanZones` GeoJSON layer.


## [0.3.0b10] - 2026-08-19

### Fixed

- **The map bundle was never readable.** `_fetch_bundle_rooms` read
  `parsed["rooms"]` expecting a bare list; a bundle file is a GeoJSON
  collection. The type check failed and the loop moved on silently,
  because "skip this map" and "this map is empty" look identical.
  @chairstacker got `0 room feature(s) found across all map bundles` on
  a robot with seven named rooms.

  The same reader is used by the Home Assistant integration to find
  zone names and by `name-clean-zone` to check existing zones before
  writing — a guard that was quietly broken until now.

### Changed

- **`--list-rooms` reads zone names from the map bundle.** It read
  `get_map_metadata` → `rooms_metadata`, which carries room names only,
  so zones always showed `name=None` regardless of whether they had
  one. Both sources are read now, with the origin marked per line, and
  an empty bundle says so rather than leaving `None` to be interpreted.

  `--dump-config` cannot answer this: its summary is deliberately
  depth-limited so real home layouts stay out of shared reports, and
  zone names sit exactly under the cutoff.

## [0.3.0b9] - 2026-08-18

### Fixed

- **`DoneCode` was wrong on nine of nineteen values.** The wire values
  are abbreviated camelCase (`dndEnd`, `cncl`, `plcDoc`, `batcncl`),
  not snake_case — confirmed from app 3.0.0, where no snake_case form
  appears at all. Only `ok` had ever been observed; the rest came from
  bytecode constant names lowercased by a rule that does not apply
  here. @chairstacker's `clean_streak` and `area_cleaned_today` read 0
  because every one of his 128 missions mapped to "unknown".

- **`region_name` was written and never parsed back.** Zone names live
  in `lastCommand.regions[].region_name`, not in the map — the app's
  own timeline resolver reads them from there. Now read as
  `Region.region_label`, separate from the map-side `name`.

- **`digiCap.matter` was declared and never read.** A robot reporting
  it lost the value silently. A guard now checks all eleven documented
  keys land somewhere.

### Added

- `roombapy-prime-name-clean-zone`. Renaming a zone is a full-list
  rewrite through `SetPermanentAreasV1` — there is no rename command.
  Dry run by default; reads the current zones first and refuses to send
  if it cannot, because a list it could not read is a list it would
  delete.

### Documented

- **The first observed `digiCap`** (@ricrog1135, W155020). Five of
  eleven gates, every one mapping to a field already declared. The
  block is partial, so an absent gate must stay absent rather than
  becoming 0.

- **`cleaningProfiles` carries no meaning.** The app types it as
  `Set<CleaningProfileType>?` and matches entries against profile
  names; a bare integer takes the else-branch and is discarded. Profiles
  come from `GET /v1/profiles`.

- **`digiCap.digiSpot` is not the spot-clean gate.** `cap.dSpot` is.
  `DigitalSpotSupportType` does not appear in the app's Dart layer.

## [0.3.0b8] - 2026-08-17

### Fixed

- **The 55-minute disconnections were self-inflicted.** @ratpic83 logged
  26 of them in one day, each exactly 55 minutes after the last,
  identical while docked, cleaning and idle overnight. The ordering in
  his log was the finding: authenticate, reconnect, *then* the drop is
  reported.

  `Normal disconnection` is paho's phrase for a clean, client-initiated
  close — ours, from the proactive token refresh disconnecting the old
  connection before opening the new one. The watcher warned about an
  event it had scheduled itself and started a **second** reconnect
  racing the one already running.

  A deliberate disconnect is now marked as such. The watcher recognises
  its own refresh and stays out of the way: no warning, no competing
  reconnect, no log line. A scheduled refresh that works as designed is
  not news.

  Two earlier attempts had guessed at this. b2 rotated the `client_id`
  on the eviction theory; b3 reverted it because a self-chosen id does
  not connect at all. The code then settled on "the phone app and this
  library evict each other" — which is why force-quitting the app on
  every phone (@ratpic83's test in #78) changed nothing.

- **`watch resumed` now means acknowledged.** @ratpic83 caught
  `no SUBACK within 3.0s` and a success message one millisecond apart,
  twice in a day. The restore path checked SUBACKs and then reported
  success regardless — so an unacknowledged subscription, which
  delivers nothing and looks exactly like a robot with nothing to say,
  was announced as a working watch.

  This is the mechanism by which a mission-end transition goes missing.
  Unacknowledged subscriptions are now named, counted and retried with
  backoff.

- **A refresh timeout is a warning, not an ERROR with a traceback.** The
  retry is the design — the refresh is scheduled five minutes before
  expiry precisely so a failed attempt has room. @ratpic83's log
  accumulated a dozen tracebacks a day for a condition that healed
  itself in two seconds. A real failure (auth rejected, malformed
  token) still gets the full stack, because there the stack is what
  identifies it.

### Changed

- **`models` re-exports `TimeEstimate` and `TimeEstimates` explicitly.**
  A `py.typed` package has to say so: under `mypy --strict` a consumer
  importing them from `roombapy_prime.models` was told the attribute is
  not exported. Invisible during development, because an editable
  install lets mypy read the working tree instead of the built package.

- `roombapy-prime-validate` prints `digiCap`'s contents rather than only
  noting that the key exists. @ricrog1135's W155020 is the first robot
  observed reporting the field, and nine capability gates modelled from
  iRobot's own table have never been seen on real hardware — the run
  that carried the answer did not show it.

- The frequent-drops warning no longer names the iRobot app first. Our
  own refresh is excluded before the warning fires, so what remains is
  genuinely another client — and the previous text sent people to check
  something that could not have been the cause.

## [0.3.0b7] - 2026-08-15

Favourite buttons could not run their favourite. Four causes, all on the same
path, all in this library.

### Fixed

- **A stored favourite's `initiator` was dropped.** `_favorite_from_json` read
  eleven fields of each command def and not that one, so `to_json()` omitted
  the key — while the one region command confirmed working on hardware carries
  `initiator: "rmtApp"`. That was the only difference between the confirmed
  payload and the one a favourite button sends. @chairstacker reported the
  button doing nothing.
- **A command def with no `command` key crashed on send.** The parser reads it
  tolerantly and produces `None`; `to_json()` then raised `AttributeError` on
  that same `None`. So a favourite that survived parsing died the moment
  somebody pressed its button.
- **A command carrying an unmodelled command string crashed the same way.** It
  is now sent as it came — the server stored it, and this library does not
  invent a replacement.
- **One malformed command def deleted the whole favourite.** `json.loads`
  raised straight out of the parser, and the caller catches per favourite. Now
  the bad command def is skipped and the favourite survives.

  Tolerant on the way in and strict on the way out is not tolerance.

### Changed

- **`verify-writes` warns when the tools and the library disagree.** They are
  two distributions; upgrading the library leaves the tools where they were.
  @chairstacker ran a b5 tool against a b6 core and was asked for a cleaning
  history entry that b6 had removed. Warning rather than refusal, and it names
  the upgrade command.
- **`pwReturn`'s upper three are described as the vendor names them** —
  `mission`, `refill`, `refillAndRoom` — and as **cumulative**: before and
  after routines, also during refills, also between rooms. The tool still said
  "Standard / Medium / High", which this project invented.
- **A new `initiator_mission` check**, marked risky and asked of nobody. It
  starts a real clean under a custom initiator, because only a mission produces
  the app timeline entry that would answer whether the field survives.

### Added

- **`FloorPlanFeatureProperties.type` has a documented vocabulary.**
  `P2MapFloorPlanSegmentType` declares `interior`, `exterior`, `door` and
  `unknown`, so a floor-plan segment says whether it is a wall or a doorway.
  Not typed — no capture shows whether the names or the integers arrive.

### Internal

- The vendor gap report ignores framework internals. Every remaining
  "unreviewed" enum began with an underscore — Flutter widget states swept up
  by the extractor, producing a number that never went down. Both vendor enums
  and classes now report **zero unreviewed**.

## [0.3.0b6] - 2026-08-14

Four corrections, three of which are to things this library told testers
that were not true. No breaking changes.

### Fixed

- **`verify-writes settings_roundtrip` always reported dotted keys as not
  read back.** `after.get("audio.volume")` on a document where `audio` is a
  nested map returns nothing, so `audio.volume` and `padWetness.padPlate`
  came back `read_back_matches: False` on every run while their parent maps
  came back `True`. Two keys failing and their parents passing was this bug,
  not a robot refusing dotted addresses. Same fix `_resolve_probes()` already
  had, one step later.
- **`verify-writes` described `pwReturn` 100/101/102 as "Standard / Medium /
  High".** Those are the vendor's `mission`, `refill` and `refillAndRoom`, and
  they are cumulative rather than a scale — the integration's copy of the same
  invention had already misled a tester into retracting a correct observation.
- **The vendor gap report counted Flutter widget internals as unreviewed
  enums**, producing a backlog number that could never reach zero. Both enums
  and classes now report zero unreviewed.
- **The MQTT reconnect logged its success at INFO while the drop logged at
  WARNING**, so at Home Assistant's default level a user saw the failure and
  never the recovery. @ratpic83 read "nothing further from roombapy_prime" as
  a dead subscription and spent two hours on it — a reasonable reading of a
  log that only reports bad news. A resolution now logs at the level of the
  problem it resolves.

### Changed

- **The frequent-disconnect warning no longer asserts eviction as the
  cause.** It named a same-`client_id` collision as the explanation;
  @ratpic83 force-quit the iRobot app on every phone in his household and the
  drops continued, roughly 82 and 55 minutes apart, recovering each time. That
  spacing reads like a session lifetime rather than a race. Another client is
  now "the first thing to rule out", and the message points at the recovery
  line — a drop is only alarming if nothing follows it.
- **`verify-writes custom_initiator` asked for an observation it could not
  produce.** It wanted a chirp *and* a cleaning-history entry; `find` is not a
  mission and creates no record. @chairstacker duly reported a chirp with no
  history entry, having already answered the question: **the server accepts an
  initiator that is not in the vendor's list of 25**, so the field is a free
  string rather than a registry.

### Added

- **`verify-writes` warns when the tools and the library disagree.** They are
  two distributions and upgrading the library leaves the tools behind;
  @chairstacker got a b5 prompt from a b6 install and reported its impossible
  instruction as a result. Warns and names the upgrade command rather than
  refusing.
- **`FloorPlanFeatureProperties.type` documented against
  `P2MapFloorPlanSegmentType`** — `interior`, `exterior`, `door`, `unknown`.
  Not typed: no capture shows whether names or integers arrive.
- **`DigiCap` gained its other nine fields.** `v3_capability_gates.json`
  declares eleven `digiCap.*` gates and this class modelled the two that
  happened to appear in one capture — so nine capability gates the vendor
  declares were unreadable through this library. Modelled from the gate table
  rather than from a capture, deliberately: a gate no robot in hand reports is
  exactly the one a reader cannot discover by looking.

## [0.3.0b5] - 2026-08-13

The same app 3.0.0 decode, read a second time — this time by measuring
what had been read rather than trusting that it had been. Seventeen of
the package's files had never been opened, and two of them held findings.

No breaking changes.

### Fixed

- **`reset_robot_parts()` sent an unusable body.** `parts` went out as a
  list of bare id strings; `AssetPartResetDto` declares objects carrying
  `part_id` AND `counter`. Neither a rejection nor a reset — a request
  that could not mean what it said. `counters` is now an optional
  argument; the default of 0 is an inference, since the model names the
  field without saying what a reset writes.

### Added

- **`edit_map_checked()` / `edit_map_v2_checked()`** return a parsed
  `MapEditResult` instead of raw JSON. Both edit paths returned an
  undecoded dict, documented as not modellable — true of V3's opaque
  `data.value`, and wrongly applied to V1 and V2. Four response shapes
  are in the extract. **`is_partial` is the reason to care**: a new map
  version with no URL means the edit applied and the rendered map did
  not follow, which raw JSON made indistinguishable from success.
  `edit_map()` itself is unchanged.
- **`MapEditingError`** — the robot's thirteen edit-failure codes,
  grouped so a caller can tell "fix your request" from "re-read the map"
  from "try again unchanged".
- **`reset_robot()` accepts `robot_password`, `synchronous` and
  `send_wipe`.** The body was declared and this sent none, leaving the
  field that decides how destructive a reset is to an unknown server
  default. Behaviour with no arguments is unchanged.
- **`get_map_raw_link()`** — the same map version in the vendor's raw
  format. The app's map-fetcher channel lists it beside the GeoJSON
  call; only one had been implemented.
- **The `dock.cap` levels are named.** `DockEvacuation`,
  `DockPadDrying`, `DockPadWashing`, `DockPadWetOut`,
  `DockFluidRefill`, `DockDetergent` — six fields previously described
  in code as "levels, not flags" with no statement of what a level
  meant. `DockFluidRefill` has three states, and the distinction
  matters: `controllable` means the user can trigger a refill,
  `automatic` means the dock decides.
- **`ScrubSupport`, `PointCleanSupport`, `MidMissionAdjustments`** —
  three more capability fields that were bare integers.
- **`Initiator`** — 25 values for who started a mission. This library
  knew two. `dockBtn`, `alexa`, `siri`, `schedule` and `manual` answer a
  question people actually ask.
- **`FaultScene` and `FaultScene.scene_for()`** — the same error code
  means different things per running task, and the scene is DERIVED from
  mission status and command rather than sent. Five of twelve scenes
  have stated conditions and are derived; the other seven have none and
  return `None` rather than a plausible default.
- **`MopInstallDetails`** — four states, not on and off. `onlyLeft` and
  `onlyRight` mean one of two pads is fitted.
- **`FaultScene` and `FaultScene.scene_for()`** — the same error code means
  different things per running task, and the scene is DERIVED from mission
  status and command rather than sent. Five of twelve have stated conditions
  and are derived; the other seven return `None` rather than a plausible
  default.
- **`MopInstallDetails`** — four states, not on and off. `onlyLeft` and
  `onlyRight` mean one of two pads is fitted.
- **`RoomStatus`, `TravelReason`, `TravelStatus`, `PadWashReason`,
  `WetOutStatus`, `TimelineEventPhase`, `MissionType`,
  `MapEditStatus`, `MapVerifyResult`, `MapEditRejectionReason`,
  `RoomTypeValue`, `RoomTypeSourceValue`, `SubModuleSwVersions`** and
  the remaining `bb*` statistics models.
- **`verify-writes custom_initiator`** — sends `find` claiming to be
  `homeassistant` rather than `localApp`, to establish whether the server
  validates the field at all. `Initiator` lists ten named third parties
  including openHAB and homey; `homeassistant` is not among them.

### Changed

- **`get_favorites()` no longer loses the whole list to one bad entry.**
  Each favourite is parsed on its own, and an unknown command value is
  tolerated rather than fatal.
- **`_either()` is case-insensitive**, so `favoriteId` resolves beside
  `favorite_id`. One capital letter was the difference between seven
  favourites and an account that looks empty.
- **`get_favorites_raw()` unwraps the response.** The diagnostic built
  to reveal an unwrapping bug carried the same bug, so every download
  taken to answer "does the server return anything?" answered no.

### Documented

- **`nsmip*` and `svcEndpoints*` were never a gap.** Twelve registry
  names carried as unresolvable are one key each, suffixed with the
  shadow it lives on. A real dump confirms it exactly.
- **Three concepts have more than one encoding**: room type (three),
  room-type source (two), routine type (two). Confusing them is silent
  in both directions.
- **`langs2.sLang` carries BCP-47**, not `DeviceLanguageType`'s ids. A
  selector built from the vendor enum would have written `2` where
  `"en-US"` is expected — caught by a real capture, not a second enum.
- **`poll_echo_value()` does not locate the robot**, field-disproven
  twice. `send_simple_command("find")` is the working mechanism.
- **The DND write shape is one step less certain than it read.**
  `DNDSchedule$DailySchedule` and `DNDSchedule$EndsAt` are in APK 2.2.4 and
  absent from 3.0.0. What a client stops shipping is a fact about that
  client -- but the honest standing is now "confirmed in 2.2.4, absent in
  3.0.0, never sent successfully by anyone".

### Tooling

- **`vendor_reference.json`** now carries the 223 serialiser classes and
  89 SDK models beside the enums.
- **`scripts/check_vendor_value_sets.py`** asserts every value set
  either matches a declared vendor enum or states why none exists.
  Reading the research did not stop two controls being built from
  recall; not looking is now a test failure.
- **`scripts/vendor_gap_report.py`** reports what the vendor knows and
  this library does not use, for enums and classes alike. Every entry
  needs a disposition; "not relevant" is a valid one, unreviewed is not.


## [0.3.0b4] - 2026-08-13

A full decode of iRobot app 3.0.0 was read against this library. Four wire
values were wrong, one filter silently discarded good data, and four
robot models were not recognised at all.

### BREAKING

Wire values only, and every one of them was wrong before. Code comparing
against these enums by value must be re-checked.

- **`MissionCommandType` lost four members and gained two.**
  `STARTDONOTDISTURB`, `STOPDONOTDISTURB`, `POINTCLEAN_VENDOR` and
  `FLUIDREFILL_VENDOR` never existed on the wire -- they were constant
  names read off an enum that carries no wire values. Do Not Disturb is
  `START_DND` / `STOP_DND`, sending `start_dnd` / `stop_dnd`.
- **`RegionType.TID` is `"tid"` again**, not `"furniture"`. The earlier
  correction cited an annotation that does not exist.
- **`CleaningProfileType` values are lowercase** (`light`, `normal`,
  `deep`, `smart`). The uppercase ones were constant names, and a real
  `"deep"` never matched them.
- **`mission_history.PadCategory` is gone**; `mission_control`'s is used
  for the same field. Two classes, one name, seven values that differed
  -- and the parser used the one whose own docstring said it was for a
  different field.
- **`RoutineCommand.command_type` may now be `None` or a plain `str`.**
  It was constructed strictly, so one unknown command raised and took an
  entire favourites list with it.

### Fixed

- **A favourites list was discarded whole when one entry failed to
  parse.** The caller logs at debug and returns an empty list, so an
  account with seven favourites looked like an account with none.
  Each favourite is now parsed on its own, at warning with the id.
- **`get_favorites_raw()` had the bug it exists to diagnose.** It
  returned `[]` for a wrapped response, so every diagnostics download
  taken to ask "did the server return anything?" answered no, whether
  or not it had.
- **Favourite ids spelled `favoriteId` were not found.** The lookup
  accepted `favoriteid` and `favorite_id`; one capital letter was the
  difference between seven favourites and an empty account.
- **Time estimates reported as good were thrown away.** `is_confident`
  compared against `GOOD_CONFIDENCE` while app 3.0.0 sends `good`.
  Both vocabularies are accepted.
- **Four Prime SKU prefixes were missing** -- `U1`, `V1`, `W2`, `Z1`.
  `is_prime_sku()` returned False for them, which routes a robot down
  the path for devices this library does not know.
- **A failed subscribe left a topic permanently unsubscribed and
  permanently registered** (@jouwdan, #62). The callback was recorded
  before the broker call, so a retry skipped the subscribe and the
  watcher reported nothing for the rest of the session.
- **`legacy_map_keys` wrote only half its pair.** `pmap_id` was added
  beside `p2map_id`; `user_pmapv_id` was not, though certain devices
  require it.
- **`DockCapabilities` dropped `fr`** (fluid refill) -- a dock could
  report it was refilling while the capability model denied it could.

### Added

- **`vendor_reference.json` ships with the library**, plus
  `vendor_reference.py` to read it: 480 enums, 35 capability gates, 24
  writable settings, 20 command wire values. Value sets can now be
  asserted against the vendor rather than recalled.
- **Six timeline status enums** -- `RoomStatus`, `TravelReason`,
  `TravelStatus`, `PadWashReason`, `WetOutStatus` and the zone
  vocabulary. Timeline statuses were bare integers before.
- **Five diagnostic stats models** (`bbrun`, `bbswitch`, `bbnav`,
  `bbpanic`, `mssnNavStats`) and thirteen missing fields in four
  existing ones.
- **Fifteen shadow properties with confirmed placement**, including
  `precheck`, `filterStatus.pctLeft`, `pwHeat` and battery details.
- **`SubModuleSwVersions`** -- four real subsystem versions that arrived
  in every capture and were stored as an untyped blob.

### Changed

- **`autoevacFreq` and `pwReturn` value sets documented against the
  vendor's enums** rather than the per-SKU picker lists. A real robot
  sits outside those lists, and a control built from them could not
  represent its own setting.
- **`verify-writes` described three settings as booleans that are not.**
  `pwReturn` carries two ranges in one field, and the wrong hint led a
  tester to report a correct value as stale.
- **`verify-writes` reported dotted setting keys as absent on every
  robot.** `audio.volume` is a write address, not a read key.
- **Map-edit V3 is nine operations, not one.** Two of them
  (`setSillReq`, `setCarpetReq`) have no V1 or V2 equivalent, so
  thresholds moved rather than vanished.

## [0.3.0b3] - 2026-08-12

### Fixed
- **Favourites arrive in two shapes and only one was handled.** The Classic path unwraps
  `{"favorites": [...]}`; this one did not, and returned an empty list for the same account that
  showed two favourites on Roomba+ v3.5.1.
- **Around twenty-five silent wire-key errors**, found by checking every `$$serializer` `<clinit>`
  block against what this library sends. Kotlin enum member names are not wire values.

### Changed
- Schedule editing confirmed reaching the server (@DaRealGuGu).

## [0.3.0b2] - 2026-08-11

### Fixed
- Three fixes from @DaRealGuGu's first run on b1, including the first concrete lead on the
  subscription wall: a second connection to the same robot evicts the first.

## [0.3.0b1] - 2026-08-10

Required by Roomba+ v4.0.0a31.

### Changed
- **Version jumps from `0.2.0`.** Seventeen beta iterations had stopped meaning anything.
- Three wire keys read real values where they read `None` before. Code checking
  `number_of_dirt_detects is None` and concluding "this robot does not report it" will start seeing
  numbers.
- `to_json()`, `get_time_estimates()` and `reset_robot_parts()` gained optional arguments.
- `error_text` is a new concept beside `error`.

## [0.2.0b16] - 2026-08-09

### Fixed
- **A shadow GET did not check that its request was sent.** publish() returns a result code and a
  handle and both were discarded, so a queued-but-unsent request looked identical to a silent robot.
- **Schedule writes dropped server fields this library does not model**, is_smart_clean_fav among
  them. Read-modify-write now carries unknown keys through untouched.

### Confirmed
- **Region-targeted cleaning works on real hardware** (@Echovictor37, Combo 105): the robot cleaned
  only the targeted room and operating_mode selected vacuum versus vacuum-and-mop. The method was
  documented as EXPERIMENTAL, UNCONFIRMED and is not any more. A third failure mode came with it:
  command_type=CLEAN with map_id=None returned a PUBACK and cleaned the whole house -- accepted,
  effective, and not what was asked.

### Added
- **--debug on the verification tool.** Asking a tester for a log previously meant guessing at an
  environment variable the tool does not read.

## [0.2.0b15] - 2026-08-06

### Fixed
- **Favourites never parsed.** The parser read favorite_id while the model own to_json writes
  favoriteid; the caller drops favourites with no id, so the mismatch produced an account with no
  favourites rather than an error. Both spellings accepted.
- **The volume probe used a guessed wire key.** It looked for audioVolume where robots report audio,
  and reported the setting as absent -- a wrong name became a wrong claim about a tester hardware.
- **get_shadow re-subscribed to topics it already held**, and that redundant step is what failed on a
  second read of the same shadow in one session. Skipped when already subscribed; cleared on
  disconnect.

### Changed
- **settings_roundtrip asks about every field before writing any of them.** Prompting per field put a
  human pause between MQTT operations and let the first failure end the run.
- **clean_score direction settled: HIGHER MEANS DIRTIER**, from an eleven-room account.

## [0.2.0b14] - 2026-08-06

### Fixed
- **TimeEstimates parsed a real response as empty.** The model was built from the app simulator, and
  the live response uses smart_maps/areas/value/seconds where the simulator uses
  pmaps/regions/estimate/minute. There is no whole-mission total and no confidence field.
- **set_map_orientation changed the orientation it claimed to preserve**, sending the argument default
  of 0.0 rather than the map current value.
- **The CLI crashed on a cp1252 console before running any check**, on the U+2713 in its own status
  lines. stdout and stderr are now reconfigured with errors=replace as the first statement of main().
- **settings_roundtrip could never read the settings shadow.** It looked for a state attribute on a
  ShadowResponse, which has topic and payload instead, and reported the failure as "this robot may be
  EPHEMERAL tier" -- a wrong claim about the tester hardware. Six controls were waiting on this check.
- **CleanScoreRegion declared four fewer fields than its parser read** -- high_traffic_enum,
  mission_last_cleaned, mission_last_unfinished and smart_clean_prefs existed at runtime and in no
  type. Which direction clean_score runs is now settled: HIGHER MEANS DIRTIER, from an eleven-room
  account where rooms cleaned by the newest mission read exactly 0.0 and one untouched for twenty
  missions read 0.6973 against a 0.7 threshold.

### Confirmed
- Mission history is a bare array -- 62 of 62 entries parsed from a real robot.
- set_map_name and order_favorite both accepted, rename verified in the app.
- The automations endpoint is alive and returns an empty list on an account with none.

## [0.2.0b13] - 2026-08-04

### Fixed
- **The live-map heading had half a turn added to it.** Undocumented, and asserted by a test that
  only restated it. The first field observation of the value showed the line pointing out of the
  back of the robot.

### Added
- **TimeEstimates model** for POST /v1/time-estimates: three levels (mission, map, region/zone),
  several estimates per room distinguished by cleaning params, unit carried in the payload, and a
  confidence gate.
- **mission_history check** -- reports the response SHAPE, and distinguishes an envelope mismatch
  from an empty history, which otherwise look identical.
- **Mission history parsing confirmed** as a bare array from the app's own restservices package.

## [0.2.0b12] - 2026-08-04

### Fixed
- **subscribe() ignored its own result code.** paho reports MQTT_ERR_NO_CONN when the client is not
  connected and sends no SUBSCRIBE packet at all; this returned as if it had worked, leaving the
  caller watching a topic it never subscribed to. Now raises, and distinguishes "never sent" from
  "rejected by the broker".
- **A missing SUBACK counted as success.** The three-second wait gave up and carried on silently.
  Unconfirmed topics are now counted and logged; still proceeding, but no longer without a word.

## [0.2.0b11] - 2026-08-03

### Added
- **`settings_roundtrip` check.** Resends each `rw-settings` field at its own current value, so
  nothing changes on the robot. Probes the six fields iRobot's own product profiles list as user
  settings -- charge light ring, audio volume, mop dry duration, pad wash frequency and two
  evacuation settings -- none of which anyone has confirmed writable. A green line means the write
  was accepted, never that the setting works: `schedHold` accepts, reads back, and is ignored.
- **`PositionUpdateMessage.expires_at`**, read from `update_expire_ts` on the outer livemap
  envelope. It rode beside `pos_update` and was discarded -- the inner object was parsed and the
  wrapper thrown away.

### Fixed
- **The live-map keep-alive slept before its first ping.** The robot only publishes while those
  pings arrive, so the subscription sat on an empty queue producing no messages, no exception and
  no counter movement. A field capture showed exactly that: mid-mission, every counter at zero, no
  error anywhere. Consecutive ping failures are now counted so one hiccup is distinguishable from
  "this has never worked".

### Changed
- **The keep-alive is paced by the robot instead of a constant.** Each position message says when
  the stream lapses; the app pings a ten-second margin before that, and this library was polling at
  a flat ten seconds -- the same number meaning something else entirely, at 8,640 REST calls per
  robot per day. With a one-minute validity window the same coverage costs about sixty. The fixed
  interval stays as the fallback for robots that never send the field.

## [0.2.0b10] - 2026-08-02

### Added
- **`dnd_read`, a read-only quiet-hours check.** Nobody has ever seen a populated DND response --
  three accounts all return empty because none has quiet hours set -- so the model has four fields
  with no example behind any of them and the write body was never investigated. One run from an
  account that has them configured unblocks the feature. Counts populated fields independently of
  the parser, because the write path resends DND from the parsed model and would drop an unmodelled
  field.

### Fixed
- **A `null` inside `commands` crashed `ScheduleOptions.from_json()`.** A bare comprehension called
  `.get()` on every entry, so one malformed element raised `AttributeError` from inside the parser.
  Everything schedule-shaped read this -- Home Assistant's calendar and every schedule switch --
  so a single bad entry would have taken all of them down at once. Same shape as the b6
  `SchedulesResponse` crash, one level further in.

## [0.2.0b9] - 2026-08-02

### Fixed
- **`create_schedules` sent the wrong body shape**, which is the HTTP 500 that blocked schedule
  creation for four field rounds. Each entry must be `{"options": {...}}`; the ScheduleOptions were
  going straight into the array. `schedule_id` is omitted rather than sent as null -- the app
  serialises without `encodeDefaults`. Confirmed from `CreateSchedulesRequest.getHttpBody()` and
  both `$$serializer` `<clinit>` blocks.
- **`schedule_create_delete` printed the inner object, not the body it sent.** Four rounds were
  spent reading a payload that never crossed the wire, in the check that exists to show what did.

### Changed
- **Clean score model corrected against the first real response**: `smart_clean_prefs` is a dict,
  not a string, and three fields the confirmed key list did not have are now modelled
  (`high_traffic_enum`, `mission_last_cleaned`, `mission_last_unfinished`). A key list confirmed
  from the vendor's parser is a floor, not a ceiling.

### Confirmed in the field
- `GET /v1/p2maps/clean-score?p2map_id=<id>` works.
- `/v1/user/automations` answers with an empty array rather than a 404 -- alive server-side despite
  being a dead constant in the app.

## [0.2.0b8] - 2026-08-02

### Changed
- **`schedule_create_delete` no longer replays `created_time`.** Copying a template schedule meant
  sending the server back a timestamp it assigns itself. Last candidate standing for the HTTP 500
  that create has returned on every attempt; the other two (`initiator`, `is_smart_clean_fav`) were
  ruled out rather than set aside. The check prints which fields it deliberately omits.

### Documented
- **`DockState` is not exhaustive, and now says so.** The server sends 671 for a pad wash blocked by
  a missing or empty tank; the code exists nowhere in the iRobot APK, whose pad-wash family ends at
  669, and the app's own fallback is `"Unknown dock state %d"`. Consumers must handle unknown
  values. A numeric overlap between this enum's low values and the shadow's `dock.cap` flags is
  recorded as an open question, deliberately not acted on.
- **Mop wetness value range resolved.** `padPlate` has its own enumeration, offset by one from the
  other two pad categories (`0 Invalid, 1 Damp, 2 Moderate, 3 Wet`). This explains a capture that
  had blocked any wetness control: the "impossible" 3 sat under `disposable`, meaning Invalid --
  no disposable pad fitted. `ppWetLvl` is a count of usable steps, not a flag. Documented only; a
  control must still choose the field via `detectedPad`.

## [0.2.0b7] - 2026-08-01

### Fixed
- **A failing check hid the server's explanation.** `RestError` carries the response body in
  `raw_response`; the report printed only `str(exc)`, which is `HTTP 500 from <url>`. A field round
  that finally reached the server produced a status code and nothing else. Third release running in
  which the fault was the reporting layer rather than the code doing the work.

### Added
- **`schedule_create_delete` prints the request body before sending it.** Two causes for the 500 are
  arguable from the source and not distinguishable without the payload: a copied `created_time`,
  which the server assigns, and copied region `commands` missing `initiator`, which the app adds at
  send time. Guessing between them is not how this project works.

## [0.2.0b6] - 2026-08-01

### Fixed
- **`verify-writes schedule_create_delete` could not run on any account.** It located the template
  schedule it copies with `getattr(schedule, "options", None)`; `SchedulesList.schedules` is
  `list[dict]`, so that returned `None` every time and the check always took the "no schedules"
  branch. Second occurrence of the b5 bug, in the same file -- these were the two code paths that
  told one tester he had none while his app showed three. b5 fixed the other one and rewrote this
  branch's wording, leaving the cause in place.
- **An unparsable schedule response is no longer reported as an empty account.** When the server
  sends schedules and none parse, the check names it as a bug here rather than sending the tester to
  create a schedule he already has.
- **`_set_dnd` implied a raw resend it never performed.** `getattr(current, "raw", None) or fields`
  -- `DNDStatusResponse` has no `raw` field, so only the fallback ever ran. Now explicit, with the
  limitation stated: the resend is reconstructed from the parsed model and drops unmodelled keys.

- **`SchedulesResponse.from_json()` raised on a malformed response.** `household_schedules` was
  iterated without checking it is a list; a dict there yields its keys and the nested parser then
  called `.get()` on a string. In the library, on the path HA's schedule calendar and switches use.
  A parser's job on an unexpected shape is to return nothing, not to raise.
- **b6's own new code crashed on a non-dict response.** `(raw or {}).get(...)` raises on any truthy
  non-dict; found in this release's bug hunt before it shipped.

### Added
- **Household-scoped checks print which household they picked**, and whether it was resolved from
  the account or passed with `--household-id`. An empty answer from the wrong household is
  indistinguishable from an empty answer from the right one, which is the ambiguity behind three
  rounds of field testing.
- Tests that execute the `verify_writes` runners against realistic server responses. The module had
  static tests only (signature matching, source grepping) -- every signature involved was correct,
  which is why three bugs of this class survived in it.

## [0.2.0b5] - 2026-08-01

### Fixed
- **`verify-writes schedules` reported "0 households" on every account, and called it a pass.** The
  check read the households list, each household and each schedule with `getattr()`; all three are
  plain dicts, so all three returned the default. `get_schedules()` was never called at all. A
  tester with three schedules visible in his app produced output byte-identical to a working
  account's -- the second field round in a row to end with no information. Three independent bugs;
  fixing only the first would have produced the same empty answer at the second.
- **A check that produced no evidence was reported as `OK`.** It now reports `SKIPPED` with the
  reason, counted separately in the final summary.

### Added
- **`get_schedules_raw()`** on `PrimeRestClient` and `PrimeRobot` -- the unparsed response, for
  diagnosis. `get_schedules()` returns a parsed `SchedulesResponse`, which cannot be used to find
  out whether the parser is dropping something. Added to the wrapper signature guard.
- **`verify-writes schedules` prints the raw server response before the parsed reading**, and names
  the disagreement when the two differ -- so a parser bug is legible without another round trip to
  the tester. Account identity (`owner_cognito_id`, `household_users`) is masked; household and
  robot ids are not, since which household holds which robot is the open question.
- First tests on `_list_schedules`, which had none.

### Changed
- `_list_schedules` accepts exactly the household response shapes `PrimeRobot.get_household_id()`
  accepts (bare list, or a single household as a top-level dict) rather than guessing at wrapper key
  names.

## [0.2.0b1] - 2026-07-30

First beta. No code changes from 0.1.11a31 -- the version number catching up with what a31 already
did, which is what a beta is for.

### Confirmed
- **Virtual wall writes work end to end**, on two independent accounts. One resent four zones of two
  types in a single command; the other wrote, re-read the new map version unchanged, and wrote
  again -- the round trip that separates "accepted" from "stored".
- **Map bundle models field-confirmed**: borders are MultiPolygon, floor types carry "carpet" under
  the wire key `type` (not `floor_type`), dockPose reports an orientation.

### Changed
- `check_version_badge.py` understands beta versions. It raised on 0.2.0b1 exactly as its own
  docstring predicted -- the badge could not silently keep saying alpha.

## [0.1.11a31] - 2026-07-29

**FIELD-CONFIRMED 30 July 2026 (chairstacker):** the count fix works. Four zones of two different
types (3x KeepOutZone + 1x NoMopZone) in one command, `{"status": "success"}`, new `p2mapv_id`
issued. The hardest case available, and the first successful virtual-wall write this project has
had.

### Fixed
- **The virtual-wall HTTP 500: `virwall` starts with a COUNT of the walls.** Confirmed from
  `CommandSerializer` bytecode -- one JsonArray, `walls.size()` first, walls after. The only
  `.size()` call in the whole serializer, and absent from the otherwise identical
  `adjust_furniture`. Explains 500-rather-than-400 (valid JSON, fails deserialising at position 0)
  and why field testing could not narrow it down.
- **`--only-first-wall` truncates the payload it sends.** It built the command before trimming the
  list, so it announced "sending 1 of 2" and sent both.

### Removed
- **Type-variant probing**, unused. Bytecode settled the id (String) and type code (Int) directly.

### Note
- `response_type` is untested again: all three variants were rejected for the missing count.

## [0.1.11a30] - 2026-07-28

### Confirmed
- **Virtual wall wire format verified against a second, independent APK read.** Type codes, the
  degenerate-quadrilateral encoding for linear walls and outer-ring-only handling all already
  matched. The wall array is not the cause of the HTTP 500.
- **`response_type` ruled out** by a field run where all three shapes genuinely reached the server.

### Fixed
- **take(4)**: the app takes exactly the first four polygon points. This library dropped the
  closing point only when the ring was closed -- identical for rectangles, wrong for anything else.

### Added
- **`--only-first-wall`** narrows the remaining question to command shape versus list contents.
- **`set_virtual_wall` replaces the whole shared list** -- documented, with a guard test. A partial
  list deletes every zone it omits.

## [0.1.11a29] - 2026-07-26

### Fixed — a28 was broken

- **`PrimeRobot.edit_map()` did not accept the `response_type` parameter a28 added to the REST
  client.** All three variants of the virtual-wall experiment died with `TypeError` before a single
  request left the machine.

  The damage went further than a wasted run: the script then printed *"All shapes failed. That
  rules out response_type as the cause"* -- which was false, because nothing had been tested. A
  tester's entire evening produced one confidently wrong conclusion.

  Reporting a local crash as a server result is the same failure mode as the PUBACK false signal
  earlier in this project's history. The script now distinguishes the two: a `TypeError` is
  reported as **"NOTHING WAS ACTUALLY SENT ... this run rules out nothing at all"**, and only a
  request that genuinely reached the server counts as evidence.

- **A guard test now compares `PrimeRobot`'s method signatures against `PrimeRestClient`'s.** A
  wrapper that cannot forward an argument fails with `TypeError` before any request is made --
  trivially detectable, expensive to miss. Verified against the actual a28 bug by reintroducing it.

### Confirmed by field testing

- **System uptime counts POWERED-ON hours**, not time since registration. Settled by two accounts
  at opposite ends of the range: one rarely switched off showed a 14-hour gap against wall-clock
  time, another unplugged for months showed 5579 hours. Both match their owners' recollection --
  and if it tracked wall-clock time, both gaps would have to be near zero.

  Consequence for consumers: do not present this as device age. On a robot that has spent months
  unplugged the two differ by more than half.

## [0.1.11a28] - 2026-07-26

### Changed

- **`edit_map()` takes a `response_type` parameter**, and the virtual-wall script now tries three
  request shapes in one run rather than one guess per round trip.

  Context: a27 fixed a genuine format deviation -- a GeoJSON ring's duplicated closing coordinate
  was being sent as a fifth point where the wire format takes four. A field retest (DaRealGuGu)
  confirmed the payload is now correct **and still returns HTTP 500**. So that deviation was real
  but demonstrably not the cause.

  The next suspect was already documented as unverified in this method's own docstring:
  `response_type: "link"` asks the server for a presigned DOWNLOAD url. That value is confirmed for
  FETCHING a map; on an EDIT it may be meaningless, which fits a body that parses and then cannot
  be honoured.

  Deliberately a parameter rather than a changed default -- nothing has confirmed the right value,
  and quietly swapping one unverified guess for another would leave us equally uninformed. The
  script tries omitting the key, then `"link"`, then `"binary"`, stopping at the first success.
  The command body is identical across all three, so a variant that works implicates the envelope
  and nothing else.

  If all three fail, that is also a result: it rules out `response_type` and moves the suspicion to
  the discriminator inside `edit_cmd`, the other item the same docstring flags as unconfirmed.

### Confirmed by field testing

- **SKU prefix `Y41`** seen on real hardware for the first time (arielgr, `Y414040`). Three of the
  fifteen prefixes in the table are now field-confirmed rather than decompilation-only.

- **Five capability flags were being silently dropped**: `cmds`, `eCmd`, `mopLift`, `odoa` and
  `p2maps_editv2_feats` all appear in a real `cap` object and were not modelled, so `from_json()`
  discarded them without error.

  That object is the only place describing what a *specific* device can do, and it is what feature
  gating reads -- a capability we never see is a feature we can never offer. A guard test now names
  any capability present in a real capture that the model does not declare.

## [0.1.11a27] - 2026-07-25

### Confirmed by field testing

- **`initiator` is REQUIRED for region commands.** Settled at last, by the first run where all
  three stages got a delivery confirmation (DaRealGuGu, a26): stage 1 without it left the robot on
  `charge`/`none`; stage 1b, identical but for that one field, started a mission. A stored
  favorite does not carry an initiator -- the app adds it when sending.

  Two earlier runs had appeared to refute this. Both were confounded by undelivered sends, which is
  exactly why the connection work in a26 had to come first.

### Fixed

- **Stage 3 (the from-scratch region command) differed from the confirmed-working shape in three
  ways** and had never been reconciled with it -- it was written when nothing worked at all:

  | | working | stage 3 |
  |---|---|---|
  | command | `start` | `clean` |
  | region key | `region_id` | `id` |
  | `user_p2mapv_id` | present | absent |

  The first two are fixed. `Region.to_json()` now emits `region_id`, which its own docstring had
  flagged as an open question ("reads show region_id, writes assumed id") -- two confirmed-working
  commands both carried `region_id`, and the robot echoed them back unchanged.

- **The map-version pre-flight was noise.** Real mission events show the robot re-versioning its
  map **five times inside 37 seconds** of cleaning, so every stored favorite is stale within a
  minute of being saved. Two confirmed-working region commands carried versions hours out of date
  and started missions regardless.

  Downgraded from FAILED to SKIPPED. A check that fires on every run for something demonstrably
  harmless is not a signal -- and noise sitting beside genuine failures makes those easier to skip
  past.

- **Virtual-wall writes sent one point too many.** A GeoJSON ring repeats its first coordinate as
  its last, so a rectangle read from the map bundle arrives with five points; the V1 wire format
  takes four. Real resends of two untouched zones returned HTTP 500 -- a server error rather than a
  400, consistent with a payload that parses and then breaks something downstream.

  The correct format was already written down in this file's own `VirtualWallRectangleV1`
  docstring, from APK decompilation, directly above the function that got it wrong.

### Confirmed along the way

- **Zone types, against real data rather than decompilation alone**: `1 = KeepOutZone`,
  `6 = NoMopZone`. The first real keep-out zone data this project has seen.

## [0.1.11a26] - 2026-07-25

### Confirmed by field testing

- **Robot settings writes work.** All five (`childLock`, `ecoCharge`, `schedHold`,
  `noAutoPasses`, `vacHigh`) were written and read back successfully on a real device
  (DaRealGuGu). `childLock` is confirmed **end to end**: the change appeared in the iRobot app and
  the robot announced it audibly -- the first setting whose physical effect is confirmed rather
  than only its acceptance.

- **`schedHold` is accepted but ineffective.** Write accepted, read-back confirmed, and the
  schedule stayed active in the app. Writing it to `rw-settings` is evidently not the mechanism
  the app uses to pause a schedule.

  Worth recording how that was caught: this project's own cross-check against the classic/unnamed
  shadow FLAGGED the divergence -- `rw-settings` said True while classic still said False --
  **before** the tester looked in the app, and the app then confirmed it. Two sources disagreeing
  turned out to mean "the write did not really take", which makes that cross-check a real signal.
  Disabling moved both sources in step, so the divergence is specific to enabling.

  `verify-settings-write` now warns before toggling it, because otherwise a tester sees five green
  checkmarks and reasonably concludes it worked.

### Fixed

- **A regression introduced in a25, caught in the field on the very next run.** Turning
  `rejected/report` off left `rejected_task` as None while `asyncio.gather()` still received it
  unconditionally, which raises `TypeError` immediately. The watch window therefore died on
  arrival, and every stage printed "NO events observed" -- including one whose mission status
  visibly changed from `charge`/`none` to `run`/`clean`.

  That is exactly the damage the a25 change existed to stop: a real success reported as nothing.
  The robot's own mission counter settled it -- `nMssn` jumped 35 to 37, and the missing 36 was
  the stage this bug had hidden.

- **`publish_cmd_payload()` published into dead connections without noticing.** `get_shadow()` has
  always revived a dead connection before using it; publish only ever checked whether a client
  object existed at all. Publishing into a dead connection is the worst failure available here: no
  error, no PUBACK, and the calling script then reports the missing confirmation as though it said
  something about the payload.

  Field evidence across three consecutive sessions: the FIRST send of every session got no PUBACK
  while later sends succeeded. The ordering in those logs identified the cause -- the shadow GET
  timed out BEFORE the publish, so the connection was already dead rather than killed by sending.
  What kills it is the interactive pause: the tool prints a large payload and waits for a human to
  read it and type y.

- **`_subscribe_and_wait()` and `subscribe()` had the same gap**, and it was the more damaging one:
  subscribing to a dead connection fails SILENTLY, so the watcher observes nothing and a real robot
  reaction is reported as "nothing happened". One field log showed all three symptoms of a single
  dead connection together -- failed subscribe, timed-out shadow GET, missing PUBACK -- with only
  the middle one surfacing as an error.

  Every operation in the MQTT client now verifies the connection is alive before using it.
  Previously two of seven did.

- **`keepalive` lowered from 300 to 60 seconds** (paho's own default). MQTT declares a connection
  dead after 1.5x the keepalive interval, so 300 meant a broken connection went unnoticed for up to
  **450 seconds** -- and during that window `publish()` succeeds locally while nothing reaches the
  broker.

- **Four remaining bare `assert self._client is not None` statements replaced**, each according to
  its purpose: reconnect where the operation needs a live connection, quiet return on the teardown
  path, and a real exception where reconnect already guarantees a client. A guard test now checks
  the pattern cannot return -- it found all four on its first run, after manual review had found
  none.

## [0.1.11a25] - 2026-07-25

### Fixed

- **The connection instability behind every unexplained "nothing happened" result was our own
  diagnostic subscription.** `rejected/report` is an EXPLORATORY topic that has never been
  confirmed live, and this project's own module header already warned that subscribing to an
  unconfirmed topic causes immediate "Unspecified error" disconnects. It was subscribed
  unconditionally in both `verify_region_commands.py` and `verify_mission_timeline.py`.

  A field log (DaRealGuGu, a24) made the mechanism unmistakable: across three stages sent in one
  session, every stage that received a PUBACK started a mission, and every stage that did not got
  nothing -- regardless of payload differences that had previously looked significant. The
  connection is shared between watchers, so one unconfirmed subscription tearing itself down took
  the whole thing with it, including the publish a real command needed.

  **`rejected/report` is now off by default in both scripts.** Across five real runs by three
  testers it has produced zero messages, so there was nothing to lose by turning it off and
  everything to gain if it was the cause. `--watch-rejected` restores it for anyone who wants to
  test the channel itself.

  This also retroactively explains earlier "nothing happened" results from other testers that had
  no PUBACK either -- they were very likely never delivered, independent of anything about the
  region-command payload.

## [0.1.11a24] - 2026-07-25

### Fixed

- **`getattr()` on data that came off the wire, in three separate places.** Some REST wrappers
  return plain `list[dict]` and others return parsed models; `getattr()` on a dict quietly returns
  the default. None of these raised -- each produced a report full of `None` that reads as "the
  robot had nothing to say" rather than "we asked wrongly", which sends an investigation looking
  at the robot instead of at us:
  - the pad pre-flight reporting "no operatingMode in regions" for a payload that visibly carried
    one on *every* region
  - `--list-maps` printing `name='(unnamed)'  --p2map-id None` for a map that certainly has both
  - the map-version pre-flight reporting "no active_p2mapv_id reported"

  A `field()` helper now reads both shapes, 25 call sites use it, and an AST-based guard rejects
  `getattr()` on wire data. The guard found more occurrences on its first run than manual review
  had.

- **Four scripts read named shadows without opening an MQTT connection.** Named shadows travel
  over MQTT, not REST, and `connected_robot()` only opens a connection when asked. The failure
  landed on a tester as a bare `AssertionError` four frames from the cause, on the very first run
  anyone had given that script. `verify_settings_write`, `verify_named_shadows`,
  `verify_mission_commands` and `verify_mission_timeline` now connect, a guard test checks that
  any script calling an MQTT-backed method asks for one, and the assertion became an error that
  explains itself.

- **Concurrent watchers fought each other over the shared connection.** Every watcher had its own
  reconnect loop, but they all use ONE mqtt client. The region-command session always watches two
  topics at once (mission/timeline plus rejected/report), so a reconnect by one tore down the
  shared connection, the other saw that as a drop and rebuilt it, and the first then saw *that* as
  a drop -- indefinitely.

  A field log showed the signature plainly: dozens of immediate drops with almost no failed
  attempts between them, because every reconnect succeeded and was then torn down by the other
  watcher. It cost two of three test stages their result -- the publish went out over a
  torn-down connection, never received a PUBACK, and the script reported that as a possible
  policy-level block. Another self-inflicted wrong diagnosis.

  Reconnects are now serialised behind a shared lock with a generation counter: the first watcher
  rebuilds the connection, the others resume on it instead of tearing it down again.

- **The event summary printed `[None]` for every event.** It was written against a parsed
  `MissionTimelineEvent` model, but what arrives is a raw `ShadowResponse` whose content sits in a
  payload dict, so every attribute lookup returned None. Harmless for months because every run saw
  zero events -- and then useless on the very first run where a robot genuinely started a mission.
  It now reads the real shape, including which regions the robot echoed back.

- **Multi-robot accounts sent commands to whichever robot came first.** A field run made this
  concrete: an account holding a Roomba 980 (classic protocol) and a Prime robot had its entire
  region-command session delivered to the 980. The proof was in the same log -- that robot's
  `ro-currentstate` shadow returned 404, a shadow every V4 device has and no classic one does.
  Nothing could have worked, and every result from that session was noise.

  Three layers now prevent it:
  - `primary_blid()` raises instead of picking one, listing each robot with the exact `--blid`
    to pass.
  - The tools prompt interactively, marking which robots this library can actually talk to and
    offering the single Prime one as the default.
  - If a favorite belongs to a different robot than the command targets, the send is **blocked**,
    not merely warned about, and names the correct BLID.

- **Household lookup assumed `robot_id == blid`.** Where they differ it silently returned None,
  which would have broken every household-scoped operation -- schedule writes above all. The
  correct identifier was in the login response all along and is now passed through.

### Added

- **`is_prime_sku()`**, moved in from ha_roomba_plus so there is one copy rather than two. This is
  protocol knowledge and the tools need it as much as the integration does. The three-character
  prefix check is load-bearing rather than fussy: R28 is Prime and R98 is Classic, and that exact
  pair sits on one tester's account -- a single-letter check would have called his Roomba 980 a
  Prime robot.

  Returns False for anything unrecognised on purpose. The table is explicitly incomplete for
  platforms nobody has field-tested, so False means "not known to be Prime", never "confirmed
  Classic" -- which is also why the tools mark and suggest rather than choosing silently.


## [0.1.11a23] - 2026-07-25

### Fixed — URGENT, a22 is broken

- **The experimental `User-Agent` header added in a22 has been removed.** It went in on a
  third-party project's documented but untested claim that AWS IoT's authorizer inspects it. The
  parallel APK research then examined the real app's own connection code and found it sends
  exactly three headers, no fourth — the hypothesis is disproven.

  Removed not because it was proven harmful, but because it shipped to every consumer of this
  library, Home Assistant included, in the same release that broke Prime setup there. Whether it
  contributed is unknown and now moot. The connection layer is back to its a19 behaviour, which
  is the state a field tester confirmed working.

- **A crash in a22's own SUBACK check killed the MQTT connection.** paho-mqtt 2.x passes
  `ReasonCode` objects, not ints, and `int()` on one raises TypeError -- inside paho's own
  network thread, which took the client down with it. Every shadow read then timed out and no
  publish was ever confirmed.

  **This affected far more than the diagnostic scripts.** `ha_roomba_plus` v4.0.0a7 pinned a22,
  so Prime robots failed to initialise in Home Assistant entirely; a6 (pinned a19) was
  unaffected. Anyone on a22 should update.

  Worse than the crash itself: the missing publish confirmation was then reported to a field
  tester as evidence of a server-side policy block. It was our bug producing a confident, wrong
  diagnosis across three test stages.

- **`primary_blid()` silently picked an arbitrary robot on multi-robot accounts.** It returned
  the first key of a dict. A tester's entire region-command session went to the wrong robot,
  while the Home Assistant integration -- which asks for a specific BLID -- was talking to the
  right one. The two tools disagreed and nothing said so. Now raises, listing every robot with
  the exact `--blid` value to pass.

- **Household lookup assumed `robot_id == blid`.** On an account where they differ (a
  16-character BLID alongside a 32-character robot_id, both real) it returned None silently,
  which would have broken every household-scoped operation -- schedule writes above all. The
  correct identifier was in the login response all along; it is now passed through.

- **Two pre-flight checks had never done anything.** One searched for `command_defs`/`commandDefs`
  when the confirmed wire key is `commanddefs`; the other used `getattr()` on regions that arrive
  as plain dicts. Neither failed -- both politely reported there was nothing to see, on every run
  they ever had.

### Added

- **Multi-robot accounts are now handled properly, and the tools help you pick.** A field run
  settled why this matters: an account with a Roomba 980 (classic protocol) and a Prime robot had
  its entire region-command session sent to the 980, because the library picked whichever robot
  came first in a dictionary. The 980 cannot speak this protocol at all -- the same log showed its
  `ro-currentstate` shadow returning 404, which a V4 device always has.

  Now: `primary_blid()` refuses to guess and lists your options; the tools prompt interactively,
  marking which robots this library can actually talk to and offering the Prime one as the
  default; and if a favorite belongs to a different robot than the command is being sent to, the
  send is **blocked** rather than warned about, naming the exact `--blid` to use instead.

- **`is_prime_sku()` moved into the library** from ha_roomba_plus. It is protocol knowledge, the
  tools need it too, and two copies would drift. The three-character prefix check is load-bearing:
  R28 is Prime and R98 is Classic, and that exact pair sits on one tester's account.

- **Every script now lists the account's robots when there is more than one**, marking which is
  targeted and showing each robot's own `robot_id` next to its BLID. The login response has always
  carried this and no script had ever shown it.

### Changed

- **The diagnostic tooling is now a separate distribution.** `roombapy-prime` (the library) and
  `roombapy-prime-tools` (the ten field-test scripts) ship from the same repository but install
  independently. The driver is not disk space: the tools register **11 console scripts, several of
  which move a real robot**, and those had no business on the PATH of every Home Assistant
  installation that merely consumes the library. The core now registers **zero** console scripts.

  **Nothing changes for testers** — installing the tools pulls the core in as a dependency, so it
  is still one command:
  ```bash
  pip install "roombapy-prime-tools@git+https://github.com/johnnyh1975/roombapy-prime.git@v0.1.11a23#subdirectory=tools"
  ```

  **Nothing changes for library consumers** either — `ha_roomba_plus` installs `roombapy-prime`
  exactly as before, and simply no longer receives the tooling.

- **Shared CLI scaffolding** (`roombapy_prime_tools/_cli.py`) replaces the account arguments, BLID
  validation and credential prompting that had been copy-pasted into all ten scripts. That
  duplication was not merely untidy — the copies had drifted, and the drift caused real bugs
  (an undefined-name crash from a helper that existed under one name in one script and not at all
  in another; three separate cases of a fix landing in a standalone script but not its
  session-runner twin). It also surfaced a user-visible inconsistency: four scripts prompted
  `"Password: "` while six prompted `"iRobot account password: "`. Now uniform.

### Added

- **`test_tools_boundary.py`** enforces the one-way dependency by AST analysis: no core module may
  import the tooling, including imports nested inside functions (which this project uses
  deliberately and a text search would miss). It also guards itself — one test asserts that core
  files were actually found, so a future layout change cannot make every check pass vacuously.

- **`scripts/check_version_pin.py`** verifies that the tools distribution pins exactly the core
  version it ships alongside. This is the one real risk introduced by the split: the tools reach
  deep into the library, so a mismatched pair fails as a confusing AttributeError in a field
  tester's terminal rather than cleanly. It caught a genuine mismatch on its very first run.

- **Tests for the shared scaffolding itself** — the duplicated copies never had any, and it is now
  reached by all ten scripts, so a regression there would break every one of them at once.


## [0.1.11a22] - 2026-07-25

### Fixed

- **Region-command tests displayed a payload that was NOT the payload actually sent.**
  `command.to_json()` was printed, but `publish_cmd_payload()` adds a `time` field just before
  publishing. The gap produced a confident wrong conclusion from an otherwise careful analysis
  pass (comparing a field tester's printed payload against the app's own builder, finding no
  `time`, and reasonably concluding it was missing — when it was on the wire all along). The
  preview now mirrors what publish actually sends.

- **`RegionType.TID` used the wrong wire value** (`"tid"` instead of the confirmed `"furniture"`).
  The old value was an inference from the confirmed `rid`/`zid` lowercasing pattern, never seen in
  any real capture. Blast radius was small — `_is_safe_command_def()` rejects TID regions outright,
  so only stage 4 (never yet run by anyone) could have sent it.

### Added

- **Mission-status readout around every region command send.** The app's own
  `applyConditionalChecks()` runs a readiness check whose refusal surfaces as a
  `ResolvedMissionStatus` value with reasons in a `vector<RobotReadinessState>` — i.e. in the
  MISSION STATUS, not on `rejected/report`, and not in any error field. That would explain a
  command producing neither effect nor error. We already modelled the two wire fields that carry
  it (`cleanMissionStatus.not_ready` / `.cond_not_ready`) but never read them during a test. Now
  snapshotted before and ~3s after each send (deliberately before the long watch window, since a
  readiness refusal is a near-instant local check), with codes named via a new partial
  `RobotReadinessState` enum — unknown values stay honestly `UNKNOWN_<n>` rather than getting a
  guessed label. Also reads `regions_left`, the single most on-point field for whether a
  region-based mission actually started.

- **Two pre-flight checks that need no robot movement at all**, both acting on confirmed research
  hypotheses: a stale map version in a stored favorite (`MAP_VERSION_MISMATCH`), and a mopping
  mode requested with no pad fitted (`NO_MOP_WITHOUT_PAD`). The latter deliberately reports both
  inputs rather than reproducing the rule — that check runs robot-side and cannot be replicated,
  and the exact `detectedPad` value set is unconfirmed for Prime.

- **A round-trip fidelity check.** We parse a stored favorite into typed models, then re-serialize
  it to send — so any field the favorite carries that our models don't know is silently DROPPED,
  producing a command subtly less complete than the app's. That failure mode looks exactly like
  this project's central symptom. Compares raw vs. re-serialized keys at command and region level;
  added fields (`initiator`, `favorite_id`) are correctly ignored, only dropped ones flagged.
  Needs `get_favorites_raw()`, also new.

- **`PolicyZoneFeature.category`** — makes the already-confirmed categorization rule applicable
  instead of leaving it in prose. One branch is genuinely counter-intuitive: a virtual wall is not
  its own zone type, it is a `"KeepOutZone"` whose geometry is a `LineString` rather than a
  `Polygon`. Anyone implementing from field names alone would almost certainly miss that.

- **Model additions:** `PadCategory` (confirmed REST-side wire values, with an explicit note that
  these are NOT confirmed to match `ro-currentstate.detectedPad`), `MissionCommandType.POINT_CLEAN`,
  and `raas`/`odoaLite` (confirmed to exist, but absent from every capture we have, so which
  shadow carries them is documented as a best guess).

- **Stage 1c (`--send-enveloped`)** — sends the command wrapped in a `cmd`/`cmdJson` envelope
  instead of flattened. HYPOTHESIS SUBSEQUENTLY DISPROVEN (the envelope rule belongs to the
  schedule deserializer, and `buildJsonCommon()` puts `initiator`/`favorite_id` at top level
  exactly as we do), so it is deliberately NOT part of the automatic session runner and cannot
  consume robot-moving test runs. Kept reachable, with the disproof documented.

### Fixed

- **Real bug found and fixed, prompted directly by a field result** (chairstacker triggered a
  favorite AND a room clean from the real app — the robot genuinely reacted to both within 20
  seconds — while `--watch-wildcard` saw nothing at all during that exact window): two separate,
  compounding issues.
  1. `_on_subscribe()` received the broker's SUBACK reason code for every single `subscribe()`
     call this library has ever made, but never checked it — a subscription actively REJECTED by
     the broker's IoT policy (MQTT's own 0x80 failure code) was recorded identically to a
     successful one. `subscribe()` now raises a new `SubscriptionRejectedError` when this happens,
     naming the specific topic that was denied.
  2. `verify_mission_timeline.py`'s `run()` collected every watch task's result via
     `asyncio.gather(*tasks, return_exceptions=True)` but never inspected the return value — ANY
     exception in ANY watch task (a rejected subscription, a dropped connection, anything) vanished
     with zero visible trace: no print, no report entry, nothing. A real failure and a genuinely
     quiet topic looked completely identical. Now checks each task's result and reports real
     failures clearly, associated with the specific topic that raised them.

### Added

- **Every diagnostic script now ends with a complete final report**, not just a bare count.
  Each result line was already printed the moment it was added, but in a long run those end up
  scattered across the whole terminal output — between login messages, subscribe notices, message
  dumps and explanatory text — with only "3 OK, 0 failed, 0 skipped" at the very end, giving no
  indication of WHICH checks those were. A tester whose run genuinely found nothing saw an
  apparently empty terminal and one number, with the actual finding ("no messages arrived during
  the watch window") buried far above; worse, a real failure printed at the end was immediately
  followed by that same bare count. New `Report.print_final_summary()` reprints everything as a
  self-contained block at the end, applied across all 10 diagnostic scripts (20 call sites).

- **The MQTT WebSocket connection now sends a `User-Agent` header** ("?SDK=Android&Version=2.17.1")
  — previously sent none at all. EXPERIMENTAL, unconfirmed for this specific app: an independent
  project reverse-engineering a related (but different) iRobot app documented that AWS IoT's
  custom Lambda authorizer inspects this exact header and grants a more restricted IoT policy
  when it's absent — though that specific claim isn't backed by any reproducible test tool in
  their own repository, so treat the underlying reasoning as unconfirmed too. The value itself is
  real, working code in that other project, not just documentation; its `?SDK=<platform>&Version=
  <version>` format looks SDK-generated rather than app-customized, making it a more defensible
  test candidate than copying an app-specific identifier would be. Still needs a real field test
  (and ideally an APK-confirmed value for Prime specifically) to know whether it changes anything.

## [0.1.11a21] - 2026-07-24

### Fixed

- **Real race condition found and fixed: region-command tests used to SEND the command before
  subscribing to watch anything.** `_watch_topic()` (the shared mechanism behind
  `watch_mission_timeline()`/`watch_rejected_commands()`) subscribes fresh on every call, not from
  a persistent subscription held since `connect()` — a response arriving faster than the time it
  takes to start watching afterward would have been silently missed entirely. Plausible for a
  REJECTION specifically (a schema/validation check could return in milliseconds, far faster than
  a physical robot could ever react) — every prior region-command test subscribed only AFTER
  already sending. `_confirm_show_send_watch()` now subscribes first (as background tasks), waits
  a short settle period for the subscriptions to actually reach the broker, THEN sends, THEN lets
  the same tasks keep running for the full watch window.

- **`send_stage_one()`/`send_stage_one_with_initiator()`/`send_stage_two()` (region-commands)
  never added `favorite_id` to the outgoing command, in any version, on any stage.** Found while
  re-analyzing this project's own prior research: `send_routine_command_via_cmd_topic()`'s own
  docstring already confirmed, via the real app's own `RoutineCommandBuilder`, that
  `setFromFavorite()` always sends `favorite_id` together with a favorite's resolved
  `command_defs` — and `RoutineCommand.to_json()` has supported emitting it since it was written
  — but nothing in this script ever actually set it on the command being sent, despite fetching
  the favorite (and therefore knowing its real `favorite_id`) in every stage. Every real payload
  shown by any field tester so far (chairstacker, jayjay13011) was missing this field entirely.
  All three stages, plus `verify-region-commands-session`'s own inline copies of the same logic,
  now add it via a new `_add_favorite_id_if_missing()` helper, mirroring
  `_add_initiator_if_missing()`'s own "only fill in if missing" contract. Stage 3
  (`--send-region`, deliberately no favorite at all) is unaffected — it has no `favorite_id` to
  add by design.

### Added

- **Region-command tests now also watch `watch_rejected_commands()` (`rejected/report`),
  concurrently with `mission/timeline/report`.** Never done before in this script, despite the
  method existing and already being proven functional elsewhere (`verify_mission_timeline.py`'s
  own combined watch). Every prior region-command "nothing happened" result only ever checked the
  mission-timeline channel — a silent server-side rejection and the robot simply ignoring an
  accepted command would have looked identical. `_confirm_show_send_watch()` now returns
  `(timeline_events, rejected_events)` instead of a single list; a failure watching the
  (exploratory, unconfirmed) rejected channel is caught and logged without affecting the
  already-working timeline watch running alongside it.

## [0.1.11a20] - 2026-07-24

### Added

- **New `roombapy-prime-verify-region-commands-session`**, a low-friction session runner for
  region-commands stages 1 / 1b / 2 — one login, one favorite lookup, a "continue to next stage?"
  prompt between each instead of retyping the full command and credentials every time. Every
  sending stage still shows the exact payload and requires its own explicit y/N confirmation —
  this only removes retyping friction between stages, not the human-in-the-loop safety gate
  itself. `--favorite-id` is now optional too: since the script fetches every favorite anyway, a
  separate `--list-favorites` run first (just to copy an id) was redundant — omit it and the
  script lists STAGE-1-ELIGIBLE command_defs inline and lets you pick one by number instead. Also
  adds `_summarize_events()` to `verify_region_commands.py` (used by both the new session runner
  and available to the existing standalone stage functions): pulls the specific fields that
  matter for judging whether region-targeting worked (echoed region/zone id, area, initiator) out
  of the raw `mission/timeline/report` events, instead of leaving a human to parse event reprs by
  eye.

### Fixed

- **`send_stage_two()`/`send_stage_three()` (region-commands) never added `initiator` at all**,
  found via a real field test (jayjay13011) that showed all three stages' actual payloads side by
  side. Only stage 1b ever added it — stage 2/3 always sent the same "no initiator" shape as
  stage 1, meaning a negative result at either never actually tested the initiator+command
  hypothesis stage 1b was specifically built to test. Both now compose with
  `_add_initiator_if_missing()`, same as stage 1b.

## [0.1.11a19] - 2026-07-24

### Added

- **New `roombapy-prime-verify-settings-write`**, a staged test package for `set_setting()` writes
  against five previously-unverified-effect settings (`child_lock`, `eco_charge`, `sched_hold`,
  `no_auto_passes`, `vac_high`). `--list-settings` (read-only) shows current values plus a
  cross-check between `rw-settings.schedHold` and the classic/unnamed shadow's own, separately-
  updated `schedHold` field. `--toggle KEY` flips one setting and reads back whether the write
  stuck — explicitly does not (and cannot) confirm the robot's actual physical behavior changed.

- **`get_state()`'s classic/unnamed shadow now has a typed model** (`ClassicShadowState`,
  `CapabilityFlags`, `DigiCap`) instead of returning an untyped `ShadowResponse`. Confirmed via
  a real live capture (chairstacker) that first surfaced this shadow's content at all.
  `CapabilityFlags` (36 fields, from the shadow's `cap` object) is the only per-device capability
  data found anywhere in this project so far — previously, no Prime-side capability gating
  existed at all. Also confirmed: this shadow's own `schedHold` is a **separate** value from
  `rw-settings.sched_hold`, updated independently — worth resolving which one the schedule
  executor actually reads before building anything that assumes they're the same value.

- **`roombapy-prime-verify-mission-timeline` gained `--watch-rrtp-candidate`**, a new, purely
  passive option subscribing to one specific candidate topic for live position/pose data
  (`.../mission/rrtp/report/update`), found via native decompilation of
  `createRobotPositionTopic()` (same report/request pair structure as
  `mission/timeline/report`/`.../request`). Deliberately kept as a diagnostic-script-only
  candidate (not a new `PrimeRobot` method) until live-confirmed — the exact template string
  itself is BSS-initialized, not found as a literal, so this remains a well-reasoned candidate,
  not a confirmed topic. Safe to combine with `--watch-wildcard`: a normal topic, not the
  reserved `$aws/` namespace.

### Fixed

- **`roombapy-prime-verify-region-commands`' stage 1b (`--send-with-initiator`) now adds
  `initiator="rmtApp"` instead of `"localApp"`.** The old default was borrowed from
  `send_simple_command()`'s own default, itself Classic's literal observed value for a
  local-MQTT connection — never independently confirmed on a real Prime device. A real
  capture (chairstacker) shows Prime's own `rw-software.lastCommand.initiator` as `"rmtApp"`
  for an app-triggered command — the first actual evidence of what this field looks like on
  Prime specifically.

- **`roombapy-prime-verify-map-edit` now sources room data exclusively from the downloaded map
  bundle, never from `get_active_map_versions()`.** A full APK decompilation (prompted by a
  cross-check with the `ha_roomba_plus` side) confirmed the app itself never reads room names
  from that endpoint at any level of richness: `fetchActiveVersions()`'s actual REST response
  class, `P2MapData`, declares no room-metadata field at all, and the further-reduced
  `P2MapIdentifier` the app builds from it (id + version only) obviously doesn't either. The
  script's own room-picking (`_pick_test_room()`/`_pick_test_room_with_category()`) now reads
  `RoomFeature.properties.name`/`.room_type` from the bundle instead — the source the app
  itself is bytecode-confirmed to actually display. The category picker additionally guards
  against `room_type`'s value space not matching the write-side `RoomCategory` enum (only the
  field *name* is confirmed on the read side, not its values) by skipping any room whose
  `room_type` doesn't parse as a known `RoomCategory`, rather than guessing.

### Docs

- **`rw-software`/`ro-configinfo`'s persistent fetch failures (even after the a14 reconnect fix)
  confirmed as NOT client-side.** `NamedThingShadowTopicFactory::getSupportedPaths()` was
  decompiled and found to do simple regex prefix-matching (`^ro-.*`/`^rw-.*`) only, with no
  per-shadow special handling anywhere — ruling out an app-side capability gate as the cause.
  See the new addendum in `docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md` for the full
  finding; the cause is now understood to be server-side (most plausibly timing/provisioning),
  not something further decompilation can resolve.

## [0.1.11a18] - 2026-07-23

### Fixed

- **`PrimeRobot.get_map_geojson_link()`'s own docstring was outdated**, still claiming the
  response shape/URL key name was unconfirmed — `rest_client.py`'s own docstring had already
  confirmed this (session 48, `P2MapURL$$serializer`'s own `<clinit>`: the key is `map_url`).
  Documentation-only fix, found while reviewing `verify-virtual-wall-write` for the same class
  of bug already found twice elsewhere this session — no such bug found there, but this stale
  claim was.

## [0.1.11a17] - 2026-07-23

### Added

- **`PrimeRobot.get_household_id()`** — convenience wrapper that finds the household_id
  containing this specific robot, without the caller needing to know
  `get_user_households()`'s own response shape (handled defensively: a single household
  dict per that method's own confirmed docstring, or a list, per `parse_user_households()`'s
  own type hint — these were never reconciled against a real multi-household account).
- **`build_room_name_map(map_versions, blid=None)`** (`models/robot_info.py`) — generic
  `{room_id: name}` lookup from a list of `P2MapVersion` objects, optionally filtered to one
  robot. Newer map versions win for a given room_id; unnamed rooms are skipped, not included
  as empty. Groundwork for calendar/schedule features that need to show a real room name
  rather than a bare region_id.

### Fixed

- **`verify-region-commands` stage 2 (`--send-modified`) crashed against EVERY real
  favorite** (jayjay, real device test): `TypeError: replace() should be called on
  dataclass instances`. Favorites are always constructed with their `command_defs[].params`
  kept as a plain dict, by design (`rest_client.py`'s own `_favorite_from_json()`) — never
  upgraded to a `CommandParams` instance. `_build_modified_command()` assumed the latter
  unconditionally. Now branches on the actual runtime type instead, and correctly preserves
  any other fields already present (e.g. a favorite's own `cleaning_profile`/"profile" — a
  confirmed, already-modeled field, not a new discovery) while only changing suction level.

### Added

- **`verify-region-commands` gained stage 1b (`--send-with-initiator`)**: a real stage-1 test
  (chairstacker) produced no observable effect, and the actual payload sent had no `"initiator"`
  field at all — the stored favorite's own `command_def` had `initiator=None`, which
  `RoutineCommand.to_json()` omits entirely. This matters because the original hypothesis behind
  this whole transport was that `"command"` AND `"initiator"` are shared keys with the
  confirmed-working simple-command payload — the real test accidentally exercised a version
  missing that second field. Stage 1b tests the natural next, still-minimal step: identical to
  stage 1, with only `initiator="localApp"` added if unset — purely additive, nothing overridden.

### Fixed

- **`verify-schedule-write` stages 1 and 2 CONFIRMED WORKING LIVE** (chairstacker): resending a
  household's schedules unchanged, and disabling one specific schedule, both genuinely took
  effect. Real-world note, not a bug: the real app's own Automations screen doesn't always
  refresh in real time after a write — `get_schedules()` itself reflects the change immediately
  regardless of what the app's own screen shows at that moment.
- **`verify-favorite-write` stages 1 and 2 CONFIRMED WORKING LIVE** (chairstacker) — the first
  live confirmation across any of this project's four new staged write-test scripts. Two real
  bugs found and fixed along the way: a hardcoded test-favorite name caused a genuine HTTP 409
  conflict on a second stage-3 run (now includes a timestamp, so every run is unique); and a
  confirmed caveat — a favorite created with empty `command_defs` is real and listable via
  `get_favorites()`, but was **not visible in the real app's own UI at all**. A new standalone
  `--delete FAVORITE_ID` command was added specifically for this — cleans up a favorite by ID
  directly, no app visibility required.

## [0.1.11a16] - 2026-07-22

### Changed

- **All credential prompts reworded from "Prime account" to "iRobot account"** — real field
  tester confusion: "Prime" is this project's own internal codename for the V4/Prime robot
  generation, not a term that appears anywhere in the real app or account itself (understandably
  got confused with "Amazon Prime"). No functional change, just clearer wording.
- **`--blid` now also falls back to a `ROOMBAPY_PRIME_BLID` environment variable**, across every
  script in this project — matching the existing `ROOMBAPY_PRIME_USERNAME`/
  `ROOMBAPY_PRIME_PASSWORD`/`ROOMBAPY_PRIME_COUNTRY` pattern. Requested by a field tester tired of
  retyping the same BLID for every run.

## [0.1.11a15] - 2026-07-22

### Fixed

- **Real UX bug found from a confused field tester's own report**: the four newest staged
  scripts (`verify-region-commands`, `verify-schedule-write`, `verify-favorite-write`,
  `verify-virtual-wall-write`) prompted for the Prime account email/password *before* checking
  whether a valid action or the required safety flag was even present — a bare or malformed
  invocation would ask for credentials first, then abort. This project's older scripts always
  validated first and only prompted for credentials once something was actually going to run.
  All four now follow that same order.

### Added

- **`SuctionLevel` enum**, from parallel native-analysis extraction: purely numeric
  (`INVALID`/`LOW`/`MEDIUM`/`HIGH`/`TURBO`) — no "Auto" value. Floor-type adaptation isn't a
  `suction_level` concept at all — it's the entirely separate `carpet_boost` bool instead, a
  real, sensor-driven, real-time "boost suction when carpet detected" feature (confirmed via
  iRobot's own public product documentation), not a three-way selector. `CarpetBoostSettings`
  (a real three-way `PERFORMANCE`/`ECO`/`AUTO` enum found alongside `SuctionLevel`) is confirmed
  **dead code** — a follow-up investigation found zero consumers anywhere for it, part of an
  older View/Fragment/XML UI generation superseded by Compose. Kept in the models module only as
  a documented dead end.
- **`set_setting()`'s own docstring clarified** with a concrete `carpetBoost` example and an
  explicit caution: the generic shadow-write mechanism it uses is confirmed to work at the
  transport level (the same one `trigger_echo_via_shadow()` already confirmed produces a real,
  accepted response) — but whether writing any individual key actually changes the robot's real
  behavior is a separate, per-key question, the same way writing `echo` was accepted but didn't
  trigger a chime.

### Fixed

- **Documented a real, structural parity gap**: the real app's own basic "Start" button
  explicitly fetches the account's active cleaning preferences and sends a full `CommandParams`
  built from them — `send_simple_command()`'s bare payload structurally cannot carry any of this
  (a fundamentally simpler wire shape than `RoutineCommand`, not a missing optional field). A
  mission started this way runs with whatever the robot's own fallback decides, not the
  account's actual saved preferences — a plausible, though not confirmed, explanation for a
  field report of a mission always running at unexpectedly high power with no adaptation.

### Fixed

- **Locate ("find my robot") CONFIRMED WORKING** (jayjay, real device test): `send_simple_command("find")`
  produces a genuine, audible chime with no robot movement — resolving this project's own
  multi-session locate-mechanism search. Two other mechanisms (a REST endpoint, a shadow write)
  were tried first and confirmed not working — this is the one that actually works. All
  documentation updated accordingly (`send_simple_command()`'s own docstring, README, API
  reference).

### Fixed

- **Real bug found and fixed**: `PolicyZoneFeature.from_json()` always assumed `Polygon`
  geometry — would have silently mis-parsed any real virtual-wall feature (confirmed
  `LineString` geometry) as if it were a list-of-rings Polygon. Now dispatches on the GeoJSON
  object's own `"type"` key.

### Added

- **Complete, confirmed categorization rule for virtual walls/keep-out/no-mop zones** (parallel
  native-analysis track, `P2MapBundleContentHolderPersistentMapKt`'s own real categorization
  code): there is no separate `"VirtualWall"` type string — a virtual wall is a
  `"KeepOutZone"`-typed feature whose geometry is a `LineString` instead of a `Polygon`.
  `policy_zone_to_virtual_wall()`/`policy_zones_to_virtual_walls()` implement this rule,
  converting raw `policyZones.geojson` features into `VirtualWallV1` subtypes ready to resend.
  Also answers `CommandPolygon.poly`'s previously-unconfirmed coordinate system: confirmed to
  pass through unchanged at every stage, so it's whatever this same geometry's own coordinate
  system already is.
- **`roombapy-prime-verify-virtual-wall-write`** — a new, staged diagnostic script for testing
  `SetVirtualWallsV1`. Stage 1 downloads the current map bundle, converts the real policy-zone
  list, and resends it completely unchanged. Two safety gates; `--list-walls` is pure
  reconnaissance.

### Added

- **`DockState` fully implemented — all 86 values**, from parallel native-analysis track
  extraction. Previously only discussed in prose, never a real enum. Directly confirms
  `DockStatus`'s own real captured values: `state=301` → `DOCK_READY`, `pw_state=601` →
  `PAD_WASH_OKAY`, `pd_state=701` → `PAD_DRY_OKAY` — what was previously only a suggestive
  numeric-band pattern is now a confirmed, named value. Two genuine duplicate values in the real
  enum itself (2 and 3, each shared by a `PAD_DRY_*`/`PAD_WASH_*` pair) — not a transcription
  error, Python's own `IntEnum` aliasing applies.
- **`ResolvedMissionStatus` fully implemented — all 49 values (0-48)**, superseding the earlier
  partial version (only ~12 named before).

### Changed

- **`get_time_estimates()`'s body shape partially clarified**: the real call site is
  `fetchTimeEstimatesWithAreasForAsset(assetId, mapId, commandDefRegions: ArrayList<String>,
  screen)` — `commandDefRegions` is a list of region-ID strings (not full objects), `screen` is
  analytics-only. Exact wire-level JSON keys remain unconfirmed (native from here).

### Added

- **`verify_map_edit.py` gained a `--test-category` mode** — changes an existing room's category
  (not name) and reverts it back, using the same `SetRoomMetadataV1` command already
  live-confirmed for renaming (its other field, not a new command type). `RoomMetadataEntry`
  gained a `category` field (the read-side counterpart of `SetRoomMetadataV1`'s own write-side
  `room_metadata.type`) — previously missing entirely, needed to capture a room's original
  category before changing it. The deprecated `SetRoomTypeV1`/`RenameRoomV1` command pair
  remains deliberately untested — the current app doesn't use either anymore,
  `SetRoomMetadataV1` replaces both (see that class's own docstring).

### Added

- **`roombapy-prime-verify-favorite-write`** — a new, staged diagnostic script for testing
  favorite writes (`create_favorite()`/`update_favorite()`/`delete_favorite()`), never tested
  live before. Stage 1 resends an existing favorite's own data unchanged (`get_favorites()`
  already returns fully-typed objects, no new parsing needed); stage 2 changes only a
  favorite's `color` (purely cosmetic, cannot affect what it cleans); stage 3 tests
  `create_favorite()`/`delete_favorite()` together, self-cleaning (creates a minimal test
  favorite, confirms it, deletes it again — same "do it, confirm it, revert it" philosophy as
  map editing's own rename-then-revert test). Two layered safety gates; `--list-favorites` is
  pure reconnaissance. Not yet live-tested.

### Added

- **`roombapy-prime-verify-schedule-write`** — a new, staged diagnostic script for testing
  schedule writes (`create_schedules()`/`update_schedules()`), never tested live before. Unlike
  region commands, a bad schedule write has a delayed effect (whenever the schedule next fires)
  rather than an immediate one — the staged approach reflects that: stage 1 resends an existing
  household's own schedules completely unchanged; stage 2 (the only modification implemented)
  disables one specific schedule, chosen because it can only prevent future unexpected activity,
  never cause it. Two layered safety gates (an explicit flag plus an interactive confirmation
  showing the exact payload before sending); `--list-schedules` is pure reconnaissance and sends
  nothing. Not yet live-tested — a reasoned, safety-checked hypothesis.
- **`HouseholdSchedule.from_json()`/`ScheduleOptions.from_json()`** — previously missing
  entirely (only `to_json()` existed), added specifically to support the new script's "resend
  unchanged" stage 1. `commands`/`end_commands` round-trip as raw dicts rather than parsed
  `RoutineCommand` objects (no `RoutineCommand.from_json()` exists in this library yet) —
  `to_json()`'s own handling of these two fields was also made tolerant of raw dicts to match,
  the same escape hatch `RoutineCommand.to_json()` itself already uses for its own
  params/regions/id_multipolys fields. A real round-trip test (not just a syntax check) confirms
  a realistic payload survives from_json() -> to_json() byte-for-byte.
- **`verify_mission_commands.py` gained a `"find"` test option**, run independently of the
  existing start/stop/pause/resume/dock sequence (find doesn't need an active mission). This is
  the third, still-untested locate candidate — the previous two (a REST endpoint, a shadow
  write) were both tried live and confirmed not working.

### Added

- **`roombapy-prime-verify-region-commands`** — a new, staged diagnostic script for testing
  region-aware mission commands (`send_routine_command_via_cmd_topic()`), the riskiest,
  least-confirmed write path this library has. Implements all four stages of a deliberately
  staged approach, each only worth attempting once the previous stage is confirmed working:
  stage 1 resends an existing favorite's own `command_def` completely unchanged; stage 2 changes
  one benign, reversible field (suction level) with `routine_modified` computed correctly; stage
  3 sends a genuinely from-scratch command for a real room/zone (no favorite at all); stage 4
  (highest risk) sends a hand-built ad-hoc/TID region, gated behind a third, dedicated safety
  flag and requiring the caller to supply a real, separately-verified `furniture_id` and polygon
  coordinates rather than auto-generating them. Every sending stage shares two layered safety
  gates (two explicit flags plus an interactive confirmation showing the exact payload before
  sending); `--list-favorites`/`--list-rooms` reconnaissance modes send nothing. Not yet
  live-tested at any stage — a reasoned, safety-checked hypothesis.

  **Self-caught bug, prompted by a direct correctness challenge**: stage 2's first version tried
  setting `routine_modified` directly on `RoutineCommand` via `dataclasses.replace()` — that
  field actually lives on `CommandParams`, and the original code would have raised `TypeError`
  the first time it actually ran. Confirmed directly against `dataclasses.fields()` on both
  classes before fixing, and a real executing test (not just a syntax check) added specifically
  to catch this class of error going forward.

### Changed

- **`ConnectionStatusShadow`/`SoftwareStatusShadow` fields properly typed**, from Ghidra
  decompilation of the app's own constructor signatures (not guessed): `connected`/
  `connected_v2`/`echo` are booleans; `deployment_id`/`software_version`/`last_sw_update` are
  strings; `deployment_state` is a small int enum (5 values, meaning not yet confirmed).
  `imu_recal`/`submodule_sw_version` confirmed genuinely absent from the app's own code
  entirely — kept as `Any`, since there's no source at all suggesting a more specific type.

### Added

- **`watch_named_shadows_updates()`** — watches `update/accepted` across all named shadows via a
  single-level (`+`) wildcard, confirmed safe and distinct from the multi-level (`#`) wildcard
  already removed elsewhere (`--watch-aws-tree`) after a real connection disruption. AWS's own
  MQTT design guidance recommends `+` wildcards for exactly this device-subscription use case,
  and a parallel native-analysis track found the real app uses this exact pattern. Built for
  read-only, report-only shadow content (like `ro-currentstate`'s battery/dock/bin fields) that
  `update/delta` structurally can never deliver — delta only reflects desired-vs-reported
  differences, and purely-reported fields never have a `desired` counterpart. Not yet
  live-tested — a reasoned, safety-checked hypothesis.

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
