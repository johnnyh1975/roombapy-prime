

class TestUnknownScheduleFieldsSurviveAWrite:
    """The server is ahead of the app this model was built from.

    `is_smart_clean_fav` arrives on real schedules and appears nowhere in
    APK 2.2.4 — neither in Kotlin nor natively. Writing a schedule is
    read-modify-write, so without a passthrough every write silently
    drops whatever the server knows and the app does not.

    **And the loss is invisible**: the request is accepted, and the field
    simply stops coming back.
    """

    def _round_trip(self, raw):
        from roombapy_prime.models.schedules_dnd import ScheduleOptions

        return ScheduleOptions.from_json(raw).to_json()

    def test_the_field_that_prompted_this(self):
        out = self._round_trip({
            "robot_id": "B", "enabled": True, "is_smart_clean_fav": True,
        })

        assert out["is_smart_clean_fav"] is True

    def test_a_field_nobody_has_seen_yet(self):
        """The point is not this one key -- it is that the next one costs
        nothing."""
        out = self._round_trip({"robot_id": "B", "somethingNewIn2027": {"a": 1}})

        assert out["somethingNewIn2027"] == {"a": 1}

    def test_known_fields_still_win(self):
        """The unknown ones are written first, so a named field always
        overrides -- our understanding of a key we model should not be
        overwritten by a stale copy of it."""
        out = self._round_trip({"robot_id": "B", "name": "Kitchen"})

        assert out["name"] == "Kitchen"
        assert out["robot_id"] == "B"

    def test_the_sixteen_known_keys_are_not_duplicated(self):
        """A named key must not also land in the passthrough, or a later
        change to how we serialise it would be shadowed by the raw copy."""
        from roombapy_prime.models.schedules_dnd import ScheduleOptions

        parsed = ScheduleOptions.from_json({
            "robot_id": "B", "name": "x", "enabled": True, "frequency": "WEEKLY",
            "deleted": False, "reminder": 5, "force_cloud": True,
            "created_time": "2026-08-09",
        })

        assert parsed.unknown_fields == {}

    def test_nothing_extra_means_nothing_added(self):
        out = self._round_trip({"robot_id": "B", "name": "x"})

        assert set(out) == {"robot_id", "name"}


class TestTheScheduleEndpointsHaveTwoTraps:
    """Both found in the field by @Nguyen, on a Combo, against a custom
    stack rather than Home Assistant -- so the cloud path, not the
    Classic MQTT one.

    Both are shaped the same way: the API accepts what you send and the
    result is wrong rather than refused.
    """

    def test_a_batch_create_is_warned_about(self, caplog) -> None:
        """`create_schedules(hh, [a, b])` returns 200 and keeps only
        `b`. The signature takes a list and the body is an array, so
        nothing about the call site suggests otherwise -- and a silently
        dropped schedule leaves no trace to notice.

        Warned rather than refused: the endpoint does accept the
        request, and raising would break a caller that passes a
        one-item list built elsewhere.
        """
        import asyncio
        import logging
        from unittest.mock import AsyncMock, MagicMock

        from roombapy_prime.rest_client import PrimeRestClient

        client = PrimeRestClient.__new__(PrimeRestClient)
        client._http_base_auth = "https://example.invalid"
        client._request = AsyncMock(return_value={})

        one = MagicMock()
        one.to_json.return_value = {"name": "Wed spot vacuum"}

        with caplog.at_level(logging.WARNING):
            asyncio.run(client.create_schedules("hh1", [one, one]))

        assert "only the last" in caplog.text

    def test_a_single_create_says_nothing(self, caplog) -> None:
        """The normal call must stay quiet, or the warning becomes noise
        and gets filtered."""
        import asyncio
        import logging
        from unittest.mock import AsyncMock, MagicMock

        from roombapy_prime.rest_client import PrimeRestClient

        client = PrimeRestClient.__new__(PrimeRestClient)
        client._http_base_auth = "https://example.invalid"
        client._request = AsyncMock(return_value={})

        one = MagicMock()
        one.to_json.return_value = {"name": "Wed spot vacuum"}

        with caplog.at_level(logging.WARNING):
            asyncio.run(client.create_schedules("hh1", [one]))

        assert "only the last" not in caplog.text

    def test_delete_documents_which_of_the_two_ids_it_wants(self) -> None:
        """A create response carries `household_schedule_id` and a
        nested `schedule_id` that is the same string plus a robot
        suffix. The nested one returns HTTP 500 -- which reads like a
        server fault rather than a wrong argument, and cost @Nguyen a
        round to work out.

        Read from the docstring: the behaviour lives on iRobot's server,
        so the only thing this library can hold is the knowledge.
        """
        import inspect

        from roombapy_prime.rest_client import PrimeRestClient

        doc = inspect.getdoc(PrimeRestClient.delete_schedule) or ""

        assert "household_schedule_id" in doc
        assert "500" in doc, "the failure mode is what makes it hard to place"
