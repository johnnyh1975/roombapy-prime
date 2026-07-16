"""Schedules and Do-Not-Disturb settings.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .mission_control import RoutineCommand


class ScheduleFrequency(str, Enum):
    """Confirmed (jadx source, @SerialName per value, identical to the
    enum name): only 4 values, no DAILY."""

    BI_WEEKLY = "BI_WEEKLY"
    MONTHLY = "MONTHLY"
    ONCE = "ONCE"
    WEEKLY = "WEEKLY"


@dataclass(frozen=True)
class ScheduleTime:
    """Confirmed (androguard): day (List -- weekdays, the list content
    type not resolvable via the bytecode field signature, presumably
    int or string abbreviation like "MO"/"TU"), hour (Integer), min
    (Integer)."""

    day: list[Any] = field(default_factory=list)
    hour: int | None = None
    min: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"day": self.day}
        if self.hour is not None:
            body["hour"] = self.hour
        if self.min is not None:
            body["min"] = self.min
        return body


@dataclass(frozen=True)
class ScheduleDateEntry:
    """Confirmed (jadx source, @SerialName per field, identical to the
    property name): dayOfMonth, hour, min, month, year -- used for
    ScheduleOptions.after/until (start/end date of a schedule)."""

    day_of_month: int | None = None
    hour: int | None = None
    min: int | None = None
    month: int | None = None
    year: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.day_of_month is not None:
            body["dayOfMonth"] = self.day_of_month
        if self.hour is not None:
            body["hour"] = self.hour
        if self.min is not None:
            body["min"] = self.min
        if self.month is not None:
            body["month"] = self.month
        if self.year is not None:
            body["year"] = self.year
        return body


@dataclass(frozen=True)
class ScheduleOptions:
    """CORRECTED (session 46): real wire keys directly confirmed via
    the `ScheduleOptions$$serializer` companion class's `<clinit>`
    (the same technique that resolved RobotStatusV2 in session 40) --
    a stronger basis than the previous "no @SerialName found, presumed
    = Kotlin property name" guess, which turned out WRONG for four of
    the 17 fields: `robot_id` (not `assetId`), `end_commands` (not
    `endCommands`), `created_time` (not `createdTime`), `force_cloud`
    (not `forceCloud`) -- snake_case, matching the pattern seen almost
    everywhere else in this library's confirmed real data, not the
    camelCase originally guessed. The remaining 13 fields (name,
    frequency, start, end, after, until, commands, enabled, deleted,
    reminder, append, exclude) were already correct.

    UNCERTAINTY: commands/end_commands are only recognizable as "List"
    via the raw bytecode field signature (Java generics type erasure
    at runtime) -- modeled here as List[RoutineCommand], in strong
    analogy to FavoriteV1.command_defs (the same pattern: a schedule
    triggers a RoutineCommand when it fires), but NOT directly
    confirmed via a generic signature. append/exclude similarly
    uncertain (content unknown, left here as a raw list)."""

    asset_id: str | None = None
    name: str | None = None
    frequency: ScheduleFrequency | None = None
    start: ScheduleTime | None = None
    end: ScheduleTime | None = None
    after: ScheduleDateEntry | None = None
    until: ScheduleDateEntry | None = None
    commands: list[RoutineCommand] = field(default_factory=list)
    end_commands: list[RoutineCommand] = field(default_factory=list)
    append: list[Any] = field(default_factory=list)
    exclude: list[Any] = field(default_factory=list)
    created_time: str | None = None
    deleted: bool | None = None
    enabled: bool | None = None
    force_cloud: bool | None = None
    reminder: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.asset_id is not None:
            body["robot_id"] = self.asset_id
        if self.name is not None:
            body["name"] = self.name
        if self.frequency is not None:
            body["frequency"] = self.frequency.value
        if self.start is not None:
            body["start"] = self.start.to_json()
        if self.end is not None:
            body["end"] = self.end.to_json()
        if self.after is not None:
            body["after"] = self.after.to_json()
        if self.until is not None:
            body["until"] = self.until.to_json()
        if self.commands:
            body["commands"] = [c.to_json() for c in self.commands]
        if self.end_commands:
            body["end_commands"] = [c.to_json() for c in self.end_commands]
        if self.append:
            body["append"] = self.append
        if self.exclude:
            body["exclude"] = self.exclude
        if self.created_time is not None:
            body["created_time"] = self.created_time
        if self.deleted is not None:
            body["deleted"] = self.deleted
        if self.enabled is not None:
            body["enabled"] = self.enabled
        if self.force_cloud is not None:
            body["force_cloud"] = self.force_cloud
        if self.reminder is not None:
            body["reminder"] = self.reminder
        return body


@dataclass(frozen=True)
class HouseholdSchedule:
    """CORRECTED (session 46): confirmed directly from
    `HouseholdSchedule$$serializer`'s `<clinit>` -- real key is
    `schedule_id` (snake_case), not `scheduleId` as previously guessed
    from the androguard field name alone. `options` was already
    correct. Used per SchedulesAPI for updateSchedules()
    (List<HouseholdSchedule>)."""

    schedule_id: str
    options: ScheduleOptions

    def to_json(self) -> dict[str, Any]:
        return {"schedule_id": self.schedule_id, "options": self.options.to_json()}


@dataclass(frozen=True)
class HouseholdScheduleUpdate:
    """CORRECTED (session 46): confirmed directly from
    `HouseholdScheduleUpdate$$serializer`'s `<clinit>` -- identical
    field shape to HouseholdSchedule (`schedule_id`, `options`, both
    snake_case) -- a separate class exists in the bytecode, presumably
    for a more specific update context, but the distinction from
    HouseholdSchedule wasn't further resolved."""

    schedule_id: str
    options: ScheduleOptions

    def to_json(self) -> dict[str, Any]:
        return {"schedule_id": self.schedule_id, "options": self.options.to_json()}


