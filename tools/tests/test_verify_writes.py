"""One entry point for the write operations that have no verifier yet."""

from __future__ import annotations

import inspect

import pytest



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


class TestTheQuietHoursCheckSendsOneVariant:
    """The body this check built was wrong in three ways at once, which
    is the HTTP 400 the one live attempt returned.

    It took every non-empty attribute off the parsed response and sent
    them as a dict, so it used the PYTHON names (daily_start) instead of
    the wire keys (dailyStart), mixed BOTH cases of a sealed type into
    one body, and included `status`, which belongs to the response side
    and is not part of the write structure at all.

    DNDPutRequest serialises a DNDSchedule directly -- no envelope --
    and DNDSchedule has exactly two mutually exclusive cases.
    """

    def _run(self, response, confirm_answer=True):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_dnd_settings.return_value = response
        with patch.object(verify_writes, "confirm", return_value=confirm_answer):
            result = asyncio.run(verify_writes._set_dnd(
                robot, SimpleNamespace(household_id=None)
            ))
        return result, robot

    def _dnd(self, **kwargs):
        from roombapy_prime.models.schedules_dnd import DNDStatusResponse

        return DNDStatusResponse(**kwargs)

    def test_a_daily_schedule_sends_only_the_daily_fields(self):
        _, robot = self._run(self._dnd(
            daily_start=1320, daily_end=420, status={"enabled": True}
        ))

        body = robot.set_dnd_settings.await_args.args[1]
        assert body == {"dailyStart": 1320, "dailyEnd": 420}
        # `status` is response-only. Sending it was one of the three
        # faults in the body that got a 400.
        assert "status" not in body

    def test_an_ends_at_schedule_sends_only_that(self):
        _, robot = self._run(self._dnd(ends_at=1785700000))

        assert robot.set_dnd_settings.await_args.args[1] == {
            "endsAt": 1785700000
        }

    def test_the_two_variants_are_never_mixed(self):
        """Two mutually exclusive cases -- the app's own type system
        makes a body carrying both impossible, and no server has ever
        been asked to accept one."""
        _, robot = self._run(self._dnd(
            daily_start=1320, daily_end=420, ends_at=1785700000
        ))

        body = robot.set_dnd_settings.await_args.args[1]
        assert ("endsAt" in body) != ("dailyStart" in body)

    def test_nothing_configured_sends_nothing(self):
        from roombapy_prime_tools.verify_writes import NoResult

        result, robot = self._run(self._dnd(status={}))

        assert isinstance(result, NoResult)
        robot.set_dnd_settings.assert_not_awaited()

    def test_the_body_is_printed_with_its_variant(self, capsys):
        """A body nobody looked at cost this check a whole release."""
        self._run(self._dnd(daily_start=1320, daily_end=420))

        out = capsys.readouterr().out
        assert "variant: daily" in out
        assert "dailyStart" in out


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
        # THE ENVELOPE, not just the inner object. This printed
        # options.to_json() while create_schedules() wraps it as
        # {"schedules": [...]}, so three field rounds went on a payload
        # that never crossed the wire -- in the check that exists to
        # show what did.
        assert "request body (as sent):" in out
        # THE WRAPPER, not just the inner object. This printed
        # options.to_json() while the client wraps each schedule as
        # {"options": ..., "schedule_id": null} -- so four field rounds
        # went on a payload that never crossed the wire, in the check
        # that exists to show what did. Printing the wrapper is what
        # made the missing `options` level visible.
        assert '"schedules"' in out
        assert '"options"' in out
        assert "robot_id" in out
        # Omitted, not sent as null: both fields are optional with
        # default null and the app serialises without encodeDefaults.
        assert '"schedule_id"' not in out


class TestTheCreateCopyDropsServerAssignedFields:
    """Copying a template schedule meant replaying its `created_time`
    into a create request, and create has returned HTTP 500 on every
    attempt against a real account:

        {"errorType": "AspenError.InternalError",
         "errorMessage": "Internal error"}

    A server crash rather than a validation error, which fits a value
    the server expected to assign itself.

    The other two candidates were ruled out rather than guessed away.
    `initiator` is not required -- @DaRealGuGu's working, server-stored
    schedules do not carry it. And `is_smart_clean_fav`, which the
    server sends and this project does not model, does not appear
    anywhere in the iRobot APK: the server added it without its own app
    knowing, so its absence cannot plausibly crash the server.
    """

    _TEMPLATE = {"household_schedules": [{
        "household_schedule_id": "HS-1",
        "schedules": [{"schedule_id": "S-1", "options": {
            "enabled": True, "robot_id": "ROBOT", "frequency": "WEEKLY",
            "created_time": "2026-08-01T18:15:09.211030+00:00",
            "start": {"day": [6], "hour": 15, "min": 45}}}],
    }]}

    def _run(self, capsys=None):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_schedules_raw.return_value = self._TEMPLATE
        robot.create_schedules.return_value = {"household_schedule_id": "NEW"}

        with patch.object(verify_writes, "confirm", return_value=False):
            asyncio.run(verify_writes._create_and_delete_schedule(
                robot, SimpleNamespace(household_id=None, schedule_name="t")
            ))
        return robot

    def test_created_time_is_not_replayed(self):
        robot = self._run()

        sent = robot.create_schedules.await_args.args[1][0]
        assert sent.created_time is None
        assert "created_time" not in sent.to_json()

    def test_everything_else_from_the_template_survives(self):
        """Dropping one field must not turn this into a schedule built
        from scratch -- that shape got its own HTTP 500 earlier, because
        a schedule with no commands says nothing about what to clean."""
        robot = self._run()

        sent = robot.create_schedules.await_args.args[1][0].to_json()
        assert sent["robot_id"] == "ROBOT"
        assert sent["frequency"] == "WEEKLY"
        assert sent["start"]["day"] == [6]
        assert sent["name"] == "t"
        assert sent["enabled"] is False

    def test_the_omission_is_printed(self, capsys):
        """Three rounds on this check have ended with a status code and
        no way to tell what was sent. If this run still fails, the next
        reader must be able to see what was left out."""
        self._run()

        out = capsys.readouterr().out
        assert "deliberately NOT sent" in out
        assert "created_time" in out
        assert "2026-08-01T18:15:09.211030+00:00" in out


