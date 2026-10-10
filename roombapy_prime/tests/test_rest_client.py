"""Tests for roombapy_prime.rest_client.

No aioresponses here: the installed aioresponses (0.7.9) is incompatible
with the installed aiohttp (3.14.1) in this environment -- its internal
ClientResponse construction is missing a now-required stream_writer
kwarg. Rather than pin/downgrade a dependency just for tests, this uses
the same hand-rolled fake-double style as test_mqtt_client.py: a minimal
stand-in for aiohttp.ClientSession that records calls and returns a
canned response, no real sockets.

Verifies URL construction, query params, request bodies, and SigV4
signature headers match what's confirmed (see aws_sigv4.py, FINDINGS).
Response handling is only checked against synthetic bodies, since no
real p2maps REST response was ever captured live.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import aiohttp
import pytest

from roombapy_prime.auth import CloudCredentials
from roombapy_prime.errors import CloudErrorReason, reason_for_status
from roombapy_prime.models import HouseholdSchedule, MergeRooms, ScheduleFrequency, ScheduleOptions
from roombapy_prime.rest_client import (
    ClassicRestClient,
    CloudRestClient,
    PrimeRestClient,
    RestClientError,
    RestConnectionError,
    RestError,
    RestHTTPError,
    RestRateLimitedError,
    RestServerError,
    RestSSLError,
    RestTimeoutError,
)

HTTP_BASE_AUTH = "https://fake-http-base-auth.example.invalid"


def _dummy_credentials() -> CloudCredentials:
    return CloudCredentials(
        access_key_id="AKIDEXAMPLE", secret_key="secretkey123",
        session_token="sessiontoken456", cognito_id="us-east-1:0",
    )


class _FakeResponse:
    def __init__(
        self, status: int, body: str, url: str, raw_bytes: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.url = url
        self._raw_bytes = raw_bytes
        self.headers = headers or {}

    async def text(self) -> str:
        return self._body

    async def read(self) -> bytes:
        return self._raw_bytes if self._raw_bytes is not None else self._body.encode()

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _RecordedCall:
    def __init__(self, method: str, url: str, params: dict | None, data: bytes | None, headers: dict | None) -> None:
        self.method = method
        self.url = url
        self.params = params
        self.data = data
        self.headers = headers

    @property
    def body_json(self) -> dict:
        assert self.data is not None
        return json.loads(self.data)


class _FakeSession:
    """Stand-in for aiohttp.ClientSession. Queue up responses with
    .queue_response(...), call get()/post() exactly like PrimeRestClient
    does, inspect .calls afterwards."""

    def __init__(self) -> None:
        self.calls: list[_RecordedCall] = []
        self._responses: list[_FakeResponse] = []

    def queue_response(
        self, status: int = 200, payload: dict | None = None, raw_body: str | None = None,
        raw_bytes: bytes | None = None, headers: dict[str, str] | None = None,
    ) -> None:
        body = raw_body if raw_body is not None else json.dumps(payload if payload is not None else {})
        self._responses.append(
            _FakeResponse(status=status, body=body, url="", raw_bytes=raw_bytes, headers=headers)
        )

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, data: bytes | None = None) -> _FakeResponse:
        self.calls.append(_RecordedCall("GET", url, params, data, headers))
        return self._responses.pop(0)

    def post(self, url: str, params: dict | None = None, headers: dict | None = None, data: bytes | None = None) -> _FakeResponse:
        self.calls.append(_RecordedCall("POST", url, params, data, headers))
        return self._responses.pop(0)

    def put(self, url: str, params: dict | None = None, headers: dict | None = None, data: bytes | None = None) -> _FakeResponse:
        self.calls.append(_RecordedCall("PUT", url, params, data, headers))
        return self._responses.pop(0)

    def delete(self, url: str, params: dict | None = None, headers: dict | None = None, data: bytes | None = None) -> _FakeResponse:
        self.calls.append(_RecordedCall("DELETE", url, params, data, headers))
        return self._responses.pop(0)


def test_path_segment_encodes_traversal_and_slash_characters() -> None:
    """NEW (session 54, security hardening pass) -- direct unit test
    for _path_segment(), the helper added after a security review found
    every URL-path identifier (BLIDs, map IDs, favorite IDs, etc.) was
    previously interpolated via a raw f-string with no escaping at all.
    A value containing "/" or ".." could otherwise redirect the request
    to an unintended path on the same host."""
    from roombapy_prime.rest_client import _path_segment

    assert _path_segment("../../etc/passwd") == "..%2F..%2Fetc%2Fpasswd"
    assert _path_segment("a/b") == "a%2Fb"
    # legitimate identifiers are a no-op -- purely additive safety
    assert _path_segment("BLID123") == "BLID123"
    assert _path_segment("map-uuid-1234") == "map-uuid-1234"


@pytest.mark.asyncio
async def test_get_map_metadata_rejects_path_traversal_in_id() -> None:
    """Regression test proving the fix actually reaches a real
    call site, not just the helper in isolation: a p2map_id containing
    a "/" must not be able to redirect the request path."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_map_metadata("../admin")

    call = session.calls[0]
    assert "/../admin" not in call.url
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps/..%2Fadmin"


@pytest.mark.asyncio
async def test_get_map_metadata_url_and_response() -> None:
    """UPDATED (session 51): get_map_metadata() now returns a parsed
    P2MapData (confirmed via P2MapData$$serializer), not raw JSON."""
    session = _FakeSession()
    session.queue_response(payload={"p2map_id": "map123", "name": "Downstairs", "visible": True})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_map_metadata("map123")

    assert result.p2map_id == "map123"
    assert result.name == "Downstairs"
    assert result.visible is True
    assert session.calls[0].method == "GET"
    assert session.calls[0].url == f"{HTTP_BASE_AUTH}/v1/p2maps/map123"


@pytest.mark.asyncio
async def test_requests_are_signed_with_sigv4() -> None:
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_map_metadata("map123")

    headers = session.calls[0].headers
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert headers["x-amz-security-token"] == "sessiontoken456"
    assert "x-amz-date" in headers


@pytest.mark.asyncio
async def test_set_map_name_body_and_query() -> None:
    """CORRECTED (session 51): confirmed via
    EditMapSettingsRequest$Command$SetName$$serializer -- real key is
    "name", not "type" as previously implemented (a genuine bug, not
    just an unconfirmed guess)."""
    session = _FakeSession()
    session.queue_response(payload={"ok": True})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.set_map_name("map123", "Erdgeschoss")

    call = session.calls[0]
    assert result == {"ok": True}
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps/map123/settings"
    assert call.params == {"trigger_fast_updates": "true"}
    assert call.body_json == {"name": "Erdgeschoss"}


@pytest.mark.asyncio
async def test_set_map_orientation_clamps_angle() -> None:
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.set_map_orientation("map123", 4.0)  # > pi, needs clamping

    sent_angle = session.calls[0].body_json["user_orientation_rad"]
    assert -3.141592653589793 < sent_angle <= 3.141592653589793


@pytest.mark.asyncio
async def test_set_map_orientation_already_in_range_is_unchanged() -> None:
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.set_map_orientation("map123", 1.0)

    sent_angle = session.calls[0].body_json["user_orientation_rad"]
    assert sent_angle == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_delete_map_is_soft_delete_via_settings_endpoint() -> None:
    """NEW (July 11, third session) -- confirmed from DeleteMapRequest.java:
    despite the name, not an HTTP DELETE, but POST .../settings with
    {"visible": false}, see delete_map()'s docstring."""
    session = _FakeSession()
    session.queue_response(payload={"ok": True})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.delete_map("map123")

    call = session.calls[0]
    assert result == {"ok": True}
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps/map123/settings"
    assert call.params == {"trigger_fast_updates": "true"}
    assert call.body_json == {"visible": False}


# --- Favorites (FavoriteV1) -- NEW, fourth session -----------------------

@pytest.mark.asyncio
async def test_get_favorites_url_and_query() -> None:
    """CONFIRMED: GET /v1/user/favorites?app_edition=1, httpMethod from
    FetchFavoriteRequest.java."""
    from roombapy_prime.models import MissionCommandType

    session = _FakeSession()
    session.queue_response(payload=[
        {
            "favorite_id": "fav1",
            "name": "Kitchen clean",
            "default": False,
            "deleted": False,
            "hidden": False,
            "commanddefs": [{"command": "clean", "robot_id": "BLID123", "ordered": 0, "select_all": True}],
        }
    ])
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_favorites()

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/user/favorites"
    assert call.params == {"app_edition": "1"}
    assert len(result) == 1
    assert result[0].favorite_id == "fav1"
    assert result[0].name == "Kitchen clean"
    assert len(result[0].command_defs) == 1
    assert result[0].command_defs[0].command_type == MissionCommandType.CLEAN
    assert result[0].command_defs[0].clean_all is True


@pytest.mark.asyncio
async def test_get_favorites_non_list_response_is_empty() -> None:
    session = _FakeSession()
    session.queue_response(payload={"unexpected": "shape"})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_favorites()

    assert result == []


@pytest.mark.asyncio
async def test_get_favorites_handles_json_encoded_commanddefs_strings() -> None:
    """NEW (this session, parallel native-analysis track): Favorite's
    own Kotlin/Java field is typed List<String>, not a list of
    already-structured objects -- meaning each entry may arrive as a
    JSON-ENCODED STRING rather than a dict directly. A real
    string-shaped response would previously have crashed outright
    (subscripting a string with c["command"]) -- this defends against
    that."""
    import json

    from roombapy_prime.models import MissionCommandType

    session = _FakeSession()
    session.queue_response(payload=[
        {
            "favorite_id": "fav1",
            "name": "Kitchen clean",
            "default": False,
            "deleted": False,
            "hidden": False,
            "commanddefs": [
                json.dumps({"command": "clean", "robot_id": "BLID123", "ordered": 0, "select_all": True})
            ],
        }
    ])
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_favorites()

    assert len(result) == 1
    assert len(result[0].command_defs) == 1
    assert result[0].command_defs[0].command_type == MissionCommandType.CLEAN
    assert result[0].command_defs[0].asset_id == "BLID123"


