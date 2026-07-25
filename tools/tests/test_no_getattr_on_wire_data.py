"""getattr() must not be used on data that crossed a REST/MQTT boundary.

THREE SEPARATE FIELD BUGS, ALL THE SAME MISTAKE:

  - the pad pre-flight reported "no operatingMode in regions" for a
    payload that visibly carried one on every single region
  - `--list-maps` printed "name='(unnamed)'  --p2map-id None" for a map
    that certainly has both
  - the map-version pre-flight reported "no active_p2mapv_id reported"

None of them raised. Some REST wrappers return plain `list[dict]` and
others return parsed models; getattr() on a dict quietly returns the
default, so the result is a report full of `None` that reads as "the
robot had nothing to say" rather than "we asked wrongly".

That failure mode is worse than a crash: a tester reports a puzzling
result, and the investigation goes looking at the robot instead of at
us. `field()` in _cli.py handles both shapes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "roombapy_prime_tools"

# Names whose values come off the wire. Deliberately a name list rather
# than type inference: these scripts are not typed tightly enough for
# inference, and the names are stable.
_WIRE_LOCALS = frozenset({"m", "v", "region", "entry", "fav", "favorite", "cmd", "event"})

# Attribute names that only exist on wire data, never on our own objects.
_WIRE_FIELDS = frozenset({
    "p2map_id", "active_p2mapv_id", "user_p2mapv_id", "region_id",
    "operatingMode", "params", "regions", "detectedPad",
})


def _offences(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr"):
            continue
        if len(node.args) < 2:
            continue
        target, attr = node.args[0], node.args[1]
        if not isinstance(attr, ast.Constant) or not isinstance(attr.value, str):
            continue
        # __dict__ is a genuine introspection case for raw capture, not a
        # wire field -- but the call site still has to handle dicts, which
        # have no __dict__ at all.
        if attr.value == "__dict__":
            continue
        name = getattr(target, "id", None)
        if name in _WIRE_LOCALS or attr.value in _WIRE_FIELDS:
            found.append(f"line {node.lineno}: getattr({name}, {attr.value!r})")
    return found


@pytest.mark.parametrize(
    "script", sorted(_TOOLS.glob("*.py")), ids=lambda p: p.name
)
def test_no_getattr_on_wire_data(script: Path) -> None:
    offences = _offences(script)

    assert not offences, (
        f"{script.name} uses getattr() on data that came off the wire:\n  "
        + "\n  ".join(offences)
        + "\n\nUse field() from _cli instead -- it reads both dicts and typed models. "
        "getattr() on a dict returns the default silently, which produces a report "
        "full of None rather than an error."
    )


class TestFieldHelperHandlesBothShapes:
    """The replacement has to work for both, since the call sites
    genuinely cannot tell which they will get -- some REST wrappers
    return `list[dict]` and others return parsed models."""

    def test_reads_a_dict(self):
        from roombapy_prime_tools._cli import field

        # Values taken from a real field report where this returned None.
        raw = {"p2map_id": "0B710054CA277C04B2700374A8349C9A-1767019490",
               "active_p2mapv_id": "260725T101729.167"}

        assert field(raw, "p2map_id").endswith("-1767019490")
        assert field(raw, "active_p2mapv_id") == "260725T101729.167"

    def test_reads_a_typed_object(self):
        from types import SimpleNamespace

        from roombapy_prime_tools._cli import field

        assert field(SimpleNamespace(p2map_id="X"), "p2map_id") == "X"

    def test_a_missing_key_returns_the_default_for_both_shapes(self):
        from types import SimpleNamespace

        from roombapy_prime_tools._cli import field

        assert field({}, "nope", "fallback") == "fallback"
        assert field(SimpleNamespace(), "nope", "fallback") == "fallback"

    def test_none_is_survivable(self):
        from roombapy_prime_tools._cli import field

        assert field(None, "anything") is None