class TestTheDropReportIsAccurate:
    """Found in the b8 bug hunt. A template with no `created_time` still
    printed "deliberately NOT sent: created_time = None", claiming an
    omission that never happened.

    This output exists so that a failing run stays readable without
    another round trip to the tester. A line that misdescribes what was
    sent defeats that.
    """

    def _run(self, options):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH"
        robot.get_schedules_raw.return_value = {"household_schedules": [{
            "household_schedule_id": "HS",
            "schedules": [{"schedule_id": "S", "options": options}],
        }]}
        robot.create_schedules.return_value = {}
        with patch.object(verify_writes, "confirm", return_value=False):
            asyncio.run(verify_writes._create_and_delete_schedule(
                robot, SimpleNamespace(household_id=None, schedule_name="t")
            ))

    def test_a_real_drop_is_named_with_its_value(self, capsys):
        self._run({"enabled": True, "created_time": "2026-08-01T18:15:09Z"})

        out = capsys.readouterr().out
        assert "deliberately NOT sent" in out
        assert "2026-08-01T18:15:09Z" in out

    def test_nothing_to_drop_is_not_reported_as_a_drop(self, capsys):
        self._run({"enabled": True, "frequency": "WEEKLY"})

        out = capsys.readouterr().out
        assert "deliberately NOT sent" not in out
        assert "no server-assigned fields to drop" in out


class TestTheCleanScoreCheckShowsWhatItGuessed:
    """The endpoint and its model chain are confirmed from Kotlin; the
    request body is not. The app calls it through
    fetchCleanScoreDataForMap(), so the body sits behind the native
    boundary exactly as /v1/time-estimates did -- and there the answer
    turned out to be a single `{"robot_id": ...}`.

    So the check prints the body it sends. A rejection naming a field
    would teach more than a success, and either beats a bare status
    code: three field rounds have already ended that way.
    """

    def _run(self, maps, response=None, effect=None):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _clean_score

        robot = AsyncMock()
        robot.get_active_map_versions.return_value = maps
        if effect is not None:
            robot.get_clean_score_raw = AsyncMock(side_effect=effect)
        else:
            robot.get_clean_score_raw.return_value = response
        result = asyncio.run(_clean_score(robot, SimpleNamespace()))
        return result, robot

    _MAPS = [{"p2map_id": "MAP-1", "name": "Ground floor"}]

    def test_the_request_is_printed(self, capsys):
        self._run(self._MAPS, response={"clean_scores": []})

        assert "clean-score?p2map_id=MAP-1" in capsys.readouterr().out

    def test_the_raw_response_is_printed(self, capsys):
        # The CONFIRMED wire shape -- snake_case, read as literals out
        # of the app's own response parser. The Kotlin side is
        # camelCase, and writing those names here would repeat the
        # confusion that once produced 21 wrong wire keys.
        self._run(self._MAPS, response={"clean_scores": [
            {"p2map_id": "MAP-1", "regions": [
                {"region_id": "13", "clean_score": 0.82,
                 "updated_ts": 1785600000},
            ]},
        ]})

        out = capsys.readouterr().out
        assert "clean_score" in out
        assert "0.82" in out
        # Parsed alongside the raw, so a mismatch between the confirmed
        # keys and what the server actually sends shows up in the same
        # run rather than a field round later.
        assert "1 room score(s)" in out
        assert "region 13: 0.82" in out

    def test_a_rejection_carries_the_servers_words(self, capsys):
        from roombapy_prime.rest_client import RestError

        result, _ = self._run(self._MAPS, effect=RestError(
            "HTTP 400", status=400,
            raw_response='{"message":"p2map_id is not a valid field"}',
        ))

        assert "p2map_id is not a valid field" in capsys.readouterr().out
        # Every map rejected is not a pass: the check did not answer its
        # own question.
        from roombapy_prime_tools.verify_writes import NoResult
        assert isinstance(result, NoResult)

    def test_a_robot_with_no_maps_is_not_a_failure(self):
        from roombapy_prime_tools.verify_writes import NoResult

        result, robot = self._run([])

        assert isinstance(result, NoResult)
        robot.get_clean_score_raw.assert_not_awaited()