@pytest.mark.asyncio
async def test_get_favorites_handles_mixed_dict_and_string_commanddefs() -> None:
    """Defensive: a response mixing both shapes across entries (however
    unlikely) must not crash either -- each entry is checked
    independently, not the whole list at once."""
    import json

    session = _FakeSession()
    session.queue_response(payload=[
        {
            "favorite_id": "fav1",
            "name": "Mixed",
            "commanddefs": [
                {"command": "clean", "robot_id": "BLID_A", "ordered": 0, "select_all": True},
                json.dumps({"command": "dock", "robot_id": "BLID_B", "ordered": 0, "select_all": False}),
            ],
        }
    ])
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_favorites()

    assert len(result[0].command_defs) == 2
    assert result[0].command_defs[0].asset_id == "BLID_A"
    assert result[0].command_defs[1].asset_id == "BLID_B"


@pytest.mark.asyncio
async def test_create_favorite_sends_body_and_query() -> None:
    """CONFIRMED (POST method, via CreateFavoriteRequest.<init>) -- see
    create_favorite()'s docstring."""
    from roombapy_prime.models import FavoriteV1, MissionCommandType, RoutineCommand

    session = _FakeSession()
    session.queue_response(payload={"favorite_id": "new1"})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    favorite = FavoriteV1(
        name="Living room",
        command_defs=[RoutineCommand(command_type=MissionCommandType.CLEAN, asset_id="BLID123")],
    )
    result = await client.create_favorite(favorite)

    call = session.calls[0]
    assert result == {"favorite_id": "new1"}
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/user/favorites"
    assert call.params == {"app_edition": "1"}
    assert call.body_json["name"] == "Living room"
    assert call.body_json["commanddefs"][0]["command"] == "clean"


@pytest.mark.asyncio
async def test_update_favorite_uses_put_and_favorite_id_in_url() -> None:
    from roombapy_prime.models import FavoriteV1

    session = _FakeSession()
    session.queue_response(payload={"favorite_id": "fav1"})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    favorite = FavoriteV1(name="Renamed")
    await client.update_favorite("fav1", favorite)

    call = session.calls[0]
    assert call.method == "PUT"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/user/favorites/fav1"
    assert call.body_json["name"] == "Renamed"


@pytest.mark.asyncio
async def test_delete_favorite_uses_delete_method() -> None:
    """CONFIRMED from DeleteFavoriteRequest.java (httpMethod = "DELETE")."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.delete_favorite("fav1")

    call = session.calls[0]
    assert call.method == "DELETE"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/user/favorites/fav1"
    assert call.params == {"app_edition": "1"}


@pytest.mark.asyncio
async def test_order_favorite_uses_put_and_order_suffix() -> None:
    """CONFIRMED from OrderFavoriteRequest.java (httpMethod = "PUT",
    urlString + "/order", insert_at/insert_before/insert_after as
    query parameters -- CORRECTED, see order_favorite()'s docstring)."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.order_favorite("fav1", insert_before="fav0")

    call = session.calls[0]
    assert call.method == "PUT"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/user/favorites/fav1/order"
    assert call.params == {"app_edition": "1", "insert_before": "fav0"}


@pytest.mark.asyncio
async def test_get_mission_history_url_and_query() -> None:
    """CONFIRMED from FetchMissionHistoryRequest.java."""
    session = _FakeSession()
    session.queue_response(payload={"missions": []})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_mission_history(
        "blid123", max_reports=10, max_age=30, supported_done_codes=["OK", "C"]
    )

    assert result == {"missions": []}
    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/blid123/missionhistory"
    assert call.params == {"maxReports": "10", "maxAge": "30", "supportedDoneCodes": "OK,C"}


@pytest.mark.asyncio
async def test_get_mission_history_no_params() -> None:
    session = _FakeSession()
    session.queue_response(payload={"missions": []})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_mission_history("blid123")

    call = session.calls[0]
    assert call.params == {}


@pytest.mark.asyncio
async def test_get_schedules_url() -> None:
    """UPDATED (session 51): get_schedules() now returns a parsed
    SchedulesResponse (confirmed via SchedulesResponse$$serializer/
    SchedulesList$$serializer), not raw JSON."""
    session = _FakeSession()
    session.queue_response(payload={
        "household_schedules": [{"household_schedule_id": "hs1", "schedules": [{"schedule_id": "s1"}]}]
    })
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_schedules("hh1")

    assert session.calls[0].url == f"{HTTP_BASE_AUTH}/v1/households/hh1/settings/schedule"
    assert session.calls[0].method == "GET"
    assert len(result.household_schedules) == 1
    assert result.household_schedules[0].household_schedule_id == "hs1"


@pytest.mark.asyncio
async def test_delete_schedule_url() -> None:
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.delete_schedule("hh1", "sched1")

    call = session.calls[0]
    assert call.method == "DELETE"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/households/hh1/settings/schedule/sched1"


@pytest.mark.asyncio
async def test_create_schedules_posts_body() -> None:
    """The shape that four field rounds of HTTP 500 came down to.

    CreateSchedulesRequest.getHttpBody() serialises a ScheduleListUpdate
    of HouseholdScheduleUpdate objects, built as
    `HouseholdScheduleUpdate(options, null)`. So each entry is
    {"options": {...}, "schedule_id": null} -- this test used to assert
    the ScheduleOptions sitting in the array directly, which is what the
    client sent and what the server rejected without ever naming a
    field.

    The top-level key was never the problem, and `schedule_id` is
    OMITTED rather than sent as null: both elements are optional with
    default null, and CreateSchedulesRequest uses Json.Default without
    encodeDefaults, so write$Self skips it. Sending an explicit null and
    leaving the key out are not the same thing to a server.

    (Earlier correction still holds: the inner key is "robot_id",
    confirmed via ScheduleOptions$$serializer's <clinit>, not
    "assetId".)"""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.create_schedules(
        "hh1", [ScheduleOptions(asset_id="asset1", name="Morning", frequency=ScheduleFrequency.WEEKLY)]
    )

    call = session.calls[0]
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/households/hh1/settings/schedule"
    assert call.body_json == {
        "schedules": [{
            "options": {
                "robot_id": "asset1", "name": "Morning", "frequency": "WEEKLY",
            },
        }]
    }


@pytest.mark.asyncio
async def test_update_schedules_puts_body() -> None:
    """CORRECTED (session 46) -- real keys are "schedule_id" (on
    HouseholdSchedule) and "robot_id" (on ScheduleOptions), both
    confirmed via their respective $$serializer <clinit>s, not
    "scheduleId"/"assetId" as previously guessed."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    options = ScheduleOptions(asset_id="asset1", name="Evening")
    await client.update_schedules("hh1", "sched1", [HouseholdSchedule(schedule_id="sched1", options=options)])

    call = session.calls[0]
    assert call.method == "PUT"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/households/hh1/settings/schedule/sched1"
    assert call.body_json == {
        "schedules": [{"schedule_id": "sched1", "options": {"robot_id": "asset1", "name": "Evening"}}]
    }


@pytest.mark.asyncio
async def test_get_user_households_url() -> None:
    """HTTP method pure REST convention, not confirmed from a request
    class -- see get_user_households()'s docstring."""
    session = _FakeSession()
    session.queue_response(payload={"households": []})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_user_households()

    assert result == {"households": []}
    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/user/households"


@pytest.mark.asyncio
async def test_get_dnd_settings_url() -> None:
    """UPDATED (session 53): get_dnd_settings() now returns a parsed
    DNDStatusResponse, not raw JSON -- a genuine architectural gap
    found in a broader review (the confirmed model existed since the
    ninth session, but was never actually wired in)."""
    session = _FakeSession()
    session.queue_response(payload={"dailyStart": 1320, "dailyEnd": 420})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_dnd_settings("hh1")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/households/hh1/settings/dnd"
    assert result.daily_start == 1320
    assert result.daily_end == 420


@pytest.mark.asyncio
async def test_set_dnd_settings_puts_body() -> None:
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.set_dnd_settings("hh1", {"enabled": True})

    call = session.calls[0]
    assert call.method == "PUT"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/households/hh1/settings/dnd"
    assert call.body_json == {"enabled": True}


@pytest.mark.asyncio
async def test_get_cleaning_profiles_query_with_map() -> None:
    """CORRECTED (session 38) -- confirmed directly from
    CleaningProfileRequest.getQueryParams()'s decompiled Kotlin logic:
    "robotId" (not "asset_id"), plus "includeSmart": "true" whenever
    p2map_id is present."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_cleaning_profiles("asset1", "map1")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/profiles"
    assert call.params == {"robotId": "asset1", "includeSmart": "true", "p2map_id": "map1"}


@pytest.mark.asyncio
async def test_get_cleaning_profiles_query_without_map() -> None:
    """CORRECTED (session 38) -- when p2map_id is absent, the real
    query drops the map id entirely and sends "includeSmart": "false"
    instead."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_cleaning_profiles("asset1")

    call = session.calls[0]
    assert call.params == {"robotId": "asset1", "includeSmart": "false"}


@pytest.mark.asyncio
async def test_get_default_routines_url() -> None:
    """UPDATED (session 53): now returns a parsed RoutinesDefaultsResponse
    -- same architectural gap as get_dnd_settings(), see that test."""
    session = _FakeSession()
    session.queue_response(payload={"routines": [{"name": "Whole Home"}]})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_default_routines("map1")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps/map1/routines/defaults"
    assert len(result.routines) == 1
    assert result.routines[0].name == "Whole Home"


@pytest.mark.asyncio
async def test_get_robot_parts_url() -> None:
    """Confirmed from base_roomba_config.json (commandId "GetRobotParts"),
    not from bytecode interpretation. UPDATED (session 53): now
    returns a parsed RobotPartsInfo -- same architectural gap as
    get_dnd_settings(), see that test."""
    session = _FakeSession()
    session.queue_response(payload={"robot_id": "BLID123", "num_parts": 2, "parts": []})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_robot_parts("BLID123")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/robots/BLID123/parts"
    assert result.robot_id == "BLID123"
    assert result.num_parts == 2


@pytest.mark.asyncio
async def test_reset_robot_parts_url() -> None:
    """Confirmed from base_roomba_config.json (commandId "ResetRobotParts")."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.reset_robot_parts("BLID123", ["35"])

    call = session.calls[0]
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/robots/BLID123/parts"


@pytest.mark.asyncio
async def test_get_serial_number_data_query() -> None:
    """Confirmed from base_roomba_config.json (commandId "GetSerialNumberData").
    UPDATED (session 53): now returns a parsed RobotSerialInfo -- same
    architectural gap as get_dnd_settings(), see that test."""
    session = _FakeSession()
    session.queue_response(payload={"sku": "i7", "name": "House_Bot"})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_serial_number_data("BLID123")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/robots"
    assert call.params == {"robot_id": "BLID123"}
    assert result.sku == "i7"
    assert result.name == "House_Bot"


@pytest.mark.asyncio
async def test_edit_map_v2_sends_command_envelope() -> None:
    """edit_map_v2() -- the unused path, see rest_client.py's
    docstring. Stays tested since the endpoint still exists."""
    session = _FakeSession()
    session.queue_response(payload={"updated": True})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.edit_map_v2("map123", MergeRooms(room_ids=["a", "b"]))

    call = session.calls[0]
    assert result == {"updated": True}
    assert call.url == f"{HTTP_BASE_AUTH}/v2/p2maps/map123/versions"
    assert call.body_json == {"command": "merge_rooms", "params": {"ids": ["a", "b"]}}


@pytest.mark.asyncio
async def test_edit_map_v1_sends_command_envelope() -> None:
    """UPDATE (this session): live APK decompilation of the FULL
    EditMapV1Request.java confirms the inner "edit_cmd" shape is
    {"command": "arrange_room", "params": {"room_ids": [...]}}, not the
    flat {"type": "MergeRooms", "room_ids": [...]} previously assumed --
    the outer envelope ({"edit_cmd": ..., "response_type": ...}) itself
    is unchanged and was already correct."""
    from roombapy_prime.models import MergeRoomsV1

    session = _FakeSession()
    session.queue_response(payload={"updated": True})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.edit_map("map123", MergeRoomsV1(ids=["a", "b"]))

    call = session.calls[0]
    assert result == {"updated": True}
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps/map123/versions"
    assert call.body_json == {
        "edit_cmd": {"command": "arrange_room", "params": {"room_ids": ["a", "b"]}},
        "response_type": "link",
    }


@pytest.mark.asyncio
async def test_get_live_map_stream_parses_response() -> None:
    session = _FakeSession()
    session.queue_response(payload={"mqtt_topic": "some/topic", "livemap_url": "https://example.invalid/m.png"})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_live_map_stream("BLID123")

    call = session.calls[0]
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps/livemap"
    assert call.params == {"robotId": "BLID123"}
    assert result.mqtt_topic == "some/topic"
    assert result.initial_map_url == "https://example.invalid/m.png"


@pytest.mark.asyncio
async def test_error_response_raises_rest_error() -> None:
    session = _FakeSession()
    session.queue_response(status=404, raw_body="not found")
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestError) as exc_info:
        await client.get_map_metadata("map123")

    assert exc_info.value.status == 404
    assert exc_info.value.raw_response == "not found"


@pytest.mark.asyncio
async def test_non_json_success_response_raises_rest_error() -> None:
    """SYNTHETIC edge case -- confirms a malformed/HTML success response
    doesn't silently return garbage."""
    session = _FakeSession()
    session.queue_response(status=200, raw_body="<html>not json</html>")
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestError, match="Non-JSON") as excinfo:
        await client.get_map_metadata("map123")

    assert excinfo.value.reason is CloudErrorReason.RESPONSE_MALFORMED


