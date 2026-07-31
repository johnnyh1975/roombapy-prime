"""One entry point for the write operations that have no verifier yet."""

from __future__ import annotations

import inspect



class TestTheChecksMatchTheLibrary:
    """Every runner calls a real method with real arguments.

    THIS IS THE TEST THAT MATTERS. A first draft of this tool got four of
    six calls wrong: set_dnd_settings and create_schedules both take a
    household_id first, order_favorite takes ONE favourite and a position
    rather than a full ordering, and set_map_orientation takes RADIANS --
    passing 90 for a quarter turn would have rotated the map fourteen
    times round.

    None of that fails at import. It fails when a tester runs it, which
    is the expensive place to find out -- a28 cost two people an evening
    for exactly this class of mistake."""

    def _robot_signature(self, name):
        from roombapy_prime.prime_robot import PrimeRobot

        return inspect.signature(getattr(PrimeRobot, name))

    def test_every_method_the_runners_call_exists(self):
        import re

        from roombapy_prime.prime_robot import PrimeRobot
        from roombapy_prime_tools import verify_writes

        source = inspect.getsource(verify_writes)
        called = set(re.findall(r"robot\.([a-z_]+)\(", source))

        for method in called:
            assert hasattr(PrimeRobot, method), method

    def test_household_scoped_calls_pass_a_household_id(self):
        """set_dnd_settings, create_schedules and delete_schedule all take
        it first. Omitting it does not raise -- it addresses somebody
        else's household and comes back empty."""
        for name in ("set_dnd_settings", "create_schedules", "delete_schedule",
                     "get_dnd_settings"):
            params = list(self._robot_signature(name).parameters)
            assert params[1] == "household_id", name

    def test_order_favorite_takes_one_favourite_not_a_list(self):
        """The obvious guess is a full ordering. It is not: one favourite
        id, and where to put it."""
        params = list(self._robot_signature("order_favorite").parameters)

        assert params[1] == "favorite_id"
        assert "insert_at" in params

    def test_map_orientation_is_in_radians(self):
        """The parameter is `orientation_rad`. A tester passing 90 for a
        quarter turn would land somewhere arbitrary, and the tool would
        report success."""
        params = list(self._robot_signature("set_map_orientation").parameters)

        assert "orientation_rad" in params

    def test_create_schedules_takes_a_list_of_options(self):
        """Not a name. Passing a string would iterate its characters."""
        params = self._robot_signature("create_schedules").parameters

        assert "schedules" in params


class TestDangerousOperationsAreNotOffered:
    """delete_map, reset_robot and reset_robot_parts are absent on
    purpose.

    A tester who accidentally deletes a map loses weeks of mapping, every
    zone and every room name on it -- to confirm a command nobody wants
    to use. reset_robot_parts takes no part argument and resets every
    consumable counter at once. reset_robot is a factory reset.

    A confirmation prompt is not enough for those. Somebody working
    through a list of things to try, in a language they only half follow,
    will eventually try one. The safest interface is the one that does
    not list them."""

    _FORBIDDEN = ("delete_map", "reset_robot", "reset_robot_parts")

    def test_none_of_them_appear_as_a_check(self):
        from roombapy_prime_tools.verify_writes import CHECKS

        names = {c.name for c in CHECKS}

        for forbidden in self._FORBIDDEN:
            assert forbidden not in names

    def test_none_of_them_are_called_anywhere_in_the_tool(self):
        """Stronger than the name check: a runner could call one while
        being listed under a harmless name."""
        from roombapy_prime_tools import verify_writes

        source = inspect.getsource(verify_writes)

        for forbidden in self._FORBIDDEN:
            assert f"robot.{forbidden}(" not in source

    def test_the_omission_is_explained_to_the_reader(self):
        """So the next person does not add them back as an oversight."""
        from roombapy_prime_tools import verify_writes

        source = inspect.getsource(verify_writes)

        assert "delete_map" in source  # mentioned...
        assert "weeks of mapping" in source  # ...with the reason


class TestEveryCheckTellsTheTesterWhatToLookAt:
    """A write this library reports as accepted is not the same as a
    write that did what the tester expected.

    That distinction is what the entire virtual-wall investigation turned
    on: the server accepted three request shapes for months while storing
    nothing useful. Every check here names what to look at afterwards."""

    def test_each_check_has_a_verification_step(self):
        from roombapy_prime_tools.verify_writes import CHECKS

        for check in CHECKS:
            assert check.verify_by
            assert "app" in check.verify_by.lower()

    def test_each_check_has_a_known_risk_level(self):
        from roombapy_prime_tools.verify_writes import CHECKS, _RISK_NOTE

        for check in CHECKS:
            assert check.risk in _RISK_NOTE

    def test_the_safe_ones_resend_what_is_already_there(self):
        """"Safe" means the robot ends up where it started. dnd and
        favourite ordering both read first and send the same values
        back."""
        from roombapy_prime_tools.verify_writes import CHECKS

        safe = {c.name for c in CHECKS if c.risk == "safe"}

        assert "set_dnd_settings" in safe
        assert "order_favorite" in safe

    def test_schedule_creation_is_marked_risky_not_safe(self):
        """It leaves something behind if the delete step fails, and the
        tester then has to find it in the app."""
        from roombapy_prime_tools.verify_writes import CHECKS

        check = next(c for c in CHECKS if c.name == "schedule_create_delete")

        assert check.risk == "risky"


class TestTheToolRefusesWithoutConsent:
    def test_an_unknown_check_name_exits(self):
        from roombapy_prime_tools.verify_writes import CHECKS

        assert not any(c.name == "not_a_real_check" for c in CHECKS)

    def test_the_consent_flag_is_required(self):
        """Listing is free; writing is not."""
        from roombapy_prime_tools import verify_writes

        source = inspect.getsource(verify_writes.main)

        assert "i_understand_this_writes_to_my_robot" in source
        assert "sys.exit(1)" in source
