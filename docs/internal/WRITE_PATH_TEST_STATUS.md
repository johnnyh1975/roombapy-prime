# roombapy-prime — Write-path test status

> **As of v0.3.2** (4 September 2026). Consolidated from every field
> session so far, so that nothing gets asked twice or quietly forgotten.
>
> That is this file's whole purpose, and it failed at it once: the header
> said `v0.1.11a29` for roughly twenty releases while section 6 below
> still called virtual wall writes broken — a problem solved before
> 0.2.0b1. @utkjmitch found the contradiction *while planning a field
> test against it* and asked instead of running it. A stale status
> document does not merely mislead; it spends other people's hardware
> time.
>
> **Rule for this file: a section that changes status gets rewritten in
> place, not appended to.** Section 6 is the example of what not to do —
> it carried a 🚫 heading and a ✅ resolution forty lines apart.
>
> Written in English to match the project's convention; the original was
> German.

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

## 6. `verify-virtual-wall-write` — ✅ writes work, one gap remains

| Step | Status | By |
|---|---|---|
| 0a — `--list-maps` | ✅ | DaRealGuGu |
| 0b — `--list-walls` | ✅ | DaRealGuGu |
| 1 — `--update-unchanged` | ✅ | chairstacker, a31, 30 July 2026 |
| 2 — **write a CHANGED list** (`--drop-one-wall`) | 🟡 **tool built, never run** | — |
| 2b — **move a zone** (`--move-one-wall`) | 🟡 **tool built, never run** | — |

**Reads produced the first real zone data this project has seen**:
`1 = KeepOutZone`, `6 = NoMopZone`, confirmed against hardware rather
than decompilation alone.