# --- reactive 403 -> relogin -> retry (ported from cloud_api.py's _aws_get) --

@pytest.mark.asyncio
async def test_get_active_map_versions_url_and_query() -> None:
    """NEW (July 11) -- endpoint confirmed from the inner coroutine
    class P2MapAPIFetching$fetchActiveVersions$2, see
    PRIME_APP_GAP_ANALYSIS."""
    session = _FakeSession()
    session.queue_response(payload=[{"mapId": "m1", "mapVersionId": "v1"}])
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_active_map_versions("BLID123")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps"
    assert call.params == {"robotId": "BLID123", "visible": "true"}
    assert result == [{"mapId": "m1", "mapVersionId": "v1"}]


@pytest.mark.asyncio
async def test_get_active_map_versions_non_list_response_is_empty() -> None:
    """SYNTHETIC defensive check -- no real non-list response ever seen."""
    session = _FakeSession()
    session.queue_response(payload={"unexpected": "shape"})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_active_map_versions("BLID123")

    assert result == []


@pytest.mark.asyncio
async def test_get_map_geojson_link_url_and_query() -> None:
    """NEW (July 11, third session) -- endpoint confirmed from
    P2MapGeoJSONRequest.java, see PRIME_APP_GAP_ANALYSIS point C2.
    The response shape itself remains unconfirmed -- this test only
    checks URL/query, not which JSON key carries the URL."""
    session = _FakeSession()
    session.queue_response(payload={"some_unconfirmed_key": "https://example.invalid/bundle.tar.gz"})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.get_map_geojson_link("map123", "v1")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/p2maps/map123/versions/v1/geojson"
    assert call.params == {"response_type": "link"}
    assert result == {"some_unconfirmed_key": "https://example.invalid/bundle.tar.gz"}


@pytest.mark.asyncio
async def test_download_map_bundle_returns_raw_bytes() -> None:
    """NEW (July 11, fifth session) -- deliberately WITHOUT SigV4
    signing, see download_map_bundle()'s docstring."""
    session = _FakeSession()
    fake_bundle_bytes = b"\x1f\x8b\x08\x00fake-gzip-bytes-not-a-real-archive"
    session.queue_response(raw_bytes=fake_bundle_bytes)
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    result = await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz?sig=abc")

    assert result == fake_bundle_bytes
    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == "https://presigned.example.invalid/bundle.tar.gz?sig=abc"
    # KEIN SigV4-Header -- bewusst, siehe Docstring
    assert call.headers is None


@pytest.mark.asyncio
async def test_download_map_bundle_error_response_raises() -> None:
    session = _FakeSession()
    session.queue_response(status=403, raw_body="access denied, link expired")
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestError) as exc_info:
        await client.download_map_bundle("https://presigned.example.invalid/expired.tar.gz")

    assert exc_info.value.status == 403


@pytest.mark.asyncio
async def test_403_without_relogin_raises_immediately() -> None:
    session = _FakeSession()
    session.queue_response(status=403, raw_body="forbidden")
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())  # no relogin

    with pytest.raises(RestError) as exc_info:
        await client.get_map_metadata("map123")

    assert exc_info.value.status == 403
    assert len(session.calls) == 1  # no retry attempted


@pytest.mark.asyncio
async def test_403_with_relogin_retries_once_with_new_credentials() -> None:
    session = _FakeSession()
    session.queue_response(status=403, raw_body="forbidden")
    session.queue_response(payload={"ok": True})  # the retry succeeds

    new_credentials = CloudCredentials(
        access_key_id="NEW_KEY", secret_key="new_secret",
        session_token="new_token", cognito_id="eu-west-1:1",
    )
    relogin_calls = []

    async def fake_relogin():
        relogin_calls.append(1)
        result = type("FakeLoginResult", (), {"credentials": new_credentials})()
        return result

    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials(), relogin=fake_relogin)

    result = await client.get_map_metadata("map123")

    # UPDATED (session 51): get_map_metadata() now returns a parsed P2MapData
    # (see test_get_map_metadata_url_and_response) -- {"ok": True} has no
    # recognized P2MapData field, so this parses to all-None. This test's
    # actual focus is the retry mechanism below, not the parsing itself.
    from roombapy_prime.models import P2MapData

    assert result == P2MapData()
    assert relogin_calls == [1]
    assert len(session.calls) == 2
    # the retried request must be signed with the NEW credentials
    assert session.calls[1].headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=NEW_KEY/")


@pytest.mark.asyncio
async def test_403_retry_only_happens_once_not_infinitely() -> None:
    """SYNTHETIC -- confirms _retry=False on the second attempt prevents
    an infinite loop if the new credentials also get a 403."""
    session = _FakeSession()
    session.queue_response(status=403, raw_body="forbidden")
    session.queue_response(status=403, raw_body="still forbidden")

    async def fake_relogin():
        result = type("FakeLoginResult", (), {"credentials": _dummy_credentials()})()
        return result

    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials(), relogin=fake_relogin)

    with pytest.raises(RestError) as exc_info:
        await client.get_map_metadata("map123")

    assert exc_info.value.status == 403
    assert len(session.calls) == 2  # exactly one retry, not more


