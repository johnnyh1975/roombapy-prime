

class TestCleanScoreAgainstARealResponse:
    """The first capture of `/v1/p2maps/clean-score` (@DaRealGuGu).

    The parser already read every field; four of them were simply not
    declared on the dataclass, so they existed at runtime and not in the
    type. That is the kind of gap that survives until somebody reads the
    class instead of the parser.
    """

    _REGION = {
        "clean_score": 0.523,
        "high_traffic_enum": "normal",
        "last_updated_by": "batch_decay",
        "mission_last_cleaned": {
            "missionId": "01KZ454SBQQ9Y9ZJ9XNQWK5V6G",
            "nMssn": 56, "startTime": 1785772270,
        },
        "mission_last_unfinished": None,
        "region_id": "13",
        "smart_clean_prefs": {
            "carpetBoost": True, "operatingMode": 6, "suctionLevel": 2,
            "swScrub": 0, "twoPass": False,
        },
        "updated_ts": 1785987644,
    }

    def _region(self, **overrides):
        from roombapy_prime.models.map_bundle import CleanScoreRegion

        data = {**self._REGION, **overrides}
        return CleanScoreRegion.from_json(data)

    def test_every_field_of_the_real_payload_lands(self):
        region = self._region()

        assert region.region_id == "13"
        assert region.clean_score == 0.523
        assert region.high_traffic_enum == "normal"
        assert region.last_updated_by == "batch_decay"
        assert region.mission_last_cleaned["nMssn"] == 56
        assert region.smart_clean_prefs["operatingMode"] == 6

    def test_the_unfinished_mission_is_carried_when_there_is_one(self):
        """It answers a question nothing else does: which room did not
        get finished. Two of the four rooms in the first capture have
        one."""
        region = self._region(
            mission_last_unfinished={"nMssn": 62, "startTime": 1785871724}
        )

        assert region.mission_last_unfinished["nMssn"] == 62

    def test_the_value_is_carried_without_being_interpreted(self):
        """WHICH DIRECTION IT RUNS IS NOT ESTABLISHED.

        The one real capture has four rooms and neither reading survives
        all four: two cleaned by the SAME mission read 0.523 and 0.3151,
        so it is not simply time since cleaning, and `last_updated_by`
        says `batch_decay`, which points the other way again.

        So this stays a number the library passes through. Naming it
        cleanliness or dirtiness on this evidence would be a guess
        wearing a label, and an automation built on the wrong reading
        does the opposite of what its author meant."""
        assert self._region(clean_score=0.25).clean_score == 0.25
        assert self._region(clean_score=0.523).clean_score == 0.523

    def test_a_response_carries_its_threshold(self):
        from roombapy_prime.models.map_bundle import CleanScoreResponse

        parsed = CleanScoreResponse.from_json({
            "clean_score_ranges": [0.7],
            "clean_scores": [{"p2map_id": "M1", "regions": [self._REGION]}],
        })

        assert parsed.clean_score_ranges == [0.7]