@dataclass(frozen=True)
class SchedulesList:
    """NEW (session 51). CONFIRMED via SchedulesList$$serializer:
    household_schedule_id, schedules (a list of HouseholdSchedule,
    presumably -- the generic list element type isn't resolvable via
    the bytecode field signature, but this is the only plausible
    reading given the surrounding class family)."""

    household_schedule_id: str | None = None
    schedules: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SchedulesList":
        return cls(
            household_schedule_id=data.get("household_schedule_id"),
            schedules=data.get("schedules") or [],
        )


@dataclass(frozen=True)
class SchedulesResponse:
    """NEW (session 51) -- the confirmed TOP-LEVEL envelope for
    get_schedules(), previously entirely unmodeled ("Response shape
    (SchedulesList) not modeled -- raw JSON" -- that docstring had
    already found the CLASS NAME, just not its fields). CONFIRMED via
    SchedulesResponse$$serializer: household_schedules (a list of
    SchedulesList, by the same naming-analogy reasoning)."""

    household_schedules: list[SchedulesList] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SchedulesResponse":
        raw = data.get("household_schedules") or []
        return cls(household_schedules=[SchedulesList.from_json(s) for s in raw])


@dataclass(frozen=True)
class DNDStatusResponse:
    """CONFIRMED (session 46, via `DNDStatusResponse$$serializer`'s
    `<clinit>` -- the same technique that resolved ScheduleOptions'
    wrong field names in this same session): dailyStart/dailyEnd
    (Integer, presumably minutes since midnight), endsAt (Long,
    presumably epoch millis for a one-time DND exception), status
    (Map -- structure not investigated). All four keys already matched
    what this class's from_json() used -- unlike ScheduleOptions, no
    fix was needed here, just confirmation. IMPORTANT: DNDSchedule (the
    sealed-class variant with DailySchedule/EndsAt as separate types)
    and DNDStatusResponse (this flat class) are TWO DIFFERENT
    representations -- DNDStatusResponse is likely the actual GET
    response shape (directly referenced by DNDGetRequest callers),
    DNDSchedule is more likely internal for building the PUT request."""

    daily_start: int | None = None
    daily_end: int | None = None
    ends_at: int | None = None
    status: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DNDStatusResponse:
        return cls(
            daily_start=data.get("dailyStart"),
            daily_end=data.get("dailyEnd"),
            ends_at=data.get("endsAt"),
            status=data.get("status") or {},
        )


@dataclass(frozen=True)
class DNDDailySchedule:
    """NEW (session 46). Confirmed directly from
    `DNDSchedule$DailySchedule$$serializer`'s `<clinit>`: dailyStart,
    dailyEnd (both camelCase, matching DNDStatusResponse's own
    confirmed keys for the same concept). This is one of two variants
    of the `DNDSchedule` sealed class used for building the PUT
    request body -- the other is `DNDEndsAt`.

    UNCONFIRMED: how these two variants get wrapped/discriminated
    under `DNDSchedule` itself on the wire. `DNDSchedule`'s own
    `<clinit>` uses a lazy `cachedSerializer` delegate pattern (common
    for Kotlin sealed-class polymorphic serializers) rather than a
    directly-readable discriminator string -- resolving that would
    need deeper native/bytecode tracing than this session pursued, the
    same kind of limit as the V1 edit commands' envelope format
    elsewhere in this file. `set_dnd_settings()` therefore still
    accepts a raw dict rather than this type -- these two dataclasses
    exist for their own confirmed fields, not yet wired into the
    request-building path."""

    daily_start: int
    daily_end: int

    def to_json(self) -> dict[str, Any]:
        return {"dailyStart": self.daily_start, "dailyEnd": self.daily_end}


@dataclass(frozen=True)
class DNDEndsAt:
    """NEW (session 46). Confirmed directly from
    `DNDSchedule$EndsAt$$serializer`'s `<clinit>`: endsAt (Long,
    presumably epoch millis, matching DNDStatusResponse's own
    confirmed key for the same concept) -- the other of the two
    `DNDSchedule` variants, see `DNDDailySchedule`'s docstring for the
    same envelope/discriminator caveat."""

    ends_at: int

    def to_json(self) -> dict[str, Any]:
        return {"endsAt": self.ends_at}