@pytest.mark.asyncio
async def test_poll_echo_value_url() -> None:
    """Confirmed from base_roomba_config.json (commandId "PollEchoValueCommand,Set")."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.poll_echo_value("BLID123")

    call = session.calls[0]
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/robots/BLID123/echo"


@pytest.mark.asyncio
async def test_get_time_estimates_sends_body() -> None:
    """Confirmed from base_roomba_config.json (commandId "GetTimeEstimates")."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_time_estimates("BLID123")

    call = session.calls[0]
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/time-estimates"
    # `robot_id`, not `assetId`. The old test asserted the latter --
    # a placeholder from when the key was unknown, which quietly read
    # as a confirmed fact for months.
    #
    # Traced from the native format string `{ "%s": "%s" }` with
    # kRobotId as the first substitution.
    assert call.body_json == {"robot_id": "BLID123"}


@pytest.mark.asyncio
async def test_reset_robot_url() -> None:
    """Confirmed from base_roomba_config.json (commandId "ResetRobotCommand")."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.reset_robot("BLID123")

    call = session.calls[0]
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/BLID123/reset"


@pytest.mark.asyncio
async def test_get_notifications_query() -> None:
    """Confirmed from base_roomba_config.json (commandId "GetNotifications")."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_notifications("BLID123", app_version="2.5.0")

    call = session.calls[0]
    assert call.method == "GET"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/robots/BLID123/timeline"
    assert call.params == {
        "event_type": "HKC",
        "details_type_filter": "all",
        "app_version": "2.5.0",
        "limit": "50",
    }


# =========================================================================
# SSL certificate error clarity (this session, same fix as auth.py --
# see _raise_clear_ssl_error()'s docstring for why this belongs here
# too: _request() is the single chokepoint nearly every endpoint in
# this file goes through).
# =========================================================================


class _NetworkFailingSession:
    """Minimal stand-in that raises a given exception on any
    get/post/put/delete call -- mirrors _FakeSession's method surface.
    Generalized (this session) from the SSL-only _SSLFailingSession to
    also cover ClientConnectorError/ServerTimeoutError."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get(self, *args: object, **kwargs: object) -> None:
        raise self._exc

    def post(self, *args: object, **kwargs: object) -> None:
        raise self._exc

    def put(self, *args: object, **kwargs: object) -> None:
        raise self._exc

    def delete(self, *args: object, **kwargs: object) -> None:
        raise self._exc


def _ssl_error() -> aiohttp.ClientSSLError:
    return aiohttp.ClientSSLError(None, OSError("certificate has expired"))


def _connector_error() -> aiohttp.ClientConnectorError:
    return aiohttp.ClientConnectorError(None, OSError("Name or service not known"))


def _timeout_error() -> aiohttp.ServerTimeoutError:
    return aiohttp.ServerTimeoutError("Connection timeout to host")


@pytest.mark.asyncio
async def test_request_chokepoint_ssl_error_gets_clear_message() -> None:
    """Exercised via get_map_metadata() (any endpoint would do -- all
    go through the same _request() chokepoint)."""
    client = PrimeRestClient(_NetworkFailingSession(_ssl_error()), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestSSLError) as excinfo:
        await client.get_map_metadata("map123")

    # "certificate has expired" -- since 0.4.0 diagnosed as login does,
    # no longer one fixed "almost always temporary" for every cause.
    assert "certificate has expired" in str(excinfo.value)
    assert excinfo.value.reason is CloudErrorReason.SSL_CERTIFICATE_EXPIRED
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientSSLError)


@pytest.mark.asyncio
async def test_download_map_bundle_ssl_error_gets_clear_message() -> None:
    """download_map_bundle() deliberately bypasses _request() (different,
    unsigned host) -- needs its own SSL wrap, tested separately here."""
    client = PrimeRestClient(_NetworkFailingSession(_ssl_error()), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestSSLError) as excinfo:
        await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz")

    assert "certificate" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientSSLError)


@pytest.mark.asyncio
async def test_request_chokepoint_connector_error_gets_clear_message() -> None:
    client = PrimeRestClient(_NetworkFailingSession(_connector_error()), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestConnectionError) as excinfo:
        await client.get_map_metadata("map123")

    assert "connect" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientConnectorError)


@pytest.mark.asyncio
async def test_download_map_bundle_connector_error_gets_clear_message() -> None:
    client = PrimeRestClient(_NetworkFailingSession(_connector_error()), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestConnectionError) as excinfo:
        await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz")

    assert isinstance(excinfo.value.__cause__, aiohttp.ClientConnectorError)


@pytest.mark.asyncio
async def test_request_chokepoint_timeout_error_gets_clear_message() -> None:
    client = PrimeRestClient(_NetworkFailingSession(_timeout_error()), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestTimeoutError) as excinfo:
        await client.get_map_metadata("map123")

    assert "too long" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ServerTimeoutError)


@pytest.mark.asyncio
async def test_download_map_bundle_timeout_error_gets_clear_message() -> None:
    client = PrimeRestClient(_NetworkFailingSession(_timeout_error()), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestTimeoutError) as excinfo:
        await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz")

    assert isinstance(excinfo.value.__cause__, aiohttp.ServerTimeoutError)


# ── every transport failure is a RestError (0.4.0) ─────────────────────────
#
# Measured before this: against a local server, a connection dropped
# mid-answer escaped as aiohttp.ServerDisconnectedError and a timeout from
# the caller's session as a bare TimeoutError -- past every `except
# RestError` and `except CloudError`.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected", "reason"),
    [
        (aiohttp.ServerDisconnectedError(), RestConnectionError, CloudErrorReason.CONNECTION_BROKEN),
        (aiohttp.ClientPayloadError("transfer cut short"), RestConnectionError,
         CloudErrorReason.CONNECTION_BROKEN),
        (aiohttp.ClientOSError(104, "Connection reset by peer"), RestConnectionError,
         CloudErrorReason.CONNECTION_BROKEN),
        (aiohttp.ClientConnectorError(None, OSError("Name or service not known")), RestConnectionError,
         CloudErrorReason.CONNECTION_FAILED),
        (TimeoutError(), RestTimeoutError, CloudErrorReason.TIMEOUT),
    ],
    ids=["server-disconnected", "payload-cut", "connection-reset", "no-connection", "bare-timeout"],
)
@pytest.mark.parametrize("call", ["request", "bundle"])
async def test_every_transport_failure_is_a_cloud_error(exc, expected, reason, call) -> None:
    """`reason` separates the two RestConnectionError cases a person is
    told different things about: a connection that broke off, and none
    at all."""
    from roombapy_prime.errors import CloudError

    client = PrimeRestClient(_NetworkFailingSession(exc), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(expected) as excinfo:
        if call == "request":
            await client.get_map_metadata("map123")
        else:
            await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz")

    assert isinstance(excinfo.value, CloudError)
    assert excinfo.value.__cause__ is exc
    assert excinfo.value.reason is reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected", "reason"),
    [
        (502, RestServerError, CloudErrorReason.SERVER_ERROR),
        (404, RestClientError, CloudErrorReason.REQUEST_REFUSED),
        (200, RestError, CloudErrorReason.RESPONSE_MALFORMED),
    ],
    ids=["502", "404", "200-not-json"],
)
async def test_a_response_error_is_a_rest_error_not_a_connection_error(status, expected, reason) -> None:
    """The server answered; calling that a broken connection would send
    someone to check their network for nothing. An error status gets its
    HTTP class; a 200 whose body is the problem stays a plain RestError."""
    exc = aiohttp.ContentTypeError(None, (), status=status, message="text/html")
    client = PrimeRestClient(_NetworkFailingSession(exc), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestError) as excinfo:
        await client.get_map_metadata("map123")

    assert type(excinfo.value) is expected
    assert not isinstance(excinfo.value, RestConnectionError)
    assert excinfo.value.status == status
    assert excinfo.value.reason is reason


# ── HTTP status as error classes (0.4.0) ───────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, RestClientError), (404, RestClientError), (409, RestClientError),
        (429, RestRateLimitedError),
        (500, RestServerError), (502, RestServerError), (503, RestServerError),
    ],
)
@pytest.mark.parametrize("call", ["request", "bundle"])
async def test_each_error_status_has_its_class(status, expected, call) -> None:
    session = _FakeSession()
    session.queue_response(status=status, raw_body="server said no")
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(expected) as excinfo:
        if call == "request":
            await client.get_map_metadata("map123")
        else:
            await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz")

    assert isinstance(excinfo.value, RestHTTPError)
    assert excinfo.value.status == status
    assert excinfo.value.reason is reason_for_status(status)
    assert excinfo.value.raw_response == "server said no"


@pytest.mark.asyncio
async def test_a_403_the_relogin_did_not_cure_is_a_client_error() -> None:
    session = _FakeSession()
    session.queue_response(status=403, raw_body="forbidden")
    session.queue_response(status=403, raw_body="still forbidden")

    async def relogin():
        result = MagicMock()
        result.credentials = _dummy_credentials()
        return result

    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials(), relogin=relogin)

    with pytest.raises(RestClientError) as excinfo:
        await client.get_map_metadata("map123")

    assert excinfo.value.status == 403
    assert len(session.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("12", 12.0),
        ("0", 0.0),
        ("-5", 0.0),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0),  # a date in the past: go now
        ("soon", None),
        (None, None),
    ],
    ids=["seconds", "zero", "negative", "past-date", "garbage", "absent"],
)
async def test_a_429_carries_the_wait_the_server_asked_for(header, expected) -> None:
    session = _FakeSession()
    session.queue_response(
        status=429, raw_body="slow down",
        headers={"Retry-After": header} if header is not None else {},
    )
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestRateLimitedError) as excinfo:
        await client.get_map_metadata("map123")

    assert excinfo.value.retry_after == expected


def test_a_future_retry_after_date_is_the_seconds_until_then() -> None:
    import datetime
    import email.utils

    from roombapy_prime.rest_client import _retry_after_seconds

    in_a_minute = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=60)
    seconds = _retry_after_seconds(email.utils.format_datetime(in_a_minute, usegmt=True))

    assert seconds is not None and 55 <= seconds <= 60


class _SlowResponse:
    def __init__(self, delay: float, status: int = 200, body: str = "{}") -> None:
        self.status = status
        self.url = ""
        self._delay = delay
        self._body = body

    async def text(self) -> str:
        return self._body

    async def read(self) -> bytes:
        return self._body.encode()

    async def __aenter__(self) -> _SlowResponse:
        await asyncio.sleep(self._delay)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _SlowSession:
    """Answers after `delay` seconds; `statuses` in order, then 200."""

    def __init__(self, delay: float, statuses: list[int] | None = None) -> None:
        self._delay = delay
        self._statuses = list(statuses or [])
        self.calls = 0

    def get(self, *args: object, **kwargs: object) -> _SlowResponse:
        self.calls += 1
        status = self._statuses.pop(0) if self._statuses else 200
        return _SlowResponse(self._delay, status)


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["request", "bundle"])
async def test_a_request_that_takes_too_long_is_a_timeout(call) -> None:
    client = PrimeRestClient(
        _SlowSession(delay=1.0), HTTP_BASE_AUTH, _dummy_credentials(), request_timeout=0.05
    )

    with pytest.raises(RestTimeoutError):
        if call == "request":
            await client.get_map_metadata("map123")
        else:
            await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz")


@pytest.mark.asyncio
async def test_the_default_timeout_is_thirty_seconds_and_none_means_no_limit() -> None:
    assert PrimeRestClient(None, HTTP_BASE_AUTH, _dummy_credentials())._request_timeout == 30.0
    client = PrimeRestClient(
        _SlowSession(delay=0.05), HTTP_BASE_AUTH, _dummy_credentials(), request_timeout=None
    )
    await client.get_map_metadata("map123")


@pytest.mark.asyncio
async def test_a_slow_relogin_does_not_eat_the_requests_time() -> None:
    """The relogin runs outside the timeout, and the retry gets its own."""
    from roombapy_prime.auth import LoginResult

    async def slow_relogin() -> LoginResult:
        await asyncio.sleep(0.3)
        result = MagicMock(spec=LoginResult)
        result.credentials = _dummy_credentials()
        return result

    session = _SlowSession(delay=0.05, statuses=[403])
    client = PrimeRestClient(
        session, HTTP_BASE_AUTH, _dummy_credentials(),
        relogin=slow_relogin, request_timeout=0.2,
    )

    await client.get_map_metadata("map123")

    assert session.calls == 2


class TestEditMapResponseType:
    """`response_type` is the least-verified part of the map-edit
    request, and a real edit keeps failing with HTTP 500.

    Two field runs (DaRealGuGu) resent two untouched zones and got a
    500 both times -- once with a payload carrying a genuine extra
    coordinate, and again after that was corrected. So the extra point
    was a real deviation from the documented format but demonstrably
    not the cause.

    "link" asks the server for a presigned DOWNLOAD url. That is
    confirmed for FETCHING a map; on an EDIT it may be meaningless.
    This module's own docstring flagged it as unverified from the
    start, which is why it became a parameter rather than a silently
    changed default -- swapping one unverified guess for another would
    leave us equally uninformed."""

    def _client(self):
        from unittest.mock import AsyncMock

        from roombapy_prime.rest_client import PrimeRestClient

        client = object.__new__(PrimeRestClient)
        client._http_base_auth = "https://auth.example"
        client._request = AsyncMock(return_value={"ok": True})
        return client

    def _command(self):
        from unittest.mock import MagicMock

        command = MagicMock()
        command.to_v1_command_body.return_value = {"command": "set_virtual_wall", "params": {}}
        return command

    def _body(self, client):
        return client._request.call_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_the_default_still_sends_link(self):
        """Unchanged behaviour for existing callers -- this is a new
        option, not a new default."""
        client = self._client()

        await client.edit_map("MAP1", self._command())

        assert self._body(client)["response_type"] == "link"

    @pytest.mark.asyncio
    async def test_none_omits_the_key_entirely(self):
        """Not an empty string, not null -- the key must be absent, so
        the server sees a request that never mentions it."""
        client = self._client()

        await client.edit_map("MAP1", self._command(), response_type=None)

        assert "response_type" not in self._body(client)

    @pytest.mark.asyncio
    async def test_an_explicit_value_is_passed_through(self):
        client = self._client()

        await client.edit_map("MAP1", self._command(), response_type="binary")

        assert self._body(client)["response_type"] == "binary"

    @pytest.mark.asyncio
    async def test_the_command_body_is_unaffected_by_the_variant(self):
        """The point of varying only the envelope: if a variant works,
        it has to be the envelope that mattered, not the command."""
        client = self._client()

        bodies = []
        for response_type in (None, "link", "binary"):
            await client.edit_map("MAP1", self._command(), response_type=response_type)
            bodies.append(self._body(client)["edit_cmd"])

        assert bodies[0] == bodies[1] == bodies[2]


@pytest.mark.asyncio
async def test_update_schedules_was_never_affected_by_the_create_bug() -> None:
    """The two paths disagreed about a shape only one of them had
    confirmed, in the same module.

    create_schedules() takes ScheduleOptions and put them straight into
    the array -- wrong. update_schedules() takes HouseholdSchedule, and
    HouseholdSchedule.to_json() already emits {schedule_id, options} --
    right, by accident of which type each signature happened to take.

    That is why toggling a schedule worked in the field for months
    while creating one never did. Worth a test of its own so a future
    refactor cannot quietly align them on the wrong shape.
    """
    from roombapy_prime.models.schedules_dnd import HouseholdSchedule

    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.update_schedules("hh1", "hs1", [
        HouseholdSchedule.from_json(
            {"schedule_id": "s1", "options": {"name": "Morning", "enabled": False}}
        )
    ])

    entry = session.calls[0].body_json["schedules"][0]
    assert set(entry) == {"schedule_id", "options"}
    assert entry["schedule_id"] == "s1"
    assert entry["options"]["enabled"] is False


class TestFavouritesParseWhicheverSpellingArrives:
    """The model's own `to_json` disagreed with this parser. It writes
    `default`, `deleted`, `hidden` and `commanddefs`; the parser read
    `favorite_id`, `display_order` and `modification_secs`. Both were
    written from the app's source, and the parser's docstring admitted
    nobody had seen a real response.

    A favourite whose id does not parse is dropped by the caller -- so a
    mismatch produces an account with no favourites rather than an
    error. @chairstacker has seven and saw no buttons.
    """

    def _parsed(self, raw):
        from roombapy_prime.rest_client import PrimeRestClient

        return PrimeRestClient._favorite_from_json(raw)

    def test_the_spelling_the_model_itself_writes(self):
        favorite = self._parsed({"favoriteid": "F1", "name": "Kitchen"})

        assert favorite.favorite_id == "F1"

    def test_the_spelling_the_parser_expected(self):
        favorite = self._parsed({"favorite_id": "F2", "name": "Hall"})

        assert favorite.favorite_id == "F2"

    def test_a_bare_id_also_works(self):
        assert self._parsed({"id": "F3"}).favorite_id == "F3"

    def test_command_defs_under_either_name(self):
        assert self._parsed({"favoriteid": "F", "commanddefs": []}) is not None
        assert self._parsed({"favoriteid": "F", "command_defs": []}) is not None

    def test_nothing_recognisable_leaves_the_id_empty(self):
        """The caller drops those, which is right -- a favourite with no
        id cannot be run."""
        assert self._parsed({"name": "Nameless"}).favorite_id is None

    def test_the_first_spelling_present_wins(self):
        """Both at once should not be ambiguous. Order is the order
        given, and the app's own is first."""
        favorite = self._parsed({"favoriteid": "A", "favorite_id": "B"})

        assert favorite.favorite_id == "A"


