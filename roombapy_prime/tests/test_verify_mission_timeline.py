"""Tests for verify_mission_timeline.py -- the new diagnostic script for
capturing whatever arrives on the mission-timeline topic(s) during a
real, actively-running mission (see the module's own docstring for the
full context: prompted by a live idle-vs-mid-mission DIFF of
get_state() -- two point-in-time snapshots compared, not a test of
watch_state()'s own persistent delta subscription, which has never
actually been run live during a mission; see watch_state()'s own
docstring for that correction)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from roombapy_prime.diagnostics import Report
from roombapy_prime.verify_mission_timeline import (
    _add_topic_grouped_views,
    _build_watch_specs,
    _confirm,
    _watch_one,
)


def _fake_robot() -> MagicMock:
    robot = MagicMock()
    robot._irbt_topic_prefix = "v011-irbthbu"
    robot.blid = "BLID123"
    return robot


def test_build_watch_specs_defaults_to_the_two_base_topics() -> None:
    robot = _fake_robot()

    specs = _build_watch_specs(robot, watch_wildcard=False, watch_shadow_delta=False)

    labels = [label for _factory, label in specs]
    assert labels == ["mission/timeline/report", "rejected/report"]


def test_build_watch_specs_adds_irbt_wildcard_when_requested() -> None:
    robot = _fake_robot()

    specs = _build_watch_specs(robot, watch_wildcard=True, watch_shadow_delta=False)

    labels = [label for _factory, label in specs]
    assert "v011-irbthbu/things/BLID123/#" in labels


def test_build_watch_specs_adds_shadow_delta_when_requested() -> None:
    """NEW (this session, parallel reverse-engineering track) --
    watch_state() has existed for a while but was never run live during
    an active mission; this flag exists to actually test that now.
    SAFE by design: subscribes to exactly one specific,
    AWS-documented shadow topic, not a wildcard on the reserved "$aws/"
    namespace -- see _build_watch_specs()'s own docstring for why that
    distinction matters (a real field incident, not just theory)."""
    robot = _fake_robot()

    specs = _build_watch_specs(robot, watch_wildcard=False, watch_shadow_delta=True)

    factories = dict(specs)
    labels = list(factories.values())
    assert "$aws/things/{blid}/shadow/update/delta" in labels
    # The factory for this one is robot.watch_state itself (not a lambda
    # wrapper, unlike the wildcard ones) -- assert that specifically.
    matching = [f for f, label in specs if label == "$aws/things/{blid}/shadow/update/delta"]
    assert matching == [robot.watch_state]


def test_build_watch_specs_adds_rrtp_candidate_when_requested() -> None:
    """NEW (this session): the position/pose candidate topic found via
    decompilation of createRobotPositionTopic() -- see the module
    docstring for the full evidence trail. Uses watch_raw_topic() (a
    lambda wrapper), same pattern as the wildcard topic, not a
    dedicated PrimeRobot method yet -- this stays a diagnostic-script-
    only candidate until live-confirmed."""
    robot = _fake_robot()

    specs = _build_watch_specs(robot, watch_wildcard=False, watch_shadow_delta=False, watch_rrtp_candidate=True)

    labels = [label for _factory, label in specs]
    assert "v011-irbthbu/things/BLID123/mission/rrtp/report/update" in labels


def test_build_watch_specs_no_longer_accepts_watch_aws_tree() -> None:
    """REMOVED (this session, real field incident): a --watch-aws-tree
    flag briefly wildcard-subscribed to the entire reserved "$aws/"
    namespace. AWS's own documentation explicitly warns against this
    ("avoid wild card subscriptions to shadow topics... avoid
    subscribing to topic filters like $aws/things/thingName/shadow/#",
    and "unsupported publish or subscribe operations to reserved
    topics can result in a terminated connection"). A field tester hit
    exactly this: a hung run needing Ctrl+C, followed by a SEPARATE,
    later process failing all four named-shadow GETs with timeouts --
    consistent with a degraded/terminated connection, not just a local
    hang. This test asserts the parameter is gone, not just unused --
    a stray keyword argument should raise, not silently succeed."""
    robot = _fake_robot()

    with pytest.raises(TypeError):
        _build_watch_specs(robot, watch_wildcard=False, watch_shadow_delta=False, watch_aws_tree=True)


def test_build_watch_specs_remaining_flags_combine_without_interfering() -> None:
    robot = _fake_robot()

    specs = _build_watch_specs(robot, watch_wildcard=True, watch_shadow_delta=True)

    labels = [label for _factory, label in specs]
    assert labels == [
        "mission/timeline/report",
        "rejected/report",
        "v011-irbthbu/things/BLID123/#",
        "$aws/things/{blid}/shadow/update/delta",
    ]


@pytest.mark.parametrize(
    "answer,expected",
    [("y", True), ("Y", True), ("yes", True), ("ja", True), ("j", True),
     ("n", False), ("", False), ("no", False), ("anything else", False)],
)
def test_confirm_only_accepts_explicit_affirmatives(answer, expected, monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert _confirm("Proceed?") is expected


async def _fake_agen(payloads: list[dict], topic: str = "some/topic"):
    for p in payloads:
        response = MagicMock()
        response.topic = topic
        response.payload = p
        yield response


class _FakeAsyncGenWrapper:
    """Wraps an async generator function so .aclose() can be asserted
    on -- MagicMock's auto-generated attributes don't track async
    generator protocol calls the same way a real one does."""

    def __init__(self, payloads: list[dict], topic: str = "some/topic") -> None:
        self._gen = _fake_agen(payloads, topic)
        self.aclose = AsyncMock(side_effect=self._gen.aclose)

    def __aiter__(self):
        return self._gen.__aiter__()


@pytest.mark.asyncio
async def test_watch_one_captures_topic_and_payload_per_message() -> None:
    """BUG FOUND AND FIXED (this session): previously stored/printed
    only the static watch label, not response.topic (the actual
    concrete topic each message arrived on) -- invisible for a
    specific-topic watch (label == topic there), but for a wildcard
    watch this silently discarded exactly the information that would
    show which distinct topics were actually active. A live capture
    with 81 wildcard messages (chairstacker) surfaced this."""
    report = Report()
    raw_capture: dict = {}
    wrapper = _FakeAsyncGenWrapper(
        [{"phase": "run"}, {"phase": "charge"}], topic="prefix/things/BLID/pos_update"
    )

    await _watch_one(lambda: wrapper, "prefix/things/BLID/#", raw_capture, report)

    assert raw_capture["watch: prefix/things/BLID/#"] == [
        {"topic": "prefix/things/BLID/pos_update", "payload": {"phase": "run"}},
        {"topic": "prefix/things/BLID/pos_update", "payload": {"phase": "charge"}},
    ]
    wrapper.aclose.assert_awaited_once()
    entry = next(e for e in report.results if e.name == "Watch prefix/things/BLID/#")
    assert entry.status == "OK"
    assert "2 message(s)" in entry.detail


@pytest.mark.asyncio
async def test_watch_one_reports_clearly_when_nothing_arrives() -> None:
    """A null result is itself meaningful (see the module docstring) --
    must still report OK with a clear explanation, not look like a
    failure or go unreported."""
    report = Report()
    raw_capture: dict = {}
    wrapper = _FakeAsyncGenWrapper([])

    await _watch_one(lambda: wrapper, "mission/timeline/report", raw_capture, report)

    assert raw_capture["watch: mission/timeline/report"] == []
    entry = next(e for e in report.results if e.name == "Watch mission/timeline/report")
    assert entry.status == "OK"
    assert "no messages arrived" in entry.detail


@pytest.mark.asyncio
async def test_watch_one_closes_generator_on_cancellation() -> None:
    """The normal way a watch ends (the surrounding asyncio.wait_for()
    timeout in run() cancels the task) must still call .aclose() on
    the generator, and must not propagate CancelledError back out --
    it's expected, not a failure."""
    report = Report()
    raw_capture: dict = {}

    async def _never_ending():
        response = MagicMock()
        response.topic = "mission/timeline/report"
        response.payload = {"phase": "run"}
        yield response
        # Then just hangs -- simulates a live, ongoing subscription.
        await asyncio.Event().wait()

    wrapper = _FakeAsyncGenWrapper([])
    wrapper._gen = _never_ending()
    wrapper.aclose = AsyncMock(side_effect=wrapper._gen.aclose)

    task = asyncio.ensure_future(
        _watch_one(lambda: wrapper, "mission/timeline/report", raw_capture, report)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    wrapper.aclose.assert_awaited_once()


class TestAddTopicGroupedViews:
    """NEW (this session) -- the terminal output already groups a
    watch's messages by distinct topic (_watch_one()'s frequency
    summary); this closes the same gap for the saved --dump-config
    JSON, which stayed a flat list even after the response.topic fix."""

    def test_groups_watch_entries_by_topic_alongside_the_flat_list(self) -> None:
        redacted = {
            "watch: prefix/things/BLID/#": [
                {"topic": "a/pos_update", "payload": {"n": 1}},
                {"topic": "a/pos_update", "payload": {"n": 2}},
                {"topic": "a/map_update", "payload": {"n": 3}},
            ],
        }

        _add_topic_grouped_views(redacted)

        # Original flat list untouched -- arrival order preserved.
        assert redacted["watch: prefix/things/BLID/#"] == [
            {"topic": "a/pos_update", "payload": {"n": 1}},
            {"topic": "a/pos_update", "payload": {"n": 2}},
            {"topic": "a/map_update", "payload": {"n": 3}},
        ]
        # New grouped sibling view.
        assert redacted["watch: prefix/things/BLID/# (grouped by topic)"] == {
            "a/pos_update": [{"n": 1}, {"n": 2}],
            "a/map_update": [{"n": 3}],
        }

    def test_ignores_empty_watch_entries(self) -> None:
        redacted = {"watch: rejected/report": []}
        _add_topic_grouped_views(redacted)
        assert "watch: rejected/report (grouped by topic)" not in redacted

    def test_ignores_non_watch_keys(self) -> None:
        """Defensive -- shouldn't break if raw_capture ever gains a key
        that isn't shaped like a watch entry."""
        redacted = {"Discovery deployment object (for irbt_topic_prefix)": {"some": "dict"}}
        _add_topic_grouped_views(redacted)
        assert len(redacted) == 1
