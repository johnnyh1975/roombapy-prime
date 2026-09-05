#!/usr/bin/env python3
"""Compare this library's enums against the vendor firmware schemas.

WHY ONLY ENUMS, AND NOT EVERY FIELD NAME. The obvious version of this
check -- take every wire key we read and flag the ones the schema does
not contain -- was tried on paper and discarded. The schemas come from
CLASSIC firmware (`cleantrack`, ruby-0.7.12/j9) while these models
describe the PRIME cloud API, so hundreds of our keys are legitimately
absent from it. A check whose output is mostly false positives gets
skimmed and then ignored, which is worse than no check.

Enums are the opposite case: narrow, high-signal, and already proven.
Reading the ruby `type` enum by hand is what resolved `wid`, whose wire
value had been explicitly recorded as unresolvable, and what surfaced
`tag` and the absence of `tid`. This script is that comparison, run
every time instead of once.

WHAT IT DOES NOT CLAIM. A schema proves what the parser ACCEPTS, and
`cleantrack` is built across product lines -- its command enum carries
Astro verbs this hardware has no use for. Agreement here is a signal
that two generations share a vocabulary. It is not proof that a Prime
value is right, and disagreement is not proof that it is wrong. Both are
prompts to look.

AND IT COVERS THE LOCAL CHANNEL ONLY. These schemas describe the
robot's own MQTT traffic. The cloud serialises the same events
differently -- different container (`eventArray` here, `finEvents`
there) and different field names. On the day this file was extracted,
`error.value` from these schemas was compared against ha_roomba_plus
reading `error.code` out of the CLOUD mission history and reported as a
wire-key bug. Both observations were correct; the comparison was
between two different formats.

That is why the pairings below are hand-made and few. A key missing
from these schemas is the normal case for anything cloud-side, and
automating the comparison would industrialise exactly that mistake.

HOW A DIVERGENCE IS HANDLED: it is recorded below with a reason, or the
check fails. Divergences are expected -- the point is that each one has
been looked at and decided, rather than accumulating unread.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "docs" / "internal" / "vendor_schemas_ruby_0_7_12.json"

sys.path.insert(0, str(ROOT))

#: Our enum -> the PATH of the schema enum to compare it against.
#:
#: Paths, not names. The first version of this script keyed on the
#: property name and compared RegionType against whichever `type` came
#: first in the file -- JSON Schema uses `type` for its own keyword too,
#: so it was reading an unrelated enum and reported `rid` and `zid` as
#: unknown. It failed on its first run, which is the only reason the
#: reference file is not still wrong.
#:
#: Deliberately a short, hand-made list. Matching by name would pair
#: things that merely share a word, and this project has spent enough
#: evenings on identifiers that looked like meanings.
PAIRS: list[tuple[str, str, str]] = [
    ("roombapy_prime.models.mission_control", "RegionType", "cmd.regions.type"),
    (
        "roombapy_prime.models.mission_history",
        "TravelDestination",
        "timeline.eventData.dest",
    ),
]

#: value -> why it is in one side and not the other.
#:
#: Every entry here is a decision someone made, not a suppression. A new
#: divergence has to be added deliberately, which is the whole mechanism.
ACCEPTED: dict[tuple[str, str], str] = {
    ("RegionType", "tid"): (
        "OURS ONLY. Confirmed for Prime from the app's own IrobotRegionType "
        "(room->rid, zone->zid, temporary->tid). Absent from Classic "
        "firmware, which suggests the ad-hoc region is generation-specific "
        "rather than shared -- untested, and worth knowing before anyone "
        "runs the ad-hoc stage against a Classic robot."
    ),
    ("RegionType", "wid"): (
        "SCHEMA ONLY, and deliberately not modelled. The wire value is now "
        "known -- it was recorded as unresolvable for months -- but this "
        "enum types what the PRIME app sends, and IrobotRegionType has "
        "exactly three members. Adding it would model a value this "
        "generation has no evidence of using."
    ),
    ("RegionType", "tag"): (
        "SCHEMA ONLY. Appears nowhere in the Prime app in any casing. A "
        "fifth region type, unaccounted for on both sides."
    ),
    ("TravelDestination", "waypoint"): (
        "OURS ONLY, and it was already marked inferred. The Classic enum is "
        "exactly [dock, room, zone, poly]. `waypoint` belongs to the Astro "
        "vocabulary that cleantrack also carries (waypoint_create, patrol, "
        "sentry), so it is most likely inherited rather than a destination "
        "any of these robots reports."
    ),
}


def _load_schema_enums() -> dict[str, list[str]]:
    if not SCHEMA.exists():
        print(f"ERROR: {SCHEMA} is missing.")
        raise SystemExit(1)
    return json.loads(SCHEMA.read_text(encoding="utf-8"))["enums"]


def _our_values(module_name: str, enum_name: str) -> set[str]:
    import importlib

    module = importlib.import_module(module_name)
    enum = getattr(module, enum_name)
    return {str(member.value) for member in enum}


def main() -> int:
    schema_enums = _load_schema_enums()
    undecided: list[str] = []
    checked = 0

    for module_name, enum_name, schema_key in PAIRS:
        if schema_key not in schema_enums:
            print(f"ERROR: the schema has no enum named {schema_key!r}")
            return 1

        ours = _our_values(module_name, enum_name)
        theirs = {str(v) for v in schema_enums[schema_key]}
        checked += 1

        for value in sorted(ours - theirs):
            if (enum_name, value) not in ACCEPTED:
                undecided.append(
                    f"  {enum_name}.{value!r} is ours and NOT in the vendor "
                    f"enum {schema_key!r}"
                )
        for value in sorted(theirs - ours):
            if (enum_name, value) not in ACCEPTED:
                undecided.append(
                    f"  {value!r} is in the vendor enum {schema_key!r} and "
                    f"NOT in {enum_name}"
                )

    stale = [
        f"  {enum}.{value!r}"
        for (enum, value) in ACCEPTED
        if not any(enum == name for _, name, _ in PAIRS)
    ]
    if stale:
        print("Recorded divergences for enums that are no longer compared:")
        print("\n".join(stale))
        print(
            "\nRemove them, or restore the pairing. A recorded decision about "
            "something nobody checks is not a decision."
        )
        return 1

    if undecided:
        print("UNDECIDED divergences against the vendor schema:\n")
        print("\n".join(undecided))
        print(
            "\nEach needs a decision, not a suppression. Add it to ACCEPTED "
            "with the reason -- which side it came from, what evidence there "
            "is, and why the enum does or does not change.\n"
            "\nRemember what a schema proves: cleantrack is Classic firmware "
            "built across product lines, so a value missing there does not "
            "make a Prime value wrong, and a value present there is not a "
            "reason to ship it."
        )
        return 1

    print(
        f"OK: {checked} enum(s) compared against ruby-0.7.12; "
        f"{len(ACCEPTED)} known divergence(s), all with a recorded reason."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