class TestTheTimeEstimateBodyHasFourFields:
    """`TimeEstimatesRequestBody` declares `robot_id`, `smart_map_id`,
    `region_id` and `zone_id`. This client sent one.

    Sending only `robot_id` asks for every estimate on every map — which
    works, and is what a caller wanting one room's number pays for.
    """

    async def _body(self, **kwargs):
        from unittest.mock import AsyncMock, patch

        from roombapy_prime.rest_client import PrimeRestClient

        client = object.__new__(PrimeRestClient)
        client._http_base_auth = "https://x"
        with patch.object(
            client, "_request", AsyncMock(return_value={}), create=True
        ) as req:
            await PrimeRestClient.get_time_estimates(client, "BLID", **kwargs)
        return req.await_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_the_broad_request_is_unchanged(self):
        """The shape field-confirmed on two accounts."""
        assert await self._body() == {"robot_id": "BLID"}

    @pytest.mark.asyncio
    async def test_a_map_can_be_named(self):
        body = await self._body(smart_map_id="M1")

        assert body == {"robot_id": "BLID", "smart_map_id": "M1"}

    @pytest.mark.asyncio
    async def test_a_room_can_be_named(self):
        body = await self._body(smart_map_id="M1", region_id="11")

        assert body["region_id"] == "11"

    @pytest.mark.asyncio
    async def test_none_means_omitted_not_null(self):
        """A JSON null is a value the server may reject; an absent key
        is what the DTO's nullability actually describes."""
        body = await self._body(zone_id=None)

        assert "zone_id" not in body


