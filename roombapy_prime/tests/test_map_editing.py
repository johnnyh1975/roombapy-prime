

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
