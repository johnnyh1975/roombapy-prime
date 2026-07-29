

class TestRingClosingPointIsDropped:
    """REAL FIELD FAILURE (DaRealGuGu). Resending two untouched zones
    came back HTTP 500 -- a server error rather than a 400, which fits
    a payload that parses but then breaks something downstream.

    The cause: a GeoJSON LinearRing repeats its first coordinate as its
    last, so a rectangle read from policyZones.geojson arrives as FIVE
    points. The V1 wire format takes four -- as this module's own
    VirtualWallRectangleV1 docstring has said all along, from APK
    decompilation. We passed the ring through unchanged.

    A documented format and an undocumented assumption disagreed, and
    nothing checked."""

    # Coordinates taken verbatim from the failing field payload.
    _KEEP_OUT = [
        (2.0463, 1.5354), (4.0463, 1.5354), (4.0463, 3.5354),
        (2.0463, 3.5354), (2.0463, 1.5354),
    ]
    _NO_MOP = [
        (2.6731, -2.7257), (4.6731, -2.7257), (4.6731, -0.7257),
        (2.6731, -0.7257), (2.6731, -2.7257),
    ]

    def _polygon(self, ring):
        from roombapy_prime.models.geometry import Polygon

        return Polygon(coordinates=[ring])

    def test_a_rectangle_serialises_to_four_points(self):
        from roombapy_prime.models.map_editing import VirtualWallRectangleV1

        out = VirtualWallRectangleV1(
            wall_id="1", polygon=self._polygon(self._KEEP_OUT)
        ).to_json()

        assert len(out) == 10, "id + type + 4 points = 10 elements"
        assert out[:2] == ["1", 1]

    def test_a_no_mop_zone_serialises_the_same_way(self):
        from roombapy_prime.models.map_editing import VirtualWallNoMopZoneV1

        out = VirtualWallNoMopZoneV1(
            wall_id="2", polygon=self._polygon(self._NO_MOP)
        ).to_json()

        assert len(out) == 10
        assert out[:2] == ["2", 6], "only the discriminator differs"

    def test_the_duplicated_closing_point_is_gone(self):
        from roombapy_prime.models.map_editing import VirtualWallRectangleV1

        out = VirtualWallRectangleV1(
            wall_id="1", polygon=self._polygon(self._KEEP_OUT)
        ).to_json()

        first_point = tuple(out[2:4])
        last_point = tuple(out[-2:])
        assert first_point != last_point

    def test_an_already_open_ring_is_left_alone(self):
        """Only a genuinely closed ring gets trimmed -- an open one is
        already the right shape and must not lose a corner."""
        from roombapy_prime.models.map_editing import VirtualWallRectangleV1

        open_ring = self._KEEP_OUT[:-1]

        out = VirtualWallRectangleV1(
            wall_id="1", polygon=self._polygon(open_ring)
        ).to_json()

        assert len(out) == 10

    def test_a_repeated_point_that_is_not_the_closure_survives(self):
        """Trimming must key on the ring being closed, not on any
        duplicate appearing anywhere in it."""
        from roombapy_prime.models.map_editing import VirtualWallRectangleV1

        ring = [(0.0, 0.0), (1.0, 1.0), (1.0, 1.0), (2.0, 0.0)]

        out = VirtualWallRectangleV1(
            wall_id="1", polygon=self._polygon(ring)
        ).to_json()

        assert len(out) == 10, "four points kept, including the interior duplicate"


