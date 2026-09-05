"""An ad-hoc polygon's metadata is optional, because furniture is.

`CommandPolygonMetadata.furniture_id` references a real furniture item
on the robot's map -- "clean around this couch". A robot that creates no
furniture objects has no valid value for it, which @chairstacker's Combo
405 turned out to be.

The guard meant to make malformed input safe used to be `return cls()`,
and `furniture_id` has no default. So the safe path raised TypeError:
a fallback that could not construct the class it returned.
"""
from __future__ import annotations

from roombapy_prime.models.mission_control import CommandPolygonMetadata


class TestMetadataParsing:
    def test_a_real_block_parses(self) -> None:
        meta = CommandPolygonMetadata.from_json({"furniture_id": 7})

        assert meta is not None
        assert meta.furniture_id == 7

    def test_a_non_dict_is_none_not_an_exception(self) -> None:
        """The case the old fallback was written for, and crashed on."""
        assert CommandPolygonMetadata.from_json("nonsense") is None  # type: ignore[arg-type]
        assert CommandPolygonMetadata.from_json(None) is None  # type: ignore[arg-type]

    def test_a_dict_without_the_key_is_none(self) -> None:
        """A robot with no furniture sends no furniture id.

        Reading it out with `data["furniture_id"]` would raise KeyError
        on a block that is simply empty.
        """
        assert CommandPolygonMetadata.from_json({}) is None
        assert CommandPolygonMetadata.from_json({"other": 1}) is None

    def test_the_class_still_has_no_default(self) -> None:
        """Guard the reason the fallback was wrong.

        If someone gives `furniture_id` a default to make `cls()` work,
        an ad-hoc polygon could be built silently referencing furniture
        item 0 -- which is a real id on some maps.
        """
        import dataclasses

        field = dataclasses.fields(CommandPolygonMetadata)[0]
        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING
