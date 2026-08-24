"""ID validation, anchored on real IDs.

The alphabet came from the firmware image; these two IDs came from a
live diagnostics download (a mission ID and a config-entry ID). If the
validator rejected either, the validator would be wrong, not the ID.
"""

from __future__ import annotations

from roombapy_prime.ids import id_problem, is_valid_id, normalise_id

# A real mission ID and a real config-entry ID from a live download.
REAL_MISSION_ID = "01M0J6GZJRNEX8TG873FAYEKGF"
REAL_ENTRY_ID = "01M051TEDT68KR7781TSQ0GK6V"


class TestAgainstRealIds:
    def test_real_ids_validate(self):
        assert is_valid_id(REAL_MISSION_ID)
        assert is_valid_id(REAL_ENTRY_ID)

    def test_real_ids_have_no_problem(self):
        assert id_problem(REAL_MISSION_ID) is None
        assert id_problem(REAL_ENTRY_ID) is None

    def test_real_ids_are_the_expected_length(self):
        """26 is not a guess -- both real IDs are exactly that."""
        assert len(REAL_MISSION_ID) == 26
        assert len(REAL_ENTRY_ID) == 26


class TestRejectsMalformed:
    def test_excluded_letters_fail(self):
        """I, L, O, U are not in Crockford base32. An ID carrying one
        is not a transcription of a real ID -- it is wrong."""
        # Replace a valid char with each excluded letter.
        for bad in "ILOU":
            candidate = bad + REAL_MISSION_ID[1:]
            assert not is_valid_id(candidate), bad

    def test_wrong_length_fails(self):
        assert not is_valid_id(REAL_MISSION_ID[:-1])
        assert not is_valid_id(REAL_MISSION_ID + "0")
        assert not is_valid_id("")

    def test_non_strings_fail_without_raising(self):
        for value in (None, 123, [], {}):
            assert not is_valid_id(value)
            assert id_problem(value) is not None


class TestProblemNamesTheFault:
    """id_problem exists so a diagnostic says HOW an ID is malformed."""

    def test_empty(self):
        assert id_problem("") == "empty"

    def test_wrong_length_reports_the_length(self):
        problem = id_problem("01M0")
        assert "wrong length" in problem
        assert "4" in problem

    def test_lowercase_is_singled_out_as_recoverable(self):
        """The one malformation a caller might choose to fix rather
        than reject: the ID is right, only its case is wire-wrong."""
        problem = id_problem(REAL_MISSION_ID.lower())
        assert "lowercase" in problem

    def test_stray_characters_are_listed(self):
        # A hyphen is neither a length nor a case problem.
        candidate = REAL_MISSION_ID[:-1] + "-"
        problem = id_problem(candidate)
        assert "-" in problem


class TestNormalise:
    def test_lowercased_id_normalises_to_the_wire_form(self):
        assert normalise_id(REAL_MISSION_ID.lower()) == REAL_MISSION_ID

    def test_normalising_makes_a_lowercase_id_valid(self):
        assert is_valid_id(normalise_id(REAL_ENTRY_ID.lower()))


class TestScopeIsMissionIdsOnly:
    """The firmware names Crockford base32 for MISSION and DEPLOYMENT
    ids. Other ids in this protocol are different formats entirely, and
    running this validator over them would report every real one as
    malformed.

    These are real values from field reports. If a future change makes
    any of them "valid", the validator has been widened past what it
    was built for.
    """

    # From live diagnostics downloads.
    REAL_P2MAP_IDS = ("DJkG17mVRx2lOkWefteHBg", "BLID-1758329350")
    REAL_P2MAPV_ID = "260607T091458"

    def test_map_ids_are_not_ulids_and_that_is_correct(self):
        for map_id in self.REAL_P2MAP_IDS:
            assert not is_valid_id(map_id), (
                f"{map_id!r} is a real map id; treating it as a ULID would "
                "flag working data as broken"
            )

    def test_map_version_ids_are_timestamps_not_ulids(self):
        assert not is_valid_id(self.REAL_P2MAPV_ID)

    def test_a_real_mission_id_is_the_one_that_validates(self):
        """The contrast is the point: same protocol, different id
        formats, and only this one is a ULID."""
        assert is_valid_id(REAL_MISSION_ID)
