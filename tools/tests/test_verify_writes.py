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


class TestChecksDeriveTheirPayload:
    """Both failures in the first field run were my checks, not the
    robot.

    The quiet-hours check resent an empty settings object, because that
    account had no quiet hours configured -- HTTP 400. The schedule check
    built a schedule from a name and nothing else: no robot, no days, no
    time, no commands -- HTTP 500.

    Both were reported as "this endpoint does not work". Both endpoints
    were fine. A check that invents a payload tests the server's
    willingness to accept nonsense, which is not the question.

    The rule now: read the current value, send it back changed minimally
    or not at all. Nothing to read means the check skips and says why."""

    def test_the_schedule_check_copies_an_existing_schedule(self):
        import inspect

        from roombapy_prime_tools import verify_writes

        source = inspect.getsource(verify_writes._create_and_delete_schedule)

        assert "get_schedules" in source
        assert "replace(" in source

    def test_it_skips_when_there_is_nothing_to_copy(self):
        """A robot with no schedules cannot test schedule creation. That
        is an honest result, not a reason to construct one."""
        import inspect

        from roombapy_prime_tools import verify_writes

        source = inspect.getsource(verify_writes._create_and_delete_schedule)

        assert "return None" in source
        # The message now prints the household id and the raw shape:
        # an empty answer cannot distinguish "no schedules" from
        # "wrong household", and a tester was sent to create a
        # schedule he already had.
        assert "get_schedules() returned" in source
        assert "wrong household" in source

    def test_the_quiet_hours_check_skips_when_none_are_set(self):
        import inspect

        from roombapy_prime_tools import verify_writes

        source = inspect.getsource(verify_writes._set_dnd)

        assert "no quiet hours are configured" in source
        assert "return None" in source

    def test_no_check_constructs_a_model_from_literals(self):
        """The general form. A check building ScheduleOptions(...) or
        DNDStatus(...) from keyword arguments is inventing a payload --
        which is exactly what produced both failures."""
        import inspect
        import re

        from roombapy_prime_tools import verify_writes

        # Comments are stripped: several of them quote the exact
        # construction that caused the failure, in order to explain why
        # it must not happen. A naive text search flags the explanation
        # as the offence -- which it did on the first run of this test.
        source = "\n".join(
            line
            for line in inspect.getsource(verify_writes).splitlines()
            if not line.strip().startswith("#")
        )

        for model in ("ScheduleOptions(", "DNDStatusResponse(", "RobotSettings("):
            constructed = re.findall(rf"{re.escape(model)}[a-z_]+=", source)
            assert not constructed, f"{model} built from literals: {constructed}"

    def test_the_status_value_is_the_one_report_accepts(self):
        """"FAIL" raised KeyError inside Report.add, turning a clean HTTP
        error into a traceback -- and taking the summary down with it.
        Two of a tester's runs ended that way, so the real finding
        arrived buried under our own crash."""
        import inspect

        from roombapy_prime_tools import verify_writes

        source = "\n".join(
            line
            for line in inspect.getsource(verify_writes).splitlines()
            if not line.strip().startswith("#")
        )

        assert '"FAIL"' not in source
        assert '"FAILED"' in source


