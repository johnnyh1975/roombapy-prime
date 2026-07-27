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

- **Virtual wall writes** either fixed or documented as known-broken.
  Documented is acceptable for a beta; silent is not.
- **One quiet alpha round with no public-signature changes.** a27 changed
  `Region.to_json()` (`id` → `region_id`) and a28/a29 changed
  `edit_map()`. Both correct, both breaking. A `b1` released immediately
  after those would claim a stability that is two versions old.

Everything else on this page is either done or explicitly optional.
