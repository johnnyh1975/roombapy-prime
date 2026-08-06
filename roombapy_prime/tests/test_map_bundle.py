

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

    def test_higher_means_dirtier(self):
        """Settled by an eleven-room account (@jouwdan): the value tracks
        how long ago each room was last cleaned. Three rooms cleaned by
        the newest mission read exactly 0.0 with
        `last_updated_by: batch_decay_skipped`; the room untouched since
        mission 12 reads 0.6973, approaching the 0.7 threshold.

        A four-room account had looked ambiguous because two rooms
        shared a mission and differed anyway -- room size and traffic
        move the rate, not the direction.

        Pinned as a fact about the data. If it ever inverts, this is
        where it should be noticed."""
        just_cleaned = self._region(clean_score=0.0,
                                    last_updated_by="batch_decay_skipped")
        long_ago = self._region(clean_score=0.6973)

        assert long_ago.clean_score > just_cleaned.clean_score

    def test_the_value_is_carried_without_being_interpreted(self):
        """The library passes the number through unchanged; naming it is
        the caller's business, and the caller now knows which way it
        runs."""
        assert self._region(clean_score=0.25).clean_score == 0.25
        assert self._region(clean_score=0.523).clean_score == 0.523

    def test_a_response_carries_its_threshold(self):
        from roombapy_prime.models.map_bundle import CleanScoreResponse

        parsed = CleanScoreResponse.from_json({
            "clean_score_ranges": [0.7],
            "clean_scores": [{"p2map_id": "M1", "regions": [self._REGION]}],
        })

        assert parsed.clean_score_ranges == [0.7]
