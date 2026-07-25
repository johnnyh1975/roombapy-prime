"""Cross-script tests for the safety gates in every main().

These were the least-covered lines in the whole tools package (20-25%
on several scripts) -- and they are the lines that decide whether a
command touches a real robot. A mistake here does not raise; it quietly
runs the wrong action, or asks for a password before discovering the
command was invalid all along.

That last one is not hypothetical: a field tester was prompted for
their iRobot credentials by a command that then told them a required
flag was missing. The ordering these tests pin down -- validate
everything, ask for credentials only once we know we are going to act
-- exists because of that report.

Parametrised across scripts on purpose: each was copied from the last,
so a gap in one is usually a gap in several.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from roombapy_prime.diagnostics import Report

# (module name, no-action argv, action-without-gate argv, the gate flag)
_SCRIPTS = [
    (
        "verify_schedule_write",
        ["--blid", "B"],
        # --update-unchanged takes the household id as its value here,
        # unlike the same-named flag in verify_virtual_wall_write. Worth
        # noting: two scripts, one flag name, two different shapes.
        ["--blid", "B", "--update-unchanged", "HOUSEHOLD"],
        "--i-understand-this-changes-a-real-schedule",
    ),
    (
        "verify_virtual_wall_write",
        ["--blid", "B"],
        ["--blid", "B", "--update-unchanged", "--p2map-id", "M", "--p2mapv-id", "V"],
        "--i-understand-this-changes-real-map-zones",
    ),
    (
        "verify_favorite_write",
        ["--blid", "B"],
        ["--blid", "B", "--create-and-delete-test"],
        "--i-understand-this-changes-a-real-favorite",
    ),
    (
        "verify_settings_write",
        ["--blid", "B"],
        ["--blid", "B", "--toggle", "child_lock"],
        "--i-understand-this-changes-a-real-setting",
    ),
]


def _module(name):
    import importlib

    return importlib.import_module(f"roombapy_prime_tools.{name}")


@pytest.mark.parametrize("name,no_action,_action,_gate", _SCRIPTS, ids=[s[0] for s in _SCRIPTS])
def test_no_action_does_nothing_and_never_asks_for_credentials(name, no_action, _action, _gate):
    module = _module(name)

    with patch("sys.argv", [name, *no_action]), \
         patch.object(module, "resolve_credentials",
                      side_effect=AssertionError("must not ask for credentials")), \
         patch("asyncio.run", side_effect=AssertionError("must not run anything")):
        module.main()


@pytest.mark.parametrize("name,_no_action,action,_gate", _SCRIPTS, ids=[s[0] for s in _SCRIPTS])
def test_missing_gate_flag_aborts_before_asking_for_credentials(name, _no_action, action, _gate):
    """THE ordering that matters: a tester must learn the flag is
    missing BEFORE typing their password, not after."""
    module = _module(name)

    with patch("sys.argv", [name, *action]), \
         patch.object(module, "resolve_credentials",
                      side_effect=AssertionError("asked for credentials before validating")), \
         patch("asyncio.run", side_effect=AssertionError("must not run anything")), \
         pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 1


@pytest.mark.parametrize("name,_no_action,action,gate", _SCRIPTS, ids=[s[0] for s in _SCRIPTS])
def test_with_the_gate_flag_it_actually_dispatches(name, _no_action, action, gate):
    """The mirror image: with everything supplied, something must
    genuinely run -- otherwise a passing gate test could just be a
    script that never does anything at all."""
    module = _module(name)
    ran: list = []

    with patch("sys.argv", [name, *action, gate]), \
         patch.object(module, "resolve_credentials", return_value=("u", "p")), \
         patch("asyncio.run", side_effect=lambda coro: ran.append(coro) or coro.close()):
        module.main()

    assert len(ran) == 1


@pytest.mark.parametrize("name,_no_action,_action,_gate", _SCRIPTS, ids=[s[0] for s in _SCRIPTS])
def test_missing_blid_aborts(name, _no_action, _action, _gate):
    module = _module(name)

    with patch("sys.argv", [name]), \
         patch.dict("os.environ", {}, clear=True), \
         patch.object(module, "resolve_credentials",
                      side_effect=AssertionError("must not ask for credentials")), \
         pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 1


def _fake_run(collected: list):
    """Stands in for asyncio.run: records the coroutine, closes it so
    Python does not warn about it never being awaited, and returns the
    (report, capture) pair main() expects. A MagicMock will not do --
    main() unpacks the report's summary() into three values."""
    def _runner(coro):
        collected.append(coro)
        coro.close()
        return Report(), {}
    return _runner


class TestMapEditAndTimelineGates:
    """The two remaining scripts' main() gates, which differ enough in
    shape not to fit the parametrised table above.

    verify_map_edit is unconditional -- it ALWAYS edits a map when it
    runs, so the gate applies to the whole script rather than to one
    action. verify_mission_timeline is the opposite: it is read-only
    unless --start-mission is passed, so its gate is conditional, and
    demanding the flag for a plain watch run would be pointless
    friction for the safest thing the script does.
    """

    def _module(self, name):
        import importlib

        return importlib.import_module(f"roombapy_prime_tools.{name}")

    def test_map_edit_without_its_flag_aborts_before_credentials(self):
        module = self._module("verify_map_edit")

        with patch("sys.argv", ["verify_map_edit", "--blid", "B"]), \
             patch.object(module, "resolve_credentials",
                          side_effect=AssertionError("asked before validating")), \
             patch("asyncio.run", side_effect=AssertionError("must not run")), \
             pytest.raises(SystemExit) as exc:
            module.main()

        assert exc.value.code == 1

    def test_map_edit_with_the_flag_but_declined_prompt_runs_nothing(self):
        """A second gate after the flag: the flag says "I understand",
        the prompt says "do it now". Both are required."""
        module = self._module("verify_map_edit")

        with patch("sys.argv", ["verify_map_edit", "--blid", "B",
                                "--i-understand-this-will-edit-my-map"]), \
             patch.object(module, "resolve_credentials", return_value=("u", "p")), \
             patch.object(module, "confirm", return_value=False), \
             patch("asyncio.run", side_effect=AssertionError("must not run")), \
             pytest.raises(SystemExit) as exc:
            module.main()

        assert exc.value.code == 0, "declining is a clean stop, not an error"

    def test_timeline_watching_needs_no_movement_flag(self):
        """The read-only path must stay frictionless -- it is the one
        we most want testers to reach for."""
        module = self._module("verify_mission_timeline")
        ran: list = []

        with patch("sys.argv", ["verify_mission_timeline", "--blid", "B", "--duration", "1"]), \
             patch.object(module, "resolve_credentials", return_value=("u", "p")), \
             patch("asyncio.run", side_effect=_fake_run(ran)):
            module.main()

        assert len(ran) == 1

    def test_timeline_start_mission_without_the_flag_aborts(self):
        module = self._module("verify_mission_timeline")

        with patch("sys.argv", ["verify_mission_timeline", "--blid", "B", "--start-mission"]), \
             patch.object(module, "resolve_credentials",
                          side_effect=AssertionError("asked before validating")), \
             patch("asyncio.run", side_effect=AssertionError("must not run")), \
             pytest.raises(SystemExit) as exc:
            module.main()

        assert exc.value.code == 1