class TestThePartResetCarriesTheAppsBody:
    """0.6.0. The body is `AssetResetHealthPayloadDto`: `parts`, a list
    of `AssetPartResetDto` (`part_id`, `counter`), and nothing else.

    0.5.0 sent `robot_id` and `num_parts` as well -- the fields of
    `AssetHealthResetDto`, which is what the request ANSWERS with, not
    what it sends. A body modelled on the response.
    """

    async def _body(self, part_ids, counters=None):
        from unittest.mock import AsyncMock, patch

        from roombapy_prime.rest_client import PrimeRestClient

        client = object.__new__(PrimeRestClient)
        client._http_base_auth = "https://x"
        with patch.object(
            client, "_request", AsyncMock(return_value={}), create=True
        ) as req:
            await PrimeRestClient.reset_robot_parts(
                client, "BLID", part_ids, counters
            )
        return req.await_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_the_body_is_parts_and_nothing_else(self):
        """The app's own request: one part, counter 0."""
        assert await self._body(["35"]) == {"parts": [{"part_id": "35", "counter": 0}]}

    @pytest.mark.asyncio
    async def test_several_parts_are_sent_as_objects_not_strings(self):
        body = await self._body(["67", "72"])

        assert body["parts"] == [
            {"part_id": "67", "counter": 0},
            {"part_id": "72", "counter": 0},
        ]

    @pytest.mark.asyncio
    async def test_an_explicit_counter_wins_over_the_default(self):
        body = await self._body(["67", "72"], {"72": 5})

        assert body["parts"] == [
            {"part_id": "67", "counter": 0},
            {"part_id": "72", "counter": 5},
        ]

    @pytest.mark.asyncio
    async def test_numeric_ids_and_counter_keys_match(self):
        """A caller holding part ids as numbers: the id goes out as a
        string, and its counter is still found."""
        body = await self._body([72], {72: 5})

        assert body["parts"] == [{"part_id": "72", "counter": 5}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("part_ids", [[], [""]])
    async def test_no_part_is_an_error_not_a_request(self, part_ids):
        """There is no "reset everything" body; 0.5.0's `robot_id`-only
        request named nothing to reset."""
        with pytest.raises(ValueError, match="at least one part id"):
            await self._body(part_ids)


class TestThePartsCatalogue:
    """0.6.0: the content host's catalogue, unauthenticated as the app's
    `ContentStackHost` is, so no SigV4 headers."""

    _ANSWER = {
        "buyPartsUrl": "https://store.example/parts",
        "parts": [
            {
                "part_id": "30", "part_name": "Filter", "part_name_id": "care_filter_dmc",
                "sku": "4419682,4639161", "clean_interval": "",
                "clean_interval_text_id": "clean_interval_1_2_weeks_dmc",
                "replace_interval": "", "replace_interval_text_id": None,
                "image": "filter", "guide_url": "https://help.example/web/parts/30.html",
                "buy_url": "https://directory.example/30", "robot_health_image": None,
                "robot_health_description_id": None, "scripted_ID": None,
                "part_category": None,
            },
            {"part_id": 31, "counter_enabled": False},
            "not a part",
        ],
    }

    @pytest.mark.asyncio
    async def test_the_request_is_unsigned_on_the_content_host(self) -> None:
        session = _FakeSession()
        session.queue_response(payload=self._ANSWER)
        client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

        catalog = await client.get_parts_catalog("R980020", "de-DE", "DE")

        call = session.calls[0]
        assert (call.method, call.url) == (
            "GET", "https://content-prod.iot.irobotapi.com/v2/de-DE/DE/R980020/parts"
        )
        assert call.headers == {"Content-Type": "application/json"}
        assert catalog.buy_parts_url == "https://store.example/parts"

    @pytest.mark.asyncio
    async def test_every_known_field_is_read(self) -> None:
        session = _FakeSession()
        session.queue_response(payload=self._ANSWER)
        client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

        catalog = await client.get_parts_catalog("R980020")

        assert session.calls[0].url.endswith("/v2/en-US/US/R980020/parts")
        assert [p.part_id for p in catalog.parts] == ["30", "31"]
        part = catalog.part("30")
        assert part is not None
        assert (part.part_name, part.sku, part.clean_interval_text_id) == (
            "Filter", "4419682,4639161", "clean_interval_1_2_weeks_dmc"
        )
        assert (part.guide_url, part.buy_url, part.image) == (
            "https://help.example/web/parts/30.html", "https://directory.example/30", "filter"
        )
        assert part.counter_enabled is None
        assert catalog.part(31) is not None
        assert catalog.part("31").counter_enabled is False
        assert catalog.part("99") is None

    @pytest.mark.asyncio
    async def test_path_segments_are_escaped(self) -> None:
        session = _FakeSession()
        session.queue_response(payload={})
        client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

        catalog = await client.get_parts_catalog("../x", "en/US", "US")

        assert session.calls[0].url == (
            "https://content-prod.iot.irobotapi.com/v2/en%2FUS/US/..%2Fx/parts"
        )
        assert catalog.parts == []

    @pytest.mark.asyncio
    async def test_an_http_error_is_a_rest_error(self) -> None:
        session = _FakeSession()
        session.queue_response(status=404, raw_body="not found")
        client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

        with pytest.raises(RestHTTPError):
            await client.get_parts_catalog_raw("X")


class TestFavouritesArriveInTwoShapes:
    """@chairstacker's two favourites appear as buttons on Roomba+
    v3.5.1 and not on the alpha — the same account, the same endpoint,
    two answers.

    v3.5.1 reaches `/v1/user/favorites` through the Classic cloud
    client, which does:

        result if isinstance(result, list) else result.get("favorites", [])

    This side returned `[]` for the object form. **That unwrap has been
    in the Classic path since it was written**, so the wrapped shape is
    not new server behaviour — this side simply never handled it.
    """

    async def _favourites(self, response):
        from unittest.mock import AsyncMock, patch

        from roombapy_prime.rest_client import PrimeRestClient

        client = object.__new__(PrimeRestClient)
        client._http_base_auth = "https://x"
        with patch.object(
            client, "_request", AsyncMock(return_value=response), create=True
        ):
            return await PrimeRestClient.get_favorites(client)

    @pytest.mark.asyncio
    async def test_a_bare_list_still_works(self):
        result = await self._favourites([{"favoriteid": "F1", "name": "Kitchen"}])

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_an_object_wrapping_the_list_is_unwrapped(self):
        """The shape the Classic path has always handled."""
        result = await self._favourites(
            {"favorites": [{"favoriteid": "F1", "name": "Kitchen"}]}
        )

        assert len(result) == 1
        assert result[0].name == "Kitchen"

    @pytest.mark.asyncio
    async def test_an_object_without_the_key_is_empty_not_a_crash(self):
        assert await self._favourites({"error": "nope"}) == []

    @pytest.mark.asyncio
    async def test_neither_shape_is_reported_rather_than_swallowed(self):
        """A string or a number is not "no favourites" -- it is a
        response nobody expected, and it should say so."""
        assert await self._favourites("nonsense") == []


class TestTheResetBodyIsAvailableButNotForced:
    """`ResetRequest$Body` declares `robot_password`, `synchronous` and
    `send_wipe`. This endpoint sent no body at all, so `send_wipe` — the
    field that decides how destructive a reset is — was left to a server
    default nobody here knows.

    Default behaviour is unchanged on purpose. Nothing has ever called
    this endpoint, and switching an untested call to a different
    untested one is a poor trade on an operation that may wipe a robot.
    """

    async def _sent(self, **kwargs):
        from unittest.mock import AsyncMock, patch

        from roombapy_prime.rest_client import PrimeRestClient

        client = object.__new__(PrimeRestClient)
        client._http_base_auth = "https://x"
        with patch.object(
            client, "_request", AsyncMock(return_value={}), create=True
        ) as req:
            await PrimeRestClient.reset_robot(client, "BLID", **kwargs)
        return req.await_args

    @pytest.mark.asyncio
    async def test_no_arguments_still_sends_no_body(self):
        call = await self._sent()

        assert "body" not in call.kwargs

    @pytest.mark.asyncio
    async def test_send_wipe_false_is_expressible(self):
        """The conservative choice, and the one worth trying first if
        this is ever exercised."""
        call = await self._sent(send_wipe=False)

        assert call.kwargs["body"] == {"send_wipe": False}

    @pytest.mark.asyncio
    async def test_all_three_fields_go_out_when_given(self):
        call = await self._sent(
            robot_password="pw", synchronous=True, send_wipe=True
        )

        assert call.kwargs["body"] == {
            "robot_password": "pw",
            "synchronous": True,
            "send_wipe": True,
        }


class TestTheRawMapEndpointWasNeverImplemented:
    """`_MapFetcherServiceChannel` lists `fetchMapRawData` beside
    `fetchMapGeoJson`, and only one of the two existed here — the
    GeoJSON one, because that is the one a bundle search turned up.

    Found by diffing the app's service channels against this client, a
    comparison nobody had made. Reading the research prose did not
    surface it; the channel list is data, and data can be diffed.
    """

    async def _url(self, **kwargs):
        from unittest.mock import AsyncMock, patch

        from roombapy_prime.rest_client import PrimeRestClient

        client = object.__new__(PrimeRestClient)
        client._http_base_auth = "https://x"
        with patch.object(
            client, "_request", AsyncMock(return_value={}), create=True
        ) as req:
            await PrimeRestClient.get_map_raw_link(client, "m1", "v2", **kwargs)
        return req.await_args

    @pytest.mark.asyncio
    async def test_it_is_the_geojson_url_with_raw(self):
        call = await self._url()

        assert call.args[1].endswith("/v1/p2maps/m1/versions/v2/raw")

    @pytest.mark.asyncio
    async def test_the_response_type_is_forwarded(self):
        call = await self._url()

        assert call.kwargs["query"] == {"response_type": "link"}

    @pytest.mark.asyncio
    async def test_it_can_be_asked_for_without_a_link(self):
        call = await self._url(response_type=None)

        assert call.kwargs["query"] == {}


class TestFirmwareCatalogueParameters:
    """The 403 was the wrong host, not a permission.

    We called `/v2/firmware` against `httpBaseAuth` (the SigV4 gateway)
    and read the 403 as "the consumer role has no invoke rights" —
    correct reading, wrong conclusion. The catalogue is on the content
    host and needs no auth.

    Found by samm-git/irobot-explore's reconstruction of app 1.6.0 and
    confirmed against 3.0.0's own `FirmwareRequest`, which declares
    **six** parameters where the reference names four.
    """

    @staticmethod
    async def _url(**kwargs):
        from unittest.mock import AsyncMock

        from roombapy_prime.rest_client import PrimeRestClient

        client = object.__new__(PrimeRestClient)
        client._request = AsyncMock(return_value={})
        await PrimeRestClient.get_firmware_raw(client, **kwargs)
        return client._request.call_args.args[1]

    @pytest.mark.asyncio
    async def test_it_uses_the_content_host(self):
        url = await self._url(sku="W155040")

        assert url.startswith("https://content-prod.iot.irobotapi.com/v2/firmware")

    @pytest.mark.asyncio
    async def test_the_plus_in_a_version_is_encoded(self):
        """`p25-705+9.3.6+I3.8.149` -- an unencoded `+` becomes a space
        and the lookup silently misses."""
        url = await self._url(software_ver="p25-705+9.3.6+I3.8.149")

        assert "%2B" in url
        assert "+9.3.6" not in url

    @pytest.mark.asyncio
    async def test_optional_parameters_are_omitted(self):
        """`FirmwareRequest` sets four of six only when non-null.
        Sending `track=prod&dockFwVer=` unconditionally is a guess about
        defaults, not what the app does."""
        url = await self._url(sku="W155040")

        for key in ("track", "dockFwVer", "dockFwVerSec", "dockHwRev"):
            assert key not in url

    @pytest.mark.asyncio
    async def test_the_two_the_reference_missed_are_available(self):
        url = await self._url(
            sku="W155040", dock_fw_ver_sec="2", dock_hw_rev="A"
        )

        assert "dockFwVerSec=2" in url
        assert "dockHwRev=A" in url


# ── Classic parity (0.4.0) ─────────────────────────────────────────────────
#
# The Classic calls moved here from ha_roomba_plus' cloud_api.py must send
# what 4.2.12 sent -- that request is the one confirmed on Classic robots.
# The expected signatures below were recorded from cloud_api.py of
# ha_roomba_plus 4.2.12 with the same host, credentials and clock, so an
# equal signature proves an equal canonical request: method, path, every
# query key and value, and the body bytes. The query and body are also
# checked directly, so a failure says which part moved.
#
# Cross-checked once against a live local aiohttp server as well: both
# implementations put the same raw path and query on the wire, key order
# included.

import datetime as _dt
from pathlib import Path
from unittest.mock import patch

from roombapy_prime import aws_sigv4

_CLASSIC_FIXTURES = Path(__file__).parent / "fixtures"
_FROZEN = _dt.datetime(2026, 9, 24, 12, 0, 0, tzinfo=_dt.UTC)


class _FrozenDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _FROZEN


def _signature(call: _RecordedCall) -> str:
    assert call.headers is not None
    return call.headers["Authorization"].split("Signature=")[1]


def _classic_fixture(name: str):
    return json.loads((_CLASSIC_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_classic_get_pmaps_matches_4_2_12_and_parses_capture() -> None:
    session = _FakeSession()
    session.queue_response(raw_body=json.dumps(_classic_fixture("classic_pmaps_i3plus.json")))
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        result = await client.get_pmaps("BLID1")

    call = session.calls[0]
    assert (call.method, call.url) == ("GET", f"{HTTP_BASE_AUTH}/v1/BLID1/pmaps")
    assert call.params == {"visible": "true", "activeDetails": "2"}
    assert _signature(call) == "cc35dc4170f87a9721229eb465660c29a772501fdbaa739c2542f74084f4e233"
    assert len(result) == 1
    assert {"pmap_id", "active_pmapv_id", "active_pmapv_details"} <= set(result[0])


@pytest.mark.asyncio
async def test_classic_get_pmaps_non_list_is_empty() -> None:
    session = _FakeSession()
    session.queue_response(payload={"unexpected": True})
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    assert await client.get_pmaps("BLID1") == []


@pytest.mark.asyncio
async def test_classic_get_pmap_umf_matches_4_2_12() -> None:
    """SYNTHETIC response: no capture of this endpoint's answer exists
    (the integration's UMF fixture is a mission map, see fixtures/README)."""
    session = _FakeSession()
    session.queue_response(payload={"format_version": 1, "maps": []})
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        result = await client.get_pmap_umf("BLID1", "PM1", "V1")

    call = session.calls[0]
    assert call.url == f"{HTTP_BASE_AUTH}/v1/BLID1/pmaps/PM1/versions/V1/umf"
    assert call.params == {"activeDetails": "2"}
    assert _signature(call) == "192725f36bd74d0539122420838a71ea2a937db2a193965edbc5acddbdb485b5"
    assert result == {"format_version": 1, "maps": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["[]", "[1]", '"x"', "", "{}"],
                         ids=["empty-list", "list", "text", "empty-body", "empty-object"])
async def test_classic_get_pmap_umf_without_a_map_is_an_error_not_an_empty_map(body) -> None:
    """An empty dict would be drawn as a blank map; ha_roomba_plus
    reported the map as unavailable instead, and still should. An empty
    body is the sly one: the request layer turns it into `{}`."""
    session = _FakeSession()
    session.queue_response(raw_body=body)
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestError, match="not a non-empty object") as excinfo:
        await client.get_pmap_umf("BLID1", "PM1", "V1")

    assert excinfo.value.reason is CloudErrorReason.RESPONSE_MALFORMED


@pytest.mark.asyncio
async def test_classic_mission_history_matches_4_2_12_and_returns_the_list() -> None:
    session = _FakeSession()
    session.queue_response(
        raw_body=json.dumps(_classic_fixture("classic_missionhistory_i3plus.json"))
    )
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        result = await client.get_mission_history(
            "BLID1",
            app_id="IOS-APPID",
            filter_type="omit_quickly_canceled_not_scheduled",
            supported_done_codes=["dndEnd", "returnHomeEnd"],
            count=50,
            before=1780000000,
        )

    call = session.calls[0]
    assert call.url == f"{HTTP_BASE_AUTH}/v1/BLID1/missionhistory"
    # Order is part of "the same request": dicts keep insertion order and
    # aiohttp writes the query in that order.
    assert list(call.params.items()) == [
        ("app_id", "IOS-APPID"),
        ("filterType", "omit_quickly_canceled_not_scheduled"),
        ("supportedDoneCodes", "dndEnd,returnHomeEnd"),
        ("count", "50"),
        ("before", "1780000000"),
    ]
    assert _signature(call) == "2cbfe39884de19c6622c0fa6a6d3c7c5a941aa7f45d67663306360de5f88269b"
    assert isinstance(result, list) and len(result) == 3


@pytest.mark.asyncio
async def test_classic_robot_parts_read_matches_4_2_12_and_parses_capture() -> None:
    session = _FakeSession()
    session.queue_response(raw_body=json.dumps(_classic_fixture("classic_parts_i3plus.json")))
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        result = await client.get_robot_parts("BLID1")

    assert _signature(session.calls[0]) == (
        "25c5dae5e04c05a8bada28ac0013ff9d15434cd53e01d7e596f6d3698c2ce641"
    )
    assert result.num_parts == 4
    assert {p.part_id for p in result.parts} == {"35", "36", "37", "139"}
    assert all(p.count_used is not None for p in result.parts)


@pytest.mark.asyncio
async def test_classic_set_robot_part_counter_matches_4_2_12_byte_for_byte() -> None:
    session = _FakeSession()
    session.queue_response(payload={"robot_id": "BLID1", "num_parts": 1, "parts": []})
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        result = await client.set_robot_part_counter("BLID1", "35", 0)

    call = session.calls[0]
    assert (call.method, call.url) == ("POST", f"{HTTP_BASE_AUTH}/v1/robots/BLID1/parts")
    assert call.data == b'{"parts":[{"part_id":"35","counter":0}]}'
    assert _signature(call) == "4968bb135d7e1cbbf1d5e980d36e04b0e2466626c72e35e9adfd1135029cb849"
    assert result["num_parts"] == 1


@pytest.mark.asyncio
async def test_prime_bodies_keep_their_spacing() -> None:
    """compact_body is Classic's; the Prime writes keep json.dumps()'s
    default, the bytes their own field tests sent."""
    session = _FakeSession()
    session.queue_response(payload={})
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.reset_robot_parts("BLID1", ["35"])

    assert session.calls[0].data == b'{"parts": [{"part_id": "35", "counter": 0}]}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "expected_signature"),
    [
        ("get_favorites_raw", "/v1/user/favorites",
         "9956b526066ce18e691e232e4406a3b27d52da67024e126c1b77978e042b5b83"),
        ("get_automations_raw", "/v1/user/automations",
         "b229f6a327460045cd8a107d85b763d6f618ae3f55a14df7e580520391a04021"),
        ("get_favorites", "/v1/user/favorites",
         "9956b526066ce18e691e232e4406a3b27d52da67024e126c1b77978e042b5b83"),
        ("get_automations", "/v1/user/automations",
         "b229f6a327460045cd8a107d85b763d6f618ae3f55a14df7e580520391a04021"),
    ],
)
async def test_classic_account_reads_match_4_2_12(
    method: str, path: str, expected_signature: str
) -> None:
    """Called with no arguments, as the integration will: Classic's
    favorites default is no app_edition (the Prime client's is 1)."""
    session = _FakeSession()
    session.queue_response(raw_body="[]")
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        await getattr(client, method)()

    call = session.calls[0]
    assert call.url == f"{HTTP_BASE_AUTH}{path}"
    assert not call.params
    assert _signature(call) == expected_signature


# ── One base, two generations (0.4.0) ──────────────────────────────────────


def test_both_clients_share_the_base_and_keep_to_their_generation() -> None:
    """Each client offers only what its generation answers. A Classic
    robot has no p2maps and a Prime robot no pmaps, and a method that
    exists on the wrong client is one somebody will call."""
    assert issubclass(PrimeRestClient, CloudRestClient)
    assert issubclass(ClassicRestClient, CloudRestClient)
    for classic_only in ("get_pmaps", "get_pmap_umf", "set_robot_part_counter"):
        assert hasattr(ClassicRestClient, classic_only)
        assert not hasattr(PrimeRestClient, classic_only), classic_only
    for prime_only in ("get_active_map_versions", "reset_robot_parts", "get_schedules"):
        assert hasattr(PrimeRestClient, prime_only)
        assert not hasattr(ClassicRestClient, prime_only), prime_only
    for shared in ("get_robot_parts", "get_favorites_raw", "get_automations_raw"):
        assert shared in vars(CloudRestClient), shared


@pytest.mark.asyncio
async def test_prime_favorites_default_is_unchanged() -> None:
    """Moving get_favorites_raw() into the base must not change what the
    Prime client sends by default."""
    session = _FakeSession()
    session.queue_response(raw_body="[]")
    client = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_favorites_raw()

    assert session.calls[0].params == {"app_edition": "1"}


@pytest.mark.asyncio
async def test_each_mission_history_takes_only_its_own_parameters() -> None:
    session = _FakeSession()
    prime = PrimeRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())
    classic = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(TypeError):
        await prime.get_mission_history("B", count=5)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        await classic.get_mission_history("B", max_reports=5)  # type: ignore[call-arg]