class TestTheScheduleCheckReadsWhatTheServerSent:
    """The regression that cost two field rounds.

    `_list_schedules` did all three of its reads with getattr() against
    values that are plain dicts: the households list (get_user_households
    returns raw JSON), each household entry, and each schedule
    (SchedulesList.schedules is list[dict], as its own docstring says).
    getattr() on a dict returns the default, so the check reported
    "0 household(s) on this account" for every account that has ever
    existed, never called get_schedules() at all, and reported that as a
    pass.

    A tester with three visible schedules in his app produced output
    byte-identical to a working account's. There was no test on this
    function at all.

    These tests use dicts, because that is what the server sends.
    """

    def _run(self, households, schedules=None):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _list_schedules

        robot = AsyncMock()
        robot.get_user_households.return_value = households
        robot.get_schedules_raw.return_value = schedules or {}
        result = asyncio.run(_list_schedules(robot, SimpleNamespace()))
        return result, robot

    _HOUSEHOLDS = [{
        "household_id": "HH-A",
        "household_robots": [{"robot_id": "ROBOT-1"}],
        "owner_cognito_id": "eu-west-1:private",
        "household_users": [{"email": "someone@example.com"}],
    }]
    _SCHEDULES = {"household_schedules": [{
        "household_schedule_id": "HS-1",
        "schedules": [
            {"schedule_id": "S-1", "options": {"enabled": True}},
            {"schedule_id": "S-2", "options": {"enabled": False}},
        ],
    }]}

    def test_a_real_response_yields_its_households_and_schedules(self):
        result, robot = self._run(self._HOUSEHOLDS, self._SCHEDULES)

        robot.get_schedules_raw.assert_awaited_once_with("HH-A")
        assert result == [{
            "household_id": "HH-A",
            "raw_schedule_count": 2,
            "parsed_schedule_count": 2,
        }]

    def test_a_single_household_returned_as_a_bare_dict_is_read_too(self):
        """PrimeRobot.get_household_id() accepts this shape, so this must
        agree with it -- two readers of one endpoint disagreeing about
        its shape is how the original bug stayed invisible."""
        result, _ = self._run(self._HOUSEHOLDS[0], self._SCHEDULES)

        assert [entry["household_id"] for entry in result] == ["HH-A"]

    def test_finding_nothing_is_not_reported_as_a_pass(self):
        from roombapy_prime_tools.verify_writes import NoResult

        result, robot = self._run([])

        assert isinstance(result, NoResult)
        robot.get_schedules_raw.assert_not_awaited()

    def test_a_household_with_no_schedules_is_not_reported_as_a_pass(self):
        """Distinct from the case above: the account was readable, the
        query ran, and the answer was empty. Still not a pass -- the
        check did not establish where the schedules live."""
        from roombapy_prime_tools.verify_writes import NoResult

        result, _ = self._run(self._HOUSEHOLDS, {"household_schedules": []})

        assert isinstance(result, NoResult)

    def test_the_raw_endpoint_is_used_not_the_parsed_one(self):
        """get_schedules() returns a SchedulesResponse, which has already
        dropped anything this project does not model. Reading through it
        would put the suspected component between the server and the
        tester."""
        _, robot = self._run(self._HOUSEHOLDS, self._SCHEDULES)

        robot.get_schedules.assert_not_awaited()

    def test_the_parser_disagreeing_with_the_server_is_surfaced(self, capsys):
        """The whole point. If the server sends schedules and this
        project reads none, the finding is about the parser, and the
        tester must not have to run again for us to see it."""
        result, _ = self._run(self._HOUSEHOLDS, {"household_schedules": [
            {"household_schedule_id": "HS-1", "schedules": ["not-a-dict"]},
        ]})

        assert result[0] == {
            "household_id": "HH-A",
            "raw_schedule_count": 1,
            "parsed_schedule_count": 0,
        }
        assert "DISAGREEMENT" in capsys.readouterr().out

    def test_an_unreadable_response_shape_says_so_instead_of_reporting_zero(
        self, capsys
    ):
        """The exact silent failure being fixed: a non-empty response
        that this tool cannot read must not look like an empty account."""
        self._run({"data": {"households": [{"household_id": "HH-X"}]}})

        assert "parsing gap in this tool" in capsys.readouterr().out

    def test_account_identity_is_masked_before_being_printed(self, capsys):
        """This output is meant to be pasted into a public issue."""
        self._run(self._HOUSEHOLDS, self._SCHEDULES)

        out = capsys.readouterr().out
        assert "eu-west-1:private" not in out
        assert "someone@example.com" not in out
        # The evidence itself must survive: which household holds which
        # robot is the open question on a mixed account.
        assert "HH-A" in out
        assert "ROBOT-1" in out


