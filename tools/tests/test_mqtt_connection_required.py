"""Every script that reads or writes over MQTT must actually connect.

REAL FIELD BUG (DaRealGuGu, stage 0 of the settings test). Named
shadows travel over MQTT, not REST. `verify_settings_write --list-settings`
called get_settings() without ever opening the connection, and failed
with a bare AssertionError four frames down -- on the very first run
anyone had ever given that script.

The shared connection helper takes connect_mqtt as an option, defaulting
to off, because the REST-only scripts would otherwise open a connection
they never use. That default is right, but it makes forgetting it silent
until someone runs the script against a real robot.

This test closes that gap by reading the source: if a script calls an
MQTT-backed method, it must ask for an MQTT connection.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "roombapy_prime_tools"

# Methods on PrimeRobot that go over MQTT rather than REST, confirmed by
# reading prime_robot.py rather than assumed from their names --
# get_schedules(), for instance, sounds like a shadow read and is REST.
_MQTT_METHODS = frozenset({
    "get_state", "get_settings", "get_named_shadow", "set_setting",
    "watch_state", "watch_mission_timeline", "watch_rejected_commands",
    "send_simple_command", "send_mission_command",
    "send_routine_command_via_cmd_topic",
})


def _scripts() -> list[Path]:
    return sorted(_TOOLS.glob("verify_*.py"))


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_every_connection_site_asks_for_mqtt_when_the_script_needs_it(script: Path) -> None:
    """Checks EVERY connected_robot() call, not just whether the file
    mentions connect_mqtt somewhere.

    The first version of this test did the latter, and it gave false
    confidence: verify_settings_write has two connection sites, one was
    fixed, and the whole file passed. The unfixed one -- written on a
    single line rather than across three, which is also why a
    text-pattern fix missed it -- then failed for a tester on the very
    next run, with the shiny new error message I had just written for
    exactly that case.

    A guard that passes when the bug is still present is worse than no
    guard, because it stops you looking."""
    source = script.read_text(encoding="utf-8")
    tree = ast.parse(source)

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    mqtt_calls = sorted(called & _MQTT_METHODS)
    if not mqtt_calls:
        return

    missing = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "connected_robot"
        and not any(kw.arg == "connect_mqtt" for kw in node.keywords)
    ]

    assert not missing, (
        f"{script.name} calls MQTT-backed method(s) {mqtt_calls}, but the "
        f"connected_robot() call(s) on line(s) {missing} do not pass connect_mqtt=True. "
        "Every connection site needs it -- one fixed site does not cover the others, and "
        "the failure only shows up against a real robot."
    )
