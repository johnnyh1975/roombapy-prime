

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