**The HTTP 500 is solved.** The `virwall` array starts with a COUNT of
the walls; the payload was failing at element zero. Confirmed on the
hardest available case — four zones of two different types
(3x KeepOutZone + 1x NoMopZone) in one command, response
`{"status": "success"}` with a new `p2mapv_id` issued. The write /
re-read / write round trip that separates "accepted" from "stored" is
included (#28, closed 30 July).

**Why it took so long, because the shape of the mistake recurs.** Three
testers between them ruled out list length, zone type mixing, map count,
account, map version, our own filtering and all three `response_type`
variants — none of which mattered. The 500-not-400 response was noted
repeatedly and read as a wrong TYPE inside a wall; it was an extra
ELEMENT before them.

**What is still open, and it is the dangerous half.** Every confirmed
write **resent the existing zones unchanged**. `set_virtual_wall` replaces
the whole shared list, so a partial list silently deletes everything
omitted — a hazard guarded by a test in this library, not by the server.

**Stage 2 now has a tool** (`--drop-one-wall`), and it answers the
question by REMOVING rather than adding. Stage 2 was deferred for months
on the grounds that it "would need a real, user-supplied polygon geometry
to add" — true of adding, and not true of removing. A removal is the
current list minus one entry with every surviving coordinate preserved
byte-for-byte, so the unconfirmed CommandPolygon coordinate system is
never touched.

The tool captures the original list before sending, verifies the removal
by re-reading rather than trusting the response, restores
unconditionally, and prints the restore payload BEFORE the change so a
tester is never left with a map it broke and cannot undo. A map with
fewer than two walls is refused: dropping the only entry sends an EMPTY
list, which is a different question and would be indistinguishable in the
result.

**Stage 2b (`--move-one-wall`) answers the coordinate question itself,
and it is the one to run first.** `policy_zone_to_virtual_wall()` passes
geometry through UNCHANGED — confirmed by native analysis, no
transformation anywhere from the bundle read to the wire. So the numbers
in a command ARE the numbers in `policyZones.geojson`, and adding a
constant to them moves the zone by that amount in whatever unit that file
uses. The unit never has to be known in advance.

And that is how it gets answered: move a zone by a known delta, look at
the app, and the observed distance against the delta gives the scale.
"Metres or millimetres" has been open on the edit path since it was first
modelled, and this measures it rather than arguing it.

The delta defaults to **a quarter of the zone's own longest side** —
visible in the app and unable to leave the floor plan whichever unit it
turns out to be. A fixed number would be either invisible or off the map
depending on the answer to the question being asked.

The run pauses after the move and waits for the tester to look, because
that is the only moment the moved zone exists: the restore puts it back,
and no re-read can say what it looked like.

Adding a NEW object is stage 3 and still deferred. Note what 2b changes
about it: once the frame is confirmed by measurement, stage 3 stops
needing a guess — a new zone can be placed relative to an existing one,
or duplicated from it with an offset.

---

## What is actually still open — v0.3.2

Six items. Everything else on the write path is either confirmed live or
answered as a dead end, and the dead ends are listed below them so nobody
re-opens one.

| | Risk | Why it has not been done |
|---|---|---|
| **A virtual wall write carrying a CHANGED list** | **high** | Tool built (`--drop-one-wall`): drops one entry, verifies, restores. Needs a tester with **at least two zones** on a map they could rebuild. Nobody has run it. |
| **Stage 4 — ad-hoc / TID zone** | **high** | The only region-command stage never run. It needs self-derived geometry rather than anything the robot supplies, which is what makes it risky. |
| **`ecoCharge`, `noAutoPasses`, `vacHigh` — real effect** | low | All three write and read back cleanly. None has an easily observable effect, so "does it do anything" needs a deliberate before/after measurement, not a check run. |
| **The discriminator inside `edit_cmd`** | low | The envelope shape and 8 of 9 commands' fields are confirmed. `SetRoomMetadata` and `VirtualWall` use custom serializers whose internals are not. |
| **Uploading a p2map back to the robot**, and the services write path | medium | Marked at the call sites too. Never attempted. |
| **Multi-robot household and teaming** beyond settings scoping | low | No tester with the setup has run it. |

### Answered — do not re-open these

Each of these has cost someone a field session or nearly did.

- **Virtual wall HTTP 500** — solved before 0.2.0b1. The `virwall` array
  starts with a COUNT. See section 6.
- **`schedHold`** — writes are accepted, read back changed, and the robot
  ignores them. Not a bug to chase: the key appears **once** in iRobot's
  own Prime app, unannotated, with no consumer across 3801 classes.
  Plumbing inherited from Classic. Prime pauses schedules through
  `enabled` per schedule.
- **`clean_all` / `select_all`** — there is no payload shape to find.
  @Echovictor37 sent it twice on hardware, with `regions` omitted and
  with an empty list: PUBACK, no effect either time. `CommandDTO` has
  thirteen fields and `select_all` is not among them; iRobot's own client
  strips the key before sending. A whole-house clean is
  `send_simple_command("start")`. The field stays in the model documented
  as inert rather than quietly dropped, so nobody spends another hardware
  session on it.
- **Region commands, stages 1 to 3** — solved. `initiator` is mandatory
  and the wire keys are `start` and `region_id`. Confirmed end to end on
  a Combo 105 (@Echovictor37, sku Y311240): the robot cleaned **only**
  the targeted room, with `operating_mode` correctly selecting
  vacuum-only versus vacuum-and-mop, both visually verified.
- **`settings_roundtrip`** — the wall was a second connection. Two
  connections to one robot displace each other; Home Assistant was still
  running during the failing run. Eleven days hung on that.
- **A map version is not required** for a region command. The robot
  re-versions its map every few seconds while cleaning.

### The recurring shape of this project's wrong turns

Worth keeping at the end of this file, because three of the entries above
are instances of it: **"not found where I looked" is not "does not
exist", and "listed as open" is not "still open".** `libapp.so` was twice
recorded as unreadable and yields every settings enum to blutter. The
value sets were declared unsourced after they were missing from
`product_profile.json`; they were in `dock_controls.json`. And this file
itself told a tester to go test something that had worked for a month.