class TestTheCleanScoreParserIsCheckedAgainstTheRaw:
    def test_a_parser_disagreement_is_surfaced(self, capsys):
        """The keys come from the app's own response parser, so a
        mismatch means either the server changed or the confirmation was
        misread. Both are worth seeing immediately."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _clean_score

        robot = AsyncMock()
        robot.get_active_map_versions.return_value = [{"p2map_id": "MAP-1"}]
        robot.get_clean_score_raw.return_value = {"clean_scores": [
            {"p2map_id": "MAP-1", "regions": ["unparsable", "also-not"]},
        ]}

        asyncio.run(_clean_score(robot, SimpleNamespace()))

        assert "DISAGREEMENT" in capsys.readouterr().out


class TestTheAutomationsCheckSettlesADeadEndpoint:
    """`/v1/user/automations` is a dead constant in the app: one
    reference, a static initialiser, no reader -- the same signature as
    two other strings that turned out to be dead. A second Home
    Assistant integration calls it, but swallows the error and never
    reads the result -- so that is not evidence it answers either.

    So a refusal is as valuable as a success here: it closes the
    question instead of leaving it open, and the check must report it
    that way rather than as a failure of the tester's account.
    """

    def _run(self, response=None, effect=None):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _automations

        robot = AsyncMock()
        if effect is not None:
            robot.get_automations_raw = AsyncMock(side_effect=effect)
        else:
            robot.get_automations_raw.return_value = response
        return asyncio.run(_automations(robot, SimpleNamespace()))

    def test_a_live_endpoint_is_parsed(self, capsys):
        result = self._run([{
            "automation_id": "A1", "automation_type": "GEOFENCE",
            "enabled": True, "favorite_id": "F-1",
            "time_window": {"hour": 14, "minute": 30},
            "service_details": {"service_id": "ecobee"},
        }])

        assert result == [{"automations": 1}]
        out = capsys.readouterr().out
        assert "A1" in out and "GEOFENCE" in out

    def test_a_refusal_is_inconclusive_not_a_failure(self):
        from roombapy_prime.rest_client import RestError
        from roombapy_prime_tools.verify_writes import NoResult

        result = self._run(effect=RestError(
            "HTTP 404", status=404, raw_response='{"message":"not found"}'))

        assert isinstance(result, NoResult)

    def test_the_servers_words_survive_a_refusal(self, capsys):
        from roombapy_prime.rest_client import RestError

        self._run(effect=RestError(
            "HTTP 403", status=403, raw_response='{"message":"forbidden"}'))

        assert "forbidden" in capsys.readouterr().out

    def test_an_empty_answer_is_not_reported_as_a_pass(self):
        from roombapy_prime_tools.verify_writes import NoResult

        assert isinstance(self._run([]), NoResult)


class TestEveryReadCheckFollowsTheProcedure:
    """The rules the schedules saga cost three field rounds to learn.

    Each was paid for once and must not have to be paid for again by
    the next endpoint someone adds:

      1. print the RAW response before this project touches it
      2. count the raw independently and say so when the two disagree
      3. an empty or unreadable answer is not a passing check

    Rule 2 is the one that keeps getting missed. `automations` shipped
    without it in this very release: an unrecognised shape would have
    parsed to nothing and been reported as "possibly an empty account",
    which is exactly the b5 bug wearing new clothes.

    Behavioural, not a source grep -- a grep tracks where code lives
    rather than what it does, and one in this file already broke for
    that reason.
    """

    def _feed(self, name, robot_setup):
        import asyncio
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot_setup(robot)
        runner = next(c.runner for c in verify_writes.CHECKS if c.name == name)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = asyncio.run(runner(robot, SimpleNamespace(household_id=None)))
        return result, buffer.getvalue()

    #: name -> how to make the robot answer with objects the parser
    #: cannot read. Each entry is one endpoint's "the server sent
    #: something, we understood none of it" case.
    UNREADABLE = {
        "schedules": lambda r: (
            setattr(r, "get_user_households", _returns([{"household_id": "HH"}])),
            setattr(r, "get_schedules_raw", _returns(
                {"household_schedules": [{"schedules": ["unparsable"]}]})),
        ),
        "clean_score": lambda r: (
            setattr(r, "get_active_map_versions", _returns([{"p2map_id": "MAP-1"}])),
            setattr(r, "get_clean_score_raw", _returns(
                {"clean_scores": [{"regions": ["unparsable"]}]})),
        ),
        "automations": lambda r: (
            setattr(r, "get_automations_raw", _returns(["unparsable", "also-not"])),
        ),
    }

    @pytest.mark.parametrize("name", sorted(UNREADABLE))
    def test_an_unreadable_response_is_never_reported_as_empty(self, name):
        from roombapy_prime_tools.verify_writes import NoResult

        result, out = self._feed(name, self.UNREADABLE[name])

        assert "DISAGREEMENT" in out, (
            f"{name} parsed nothing out of a non-empty response and said nothing "
            "about it -- that is the b5 bug: a derived zero hiding a parser miss"
        )
        assert not isinstance(result, list) or isinstance(result, NoResult) or result, (
            f"{name} reported a clean pass for a response it could not read"
        )

    @pytest.mark.parametrize("name", sorted(UNREADABLE))
    def test_the_raw_response_reaches_the_output(self, name):
        """Before any parsing. A parsed count cannot distinguish "the
        server sent nothing" from "we failed to read what it sent"."""
        _, out = self._feed(name, self.UNREADABLE[name])

        assert "unparsable" in out, (
            f"{name} did not print what the server actually sent"
        )