# ── Classic defaults and normalised answers (0.4.0) ────────────────────────


@pytest.mark.asyncio
async def test_classic_mission_history_default_call_is_the_4_2_12_request() -> None:
    """get_mission_history(blid) alone -- the call ha_roomba_plus makes
    most -- sends the Classic app's defaults and the client's app id."""
    session = _FakeSession()
    session.queue_response(raw_body="[]")
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials(), app_id="IOS-APPID")

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        await client.get_mission_history("BLID1")

    call = session.calls[0]
    assert list(call.params.items()) == [
        ("app_id", "IOS-APPID"),
        ("filterType", "omit_quickly_canceled_not_scheduled"),
        ("supportedDoneCodes", "dndEnd,returnHomeEnd"),
        ("count", "100"),
    ]
    assert _signature(call) == "2e4072ec8e82de02c0d99a602f66b5065d3644bbef7abc35882d8c7b753ecc1f"


@pytest.mark.asyncio
async def test_classic_mission_history_page_is_the_measured_prime_request() -> None:
    """0.4.0b3. The page request must be the one the 980 and the i7
    answered correctly -- PrimeRestClient's, key for key and in its
    order -- not the Classic one they ignored. No app_id, even though
    this client has one: the measured request carried none."""
    classic_session, prime_session = _FakeSession(), _FakeSession()
    classic_session.queue_response(raw_body="[]")
    prime_session.queue_response(raw_body="[]")
    classic = ClassicRestClient(
        classic_session, HTTP_BASE_AUTH, _dummy_credentials(), app_id="IOS-APPID"
    )
    prime = PrimeRestClient(prime_session, HTTP_BASE_AUTH, _dummy_credentials())

    with patch.object(aws_sigv4, "datetime", _FrozenDatetime):
        result = await classic.get_mission_history_page("BLID1", before=1780000000, page_size=10)
        await prime.get_mission_history(
            "BLID1", max_reports=10, filter_type="omit_quickly_canceled_not_scheduled",
            exclusive_start_timestamp=1780000000,
            supported_done_codes=["dndEnd", "returnHomeEnd"],
        )

    call = classic_session.calls[0]
    assert (call.method, call.url) == ("GET", f"{HTTP_BASE_AUTH}/v1/BLID1/missionhistory")
    assert list(call.params.items()) == [
        ("maxReports", "10"),
        ("filterType", "omit_quickly_canceled_not_scheduled"),
        ("exclusiveStartTimestamp", "1780000000"),
        ("supportedDoneCodes", "dndEnd,returnHomeEnd"),
    ]
    assert list(call.params.items()) == list(prime_session.calls[0].params.items())
    assert _signature(call) == _signature(prime_session.calls[0])
    assert result == []