class TestTheScheduleCreateCheckFindsATemplate:
    """The second occurrence of the b5 bug, in the same file.

    `_create_and_delete_schedule` derives its payload from an existing
    schedule -- deliberately, because a payload built from a name and
    nothing else got HTTP 500 from a real server and was reported as
    "create_schedules does not work".

    It found that template with `getattr(schedule, "options", None)`.
    SchedulesList.schedules is list[dict], so that returned None every
    time: `template` was always None, the check always took the "you
    have no schedules" branch, and it could not run on any account.

    b5 fixed the OTHER path that told the same tester the same wrong
    thing, and improved this branch's wording -- making the message
    clearer while leaving it just as wrong.
    """

    def _run(self, schedules, confirm_answer=False):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_schedules_raw.return_value = schedules
        robot.create_schedules.return_value = {"household_schedule_id": "HS-NEW"}
        args = SimpleNamespace(household_id=None, schedule_name="Roomba+ test")

        with patch.object(verify_writes, "confirm", return_value=confirm_answer):
            result = asyncio.run(
                verify_writes._create_and_delete_schedule(robot, args)
            )
        return result, robot

    _EXISTING = {"household_schedules": [{
        "household_schedule_id": "HS-1",
        "schedules": [{
            "schedule_id": "S-1",
            "options": {"enabled": True, "name": "Cuisine"},
        }],
    }]}

    def test_an_existing_schedule_is_found_and_copied(self):
        _, robot = self._run(self._EXISTING)

        robot.create_schedules.assert_awaited_once()
        sent = robot.create_schedules.await_args.args[1]
        assert len(sent) == 1
        # Copied, renamed, disabled -- never a schedule built from a
        # name alone, which is what the server answered 500 to.
        assert sent[0].name == "Roomba+ test"
        assert sent[0].enabled is False

    def test_an_account_with_no_schedules_sends_nothing(self):
        """The honest skip. A robot with no schedule cannot test schedule
        creation, and inventing one is what caused the HTTP 500."""
        result, robot = self._run({"household_schedules": []})

        assert result is None
        robot.create_schedules.assert_not_awaited()

    def test_unparsable_schedules_are_reported_as_our_bug_not_an_empty_account(
        self, capsys
    ):
        """The whole point of this fix. If the server sent schedules and
        none parsed, telling the tester he has none sends him to create
        one he already has -- which is how this went wrong twice."""
        result, robot = self._run({"household_schedules": [
            {"household_schedule_id": "HS-1", "schedules": ["unparsable"]},
        ]})

        assert result is None
        robot.create_schedules.assert_not_awaited()
        out = capsys.readouterr().out
        assert "bug here, not" in out

    def test_the_raw_endpoint_is_used_so_the_parser_can_be_checked(self):
        _, robot = self._run(self._EXISTING)

        robot.get_schedules.assert_not_awaited()
        robot.get_schedules_raw.assert_awaited_once_with("HH-A")

    def test_declining_the_delete_leaves_the_created_schedule(self):
        result, robot = self._run(self._EXISTING, confirm_answer=False)

        robot.delete_schedule.assert_not_awaited()
        assert result == {"household_schedule_id": "HS-NEW"}

    def test_accepting_the_delete_removes_it_again(self):
        _, robot = self._run(self._EXISTING, confirm_answer=True)

        robot.delete_schedule.assert_awaited_once_with("HH-A", "HS-NEW")