def _returns(value):
    from unittest.mock import AsyncMock

    return AsyncMock(return_value=value)


class TestTheDndReadCheck:
    """Quiet hours are the last unbuilt feature of this line, and the
    obstacle is not demand -- nobody has ever seen a populated response.

    Three accounts all return `status` empty and every other field null,
    because none of those users has quiet hours set. So the model has
    four fields with no example behind any of them, the write body was
    never investigated, and the one live write attempt returned HTTP 400
    from a check resending an empty settings object.
    """

    def _run(self, response=None, effect=None):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _dnd_read

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        if effect is not None:
            robot.get_dnd_settings_raw = AsyncMock(side_effect=effect)
        else:
            robot.get_dnd_settings_raw.return_value = response
        return asyncio.run(_dnd_read(robot, SimpleNamespace(household_id=None)))

    def test_it_writes_nothing(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _dnd_read

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_dnd_settings_raw.return_value = {}
        asyncio.run(_dnd_read(robot, SimpleNamespace(household_id=None)))

        robot.set_dnd_settings.assert_not_awaited()

    def test_a_populated_response_is_a_result(self, capsys):
        """camelCase on the wire -- dailyStart, not daily_start. An
        earlier version of this test used the Python field names and the
        disagreement check caught it, which is what it is for."""
        result = self._run({
            "dailyStart": 1320, "dailyEnd": 420, "status": {"enabled": True},
        })

        assert result == [{"populated_fields": 3}]
        assert "1320" in capsys.readouterr().out

    def test_an_empty_account_says_what_is_actually_needed(self):
        """A valid answer, but not the one that unblocks anything -- and
        the message has to say so, or three more people run it and
        report success."""
        from roombapy_prime_tools.verify_writes import NoResult

        result = self._run({"status": {}, "dailyStart": None})

        assert isinstance(result, NoResult)
        assert "has set them" in result.detail

    def test_a_field_the_model_does_not_know_is_surfaced(self, capsys):
        """The whole point of reading before writing: an unmodelled
        field would be silently dropped on the next write, and this
        library resends DND from the parsed model."""
        from roombapy_prime_tools.verify_writes import NoResult

        result = self._run({"dailyStart": 1320, "unknown_future": "x"})

        assert "DISAGREEMENT" in capsys.readouterr().out
        assert isinstance(result, NoResult)


class TestTheDndReadShowsTheClockTime:
    """The unit is minutes since midnight per the app's machine code
    (hour * 60 + minute, range 0-1439) -- but the formula came from the
    general schedule-conflict check rather than the DND path itself, so
    it is well-founded rather than proven.

    Printing the reading turns the first real response into a
    confirmation or a refutation on the spot: the tester glances at
    their own app instead of doing arithmetic, and there is no second
    round.
    """

    def _run(self, response):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _dnd_read

        robot = AsyncMock()
        robot.get_household_id.return_value = "HH-A"
        robot.get_dnd_settings_raw.return_value = response
        asyncio.run(_dnd_read(robot, SimpleNamespace(household_id=None)))

    def test_a_plausible_value_is_shown_as_a_time(self, capsys):
        self._run({"dailyStart": 1320, "dailyEnd": 450})

        out = capsys.readouterr().out
        assert "22:00" in out
        assert "07:30" in out

    def test_midnight_is_shown_rather_than_skipped(self, capsys):
        """0 is a real time. Anything treating it as falsy would hide
        the one value most likely to be set."""
        self._run({"dailyStart": 0, "dailyEnd": 360})

        assert "00:00" in capsys.readouterr().out

    def test_a_value_outside_the_range_refutes_the_reading(self, capsys):
        """If the unit turns out to be seconds, 79200 shows up here and
        the output says so instead of printing a nonsense time."""
        self._run({"dailyStart": 79200})

        out = capsys.readouterr().out
        assert "NOT a minutes-since-midnight" in out


class TestSettingsRoundtripChangesNothing:
    """Every write resends the value the robot already reports, so a
    success proves the path and a failure costs nothing.

    Worth running because six controls hang on it: a volume slider, the
    charge light ring pattern, mop dry duration, pad wash frequency and
    two evacuation settings -- all listed as user settings in the app's
    own product profiles, none confirmed writable.
    """

    def _run(self, reported, confirm_answer=True):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_settings.return_value = {"state": {"reported": reported}}
        with patch.object(verify_writes, "confirm", return_value=confirm_answer):
            result = asyncio.run(
                verify_writes._settings_roundtrip(robot, SimpleNamespace())
            )
        return result, robot

    def test_each_field_is_resent_at_its_own_value(self):
        _r, robot = self._run({"chrgLrPtrn": 2, "padDryDur": 12, "unrelated": 1})

        sent = {c.args[0]: c.args[1] for c in robot.set_setting.await_args_list}
        assert sent == {"chrgLrPtrn": 2, "padDryDur": 12}

    def test_fields_the_robot_does_not_have_are_not_written(self):
        """The profiles say which model gets which dock setting -- a
        robot with no mop has no pad fields, and that is a result rather
        than a gap."""
        _r, robot = self._run({"chrgLrPtrn": 0})

        assert [c.args[0] for c in robot.set_setting.await_args_list] == ["chrgLrPtrn"]

    def test_declining_writes_nothing(self):
        _r, robot = self._run({"chrgLrPtrn": 1}, confirm_answer=False)

        robot.set_setting.assert_not_awaited()

    def test_a_robot_with_none_of_them_is_a_result_not_an_error(self):
        from roombapy_prime_tools.verify_writes import NoResult

        result, robot = self._run({"name": "Henriette"})

        assert isinstance(result, NoResult)
        robot.set_setting.assert_not_awaited()

    def test_an_unreadable_shadow_does_not_raise(self):
        """EPHEMERAL-tier robots time out on rw-settings entirely."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import NoResult, _settings_roundtrip

        robot = AsyncMock()
        robot.get_settings.return_value = "nope"
        result = asyncio.run(_settings_roundtrip(robot, SimpleNamespace()))

        assert isinstance(result, NoResult)

    def test_a_changed_read_back_is_reported_as_unexpected(self, capsys):
        """Resending the same value must read back the same value. If it
        does not, something else moved and that is worth seeing."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_writes

        robot = AsyncMock()
        robot.get_settings.side_effect = [
            {"state": {"reported": {"chrgLrPtrn": 1}}},
            {"state": {"reported": {"chrgLrPtrn": 9}}},
        ]
        with patch.object(verify_writes, "confirm", return_value=True):
            asyncio.run(verify_writes._settings_roundtrip(robot, SimpleNamespace()))

        assert "CHANGED -- unexpected" in capsys.readouterr().out


class TestReportedSettingsUnwrapping:
    """Three shapes are in play and the first attempt handled none of
    them: a ShadowResponse with a `.state` attribute, a plain dict
    `{"state": {"reported": {...}}}`, and a bare reported dict.

    The wrong version produced the OUTER dict for the middle shape, so
    the probe saw a key list of exactly ["state"] and reported "none of
    these settings exist on this robot" -- a plausible negative result,
    which is the worst kind of wrong.
    """

    def _unwrap(self, raw):
        from roombapy_prime_tools.verify_writes import _reported_settings

        return _reported_settings(raw)

    def test_a_plain_nested_dict(self):
        assert self._unwrap({"state": {"reported": {"a": 1}}}) == {"a": 1}

    def test_an_object_with_state_and_reported(self):
        from types import SimpleNamespace

        raw = SimpleNamespace(state=SimpleNamespace(reported={"a": 1}))
        assert self._unwrap(raw) == {"a": 1}

    def test_a_bare_reported_dict(self):
        assert self._unwrap({"childLock": True}) == {"childLock": True}

    def test_anything_unreadable_yields_none(self):
        for raw in ("nope", None, 7, {"state": "nope"}):
            assert self._unwrap(raw) is None


class TestMissionHistoryShapeCheck:
    """Four Home Assistant sensors read a store only the Classic path
    fills. The Prime path could fill it from this endpoint, and the
    mapping is a small function once the wire shape is known.

    It is not known. The model parses the vendor's own sample from the
    app's raw resources, but no Prime robot has ever been asked, and
    that sample is a Classic-era platform.
    """

    def _run(self, raw):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _mission_history

        robot = AsyncMock()
        robot.blid = "B"
        robot.get_mission_history.return_value = raw
        return asyncio.run(_mission_history(robot, SimpleNamespace()))

    _ENTRY = {
        "robot_id": "B", "nMssn": 36, "startTime": 1589884703,
        "timestamp": 1589884703, "durationM": 12, "sqft": 240,
        "done_raw": "ok", "evacs": 1,
    }

    def test_a_bare_list_parses(self):
        result = self._run([self._ENTRY])

        assert result["raw_count"] == 1
        assert result["parsed_count"] == 1

    def test_an_unknown_envelope_is_called_out(self, capsys):
        """The discriminator this check exists for. The parser tries
        `missions` and `history`, neither confirmed -- so a response full
        of missions can parse to nothing and read exactly like a robot
        with no history."""
        self._run({"responseBody": [self._ENTRY, self._ENTRY]})

        out = capsys.readouterr().out
        assert "the envelope key is wrong, not the history" in out

    def test_a_genuinely_empty_history_says_so(self):
        from roombapy_prime_tools.verify_writes import NoResult

        assert isinstance(self._run([]), NoResult)

    def test_a_known_envelope_parses(self):
        result = self._run({"missions": [self._ENTRY]})

        assert result["parsed_count"] == 1

    def test_nothing_is_sent_to_the_robot(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _mission_history

        robot = AsyncMock()
        robot.blid = "B"
        robot.get_mission_history.return_value = [self._ENTRY]
        asyncio.run(_mission_history(robot, SimpleNamespace()))

        robot.send_simple_command.assert_not_awaited()
        robot.update_schedules.assert_not_awaited()
        robot.set_setting.assert_not_awaited()


class TestOrientationResendsWhatIsThere:
    """The abort notice promises this check "resends what is already
    there", and it did not: it sent the argument default of 0.0
    regardless. @DaRealGuGu's map went from -0.0035 rad to 0.0 --
    invisible on screen, and still a change made by a check that said it
    made none.

    A tool that misdescribes itself is worse than one that refuses: the
    point of these checks is that a tester can trust the summary before
    running them.
    """

    def _run(self, orientation, current=-0.0035279512971639893):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_writes import _set_map_orientation

        robot = AsyncMock()
        robot.blid = "B"
        robot.get_active_map_versions.return_value = [
            {"p2map_id": "M1", "user_orientation_rad": current}
        ]
        args = SimpleNamespace(orientation=orientation, p2map_id="M1")
        asyncio.run(_set_map_orientation(robot, args))
        return robot.set_map_orientation.await_args.args[1]

    def test_without_an_argument_the_current_value_goes_back(self):
        assert self._run(None) == -0.0035279512971639893

    def test_an_explicit_value_is_honoured(self):
        assert self._run(1.5708) == 1.5708

    def test_zero_can_still_be_asked_for_explicitly(self):
        """Straightening a map is a legitimate request -- it just must
        not happen by accident."""
        assert self._run(0.0) == 0.0

    def test_a_map_with_no_orientation_falls_back_to_zero(self):
        assert self._run(None, current=None) == 0.0


class TestTheSettingsShadowIsRead:
    """`get_settings()` returns a ShadowResponse, whose fields are
    `topic` and `payload` -- there is no `state` attribute at all.
    Looking for one fell through to the object itself, found no
    `reported`, and reported "could not read rw-settings -- this robot
    may be EPHEMERAL tier".

    @DaRealGuGu's robot reports rw-settings perfectly well; his own
    diagnostics list it among the seeded shadows. So the check answered
    a question about our attribute name and phrased it as a fact about
    his hardware.

    Six controls were waiting on this check. It had never been able to
    succeed.
    """

    def _read(self, raw):
        from roombapy_prime_tools.verify_writes import _reported_settings

        return _reported_settings(raw)

    def test_the_real_shape_a_robot_returns(self):
        from roombapy_prime.mqtt_client import ShadowResponse

        response = ShadowResponse(
            topic="$aws/things/B/shadow/name/rw-settings/get/accepted",
            payload={"state": {"reported": {"vol": 3, "chrgLrPtrn": 1}}},
        )

        assert self._read(response) == {"vol": 3, "chrgLrPtrn": 1}

    def test_a_plain_dict_with_the_same_wrapping(self):
        assert self._read({"state": {"reported": {"vol": 3}}}) == {"vol": 3}

    def test_a_bare_reported_block(self):
        assert self._read({"vol": 3}) == {"vol": 3}

    def test_nothing_usable_is_still_nothing(self):
        """The skip message stays correct for a robot that genuinely has
        no settings shadow -- it just must not fire for one that does."""
        from roombapy_prime.mqtt_client import ShadowResponse

        assert self._read(None) is None
        assert self._read(ShadowResponse(topic="t", payload="nope")) is None


class TestTheToolSurvivesANarrowConsole:
    """The status lines print U+2713. A cp1252 console cannot encode it,
    so `print()` raised UnicodeEncodeError before the check did any work
    -- the tool died on its own decoration, and the error named an
    encoding rather than anything the tester had done (@utkjmitch).
    """

    def test_both_streams_are_reconfigured(self):
        from unittest.mock import MagicMock, patch

        from roombapy_prime_tools.verify_writes import _survive_a_narrow_console

        out, err = MagicMock(), MagicMock()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            _survive_a_narrow_console()

        out.reconfigure.assert_called_once_with(errors="replace")
        err.reconfigure.assert_called_once_with(errors="replace")

    def test_a_stream_that_cannot_be_reconfigured_is_left_alone(self):
        """Piped and captured streams do not all offer it, and refusing
        to start over that would be the same failure in a new coat."""
        from unittest.mock import MagicMock, patch

        from roombapy_prime_tools.verify_writes import _survive_a_narrow_console

        plain = MagicMock(spec=[])
        with patch("sys.stdout", plain), patch("sys.stderr", plain):
            _survive_a_narrow_console()

    def test_a_refusing_stream_does_not_stop_the_run(self):
        from unittest.mock import MagicMock, patch

        from roombapy_prime_tools.verify_writes import _survive_a_narrow_console

        stream = MagicMock()
        stream.reconfigure.side_effect = ValueError("read-only")
        with patch("sys.stdout", stream), patch("sys.stderr", stream):
            _survive_a_narrow_console()

    def test_it_runs_before_anything_prints(self):
        """The point is that it happens first -- the crash was in the
        very first status line."""
        import inspect

        from roombapy_prime_tools import verify_writes

        body = inspect.getsource(verify_writes.main)
        assert body.splitlines()[1].strip() == "_survive_a_narrow_console()"


class TestTheSettingsRunAsksBeforeItWrites:
    """The prompts used to sit between two MQTT operations, and
    @DaRealGuGu's run failed on exactly the operation after one.

    Worse, prompting per field meant the first failure ended the run: he
    answered one question, it failed, and the other four probes were
    never attempted. Six fields with a question each is six chances to
    lose the remaining ones.
    """

    def _source(self):
        import inspect

        from roombapy_prime_tools.verify_writes import _settings_roundtrip

        return inspect.getsource(_settings_roundtrip)

    def test_every_prompt_happens_before_the_first_write(self):
        source = self._source()
        last_confirm = source.rindex("confirm(")
        first_write = source.index("await robot.set_setting")

        assert last_confirm < first_write

    def test_a_failed_write_does_not_end_the_run(self):
        """One refused write says nothing about the others, and finding
        out costs one more attempt."""
        source = self._source()
        after_failure = source[source.index('"write": "failed"'):]

        assert "continue" in after_failure[:400]
        assert "return" not in after_failure[:200]

    def test_confirming_nothing_writes_nothing(self):
        """Already handled at the end -- an empty result set reports
        "every field was skipped" rather than a success with no
        writes."""
        source = self._source()

        assert 'NoResult("every field was skipped")' in source


class TestTheToolCanProduceItsOwnDebugLog:
    """Asking a tester for a debug log meant telling them to set an
    environment variable this tool does not read, or to wrap the call in
    a Python one-liner. Both were guessed rather than checked.

    A check whose failures need a log should be able to produce one.
    """

    def _run_main(self, argv):
        from unittest.mock import MagicMock, patch

        from roombapy_prime_tools import verify_writes

        with patch.object(verify_writes.sys, "argv", argv), patch.object(
            verify_writes, "logging"
        ) as log, patch.object(
            verify_writes, "_run", MagicMock(), create=True
        ), patch.object(
            verify_writes, "_survive_a_narrow_console", MagicMock()
        ):
            try:
                verify_writes.main()
            except SystemExit:
                pass
            except Exception:
                pass
        return log, argv

    def test_the_flag_turns_debug_on(self):
        log, _ = self._run_main(["prog", "--debug", "settings_roundtrip"])

        assert log.basicConfig.called

    def test_the_flag_is_removed_before_parsing(self):
        """argparse would reject an unknown argument, so the check would
        never run at all."""
        _, argv = self._run_main(["prog", "--debug", "settings_roundtrip"])

        assert "--debug" not in argv

    def test_without_it_nothing_is_configured(self):
        log, _ = self._run_main(["prog", "settings_roundtrip"])

        assert not log.basicConfig.called


class TestTheCandidateKeysAreTheVendorsOwn:
    """Two entries in this list were wrong, and both are the ones that
    failed in the field.

    `audio` should be `audio.volume` — the app addresses the sub-key
    with a dot rather than writing the whole map, and @jouwdan's write
    of `audio` = `{"volume": 100}` got no UPDATE response at all.

    `evacAllowed` is readable and appears in `rw-settings`, but is
    **not** one of the 24 keys `RobotServiceHandler.settingFromKey`
    writes. @DaRealGuGu's write of it was the one that failed on
    re-read.

    Neither is proof — a robot may accept more than its app sends. But
    asking for a key the vendor never writes, and calling the silence a
    bug, is how three testers spent a week on this check.
    """

    #: `RobotServiceHandler.settingFromKey`, app 3.0.0.
    VENDOR_KEYS = {
        "audio.volume", "autoevacFreq", "carpetBoost", "childLock",
        "detergent", "langs2.aSlots", "langs2.dLangs", "langs2.sLang",
        "langs2.sVer", "langs2.uLangs", "mapUploadAllowed", "name",
        "odoaMode", "padDryDur", "padWashAllowed", "padWetness.padPlate",
        "pwAreaInterval", "pwHeat", "pwReturn", "pwTimeInterval",
        "suctionLevel", "swScrub", "timezone", "twoPass",
    }

    #: On every robot seen so far and NOT in the vendor's 24. They are
    #: readable and this check only resends them unchanged, so probing
    #: them is safe -- but it is a different claim from "the app writes
    #: this", and the guard should not blur the two.
    PRESENT_BUT_NOT_VENDOR_WRITTEN = {
        "audio", "ecoCharge", "noAutoPasses", "padWetness",
    }

    def _candidates(self):
        import re
        import pathlib

        source = pathlib.Path(
            "tools/roombapy_prime_tools/verify_writes.py"
        ).read_text()
        # THE WHOLE LIST, not a fixed slice of it.
        #
        # This read 2000 characters from a marker and stopped, so six
        # candidates added later sat outside the window and were never
        # checked. A guard with a hard-coded length silently stops
        # guarding as soon as the thing it guards grows.
        i = source.index("THE VENDOR'S OWN 24 WRITABLE KEYS")
        j = source.index("\n)", i)
        return set(re.findall(r'^\s*\("([a-zA-Z0-9_.]+)",', source[i:j], re.M))

    def test_every_candidate_is_one_the_vendor_writes(self):
        stray = sorted(
            self._candidates()
            - self.VENDOR_KEYS
            - self.PRESENT_BUT_NOT_VENDOR_WRITTEN
        )

        assert not stray, (
            "these are asked for but the app never writes them, so a "
            f"silent robot proves nothing: {stray}"
        )

    def test_the_volume_key_is_dotted(self):
        """`audio.volume` is what iRobot's handler writes.

        Bare `audio` is probed too, and deliberately: every robot lists
        it, no robot lists a dotted key, and @utkjmitch's reading is
        that the dot is write-addressing *into* the bare key rather than
        a key name of its own. Probing both is how that gets settled."""
        assert "audio.volume" in self._candidates()
        assert "audio" in self._candidates()

    def test_evac_allowed_is_gone(self):
        assert "evacAllowed" not in self._candidates()


class TestTheNewCallsHaveChecks:
    """A method nobody can run is a method nobody will confirm.

    `get_firmware_raw()` and `request_mission_timeline()` were built from
    the vendor's model and have never been sent to a robot. Both are
    read-only or publish-only, so there is no reason for a tester to
    have to write a script.
    """

    def _check(self, name):
        from roombapy_prime_tools.verify_writes import CHECKS

        return next((c for c in CHECKS if c.name == name), None)

    def test_the_firmware_read_is_offered(self):
        check = self._check("firmware_catalogue")

        assert check is not None
        # "read", not "safe" -- @utkjmitch found the gate demanding the
        # write flag and telling him the check "writes to your real
        # robot", while its own banner said it writes nothing.
        assert check.risk == "read"

    def test_it_says_a_rejection_is_a_result(self):
        """@utkjmitch got a **403**: the path exists and GET resolves,
        but the consumer Cognito role has no `execute-api:Invoke` on
        it. A tester should not read that as their account being
        broken."""
        check = self._check("firmware_catalogue")

        assert "403" in check.verify_by
        assert "not a failure of your account" in check.verify_by

    def test_the_timeline_request_is_offered(self):
        check = self._check("timeline_request")

        assert check is not None
        assert check.risk == "read"

    def test_it_says_silence_is_expected(self):
        """Updated after @jouwdan watched for a report on a single
        connection and got none in 35 seconds on an idle robot.

        The wording used to say the path had never been sent. It has
        now, twice, and the answer is that an idle robot does not
        reply — so a tester seeing nothing should not report a bug."""
        check = self._check("timeline_request")

        assert "IDLE robot does not do is answer" in check.verify_by
        assert "not a failure" in check.verify_by

    def test_every_check_declares_its_risk(self):
        """So nobody runs a write thinking it reads."""
        from roombapy_prime_tools.verify_writes import CHECKS

        assert all(c.risk for c in CHECKS)


class TestDottedProbeKeysResolveAgainstTheirParent:
    """`audio.volume` and `padWetness.padPlate` are two of the vendor's
    24 writable settings, but the shadow reports `audio` and
    `padWetness` as MAPS — the dotted form never appears as a key of its
    own.

    A plain membership test therefore called both absent on every robot.
    @chairstacker's run printed `not on this robot: chrgLrPtrn,
    audio.volume, pwHeat, padWetness.padPlate` and then, a few lines
    later, `audio = {'volume': 100}`.

    411 tests passed throughout. Nothing here probed a dotted key, so
    nothing could go red — the same shape as the orphaned constants and
    the duplicate PadCategory.
    """

    @staticmethod
    def _shadow():
        return {
            "audio": {"volume": 100},
            "padWetness": {"disposable": 3, "reusable": 1, "padPlate": 4},
            "carpetBoost": True,
        }

    def test_a_dotted_key_is_found_through_its_parent(self):
        from roombapy_prime_tools.verify_writes import _resolve_probes

        present, missing = _resolve_probes(self._shadow())
        found = {key: value for key, _l, _n, value in present}

        assert found.get("audio.volume") == 100
        assert found.get("padWetness.padPlate") == 4
        assert "audio.volume" not in missing
        assert "padWetness.padPlate" not in missing

    def test_a_genuinely_absent_setting_is_still_reported_absent(self):
        """The fix must not turn every probe into a false positive —
        absence is a real result here, and a mop-less robot has no pad
        fields."""
        from roombapy_prime_tools.verify_writes import _resolve_probes

        _present, missing = _resolve_probes({"carpetBoost": True})

        assert "audio.volume" in missing
        assert "padWetness.padPlate" in missing
        assert "pwHeat" in missing

    def test_a_parent_that_is_not_a_map_counts_as_absent(self):
        """A robot reporting `audio` as a scalar has no `audio.volume`
        to write, and walking into it would raise."""
        from roombapy_prime_tools.verify_writes import _resolve_probes

        _present, missing = _resolve_probes({"audio": 100})

        assert "audio.volume" in missing

    def test_plain_keys_are_unaffected(self):
        from roombapy_prime_tools.verify_writes import _resolve_probes

        present, _missing = _resolve_probes(self._shadow())
        found = {key: value for key, _l, _n, value in present}

        assert found.get("carpetBoost") is True

    def test_the_two_range_field_is_no_longer_called_a_boolean(self):
        """`pwReturn` was described as "boolean" in the probe list.
        @chairstacker read `pwReturn = 2` against that hint, saw the
        app's three-option Mop Wash Frequency screen, and reported the
        value as stale. It is neither boolean nor stale — `ReturnByMode`
        has six values across two ranges and 2 is `byArea`."""
        from roombapy_prime_tools.verify_writes import _SETTING_PROBES

        notes = {key: note for key, _label, note in _SETTING_PROBES}

        assert "boolean" not in notes["pwReturn"]
        assert "TWO RANGES" in notes["pwReturn"]
