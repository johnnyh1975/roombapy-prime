"""Favorites (FavoriteV1) -- saved cleaning routines.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .mission_control import RoutineCommand


class TimeEstimateConfidence(StrEnum):
    """Confirmed from TimeEstimateConfidence, complete list."""

    GOOD_CONFIDENCE = "GOOD_CONFIDENCE"
    POOR_CONFIDENCE = "POOR_CONFIDENCE"
    PARTIAL_CONFIDENCE = "PARTIAL_CONFIDENCE"


class TimeEstimateTimeUnit(StrEnum):
    """Confirmed from TimeEstimateTimeUnit -- both singular and plural
    forms exist as their own values (not an error correction on my
    part, that's how it is in the source code)."""

    HOUR = "hour"
    HOURS = "hours"
    MINUTE = "minute"
    MINUTES = "minutes"
    SECOND = "second"
    SECONDS = "seconds"


@dataclass(frozen=True)
class FavoriteTimeEstimate:
    """Confirmed via androguard bytecode inspection (the base class
    itself wasn't emitted by jadx, no error reported for it -- a
    similar silent failure as with the createFavorite/updateFavorite
    lambdas): confidence (TimeEstimateConfidence), estimate (double),
    unit (TimeEstimateTimeUnit). No @SerialName deviation found for
    the field names themselves -- presumably serialized directly
    under their property name."""

    estimate: float
    unit: TimeEstimateTimeUnit
    confidence: TimeEstimateConfidence

    def to_json(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "unit": self.unit.value,
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class FavoriteV1:
    """Confirmed from FavoriteV1.java (@Serializable, cleanly
    decompiled). Field name mapping from the @SerialName annotations:
      commandDefs -> "commanddefs" (List<RoutineCommand> -- see
      above), creationTimestamp -> "creation_timestamp",
      displayOrder -> "display_order", favoriteId -> "favorite_id",
      lastModified -> "last_modified",
      lastUserModified -> "last_user_modified",
      modificationSecs -> "modification_secs",
      timeEstimates -> "time_estimates", isDefault -> "default",
      isDeleted -> "deleted", isHidden -> "hidden". color/icon/name/
      order/version have NO dedicated @SerialName -- property name
      taken directly."""

    name: str | None = None
    color: str | None = None
    icon: str | None = None
    order: str | None = None
    display_order: int | None = None
    is_default: bool = False
    is_deleted: bool = False
    is_hidden: bool = False
    modification_secs: str | None = None
    version: str | None = None
    command_defs: list[RoutineCommand] = field(default_factory=list)
    creation_timestamp: int | None = None
    last_user_modified: int | None = None
    last_modified: int | None = None
    time_estimates: list[FavoriteTimeEstimate] | None = None
    favorite_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "default": self.is_default,
            "deleted": self.is_deleted,
            "hidden": self.is_hidden,
            "commanddefs": [c.to_json() for c in self.command_defs],
        }
        if self.name is not None:
            body["name"] = self.name
        if self.color is not None:
            body["color"] = self.color
        if self.icon is not None:
            body["icon"] = self.icon
        if self.order is not None:
            body["order"] = self.order
        if self.display_order is not None:
            body["display_order"] = self.display_order
        if self.modification_secs is not None:
            body["modification_secs"] = self.modification_secs
        if self.version is not None:
            body["version"] = self.version
        if self.creation_timestamp is not None:
            body["creation_timestamp"] = self.creation_timestamp
        if self.last_user_modified is not None:
            body["last_user_modified"] = self.last_user_modified
        if self.last_modified is not None:
            body["last_modified"] = self.last_modified
        if self.time_estimates is not None:
            body["time_estimates"] = [t.to_json() for t in self.time_estimates]
        return body


