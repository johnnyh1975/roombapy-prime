# What app 3.0.0 told us

`com.irobot.home.prime` **3.0.0** (build 3000008) is a Flutter rewrite. The nine native C++
libraries of 2.2.4 are gone, and with them every finding that rested on BSS constants, Djinni
bridges or the `ProtocolAdapter` hierarchy.

**The protocol survived the rewrite intact.** What changed is that the data layer now ships as
plain Kotlin serialisers and locale JSON instead of compiled constants — so questions this project
closed as "not decidable" in 2.2.4 are answerable by reading.

## Why this document exists

Every correction below was **invisible from the outside**. Nothing threw an error, no request
failed, no tester could have reported it: a field that reads `None` looks exactly like a robot that
does not report it.

That is the case for reading a vendor's own model even when everything appears to work.

## What was compared

| Source | Size | Used for |
|---|---|---|
| `$$serializer` `<clinit>` blocks | 223 classes | wire keys, authoritative |
| Model dump with declared types | 105 KB | types the key names cannot express |
| Enum extraction | 87 enums, 530 values | value lists we had assembled from observation |
| Request classes | 71 | endpoints, methods, bodies |
| Locale files | 25 languages | error text, part names, units |
| Generated 2.2.4 → 3.0.0 diff | 5 changed, 126 removed, 151 added | what moved between versions |
| Real diagnostics from testers | 621 distinct keys | what the robot sends, as opposed to what the app reads |

The last row matters more than its position suggests. **The app model is authoritative for what
iRobot uses, not for what the robot sends** — and that difference produced the single most useful
find in the set.

## Corrections

### Keys that were plausible guesses

```
read here              robot sends
numberOfDirtDetects -> dirt
staticMapId         -> map_id
coverageStrategy    -> covStrat
```

None of these names has ever appeared in a payload. All three read `None` on every mission this
project has ever recorded, so the dirt-detection counter looked like a feature Prime robots lack.

The same shape, in the timeline: `MissionEventDto` declares `cmd`, `disc`, `poly` and
`tentativeLoc`; this library read the long forms and dropped four event types from every real
timeline. `reloc` was already the exception, which is how a partial fix survives — somebody noticed
one short name and did not generalise.

### Fields declared and never read

- **`coverage`** on rooms and zones — per-room mission progress, sitting beside `area`, `passArea`
  and `totalArea`, all of which were read. `RoomEvent`'s docstring spends fourteen lines reasoning
  about what `area` and `total_area` mean; the field that answers it was in the same object.
- **`futureEvents`** — finished events say where the robot has been, these say where it is going.
- **`frState`** — the dock's fluid-refill state, counterpart to `pwState` and `pdState` which were
  already read. A dock could report that it was refilling and nothing here could say so.
- **`expireTm` / `rechrgTm`** — when a pause lapses and when the robot means to resume.
- **Twelve of thirteen `digiCap` flags**, including `cwia` (whether iRobot's own presence cleaning
  exists on this model) and `ddAutomation` (their demand cleaning).
- **`smart_clean_prefs`** — typed `RegionParamsDTO`, so it carries the same eight keys a region
  command does. The server's record of "always mop the kitchen", and the only explanation for a room
  that cleans differently from the robot's global settings. Empty on every capture so far.
- **`pose`**, which separates a robot reporting its position from one that does not — the same
  distinction this project derives from SKU prefixes.

### Types the key names could not express

`targetSoftwareVer` is `List<String>`: one firmware release can target several installed versions.
Modelled here as a string, from the key list alone.

Checking 67 container fields found only that one, and 27 scalar-versus-container comparisons found
one more (`mission_last_cleaned` is a mission-info object, not a number). **Everywhere else the
types hold** — the models came through the vendor's rewrite intact.

### What the robot sends and the app does not declare

`oModeStats` — `{"vac": {"nMin": 10, "sqft": 90}}` — appears in real mission entries and in
**neither** iRobot's 33-key response model nor this reader.

It answers the one question a Combo mission raises that a single duration cannot: how much of it
was vacuuming and how much mopping.

Found only because the third source was checked. Neither of the first two knew about it.

## Things confirmed rather than changed

- **One key, one value** is the vendor's write shape (`updateSetting(keyPath, value)`), and dotted
  keys such as `padWetness.padPlate` are addressed directly. That retires this project's
  read-modify-write recommendation, which described the older app.
- **`schedHold` has no consumer** across 3801 classes — it is plumbing inherited from Classic, not
  a control. Prime pauses schedules through `enabled` per schedule.
- **The topic split** — shadows on the AWS namespace, everything else on the vendor prefix — matches
  what this library already does.
- **`mask = 1 << bit`** survives as `IrobotOperatingModeCodec`, though the named bitmask class is
  gone. "The vendor removed it" here means the opposite of "stop using it".
- **`notReady` is a scalar beside a `condNotReady` list.** This model already had it right, which is
  worth recording: the comparison was not one-directional.

## Things deliberately not changed

Each of these is a place where the app disagrees with something a real robot demonstrated. **A
confirmed shape outranks a plausible one**, and this rule was applied four times:

| Kept | Why |
|---|---|
| `robot_id`, `select_all` in commands | iRobot's own code strips both before sending; @Echovictor37's confirmed region clean carried them |
| `washpad`, `drypad`, `stopevac`, `stoppaddry` lowercase | the app is camelCase; a real robot recorded the lowercase forms in its own shadow, pad-wash counter at 90 |
| `point_clean` | the app spells it `pointClean`; the server returned `point_clean` verbatim in a favourite it stores |
| `SetFloorTypes`, `SetThresholds` | dropped from the app in 3.0.0, never sent from here — removing them would trade an untested path for an untested absence |

`select_all` did get one guard: it no longer travels with a region list. Nothing sets it, but if any
firmware reads the key, a `True` beside regions is the one combination that could reproduce
@Echovictor37's whole-house clean.

## Things the APK cannot answer

- **Map edit V3 payloads.** `P2MapV3Editor` publishes `{"method": "service.mapedit", "msgId": ...,
  "params": {"map_id": ..., "data": {"value": <any JSON>}}}` over MQTT. `method` is a constant; the
  operation lives in an uninterpreted `JsonElement`. The channel is discoverable and its contents
  are not. **Scope is smaller than it looks**: of 34 map service methods, exactly two mention V3.
- **Whether the server still sends fields the app dropped.** `after`, `until`, `append`, `exclude`
  and `reminder` existed in 2.2.4 and are gone from 3.0.0's schedule options. The app not writing
  them does not mean the server stops returning them.
- **Whether a robot accepts more than its app sends.** Which is why the four "deliberately not
  changed" rows are decisions rather than oversights.

## Method notes

- **Wire keys only from `$$serializer` `<clinit>` blocks.** An audit found 21 wrong keys taken from
  DEX field lists — Kotlin property names instead of `@SerialName` values.
- **Kotlin property names are not wire keys**, and comparing them against snake_case readers
  produces a stream of false positives. Three separate checks in this session had to be discarded
  for that reason.
- **Check all three sources.** App model, declared types, and real captures each found something the
  other two missed.
