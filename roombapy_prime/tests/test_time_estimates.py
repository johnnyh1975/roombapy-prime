"""The response of `POST /v1/time-estimates`.

The endpoint was implemented long before anyone knew what came back:
the only test used `{"minutes": 30}`, invented. The real key is
`estimate`, and the unit travels beside it rather than in the name.

Tested against the vendor's own simulator response, three levels deep.
"""

import pytest

_SAMPLE = {
    "robot_id": "BLID",
    "time_estimates": [
        {"unit": "minute", "estimate": 84, "confidence": "GOOD_CONFIDENCE",
         "params": {}},
    ],
    "pmaps": [{
        "pmap_id": "M1",
        "time_estimates": [
            {"unit": "minute", "estimate": 80, "confidence": "GOOD_CONFIDENCE",
             "params": {}},
        ],
        "regions": [{
            "region_id": "5",
            "time_estimates": [
                {"unit": "minute", "estimate": 18,
                 "confidence": "PARTIAL_CONFIDENCE",
                 "params": {"noKOZ": 0, "twoPass": True}},
                {"unit": "minute", "estimate": 34,
                 "confidence": "PARTIAL_CONFIDENCE",
                 "params": {"twoPass": True}},
            ],
        }],
        "zones": [{
            "zone_id": "1",
            "time_estimates": [
                {"unit": "minute", "estimate": 5,
                 "confidence": "GOOD_CONFIDENCE", "params": {}},
            ],
        }],
    }],
}


def _parsed():
    from roombapy_prime.models import TimeEstimates

    return TimeEstimates.from_json(_SAMPLE)


class TestAllThreeLevels:
    """One request answers both open questions: a calendar needs the
    mission total, a progress sensor needs the per-room split."""

    def test_the_mission_total(self):
        assert _parsed().mission[0].estimate == 84

    def test_per_map(self):
        assert _parsed().by_map["M1"][0].estimate == 80

    def test_per_region_and_per_zone(self):
        parsed = _parsed()

        assert len(parsed.by_region["5"]) == 2
        assert parsed.by_zone["1"][0].estimate == 5

    def test_regions_are_also_reachable_unambiguously(self):
        """Region ids are unique per map rather than globally, so a
        household with two maps could collide in the flat index."""
        assert _parsed().by_map_region[("M1", "5")][0].estimate == 18


class TestSeveralEstimatesPerRoom:
    """They differ by `params`. The vendor's own sample gives region 5
    eighteen minutes with two conditions and thirty-four with one, so a
    caller has to say which mode it means."""

    def _best(self, **params):
        from roombapy_prime.models import TimeEstimates

        return TimeEstimates.best(_parsed().by_region["5"], **params)

    def test_naming_a_condition_selects_its_estimate(self):
        assert self._best(noKOZ=0).estimate == 18

    def test_a_broader_request_gets_the_broader_estimate(self):
        """`twoPass=True` matches both entries; the one with fewer
        conditions is the answer, because preferring fewer would
        otherwise return the narrow figure for a general question."""
        assert self._best(twoPass=True).estimate == 34

    def test_a_mode_nobody_estimated_yields_nothing(self):
        """Not the nearest match. A wrong duration is worse than none --
        it looks equally authoritative."""
        assert self._best(threePass=True) is None

    def test_no_params_gives_a_confident_estimate(self):
        assert self._best() is not None


class TestUnitsAndConfidence:
    def _estimate(self, **kwargs):
        from roombapy_prime.models import TimeEstimate

        base = {"estimate": 10, "unit": "minute", "confidence": "GOOD_CONFIDENCE"}
        base.update(kwargs)
        return TimeEstimate.from_json(base)

    def test_the_unit_comes_from_the_payload(self):
        """It is not assumed. The enum knows singular and plural of
        seconds, minutes and hours, and the payload may use either."""
        assert self._estimate(unit="minute").seconds == 600
        assert self._estimate(unit="minutes").seconds == 600
        assert self._estimate(unit="hour").seconds == 36000
        assert self._estimate(unit="seconds").seconds == 10

    def test_an_unrecognised_unit_yields_nothing(self):
        """Rather than a guess. An estimate read as minutes when it meant
        hours is off by sixty and still looks plausible."""
        assert self._estimate(unit="fortnights").seconds is None

    def test_poor_confidence_is_not_worth_showing(self):
        """A progress percentage built on one is worse than none: it
        looks equally authoritative and is not."""
        assert self._estimate(confidence="POOR_CONFIDENCE").is_confident is False
        assert self._estimate(confidence="GOOD_CONFIDENCE").is_confident is True
        assert self._estimate(confidence="PARTIAL_CONFIDENCE").is_confident is True


class TestMalformedResponses:
    """Runs against a cloud endpoint whose shape was unknown until an
    APK pass. Nothing here should raise."""

    def _parse(self, payload):
        from roombapy_prime.models import TimeEstimates

        return TimeEstimates.from_json(payload)

    @pytest.mark.parametrize("payload", [None, [], "nope", 7, {}])
    def test_nothing_usable_gives_an_empty_result(self, payload):
        parsed = self._parse(payload)

        assert parsed.mission == []
        assert parsed.by_region == {}

    def test_entries_without_ids_are_skipped(self):
        parsed = self._parse({"pmaps": [{"regions": [{"time_estimates": []}]}]})

        assert parsed.by_region == {}

    def test_a_map_with_no_regions_is_fine(self):
        parsed = self._parse({"pmaps": [{"pmap_id": "M1"}]})

        assert parsed.by_map["M1"] == []
