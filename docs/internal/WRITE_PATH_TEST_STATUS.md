# roombapy-prime — Write-path test status

> As of v0.1.11a29. Consolidated from every field session so far, so that
> nothing gets asked twice or quietly forgotten.
>
> Rewritten in English (this revision) to match the project's own
> convention; the previous version was German.

## Legend

✅ confirmed live · ⚠️ partial or with caveats · ❌ never tested · 🚫 broken

---

## 1. `verify-schedule-write` — `update_schedules()`

| Step | Status | By |
|---|---|---|
| Unchanged resend | ✅ | chairstacker |
| Disable a schedule | ✅ | chairstacker |

**Done.** No further testing needed.

---

## 2. `verify-map-edit` — `edit_map()`, room rename

| Step | Status | By |
|---|---|---|
| Rename a room | ✅ | chairstacker (twice) |

**Done.**

---

## 3. `verify-favorite-write`

| Step | Status | By |
|---|---|---|
| List | ✅ | chairstacker |
| Unchanged resend | ✅ | chairstacker |
| Change colour | ✅ | chairstacker |
| Create + delete | ✅ | chairstacker, arielgr |
| Standalone `--delete` | ✅ | chairstacker |

**Done.** arielgr's full validation run created and deleted a test
favorite cleanly, which closed the last open question here.

---

## 4. `verify-region-commands` — **SOLVED**

| Step | Status | By |
|---|---|---|
| List favorites | ✅ | chairstacker, jayjay13011, DaRealGuGu |
| 1 — unchanged resend | ✅ negative | DaRealGuGu |
| 1b — with `initiator` | ✅ **mission started** | DaRealGuGu |
| 2 — change suction level | ✅ **mission started** | DaRealGuGu |
| 3 — from scratch, no favorite | ✅ **robot cleaned room 12** | DaRealGuGu |
| 4 — ad-hoc / TID zone | ❌ | — |

**What it took.** Two requirements, neither obvious:

- **`initiator` is mandatory.** A stored favorite carries none; the app
  adds it at send time. Without it the command is delivered,
  acknowledged and silently ignored.
- **The wire keys are `start` and `region_id`**, not `clean` and `id`.
  The latter pair was an assumption recorded in this project's own code
  and never verified. Stage 3 had shipped with it since it was written,
  back when nothing worked at all.

**Why it took three rounds.** Two earlier sessions appeared to *disprove*
the `initiator` requirement. Both were confounded by sends that never
reached the broker — the connection work had to land before the question
could even be asked. Nothing about the payload analysis was wrong; the
experiment was.

**A map version is not required.** The robot re-versions its map every
few seconds while cleaning (five values inside 37 seconds in one
capture), and confirmed-working commands carried versions hours out of
date. The pre-flight check that warned about this was downgraded from a
failure to a note.

Only stage 4 remains, and it carries the highest risk — it needs
self-derived geometry rather than anything the robot supplies.

---

## 5. `verify-settings-write` — `set_setting()`

| Setting | Write | Read-back | Real effect |
|---|---|---|---|
| `childLock` | ✅ | ✅ | ✅ **app showed it, robot announced it audibly** |
| `ecoCharge` | ✅ | ✅ | ❌ no observable effect to check |
| `noAutoPasses` | ✅ | ✅ | ❌ untested |
| `vacHigh` | ✅ | ✅ | ❌ untested |
| `schedHold` | ✅ | ✅ | 🚫 **accepted but ineffective** |

All by DaRealGuGu.

**`childLock` is the first setting whose physical effect is confirmed**,
not merely its acceptance.

**`schedHold` does nothing, and the reason is now known.** It appears
once in iRobot's own Prime app, unannotated and with no consumer across
3801 classes -- plumbing inherited from Classic. Prime pauses schedules
through `enabled` per schedule.

**And the settings_roundtrip failures were ours, all four of them.**
A wrong attribute that blamed the tester's hardware, a guessed wire key
for the volume, a discarded publish result, and two candidate keys the
app does not write: `audio` should be `audio.volume`, and `evacAllowed`
is readable but not among the 24 keys `settingFromKey` writes. Asking
for a key the vendor never writes, and calling the silence a bug, is how
three testers spent a week on this check.

## RESOLVED: the wall was a second connection, and it was Home Assistant

**11 August 2026.** @DaRealGuGu's `settings_roundtrip` completed on his N185240 — six settings
written and read back, every one `ok`. The difference from the failing run twenty minutes earlier
was that Home Assistant's integration was still running the first time.

So the eviction theory holds and the culprit was external after all. **The client-id rotation added
in b2 was not what fixed this** — his successful run used the same id as always. That change is
worth keeping as a defence, but it should not be credited with the result.

Six controls become buildable: `padDryDur`, `pwAreaInterval`, `autoevacFreq`, `padWashAllowed`,
`pwReturn`, `pwTimeInterval`. Issue #46 is unblocked.

### And the caveat this check exists for did its job