@pytest.mark.asyncio
async def test_classic_mission_history_first_page_and_none_leave_keys_out() -> None:
    session = _FakeSession()
    session.queue_response(raw_body="[]")
    session.queue_response(raw_body="[]")
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_mission_history_page("BLID1")
    await client.get_mission_history_page(
        "BLID1", page_size=None, filter_type=None, supported_done_codes=None
    )

    assert list(session.calls[0].params.items()) == [
        ("maxReports", "100"),
        ("filterType", "omit_quickly_canceled_not_scheduled"),
        ("supportedDoneCodes", "dndEnd,returnHomeEnd"),
    ]
    assert session.calls[1].params == {}


@pytest.mark.asyncio
async def test_classic_mission_history_none_leaves_a_key_out() -> None:
    session = _FakeSession()
    session.queue_response(raw_body="[]")
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    await client.get_mission_history(
        "BLID1", filter_type=None, supported_done_codes=None, count=None
    )

    assert session.calls[0].params == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ([{"favorite_id": "F"}], [{"favorite_id": "F"}]),
        ({"favorites": [{"favorite_id": "F"}]}, [{"favorite_id": "F"}]),
        ({"something_else": 1}, []),
        ({"favorites": "not a list"}, []),
        ("text", []),
    ],
    ids=["list", "wrapped", "object-without-favorites", "favorites-not-a-list", "scalar"],
)
async def test_classic_favorites_are_only_favorites(answer, expected) -> None:
    """An object without a list is no favourites -- not one favourite
    made of the whole answer, which became a nameless button."""
    session = _FakeSession()
    session.queue_response(raw_body=json.dumps(answer))
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    assert await client.get_favorites() == expected
    assert not session.calls[0].params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected"),
    [({"automations": []}, {"automations": []}), ([1, 2], {}), ("text", {})],
    ids=["object", "list", "scalar"],
)
async def test_classic_automations_are_an_object(answer, expected) -> None:
    session = _FakeSession()
    session.queue_response(raw_body=json.dumps(answer))
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    assert await client.get_automations() == expected


@pytest.mark.asyncio
async def test_robot_parts_raw_keeps_every_key_the_model_would_drop() -> None:
    capture = _classic_fixture("classic_parts_i3plus.json")
    capture["parts"][0]["a_field_nobody_modelled"] = 7
    session = _FakeSession()
    session.queue_response(raw_body=json.dumps(capture))
    session.queue_response(raw_body=json.dumps(capture))
    client = ClassicRestClient(session, HTTP_BASE_AUTH, _dummy_credentials())

    raw = await client.get_robot_parts_raw("BLID1")
    typed = await client.get_robot_parts("BLID1")

    assert raw == capture
    assert typed.num_parts == 4
    assert session.calls[0].url == session.calls[1].url


# ── reason: what to tell a person (0.4.0) ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("openssl_says", "reason", "wording"),
    [
        ("unable to get local issuer certificate", CloudErrorReason.SSL_LOCAL_TRUST_STORE,
         "waiting will not fix it"),
        ("certificate has expired", CloudErrorReason.SSL_CERTIFICATE_EXPIRED, "on their end"),
        ("some unfamiliar TLS failure", CloudErrorReason.SSL_UNVERIFIED, "trusted-root store"),
    ],
    ids=["local-trust-store", "expired", "unknown"],
)
@pytest.mark.parametrize("call", ["request", "bundle"])
async def test_a_certificate_failure_is_diagnosed_as_login_diagnoses_it(
    openssl_says, reason, wording, call
) -> None:
    """REST said "almost always a temporary problem on iRobot's servers
    ... not something wrong with your setup" for every certificate
    failure -- the claim login had dropped after a field report showed
    it wrong for a local trust store. Now the same three causes, and
    the one that says to wait is only the one where waiting helps."""
    exc = aiohttp.ClientSSLError(None, OSError(openssl_says))
    client = PrimeRestClient(_NetworkFailingSession(exc), HTTP_BASE_AUTH, _dummy_credentials())

    with pytest.raises(RestSSLError) as excinfo:
        if call == "request":
            await client.get_map_metadata("map123")
        else:
            await client.download_map_bundle("https://presigned.example.invalid/bundle.tar.gz")

    assert excinfo.value.reason is reason
    assert wording in str(excinfo.value)
    assert "almost always a temporary problem" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (None, CloudErrorReason.RESPONSE_MALFORMED),
        (200, CloudErrorReason.RESPONSE_MALFORMED),
        (404, CloudErrorReason.REQUEST_REFUSED),
        (429, CloudErrorReason.RATE_LIMITED),
        (502, CloudErrorReason.SERVER_ERROR),
    ],
)
def test_a_plain_rest_error_takes_its_reason_from_its_status(status, reason) -> None:
    """A RestError built elsewhere with an error status must not claim
    the answer was malformed when the status says why."""
    assert RestError("x", status=status).reason is reason


def test_a_rest_error_subclass_keeps_its_own_reason_whatever_the_status() -> None:
    assert RestConnectionError("x", status=502).reason is CloudErrorReason.CONNECTION_FAILED
    assert RestSSLError("x", status=404).reason is CloudErrorReason.SSL_UNVERIFIED
    assert RestRateLimitedError("x").reason is CloudErrorReason.RATE_LIMITED
    assert RestHTTPError("x", 404).reason is CloudErrorReason.REQUEST_REFUSED
    assert RestHTTPError("x", 503).reason is CloudErrorReason.SERVER_ERROR


def test_an_explicit_reason_wins_over_the_status() -> None:
    error = RestError("x", status=502, reason=CloudErrorReason.CONNECTION_BROKEN)
    assert error.reason is CloudErrorReason.CONNECTION_BROKEN
