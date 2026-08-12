"""The response of `POST /v1/time-estimates`.

CONFIRMED, and it took an APK pass to get here. The endpoint was
implemented long before anyone knew what came back: the only test used
`{"minutes": 30}`, invented. The real key is `estimate`, and the unit
travels beside it rather than in the name.

The shape, from the vendor's own simulator response in
`base_apigw_sim_responses.json`, with the three innermost names
independently confirmed against `FavoriteTimeEstimate$$serializer`:

    {
      "robot_id": ...,
      "api_version": "v1",
      "smart_maps": [{
        "smart_map_id": ...,
        "areas": [{
          "area_id": "10",
          "area_type": "region",
          "estimates": [{
            "value": 3120, "unit": "seconds",
            "deviation": 0.0, "data_model_version": "app_prime",
            "params": {"operatingMode": 512, "suctionLevel": 1,
                       "swScrub": 1, "twoPass": False}
          }]
        }],
        "cleaning_rates": {"deep": 923.0, "light": 373.0, "standard": 466.0}
      }]
    }

NO WHOLE-MISSION TOTAL. The simulator's shape had one; the real response
does not. A caller wanting a mission duration has to sum the areas it
plans to clean -- which is the honest arithmetic anyway, since a mission
covering three rooms is not the same as one covering all of them.

NO CONFIDENCE FIELD EITHER. `deviation` sits where confidence was
expected, and it reads 0.0 on every one of the 44 estimates in the first
real capture. Whether it ever moves is unknown, so nothing is filtered
on it -- filtering on a field that is always zero would silently drop
everything the day it stops being zero.

THREE LEVELS, AND THEY ARE ALL USEFUL. One request answers both open
questions: a calendar needs the mission total, a progress sensor needs
the per-room split.

SEVERAL ESTIMATES PER ROOM, not one. They differ by `params` -- the
vendor's own sample gives region 5 eighteen minutes with
`{"noKOZ": 0, "twoPass": true}` and thirty-four with `{"twoPass": true}`.
A caller has to say which mode it means, and a mode nobody estimated
simply has no entry. The same arrangement Classic uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .favorites import TimeEstimateConfidence

#: Seconds per unit. The enum carries singular and plural spellings and
#: the payload may use either, so both map here.
_UNIT_SECONDS: dict[str, float] = {
    "second": 1.0, "seconds": 1.0,
    "minute": 60.0, "minutes": 60.0,
    "hour": 3600.0, "hours": 3600.0,
}


@dataclass(frozen=True)
class TimeEstimate:
    """One estimate, for one set of cleaning parameters."""

    estimate: float
    unit: str
    confidence: str
    #: What this estimate assumes -- `twoPass`, `noKOZ` and so on. The
    #: discriminator between several estimates for one room, kept raw
    #: because the full set of keys is not enumerated anywhere.
    params: dict[str, Any] = field(default_factory=dict)
    #: Sits where a confidence value was expected, and read 0.0 on every
    #: estimate of the first real capture. Kept raw because nobody knows
    #: what a non-zero one means.
    deviation: float | None = None

    @property
    def seconds(self) -> float | None:
        """The estimate in seconds, or None for an unrecognised unit.

        None rather than a guess: an estimate silently read as minutes
        when it meant hours is off by sixty and still looks plausible on
        a progress bar.
        """
        factor = _UNIT_SECONDS.get(str(self.unit).lower())
        return None if factor is None else float(self.estimate) * factor

    @property
    def is_confident(self) -> bool:
        """Whether this is worth showing.

        `POOR_CONFIDENCE` exists in the enum, and a progress percentage
        built on one is worse than no percentage: it looks equally
        authoritative and is not.
        """
        # NOTHING IS FILTERED OUT WHEN THERE IS NO CONFIDENCE FIELD,
        # and the live response has none. Treating its absence as poor
        # would discard every estimate a real robot returns.
        if not self.confidence:
            return True
        return str(self.confidence).upper() in (
            TimeEstimateConfidence.GOOD_CONFIDENCE.value.upper(),
            TimeEstimateConfidence.PARTIAL_CONFIDENCE.value.upper(),
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TimeEstimate:
        if not isinstance(data, dict):
            return cls()
        # `value` on the wire. `estimate` was the simulator's name and
        # is kept as a fallback -- it costs one `or` and covers the case
        # where the two shapes turn out to be per-account or
        # per-firmware rather than simulator-versus-live.
        raw = data.get("value")
        if raw is None:
            raw = data.get("estimate")
        return cls(
            estimate=float(raw or 0.0),
            unit=str(data.get("unit") or ""),
            confidence=str(data.get("confidence") or ""),
            params=data.get("params") or {},
            deviation=data.get("deviation"),
        )


def _estimates(data: Any) -> list[TimeEstimate]:
    if not isinstance(data, list):
        return []
    return [TimeEstimate.from_json(e) for e in data if isinstance(e, dict)]


@dataclass(frozen=True)
class TimeEstimates:
    """Every estimate the robot offered, indexed by where it applies."""

    robot_id: str | None = None
    #: The whole mission.
    mission: list[TimeEstimate] = field(default_factory=list)
    #: Per map id.
    by_map: dict[str, list[TimeEstimate]] = field(default_factory=dict)
    #: Per region id, across all maps. Region ids are unique per map
    #: rather than globally, so a household with two maps could in
    #: principle collide here -- no such collision has been observed,
    #: and `by_map_region` below keeps the unambiguous form for callers
    #: that need it.
    by_region: dict[str, list[TimeEstimate]] = field(default_factory=dict)
    by_map_region: dict[tuple[str, str], list[TimeEstimate]] = field(
        default_factory=dict
    )
    by_zone: dict[str, list[TimeEstimate]] = field(default_factory=dict)
    #: Square units per hour, or some such, for each cleaning mode --
    #: `deep`, `light`, `standard`. Carried because the response offers
    #: it; what the numbers measure is not established.
    cleaning_rates: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Any) -> TimeEstimates:
        if not isinstance(data, dict):
            return cls()
        by_map: dict[str, list[TimeEstimate]] = {}
        by_region: dict[str, list[TimeEstimate]] = {}
        by_map_region: dict[tuple[str, str], list[TimeEstimate]] = {}
        by_zone: dict[str, list[TimeEstimate]] = {}
        rates: dict[str, float] = {}

        # THE LIVE SHAPE FIRST. `smart_maps` / `areas` is what a real
        # robot returns; the `pmaps` / `regions` branch below is the
        # simulator's and is kept because it costs one loop.
        for smart_map in data.get("smart_maps") or []:
            if not isinstance(smart_map, dict):
                continue
            map_id = str(smart_map.get("smart_map_id") or "")
            for key, value in (smart_map.get("cleaning_rates") or {}).items():
                if isinstance(value, (int, float)):
                    rates[str(key)] = float(value)
            for area in smart_map.get("areas") or []:
                if not isinstance(area, dict):
                    continue
                area_id = str(area.get("area_id") or "")
                if not area_id:
                    continue
                estimates = _estimates(area.get("estimates"))
                # `area_type` separates rooms from zones in one list.
                if str(area.get("area_type") or "region") == "zone":
                    by_zone[area_id] = estimates
                else:
                    by_region[area_id] = estimates
                    by_map_region[(map_id, area_id)] = estimates

        for pmap in data.get("pmaps") or []:
            if not isinstance(pmap, dict):
                continue
            pmap_id = str(pmap.get("pmap_id") or "")
            if pmap_id:
                by_map[pmap_id] = _estimates(pmap.get("time_estimates"))
            for region in pmap.get("regions") or []:
                if not isinstance(region, dict):
                    continue
                rid = str(region.get("region_id") or "")
                if not rid:
                    continue
                estimates = _estimates(region.get("time_estimates"))
                by_region[rid] = estimates
                by_map_region[(pmap_id, rid)] = estimates
            for zone in pmap.get("zones") or []:
                if not isinstance(zone, dict):
                    continue
                zid = str(zone.get("zone_id") or "")
                if zid:
                    by_zone[zid] = _estimates(zone.get("time_estimates"))

        return cls(
            robot_id=data.get("robot_id"),
            mission=_estimates(data.get("time_estimates")),
            by_map=by_map,
            by_region=by_region,
            by_map_region=by_map_region,
            by_zone=by_zone,
            cleaning_rates=rates,
        )

    @staticmethod
    def best(estimates: list[TimeEstimate], **params: Any) -> TimeEstimate | None:
        """The estimate matching the given cleaning parameters.

        Matched on the params the caller NAMES, ignoring the rest: asking
        for `twoPass=True` accepts an entry that also carries `noKOZ`,
        because a caller that knows about one setting should not have to
        enumerate every other one to get an answer.

        Ties go to the first listed, and an exact match to the shortest
        params -- the vendor's sample offers eighteen minutes with two
        conditions and thirty-four with one, so preferring fewer
        conditions would return the wrong figure for a specific request.
        """
        if not estimates:
            return None
        if not params:
            confident = [e for e in estimates if e.is_confident]
            return (confident or estimates)[0]
        matches = [
            e for e in estimates
            if all(e.params.get(k) == v for k, v in params.items())
        ]
        if not matches:
            return None
        return min(matches, key=lambda e: len(e.params))
