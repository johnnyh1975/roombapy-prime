# What values a setting accepts

Six `rw-settings` keys are confirmed writable (@DaRealGuGu, 11 August: written and read
back, all six `ok`). Building controls for them needs one more thing: **what may a user
choose, and does choosing it write more than one field?**

Both are now answered from iRobot's own code.

## Where these values live

App 3.0.0 is Flutter, and the settings logic sits in the Dart AOT snapshot — which this
project twice recorded as unreadable. **It is readable**, with
[blutter](https://github.com/worawit/blutter) and a locally built Dart 3.9.2 VM. Every
enum below comes from the object pool with its index and its wire value.

That correction matters beyond this document: `libapp.so` was treated as a wall for
weeks, and it is not one.

## The enums

```
ReturnByMode        pwReturn
  evRoom          0    "Each Room"
  byTime          1    "Cleaning Duration"
  byArea          2    "Wash by Area"
  mission       100    "Standard"    before and after the routine
  refill        101    "Medium"      also on refills
  refillAndRoom 102    "High"        also between rooms

ReturnByArea        pwAreaInterval      6 · 8 · 10 · 15 · 20
ReturnByTime        pwTimeInterval     10 · 15 · 20 · 25
DryDurType          padDryDur           2 · 3 · 4 · 5 · 6   (hours, value = hours)
HeatType            pwHeat              0 · 1 · 2

ClearFreqType       autoevacFreq
  evClean       0    After Every Routine
  evClean2      1    After Every 2 Routines      ← writes 1, not 2
  evClean3      2    After Every 3 Routines
  evBackHome    4    Every time returning to the dock
  ev10 … ev50   10 · 15 · 25 · 30 · 50           area-based
```

## One field per user action

`wash_frequency_view_model` calls three setters, which looked like bundling. It is not:
`_updateWashFreqByType` is a **type switch** on the value's class id.

```
value is ReturnByArea    -> setPwAreaInterval()   pwAreaInterval
value is ReturnByTime    -> setPwTimeInterval()   pwTimeInterval
otherwise (ReturnByMode) -> setWashFreq()         pwReturn
```

| the user taps | field written | value |
|---|---|---|
| Each Room / Cleaning Duration / Wash by Area | `pwReturn` | 0 / 1 / 2 |
| Standard / Medium / High | `pwReturn` | 100 / 101 / 102 |
| an area value | `pwAreaInterval` | 6 / 8 / 10 / 15 / 20 |
| a time value | `pwTimeInterval` | 10 / 15 / 20 / 25 |

**Nothing is bundled.** Picking a level writes `pwReturn = 101` and nothing else -- the
101 *is* the complete statement, because mode and level share one field. `pwReturn = 2` is
the mode, `pwReturn = 101` is the level; they are alternatives rather than a pair.

So the thing @chairstacker warned about on issue #46 -- a control writing one field where
the robot expects two -- does not apply. It was the right question, and the answer is no.

The earlier reading here said the opposite, from counting the setter calls instead of
reading the control flow.

## Two earlier readings, both wrong

**`pwAreaInterval = 8` is a valid option**, not a stray value the app cannot render.
@DaRealGuGu holds it, his app shows "not set", and `ReturnByArea` contains 8. The earlier
conclusion — "the list is a display constraint" — was built on that misreading.

**The 2.2.4 per-series table does not describe 3.0.** `dock_controls.json` gives the 405
series `[4, 6, 9, 12]` hours; @chairstacker's 405 offers **3, 4, 6**, and `DryDurType` is
`[2, 3, 4, 5, 6]` with no series split at all. The screenshots were right and the table
was stale.

For the record, this document has now said four different things about these values:
unsourced, then sourced from `dock_controls.json`, then contradicted by a screenshot, now
read from the app's own enums. **The pattern each time was concluding "not found where I
looked" meant "does not exist".**

## Capability gating

`CapAutoEvac` decides which auto-empty options a robot offers:

```
taskEndOnly           mission end only
freqModes             routine counter
freqWithArea          plus area-based
taskEndOrDockReturn   mission end or dock return
```

So the option list is per robot, but the gate is a capability the robot reports — not a
table we have to carry.

## What this means for the controls

| Setting | Control |
|---|---|
| `padDryDur` | picker, `[2, 3, 4, 5, 6]` — **narrowed on five SKUs** |
| `pwHeat` | picker, three levels, narrowed by `dock.cap.pw` (an inference, see below) |
| `autoevacFreq` | picker, options gated on `CapAutoEvac` |
| wash frequency | **mode first, then a value** — and the mode write is not optional |

### CORRECTED: the sets DO vary by series

An earlier version of this line said the sets "do not vary by series". They do.
`getListBySKU` narrows five product modes:

```
G2  pwAreaInterval [6, 8, 10]     padDryDur [2,3,4]
N2  pwAreaInterval [10, 15, 20]   padDryDur [2,3,4]
R2  pwAreaInterval [10, 15, 20]   padDryDur [2,3,4]
V1  pwTimeInterval [10, 15, 20]   padDryDur [4,5,6]
Z1  pwTimeInterval [10, 15, 20]   padDryDur [4,5,6]
```

The report itself contradicts this in two places: an earlier note says `DryDurType` is not
series-dependent and the 2.2.4 profiles no longer apply; a later one finds `getListBySKU`
and confirms the mapping *by* agreement with those same profiles. **The later reading
wins**, and its own caveat stands — the assembler does not separate the branches cleanly,
so narrowing is applied only where both sources agree.

**Why it matters despite no tester owning one:** `V1` and `Z1` were added to
`PRIME_SKU_PREFIXES` in the same session as these controls. A robot that only just became
recognisable would have been offered intervals its own app does not show.

### `autoevacFreq` is the exception, and a screenshot proves it

`getListBySKU` gives a standard `autoevacFreq` list of `[0, 10, 15, 25, 30]` — area values
only. @chairstacker's G1 takes that standard list, and his Auto-Empty Frequency screen
shows three options: every routine, every 2, every 3. That is `0/1/2`, and none of them is
in the SKU list. His `cap.autoevac = 1` (`freqModes`) predicts exactly those three.

So the capability decides this control and the SKU list does not.

### `pwHeat`'s gate is an inference, not a reading

`DockPadWashingType` names `dock.cap.pw` as notSupported / supported / heatedSupported /
highHeatSupported, and the heat levels are narrowed accordingly. **Nobody has read what
actually gates `pwHeat`** — and the research's own correction table records "gate über
`dock.cap.pw`" as *wrong* for the neighbouring wash-frequency screen, where
`ProductMode::getModeBySku()` decides.

Kept because the risk points the safe way: offering high heat to a level-2 dock is
accepted and silently not produced, while a wrong gate hides an option on a capable dock —
and someone reports that.

The wash frequency needs two controls rather than one, but neither is a compound write.
`pwReturn` holds either a mode or a level; the interval fields are only meaningful when
the matching mode is selected, which is a presentation question rather than a correctness
one.