class TestVirtualWallWireFormatAgainstSecondAPKRead:
    """The wall array format, verified against an independent second
    reading of the app's custom VirtualWall serializer.

    Worth doing because this is the ONE map-edit command that has never
    been observed on the wire. rename_room is confirmed live; every
    detail of set_virtual_wall comes from decompilation, so a second
    independent read is the closest thing to corroboration available.

    Result: the format was already correct. All three type codes match,
    the degenerate-quadrilateral encoding for Linear matches, and a
    real field payload matches element for element. That is a NEGATIVE
    result for the HTTP 500 investigation and a useful one -- it moves
    the remaining suspicion off the wall array entirely."""

    def _polygon(self, ring):
        from roombapy_prime.models.geometry import Polygon

        return Polygon(coordinates=[ring])

    def test_the_three_type_codes(self):
        """1 and 6 were already confirmed against real zone data from
        three accounts. 2 for Linear had never been corroborated
        anywhere -- no tester has a linear wall configured."""
        from roombapy_prime.models.map_editing import (
            VirtualWallLinearV1,
            VirtualWallNoMopZoneV1,
            VirtualWallRectangleV1,
        )

        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

        assert VirtualWallLinearV1(
            wall_id="1", from_pos=(0.0, 0.0), to_pos=(1.0, 1.0)
        ).to_json()[1] == 2
        assert VirtualWallRectangleV1("1", self._polygon(square)).to_json()[1] == 1
        assert VirtualWallNoMopZoneV1("1", self._polygon(square)).to_json()[1] == 6

    def test_linear_encodes_a_line_as_a_degenerate_quadrilateral(self):
        """from -> to -> to -> from. Not an obvious shape to guess: it
        exists so a two-point line fits the same fixed 10-element schema
        as the two area types."""
        from roombapy_prime.models.map_editing import VirtualWallLinearV1

        out = VirtualWallLinearV1(
            wall_id="w", from_pos=(1.0, 2.0), to_pos=(3.0, 4.0)
        ).to_json()

        assert out == ["w", 2, 1.0, 2.0, 3.0, 4.0, 3.0, 4.0, 1.0, 2.0]

    def test_every_type_produces_exactly_ten_elements(self):
        """id + type + four (x,y) pairs. The schema is fixed, which is
        why the app truncates rather than trusting its input."""
        from roombapy_prime.models.map_editing import (
            VirtualWallLinearV1,
            VirtualWallNoMopZoneV1,
            VirtualWallRectangleV1,
        )

        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

        assert len(VirtualWallLinearV1("1", (0.0, 0.0), (1.0, 1.0)).to_json()) == 10
        assert len(VirtualWallRectangleV1("1", self._polygon(square)).to_json()) == 10
        assert len(VirtualWallNoMopZoneV1("1", self._polygon(square)).to_json()) == 10

    def test_take_four_truncates_a_larger_polygon(self):
        """CORRECTED THIS SESSION. The rule was "drop the closing point
        if the ring is closed", which gives identical output for a
        rectangle read out of policyZones -- so field payloads looked
        right and the difference stayed invisible.

        A five-point open polygon produced 12 elements here against the
        app's 10. The app takes exactly four points regardless."""
        from roombapy_prime.models.map_editing import VirtualWallRectangleV1

        pentagon = [(1.0, 1.0), (2.0, 1.0), (3.0, 1.5), (2.0, 2.0), (1.0, 2.0)]

        out = VirtualWallRectangleV1("1", self._polygon(pentagon)).to_json()

        assert len(out) == 10
        assert out[2:] == [1.0, 1.0, 2.0, 1.0, 3.0, 1.5, 2.0, 2.0]

    def test_interior_rings_are_ignored(self):
        """Only coordinates[0]. A Polygon can carry holes and nothing
        else in this file would have dropped them."""
        from roombapy_prime.models.geometry import Polygon
        from roombapy_prime.models.map_editing import VirtualWallRectangleV1

        outer = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        hole = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]

        out = VirtualWallRectangleV1(
            "1", Polygon(coordinates=[outer, hole])
        ).to_json()

        assert out[2:] == [0.0, 0.0, 4.0, 0.0, 4.0, 4.0, 0.0, 4.0]

    def test_a_real_field_payload_round_trips_unchanged(self):
        """DaRealGuGu's actual NoMopZone, verbatim. If this ever stops
        matching, the format changed -- and that robot's server rejects
        the command for a reason that is NOT the wall array."""
        from roombapy_prime.models.map_editing import VirtualWallNoMopZoneV1

        ring = [(2.5836, 0.9459), (4.5836, 0.9459), (4.5836, 2.9459), (2.5836, 2.9459)]

        assert VirtualWallNoMopZoneV1("1", self._polygon(ring)).to_json() == [
            "1", 6, 2.5836, 0.9459, 4.5836, 0.9459, 4.5836, 2.9459, 2.5836, 2.9459,
        ]