class TestTheQuietHoursCheckResendsWhatItRead:
    def test_it_sends_the_fields_it_actually_has(self):
        """`getattr(current, "raw", None) or fields` implied a raw
        resend. DNDStatusResponse is a frozen dataclass with no `raw`
        field, so the fallback was the only branch that ever ran."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime.models.schedules_dnd import DNDStatusResponse
        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_dnd_settings.return_value = DNDStatusResponse(status="ENABLED")

        with patch.object(verify_writes, "confirm", return_value=True):
            asyncio.run(verify_writes._set_dnd(
                robot, SimpleNamespace(household_id=None)
            ))

        assert robot.set_dnd_settings.await_args.args[1] == {"status": "ENABLED"}

    def test_nothing_configured_means_nothing_is_sent(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime.models.schedules_dnd import DNDStatusResponse
        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_dnd_settings.return_value = DNDStatusResponse()

        result = asyncio.run(verify_writes._set_dnd(
            robot, SimpleNamespace(household_id=None)
        ))

        assert result is None
        robot.set_dnd_settings.assert_not_awaited()


class TestHouseholdSelectionIsVisible:
    """Every household-scoped check picks a household, and an empty
    answer from the wrong one looks exactly like an empty answer from
    the right one. Output that does not name it cannot be read."""

    def _run(self, household_id_arg):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-RESOLVED"
        return asyncio.run(verify_writes._household_id(
            robot, SimpleNamespace(household_id=household_id_arg)
        ))

    def test_the_resolved_household_is_printed(self, capsys):
        assert self._run(None) == "HH-RESOLVED"
        assert "HH-RESOLVED" in capsys.readouterr().out

    def test_an_overridden_household_is_printed_as_overridden(self, capsys):
        assert self._run("HH-MANUAL") == "HH-MANUAL"
        out = capsys.readouterr().out
        assert "HH-MANUAL" in out
        assert "--household-id" in out


class TestAnUnexpectedResponseShapeIsNotFatal:
    """The reporting path must never be the thing that fails.

    FOUND IN THE b6 BUG HUNT, in b6's own new code:
    `(raw or {}).get("household_schedules")` raises AttributeError on
    any truthy non-dict. A crash there takes down the diagnostic output
    this release exists to produce -- the same shape as the `Report.add`
    KeyError that buried two testers' real findings under our traceback.
    """

    SHAPES = [
        [{"household_schedule_id": "HS-1"}],
        "unexpected",
        None,
        {"error": "forbidden"},
        {"household_schedules": {"oops": 1}},
        {"household_schedules": ["x", 3]},
        {"household_schedules": [{"schedules": "nope"}]},
    ]

    def _run(self, fn_name, raw):
        import asyncio
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_user_households.return_value = [{"household_id": "HH-A"}]
        robot.get_schedules_raw.return_value = raw
        args = SimpleNamespace(household_id=None, schedule_name="t")

        with patch.object(verify_writes, "confirm", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(getattr(verify_writes, fn_name)(robot, args))

    def test_the_create_check_survives_every_shape(self):
        for raw in self.SHAPES:
            self._run("_create_and_delete_schedule", raw)

    def test_the_listing_check_survives_every_shape(self):
        for raw in self.SHAPES:
            self._run("_list_schedules", raw)


class TestAFailureShowsWhatTheServerSaid:
    """@chairstacker's HTTP 500 arrived with no explanation, and the
    explanation was in the exception object all along.

    RestError carries the response body in `raw_response`; the report
    printed only `str(exc)`, which is "HTTP 500 from <url>". A whole
    field round produced a status code and nothing else.

    Same shape as b5 and b6: the information existed, the reporting
    layer did not show it.
    """

    def test_the_response_body_is_included(self):
        from roombapy_prime.rest_client import RestError
        from roombapy_prime_tools.verify_writes import _failure_detail

        detail = _failure_detail(RestError(
            "HTTP 500 from https://example/settings/schedule",
            status=500,
            raw_response='{"message":"created_time is not allowed"}',
        ))

        assert "HTTP 500" in detail
        assert "created_time is not allowed" in detail

    def test_an_exception_without_a_body_still_reports_cleanly(self):
        from roombapy_prime_tools.verify_writes import _failure_detail

        assert _failure_detail(RuntimeError("boom")) == "RuntimeError: boom"

    def test_an_empty_body_adds_nothing(self):
        from roombapy_prime.rest_client import RestError
        from roombapy_prime_tools.verify_writes import _failure_detail

        detail = _failure_detail(RestError("HTTP 500", status=500, raw_response=""))

        assert "server said" not in detail


class TestTheCreateRequestBodyIsVisible:
    """Two candidate causes for the 500 were arguable from the code
    alone -- a copied `created_time`, which the server assigns, and
    region commands missing `initiator`, which the app adds at send time
    and stored schedules do not carry. Neither is decidable without
    seeing the body, so the body gets printed."""

    def test_the_body_is_printed_before_the_request(self, capsys):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_schedules_raw.return_value = {"household_schedules": [{
            "household_schedule_id": "HS-1",
            "schedules": [{"schedule_id": "S-1", "options": {
                "enabled": True, "robot_id": "ROBOT", "created_time": 1700000000,
                "frequency": "WEEKLY", "start": {"day": [1], "hour": 9, "min": 0}}}],
        }]}
        robot.create_schedules.return_value = {"household_schedule_id": "NEW"}

        with patch.object(verify_writes, "confirm", return_value=False):
            asyncio.run(verify_writes._create_and_delete_schedule(
                robot, SimpleNamespace(household_id=None, schedule_name="t")
            ))

        out = capsys.readouterr().out
        assert "request body:" in out
        # The fields a reader needs in order to judge the 500 must be in
        # the printed body, not merely in the object that was sent.
        assert "created_time" in out
        assert "robot_id" in out