`pwAreaInterval` wrote as `8`, read back as `8` — and the iRobot app shows the setting as **"not
set"**, which it also did before the run. So the value is genuinely on the robot and the app cannot
render it.

`8` is not in the `[5, 10, 15]` set iRobot publishes for the 405 series. It **is** in the
`[6, 8, 10]` set they publish for the 410 and 510. His `padDryDur: 3` is likewise outside the 405's
`[4, 6, 9, 12]` and inside the 505's `[3, 4, 5]`.

**Two things follow, and they matter for any control built on these:**

1. A robot can hold a value its own app cannot display. "Not set" in the app is not proof that a
   write failed — it may mean the app has no label for what is there.
2. Any control must read its valid values from the robot's own model rather than a table. A picker
   offering `[4, 6, 9, 12]` to a robot whose set is `[3, 4, 5]` would look like our bug when the
   robot rejected or ignored it.

That second point was @DaRealGuGu's own argument on issue #46 a week before this run produced the
evidence for it.

## The wall has three faces and one likely cause

```
@DaRealGuGu   PUBLISH queued but never sent
@jouwdan      no SUBACK, then no response
@utkjmitch    PUBLISH refused, paho rc=4
```

**@utkjmitch's session was half alive**: shadow subscribes dead, cmd-topic publishes working, the
robot physically obeying commands — one connection, one moment. Whatever kills it does so between
CONNACK and the first SUBACK, and paho notices only at the next publish. A subscribe always loses
that race; a bare publish fired quickly enough wins it.

That fits **one connection evicting another** better than anything else: AWS IoT drops the older
client when a second arrives with the same client_id. It also explains why a *first* read sometimes
succeeds and every later one fails, which a policy denial would not — a policy denies every time.

The broker's own disconnect reason is now carried into both the SUBACK warning and the publish
error; this library recorded it and never showed it. And the tool says so **before** the run: close
the iRobot phone app, and stop any Home Assistant integration pointing at the same robot.

**Ruling this out costs nothing and has never been done.** Two of three testers reasonably concluded
their hardware was at fault.

The original note follows.

**`schedHold` does nothing.** The write succeeds, the read-back confirms
it, and the schedule stays active in the app. Writing it to
`rw-settings` is evidently not the mechanism the app uses.

Worth recording how that surfaced: the cross-check against the classic
shadow flagged the divergence — `rw-settings` said True while classic
still said False — **before** the tester looked in the app. Two sources
disagreeing turned out to mean "the write did not take". Notably, only
*enabling* diverges; disabling moved both in step.

---

## 6. `verify-virtual-wall-write` — 🚫 **broken**

| Step | Status | By |
|---|---|---|
| 0a — `--list-maps` | ✅ | DaRealGuGu |
| 0b — `--list-walls` | ✅ | DaRealGuGu |
| 1 — `--update-unchanged` | 🚫 HTTP 500 | DaRealGuGu (three attempts) |

**Reads work and produced the first real zone data this project has
seen**: `1 = KeepOutZone`, `6 = NoMopZone`, confirmed against hardware
rather than decompilation alone.

**Writes fail with HTTP 500.** Two causes ruled out:

1. **A duplicated closing coordinate.** A GeoJSON ring repeats its first
   point as its last, so rectangles went out with five points where the
   format takes four. Real deviation, genuinely fixed — and demonstrably
   not the cause, since the corrected payload still returned 500.
2. **`response_type` in the request envelope** — *not actually tested
   yet.* The a28 attempt died with `TypeError` before any request left
   the machine, because the parameter was added to the REST client and
   not to the wrapper. a29 fixes that; the experiment is still pending.

That it returns **500 rather than 400** is itself a clue: the body parses
and then breaks something downstream.

Next suspect after `response_type`: the discriminator inside `edit_cmd`,
the other item this project's own docstring has flagged as unverified
since it was written.

---

## What a 0.2.0 beta still needs

- ~~**Virtual wall writes** either fixed or documented as known-broken.~~
  **DONE (a31, field-confirmed 30 July 2026, chairstacker).** The
  `virwall` array starts with a COUNT of the walls. Confirmed working on
  the hardest available case: four zones of two different types
  (3x KeepOutZone + 1x NoMopZone) in one command, response
  `{"status": "success"}` with a new `p2mapv_id` issued.

  Worth recording why it took so long: three testers between them ruled
  out list length, zone type mixing, map count, account, map version,
  our own filtering and all three `response_type` variants -- none of
  which mattered, because the payload failed at element zero. The
  500-not-400 response was noted repeatedly and read as a wrong TYPE
  inside a wall; it was an extra ELEMENT before the walls.
- **One quiet alpha round with no public-signature changes.** a27 changed
  `Region.to_json()` (`id` → `region_id`) and a28/a29 changed
  `edit_map()`. Both correct, both breaking. A `b1` released immediately
  after those would claim a stability that is two versions old.

Everything else on this page is either done or explicitly optional.
