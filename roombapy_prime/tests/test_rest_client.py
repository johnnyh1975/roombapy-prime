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

import json

import aiohttp
import pytest

from roombapy_prime.auth import CloudCredentials
from roombapy_prime.models import HouseholdSchedule, MergeRooms, ScheduleFrequency, ScheduleOptions
from roombapy_prime.rest_client import (
    PrimeRestClient,
    RestConnectionError,
    RestError,
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
    def __init__(self, status: int, body: str, url: str, raw_bytes: bytes | None = None) -> None:
        self.status = status
        self._body = body
        self.url = url
        self._raw_bytes = raw_bytes

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
        raw_bytes: bytes | None = None,
    ) -> None:
        body = raw_body if raw_body is not None else json.dumps(payload if payload is not None else {})
        self._responses.append(_FakeResponse(status=status, body=body, url="", raw_bytes=raw_bytes))

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
    """CORRECTED (session 46) -- real key is "robot_id" (confirmed via
    ScheduleOptions$$serializer's <clinit>), not "assetId" as
    previously guessed."""
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
        "schedules": [{"robot_id": "asset1", "name": "Morning", "frequency": "WEEKLY"}]
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

    await client.reset_robot_parts("BLID123")

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

    with pytest.raises(RestError, match="Non-JSON"):
        await client.get_map_metadata("map123")


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

    await client.get_time_estimates({"assetId": "BLID123"})

    call = session.calls[0]
    assert call.method == "POST"
    assert call.url == f"{HTTP_BASE_AUTH}/v1/time-estimates"
    assert call.body_json == {"assetId": "BLID123"}


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

    assert "certificate" in str(excinfo.value).lower()
    assert "temporary" in str(excinfo.value).lower()
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