class TestSetVirtualWallsPayload:
    """The virwall array and its leading count.

    THE COUNT IS THE FIX FOR MONTHS OF HTTP 500s. The array starts with
    the number of walls as an Int, then the wall arrays:

        "virwall": [2, [...], [...]]

    Confirmed from the app's CommandSerializer bytecode -- one
    JsonArray, walls.size() added first, walls appended after. The
    server reads position 0 expecting a number, found an array, and
    failed while deserialising a body that was otherwise valid JSON.
    Hence 500 rather than 400.

    It also explains why no field test could narrow it down: wall
    count, zone types, account and map version were all irrelevant,
    because the payload failed at element zero.

    Not a general convention -- adjust_furniture is equally list-based
    and has no counter. This is the only .size() call in the entire
    serializer.

    THE LIST IS ALSO REPLACED WHOLESALE, and all three zone kinds share
    it. Sending only the wall you want to add deletes every keep-out
    and no-mop zone on the map, silently. Now doubly so: a partial list
    changes both the contents and the count."""

    def _wall(self, wall_id):
        from roombapy_prime.models.geometry import Polygon
        from roombapy_prime.models.map_editing import VirtualWallRectangleV1

        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        return VirtualWallRectangleV1(wall_id, Polygon(coordinates=[square]))

    def _virwall(self, walls):
        from roombapy_prime.models.map_editing import SetVirtualWallsV1

        return SetVirtualWallsV1(walls=walls).to_v1_command_body()["params"]["virwall"]

    def test_the_first_element_is_the_wall_count(self):
        virwall = self._virwall([self._wall("1"), self._wall("2")])

        assert virwall[0] == 2

    def test_the_count_matches_the_walls_actually_sent(self):
        """A partial list changes both -- which is the second reason
        sending one wall of three is destructive rather than merely
        incomplete."""
        for n in (1, 2, 3):
            virwall = self._virwall([self._wall(str(i)) for i in range(n)])
            assert virwall[0] == n
            assert len(virwall) == n + 1

    def test_an_empty_list_still_carries_a_count(self):
        """Clearing every zone is a legitimate edit, and the count has
        to say zero rather than the array being empty."""
        assert self._virwall([]) == [0]

    def test_the_walls_follow_the_count_unchanged(self):
        virwall = self._virwall([self._wall("7")])

        assert virwall[1][0] == "7"
        assert len(virwall[1]) == 10

    def test_all_three_kinds_share_one_array(self):
        """Why a partial list is destructive rather than incomplete:
        no-mop and keep-out zones are not separate fields that would be
        left alone."""
        from roombapy_prime.models.geometry import Polygon
        from roombapy_prime.models.map_editing import (
            VirtualWallLinearV1,
            VirtualWallNoMopZoneV1,
        )

        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        virwall = self._virwall([
            self._wall("1"),
            VirtualWallNoMopZoneV1("2", Polygon(coordinates=[square])),
            VirtualWallLinearV1("3", (0.0, 0.0), (1.0, 1.0)),
        ])

        assert virwall[0] == 3
        assert [entry[1] for entry in virwall[1:]] == [1, 6, 2]

    def test_the_command_name_is_singular(self):
        """set_virtual_wall, not SetVirtualWalls as the class name
        suggests."""
        from roombapy_prime.models.map_editing import SetVirtualWallsV1

        body = SetVirtualWallsV1(walls=[self._wall("1")]).to_v1_command_body()

        assert body["command"] == "set_virtual_wall"
        assert "virwall" in body["params"]
