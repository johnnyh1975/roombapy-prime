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
      "time_estimates": [ ... ],              whole mission
      "pmaps": [{
        "pmap_id": ...,
        "time_estimates": [ ... ],            whole map
        "regions": [{"region_id": "5", "time_estimates": [ ... ]}],
        "zones":   [{"zone_id": "1",   "time_estimates": [ ... ]}]
      }]
    }

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
        return str(self.confidence).upper() in (
            TimeEstimateConfidence.GOOD_CONFIDENCE.value.upper(),
            TimeEstimateConfidence.PARTIAL_CONFIDENCE.value.upper(),
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TimeEstimate:
        return cls(
            estimate=float(data.get("estimate") or 0.0),
            unit=str(data.get("unit") or ""),
            confidence=str(data.get("confidence") or ""),
            params=data.get("params") or {},
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

    @classmethod
    def from_json(cls, data: Any) -> TimeEstimates:
        if not isinstance(data, dict):
            return cls()
        by_map: dict[str, list[TimeEstimate]] = {}
        by_region: dict[str, list[TimeEstimate]] = {}
        by_map_region: dict[tuple[str, str], list[TimeEstimate]] = {}
        by_zone: dict[str, list[TimeEstimate]] = {}

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
