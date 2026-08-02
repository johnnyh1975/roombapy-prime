"""p2maps REST client: map metadata, edit commands, live stream init.

STATUS: Draft. Endpoints/payload shapes confirmed from Java source code
analysis (see docs/archive/FINDINGS_2026-07-11.md), NOT live-tested against a
real server -- neither Classic nor V4/Prime. Not a single one of
these calls has actually been executed yet.

Also: AWS SigV4 signing (see aws_sigv4.py) and `http_base_auth` instead
of `http_base` -- both carried over from ha_roomba_plus's already-
production cloud_api.py (a third, independent confirmation source
alongside live tests and APK analysis). Previously, `auth_headers` here
was a vague, never-populated passthrough dict -- that was simply
modeled wrong, not just incomplete.

Open questions, deliberately not guessed at:
  - Whether p2maps needs SigV4 signing at all (vs. e.g. one of the
    login tokens as a Bearer header) is an analogy assumption from
    other /v1/ endpoints in the same cloud API family, not a fact
    confirmed for p2maps itself.
  - SigV4 for POST-with-body is MY extension of the original (which
    only signs GET) -- see aws_sigv4.py's docstring.
  - 403 -> reauth retry is carried over 1:1 from cloud_api.py's
    _aws_get() (confirmed there for Classic REST endpoints), applied
    here to p2maps for the first time.
"""
from __future__ import annotations

import json
import logging
import math
import urllib.parse
from collections.abc import Awaitable, Callable
from json.decoder import JSONDecodeError
from typing import Any

import aiohttp

from .auth import CloudCredentials, LoginResult
from .aws_sigv4 import AwsSigV4Signer
from .models import (
    DNDStatusResponse,
    FavoriteV1,
    HouseholdSchedule,
    LiveMapStreamInit,
    MapEditCommand,
    MapEditCommandV1,
    MissionCommandType,
    P2MapData,
    RobotPartsInfo,
    RobotSerialInfo,
    RoutineCommand,
    RoutinesDefaultsResponse,
    ScheduleOptions,
    SchedulesResponse,
)

_LOGGER = logging.getLogger(__name__)

Relogin = Callable[[], Awaitable[LoginResult]]


def _path_segment(value: str) -> str:
    """NEW (session 54, security hardening pass). Every identifier this
    library embeds into a URL path (BLIDs, p2map IDs, favorite IDs,
    household IDs, etc.) was previously interpolated directly via an
    f-string, with no escaping at all. In this library's own normal
    usage these values typically come from a trusted source (this
    library's own login/API responses, or values a developer passes
    directly) -- but this library provides no protection at all if a
    consuming application (e.g. a Home Assistant integration built on
    top of this, which is an explicit goal for this project) ever lets
    a corrupted config value or, in principle, untrusted input reach
    one of these parameters. A value like `"../whatever"` or one
    containing a literal `/` could redirect the request to an
    unintended path on the same host.

    `urllib.parse.quote(value, safe="")` is a no-op for any
    legitimate identifier this API actually uses (BLIDs, UUIDs, and
    similar are alphanumeric/hyphen strings, unaffected by
    URL-encoding) -- this is purely additive safety, not a behavior
    change for any well-formed input. Applied at every URL
    construction site in this file that embeds a caller-supplied
    identifier."""
    return urllib.parse.quote(str(value), safe="")


class RestError(Exception):
    """Raised for any non-2xx response or unparseable body, with the
    raw response text attached where available."""

    def __init__(self, message: str, status: int | None = None, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.raw_response = raw_response


class RestSSLError(RestError):
    """TLS/certificate verification failure -- see
    _raise_clear_ssl_error()."""


class RestConnectionError(RestError):
    """Could not establish a connection at all (DNS failure, connection
    refused, network unreachable) -- see _raise_clear_connection_error().
    Deliberately does NOT claim to know whether this is iRobot's fault
    or the caller's own network."""


class RestTimeoutError(RestError):
    """Request was sent but no response came back in time -- see
    _raise_clear_timeout_error()."""


def _raise_clear_ssl_error(exc: aiohttp.ClientSSLError) -> None:
    """Re-raise an aiohttp SSL/certificate failure as a clear
    RestSSLError instead of letting the raw aiohttp exception bubble
    up as an opaque "unknown error occurred".

    NEW (V4/Prime prep, following the same fix in auth.py). This
    class's own _request() is the single chokepoint nearly every
    endpoint method in this file goes through, so wrapping it there
    covers p2maps, favorites, schedules, DND, mission history, and map
    editing all at once; download_map_bundle() is the one call that
    deliberately bypasses _request() (a different, unsigned host, see
    its own docstring) and is wrapped separately below for the same
    reason."""
    raise RestSSLError(
        "Could not verify iRobot's cloud server certificate. This is "
        "almost always a temporary problem on iRobot's servers (an "
        "expired or currently-renewing TLS certificate), not something "
        "wrong with your setup -- it should resolve on its own within a "
        "few hours."
    ) from exc


def _raise_clear_connection_error(exc: aiohttp.ClientConnectorError) -> None:
    """Re-raise a connection failure (DNS, connection refused, network
    unreachable) as a clear RestConnectionError. See auth.py's
    equivalent for why this deliberately doesn't claim confident fault
    attribution the way _raise_clear_ssl_error() does."""
    raise RestConnectionError(
        "Could not connect to iRobot's cloud servers. This could be a "
        "temporary problem with iRobot's servers, or with your own "
        "internet connection -- check that other internet-dependent "
        "services are working, and try again in a few minutes."
    ) from exc


def _raise_clear_timeout_error(exc: BaseException) -> None:
    """Re-raise a request timeout as a clear RestTimeoutError. Accepts
    BaseException -- see auth.py's equivalent for why."""
    raise RestTimeoutError(
        "iRobot's cloud servers took too long to respond. This is "
        "usually temporary -- please try again in a few minutes."
    ) from exc


class PrimeRestClient:
    """Thin wrapper around the p2maps REST surface. Takes an existing
    aiohttp.ClientSession (same one used for auth.login(), so cookies/
    connection pooling are shared) rather than owning its own.

    credentials: AWS Cognito credentials (see auth.CloudCredentials) --
    every request is SigV4-signed with these, replacing the earlier
    (never-populated) generic auth_headers passthrough.

    relogin: optional async callback that's called exactly once on an
    HTTP 403, to fetch new credentials and retry the call (see
    cloud_api.py's _aux_get() for the original of this pattern). None
    (default) -- no automatic retry, a 403 is passed through as a
    RestError."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        http_base_auth: str,
        credentials: CloudCredentials,
        relogin: Relogin | None = None,
    ) -> None:
        self._session = session
        self._http_base_auth = http_base_auth.rstrip("/")
        self._credentials = credentials
        self._relogin = relogin

    async def get_map_metadata(self, p2map_id: str) -> P2MapData:
        """GET /v1/p2maps/{p2mapId}. CORRECTED (session 51): response
        shape is now confirmed via P2MapData$$serializer -- p2map_id,
        active_p2mapv_id, create_time, last_p2mapv_ts, state, visible,
        name, user_orientation_rad. Previously returned raw JSON
        ("response shape not modeled yet") -- now parsed into
        P2MapData directly."""
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}"
        data = await self._request("GET", url)
        return P2MapData.from_json(data)

    async def get_active_map_versions(self, blid: str) -> list[dict[str, Any]]:
        """GET /v1/p2maps?robotId={blid}&visible=true -- NEW (July 11),
        confirmed from P2MapAPIFetching$fetchActiveVersions$2 (this
        inner coroutine class decompiled cleanly, unlike the three
        fetchPersistentMap/fetchLatestPersistentMap/fetchMissionMap
        equivalents, see PRIME_APP_GAP_ANALYSIS point C2).

        CORRECTED (session 25): the original assumption "at least
        'mapId' and 'mapVersionId'" was WRONG -- a real live response
        (chairstacker) shows the actual fields: `p2map_id`,
        `entity_type`, `create_time`, `robot_id`, `sku`,
        `active_p2mapv_id` (that's the map version ID),
        `last_p2mapv_ts`, `state`, `visible`, `name`, `rooms_metadata`.
        Still passed through here as raw JSON -- for a typed result
        use models/robot_info.py::parse_active_map_versions() (NEW, session 26,
        includes room metadata with reusable CommandParams presets per
        operating mode)."""
        url = f"{self._http_base_auth}/v1/p2maps"
        data = await self._request("GET", url, query={"robotId": blid, "visible": "true"})
        return data if isinstance(data, list) else []

    async def get_map_geojson_link(self, map_id: str, map_version: str) -> dict[str, Any]:
        """NEW (July 11, third session -- after renewed, targeted
        searching). Finally resolves how fetchPersistentMap/
        fetchLatestPersistentMap/fetchMissionMap get their tar.gz map
        bundle (see PRIME_APP_GAP_ANALYSIS point C2, previously marked
        as "not economically resolvable further" -- that was given up
        too early, a broader source-code search for "/versions/" found
        P2MapGeoJSONRequest.java directly):

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#rest_clientget_map_geojson_link
    """
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(map_id)}/versions/{_path_segment(map_version)}/geojson"
        return await self._request("GET", url, query={"response_type": "link"})

    async def download_map_bundle(self, url: str) -> bytes:
        """NEW (July 11, fifth session). Downloads the raw tar.gz map
        bundle from a PRESIGNED URL (see get_map_geojson_link()).

        DELIBERATELY WITHOUT SigV4 signing -- confirmed from P2MapAPI.
        MapUnpacker.fetchMapBundleContentHolder(P2MapIdentifier, URL):
        the app opens the presigned URL directly
        (`mapURL.openConnection()`), with no auth header of its own.
        Presigned URLs (S3-style) typically carry their
        authentication in their own query parameters -- additional
        signing wouldn't just be unnecessary, it would overwrite/
        corrupt the signature the server expects.

        Returns the raw bytes (tar.gz archive) -- for unpacking and
        parsing see models/map_bundle.py::parse_map_bundle(). Separate from
        _request(), since this URL doesn't live under
        self._http_base_auth (typically an S3 bucket or similar CDN
        host) and therefore shouldn't go through this class's SigV4
        signing scheme."""
        try:
            async with self._session.get(url) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RestError(
                        f"HTTP {resp.status} downloading map bundle from {url}",
                        status=resp.status,
                        raw_response=text,
                    )
                return await resp.read()
        except aiohttp.ClientSSLError as exc:
            _raise_clear_ssl_error(exc)
        except aiohttp.ServerTimeoutError as exc:
            _raise_clear_timeout_error(exc)
        except aiohttp.ClientConnectorError as exc:
            _raise_clear_connection_error(exc)

    async def set_map_name(self, p2map_id: str, name: str) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/settings, body {"name": ...}.

        CORRECTED (session 51): confirmed directly via
        EditMapSettingsRequest$Command$SetName$$serializer's
        <clinit> -- real field name is `name`, NOT `type` as
        previously implemented. This was a genuine bug: sending
        `{"type": name}` would very likely have been silently ignored
        or rejected by the real server, not just an unconfirmed
        guess that happened to work."""
        return await self._post_settings(p2map_id, {"name": name})

    async def set_map_orientation(self, p2map_id: str, orientation_rad: float) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/settings, body {"user_orientation_rad": ...}.

        Original clamps the angle into (-pi, pi] before sending (see
        EditMapSettingsRequest$Command$SetUserPreferredOrientation$Companion
        .clampRadians in FINDINGS) -- replicated here rather than trusting
        the caller to have already done it.
        """
        two_pi = 6.283185307179586
        pi = 3.141592653589793
        clamped = orientation_rad - (math.ceil((orientation_rad + pi) / two_pi) - 1) * 2 * pi
        return await self._post_settings(p2map_id, {"user_orientation_rad": clamped})

    async def delete_map(self, p2map_id: str) -> dict[str, Any]:
        """NEW (July 11, third session) -- confirmed from
        DeleteMapRequest.java: despite the name, NOT an HTTP DELETE,
        but a "soft delete" via the same settings endpoint as
        set_map_name()/set_map_orientation():

            POST /v1/p2maps/{p2mapId}/settings?trigger_fast_updates=true
            Body: {"visible": false}

        Field name "visible" -- CONFIRMED (session 50, re-verification
        pass) directly via DeleteMapRequest$Body$$serializer's
        <clinit>: this is the class's only field, confirmed the same
        way as the rest of this project's `$$serializer` findings, not
        merely "found without a @SerialName, presumably" as this
        docstring said before that specific check."""
        return await self._post_settings(p2map_id, {"visible": False})

    async def _post_settings(self, p2map_id: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}/settings"
        return await self._request("POST", url, query={"trigger_fast_updates": "true"}, body=body)

    async def edit_map_v2(self, p2map_id: str, command: MapEditCommand) -> dict[str, Any]:
        """POST /v2/p2maps/{p2mapId}/versions -- NOTE (July 11, fourth
        session): after a full re-decompilation of the app, confirmed
        that requestEditV2() is NEVER called anywhere in the ENTIRE app
        code. This path presumably still exists server-side (the
        endpoint itself isn't made up), but isn't used anywhere by the
        current app version (2.2.4). edit_map() (V1) is the actually
        active path -- see there. Kept available here, renamed from
        edit_map() to edit_map_v2(), so the name no longer suggests
        this is the standard way.

        Response shape (the updated P2PersistentMap, per the Kotlin
        repository interfaces) not modeled -- raw JSON."""
        url = f"{self._http_base_auth}/v2/p2maps/{_path_segment(p2map_id)}/versions"
        return await self._request("POST", url, body=command.to_command_body())

    async def edit_map(
        self, p2map_id: str, command: MapEditCommandV1,
        response_type: str | None = "link",
    ) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/versions -- NEW (July 11, fourth
        session), the ACTUALLY ACTIVE edit path (see models/map_editing.py's V1
        section and PRIME_APP_GAP_ANALYSIS): every single edit
        operation in the app code calls requestEditV1(), never
        requestEditV2(). Replaces the previous default assumption
        (edit_map() = V2) -- the old path is now separately available
        under edit_map_v2(), with a warning that it's unused code.

        CORRECTED (session 48): the request body envelope is now
        confirmed via EditMapV1Request$Body$$serializer --
        {"edit_cmd": {...the command's own fields...}, "response_type":
        "..."}, not the previously-assumed flat structure. See
        MapEditCommandV1's module docstring in models/map_editing.py for the full
        story, including which parts remain unconfirmed (the
        discriminator inside "edit_cmd", and SetRoomMetadata/
        VirtualWall's own custom serializers). `response_type`'s
        correct value for an EDIT (as opposed to the already-confirmed
        "link"/"binary" values for FETCHING a map) is not confirmed --
        left as a simple default here, honestly not verified.

        Response shape not modeled -- raw JSON.

        RESPONSE_TYPE IS NOW A PARAMETER (this session), because it is
        the least-verified part of this request and a real edit keeps
        failing with HTTP 500.

        Two field runs (DaRealGuGu) resent two untouched zones and got
        a 500 both times -- once with a payload that had a genuine
        extra point, and again after that was fixed. So the extra point
        was a real deviation from the documented format but not the
        cause.

        "link" asks the server for a presigned download URL. That is
        the confirmed value for FETCHING a map; for an EDIT it may well
        be meaningless or actively wrong, which would fit a 500 (the
        body parses, then something downstream cannot honour it).
        Passing None omits the key entirely.

        This is deliberately a parameter rather than a changed default:
        nothing has confirmed what the right value is, and quietly
        swapping one unverified guess for another would leave us
        exactly as uninformed."""
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}/versions"
        body: dict[str, Any] = {"edit_cmd": command.to_v1_command_body()}
        if response_type is not None:
            body["response_type"] = response_type
        return await self._request("POST", url, body=body)

    async def get_live_map_stream(self, blid: str) -> LiveMapStreamInit:
        """GET /v1/p2maps/livemap?robotId={blid} -> the MQTT topic to
        subscribe on the already-open AWS IoT connection (see
        mqtt_client.py's docstring and FINDINGS section 2)."""
        url = f"{self._http_base_auth}/v1/p2maps/livemap"
        data = await self._request("GET", url, query={"robotId": blid})
        return LiveMapStreamInit.from_json(data)

    # --- Favorites (FavoriteV1) -------------------------------------------
    #
    # NEW (July 11, fourth session). Base URL and app_edition query param
    # confirmed from FavoriteCommonRequest.java, see models/favorites.py's
    # favorites section for the full derivation including which
    # HTTP methods are confirmed vs. assumed.

    _FAVORITES_QUERY = {"app_edition": "1"}

    async def get_favorites(self) -> list[FavoriteV1]:
        """GET /v1/user/favorites?app_edition=1 -- CONFIRMED (FetchFavoriteRequest,
        httpMethod = "GET")."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        data = await self._request("GET", url, query=self._FAVORITES_QUERY)
        raw_list = data if isinstance(data, list) else []
        return [self._favorite_from_json(item) for item in raw_list]

    async def get_favorites_raw(self) -> list[dict[str, Any]]:
        """Same endpoint as get_favorites(), but returns the UNPARSED
        response. Added (this session) for a round-trip fidelity check:
        if a stored favorite carries fields our own models don't know
        about, parsing and re-serializing it would silently DROP them,
        and we would resend a command that is subtly less complete than
        what the app itself sends -- a failure mode that looks exactly
        like this project's central symptom (structurally valid,
        no effect, no error). Diagnostic use only; nothing in the
        library's normal path should need this."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        data = await self._request("GET", url, query=self._FAVORITES_QUERY)
        return data if isinstance(data, list) else []

    async def create_favorite(self, favorite: FavoriteV1) -> dict[str, Any]:
        """POST /v1/user/favorites?app_edition=1 -- CONFIRMED (eighth
        session: CreateFavoriteRequest.<init> sets httpMethod = "POST"
        directly, androguard bytecode inspection -- previously only
        assumed, since jadx had silently skipped the lambda class for
        this). Response is a FavoriteIdResponse -- CORRECTED (session
        48): its one field's real key is confirmed via
        FavoriteIdResponse$$serializer's <clinit> to be `favorite_id`,
        not a guessed/heuristically-detected key. Passed through here
        as raw JSON (not worth a dedicated dataclass for one field),
        but callers can now reliably do `result["favorite_id"]`."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        return await self._request(
            "POST", url, query=self._FAVORITES_QUERY, body=favorite.to_json()
        )

    async def update_favorite(self, favorite_id: str, favorite: FavoriteV1) -> dict[str, Any]:
        """PUT /v1/user/favorites/{favoriteId}?app_edition=1 --
        CONFIRMED (eighth session: UpdateFavoriteRequest.<init> sets
        httpMethod = "PUT" directly -- previously only assumed)."""
        url = f"{self._http_base_auth}/v1/user/favorites/{_path_segment(favorite_id)}"
        return await self._request(
            "PUT", url, query=self._FAVORITES_QUERY, body=favorite.to_json()
        )

    async def delete_favorite(self, favorite_id: str) -> dict[str, Any]:
        """DELETE /v1/user/favorites/{favoriteId}?app_edition=1 --
        CONFIRMED (DeleteFavoriteRequest, httpMethod = "DELETE")."""
        url = f"{self._http_base_auth}/v1/user/favorites/{_path_segment(favorite_id)}"
        return await self._request("DELETE", url, query=self._FAVORITES_QUERY)

    async def order_favorite(
        self,
        favorite_id: str,
        *,
        insert_at: int | None = None,
        insert_before: str | None = None,
        insert_after: str | None = None,
    ) -> dict[str, Any]:
        """PUT /v1/user/favorites/{favoriteId}/order?app_edition=1 --
        CONFIRMED (OrderFavoriteRequest, httpMethod = "PUT"). CORRECTED:
        insert_at/insert_before/insert_after are QUERY PARAMETERS
        (snake_case: insert_at/insert_before/insert_after), not body
        fields -- bytecode-confirmed from OrderFavoriteRequest.
        getQueryParams() (via androguard/jadx: r0.put("insert_at", ...),
        r0.put("insert_before", ...), r0.put("insert_after", ...)). No
        httpBody found for this request. Presumably exactly one of the
        three is expected -- which combination(s) the server actually
        accepts is not confirmed."""
        url = f"{self._http_base_auth}/v1/user/favorites/{_path_segment(favorite_id)}/order"
        query = dict(self._FAVORITES_QUERY)
        if insert_at is not None:
            query["insert_at"] = str(insert_at)
        if insert_before is not None:
            query["insert_before"] = insert_before
        if insert_after is not None:
            query["insert_after"] = insert_after
        return await self._request("PUT", url, query=query)

    async def get_mission_history(
        self,
        blid: str,
        *,
        max_reports: int | None = None,
        max_age: int | None = None,
        filter_type: str | None = None,
        exclusive_start_timestamp: int | None = None,
        supported_done_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """GET /v1/{blid}/missionhistory -- NEW (July 11, sixth
        session). CONFIRMED from FetchMissionHistoryRequest.java
        (httpMethod = "GET", urlString = "/v1/" + robotId +
        "/missionhistory"). Matches the endpoint of the same name in
        ha_roomba_plus' cloud_api.py for Classic devices -- Prime uses
        the same URL pattern.

        Query parameters all confirmed (camelCase, Kotlin property name
        = wire name, no @SerialName found): maxReports, maxAge,
        filterType, exclusiveStartTimestamp, supportedDoneCodes (list
        joined with commas -- confirmed from
        ProvisioningErrorConstants.LAST_ERROR_INTERNAL_LINE_DELIMITER =
        ","). Response shape NOW modeled (ninth session) --
        models/mission_history.py::parse_mission_history() converts this method's
        result into a list of typed MissionHistoryEntry objects
        (analogous to parse_map_bundle() -- a separate, optional step
        rather than automatic conversion here)."""
        url = f"{self._http_base_auth}/v1/{_path_segment(blid)}/missionhistory"
        query: dict[str, str] = {}
        if max_reports is not None:
            query["maxReports"] = str(max_reports)
        if max_age is not None:
            query["maxAge"] = str(max_age)
        if filter_type is not None:
            query["filterType"] = filter_type
        if exclusive_start_timestamp is not None:
            query["exclusiveStartTimestamp"] = str(exclusive_start_timestamp)
        if supported_done_codes:
            query["supportedDoneCodes"] = ",".join(supported_done_codes)
        return await self._request("GET", url, query=query)

    async def get_schedules(self, household_id: str) -> SchedulesResponse:
        """GET /v1/households/{householdId}/settings/schedule -- NEW
        (July 11, sixth session). CONFIRMED from SchedulesCommonRequest/
        FetchSchedulesRequest (httpMethod = "GET", urlString without a
        householdScheduleId suffix). CORRECTED (session 51): the
        response shape (SchedulesResponse -> household_schedules ->
        SchedulesList) is now confirmed via SchedulesResponse$$serializer/
        SchedulesList$$serializer -- previously the class names had
        been found but not their fields, so this returned raw JSON.
        Now parsed directly."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule"
        data = await self._request("GET", url)
        return SchedulesResponse.from_json(data)

    async def get_schedules_raw(self, household_id: str) -> Any:
        """Same endpoint as get_schedules(), but returns the UNPARSED
        response. Added for field diagnosis, same reasoning as
        get_favorites_raw().

        WHY THIS WAS NEEDED. A tester whose app shows three schedules
        was told by this library that he has none. Every layer between
        the server and him -- SchedulesResponse.from_json(), which drops
        anything not under "household_schedules", and the reporting tool
        above it -- could produce that same empty answer, and a parsed
        result cannot distinguish "the server sent nothing" from "we
        failed to read what it sent". Two field rounds were spent on
        that ambiguity.

        Diagnostic use only; the library's normal path uses
        get_schedules()."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule"
        return await self._request("GET", url)

    async def delete_schedule(self, household_id: str, household_schedule_id: str) -> dict[str, Any]:
        """DELETE /v1/households/{householdId}/settings/schedule/{id} --
        CONFIRMED from DeleteSchedulesRequest (httpMethod = "DELETE")."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule/{_path_segment(household_schedule_id)}"
        return await self._request("DELETE", url)

    async def create_schedules(self, household_id: str, schedules: list[ScheduleOptions]) -> dict[str, Any]:
        """POST /v1/households/{householdId}/settings/schedule --
        CONFIRMED (eighth session: CreateSchedulesRequest.<init> sets
        httpMethod = "POST" directly, androguard bytecode inspection --
        previously only assumed). Field structure also confirmed, see
        models/schedules_dnd.py::ScheduleOptions.

        THE BODY NESTS EACH SCHEDULE, and getting that wrong is what
        returned AspenError.InternalError on every attempt for four
        field rounds.

        CreateSchedulesRequest.getHttpBody() serialises a
        ScheduleListUpdate whose entries are HouseholdScheduleUpdate
        objects, built as `HouseholdScheduleUpdate(options, null)`. With
        the @SerialName annotations from both $$serializers that is:

            {"schedules": [{"options": {...}}]}

        WITHOUT `schedule_id`, and that distinction was itself corrected
        during the analysis. The constructor does receive null, so the
        first reading was "sent explicitly as null". But both elements
        are registered with isOptional=true and default null, and
        CreateSchedulesRequest uses Json.Default -- no encodeDefaults --
        so write$Self skips the field entirely. (P2MapsRequest sets
        encodeDefaults=true; this one does not.)

        Sending `"schedule_id": null` and omitting the key are not the
        same thing to a server, and this library would have written the
        null.

        The top-level key was right all along. What was missing is the
        `options` wrapper: this used to put the ScheduleOptions straight
        into the array, so the server found `name`, `commands`,
        `frequency` where it expected `{options, schedule_id}`. A
        structural mismatch like that produces a 500 rather than a 400 --
        which is exactly what came back, with no field named, four
        times.

        WHY UPDATE WAS NEVER AFFECTED: update_schedules() takes
        HouseholdSchedule objects, and HouseholdSchedule.to_json()
        already emits {schedule_id, options}. So toggling a schedule
        worked in the field while creating one never did, from the same
        module -- the two paths happened to disagree about a shape only
        one of them had confirmed."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule"
        body = {"schedules": [{"options": s.to_json()} for s in schedules]}
        return await self._request("POST", url, body=body)

    async def update_schedules(
        self, household_id: str, household_schedule_id: str, schedules: list[HouseholdSchedule]
    ) -> dict[str, Any]:
        """PUT /v1/households/{householdId}/settings/schedule/{id} --
        CONFIRMED (eighth session: UpdateSchedulesRequest.<init> sets
        httpMethod = "PUT" directly -- previously only assumed). Field
        structure confirmed, see models/schedules_dnd.py::HouseholdSchedule."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule/{_path_segment(household_schedule_id)}"
        return await self._request("PUT", url, body={"schedules": [s.to_json() for s in schedules]})

    async def get_user_households(self) -> dict[str, Any]:
        """GET /v1/user/households -- NEW (July 11, seventh session).
        HTTP method was pure REST convention (this endpoint isn't
        called anywhere by app version 2.2.4 -- the constant
        HOUSEHOLDS_TEMPLATE exists, but no request class uses it).
        STILL LIVE CONFIRMED (session 28, chairstacker): works
        flawlessly, returns a real, clearly structured response --
        "unused in the current app code" here actually just meant
        "this version doesn't need it", not "the server no longer
        supports it".

        Response shape confirmed: household_id, owner_cognito_id,
        household_name (observed: "#AUTO_GENERATED_HOUSEHOLD#"),
        has_precise_location, household_robots, household_users. For a
        typed result use models/robot_info.py::parse_user_households()."""
        url = f"{self._http_base_auth}/v1/user/households"
        return await self._request("GET", url)

    async def get_dnd_settings(self, household_id: str) -> DNDStatusResponse:
        """GET /v1/households/{householdId}/settings/dnd -- NEW (July
        11, sixth session). CONFIRMED from DNDGetRequest (httpMethod
        = "GET"). Response shape confirmed (ninth session) --
        models/schedules_dnd.py::DNDStatusResponse. IMPORTANT: see
        DNDStatusResponse's docstring for the distinction from the
        separate DNDSchedule class family.

        CORRECTED (session 53): actually parsed into DNDStatusResponse
        now -- same architectural gap as get_robot_parts()/
        get_serial_number_data(), see get_robot_parts()'s docstring."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/dnd"
        data = await self._request("GET", url)
        return DNDStatusResponse.from_json(data)

    async def set_dnd_settings(self, household_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """PUT /v1/households/{householdId}/settings/dnd -- CONFIRMED
        from DNDPutRequest (httpMethod = "PUT"). Exact body format
        (time-window fields) not further investigated -- raw JSON
        passed through."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/dnd"
        return await self._request("PUT", url, body=settings)

    async def get_cleaning_profiles(self, asset_id: str, p2map_id: str | None = None) -> dict[str, Any]:
        """GET /v1/profiles -- NEW (July 11, sixth session). CONFIRMED
        from CleaningProfileRequest (httpMethod = "GET").

        CORRECTED (session 38): the previous query parameter names
        ("asset_id"/"p2map_id") were wrong and are the confirmed cause
        of a live HTTP 400 (chairstacker). Read directly from
        CleaningProfileRequest.getQueryParams()'s decompiled Kotlin
        logic (jadx, cleanly decompiled -- not a guess this time):
          - robot/asset id key is "robotId" (NotificationCenterConsts
            .IN_APP_NAV_QUERY_PARAM_ROBOT_ID's literal value) --
            camelCase, NOT "asset_id" as previously assumed.
          - map id key is "p2map_id" (PushNotificationConsts
            .PERSISTENT_MAP_ID's literal value) -- this one was
            already correct.
          - a THIRD query parameter, "includeSmart", was completely
            missing before: "true" whenever p2map_id is present and
            non-blank, "false" otherwise -- and in the "false" case,
            p2map_id itself is dropped from the query entirely (not
            sent even as an empty string). `p2map_id` is therefore
            made optional here to mirror that real branching, not
            just to be permissive.

        NOT yet live-verified with this corrected query shape -- the
        previous snake_case attempt (session 33) was itself an
        unconfirmed guess that turned out wrong; this one is a direct
        bytecode read, a much stronger basis, but still unconfirmed
        against a real server until re-tested. Response shape modeled
        (ninth session) -- models/robot_info.py::CleaningProfile.from_json() per
        entry."""
        url = f"{self._http_base_auth}/v1/profiles"
        query = {"robotId": asset_id}
        if p2map_id:
            query["includeSmart"] = "true"
            query["p2map_id"] = p2map_id
        else:
            query["includeSmart"] = "false"
        return await self._request("GET", url, query=query)

    async def get_default_routines(self, p2map_id: str) -> RoutinesDefaultsResponse:
        """GET /v1/p2maps/{p2mapId}/routines/defaults -- NEW (July 11,
        sixth session). Automatically generated cleaning suggestions
        per map (e.g. "whole home", "kitchen only"). Response shape
        confirmed (forty-ninth session) --
        models/robot_info.py::RoutinesDefaultsResponse.

        CORRECTED (session 53): actually parsed into
        RoutinesDefaultsResponse now (which also captures
        routine_builder_defaults, previously not exposed here at all)
        -- same architectural gap as get_robot_parts() and others, see
        that method's docstring."""
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}/routines/defaults"
        data = await self._request("GET", url)
        return RoutinesDefaultsResponse.from_json(data)

    async def get_robot_parts(self, blid: str) -> RobotPartsInfo:
        """GET /v1/robots/{blid}/parts -- NEW (session 15). CONFIRMED
        from the actual APK configuration file
        (res/raw/base_roomba_config.json, commandId "GetRobotParts":
        httpMethod=GET, urlPath="/v1/robots/%s/parts",
        networkList=["awsApiGateway"]) -- a primary source, not
        bytecode interpretation.

        Response shape confirmed (session 27, real live response from
        chairstacker): robot_id, num_parts, parts (list with part_id,
        counter, minutes_remaining, count_type e.g.
        "combo_missions"/"pad_washes_used"/"minutes"/"evacs",
        count_remaining, count_used, counter_category, reset_by).

        CORRECTED (session 53): actually parsed into RobotPartsInfo
        now, rather than returning raw JSON with a docstring pointing
        at a parser that was never called -- a genuine architectural
        gap found during a broader review, not new field-level
        information."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/parts"
        data = await self._request("GET", url)
        return RobotPartsInfo.from_json(data)

    async def reset_robot_parts(self, blid: str) -> dict[str, Any]:
        """POST /v1/robots/{blid}/parts -- NEW (session 15). CONFIRMED
        from the same configuration file (commandId "ResetRobotParts",
        httpMethod=POST, identical urlPath to get_robot_parts()).
        Presumably resets consumable-part counters (e.g. after a part
        replacement) -- body shape not investigated, raw JSON passed
        through."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/parts"
        return await self._request("POST", url)

    async def get_serial_number_data(self, blid: str) -> RobotSerialInfo:
        """GET /v1/robots?robot_id={blid} -- NEW (session 15). CONFIRMED
        from the same configuration file (commandId "GetSerialNumberData",
        httpMethod=GET, urlPath="/v1/robots?robot_id=%s").

        Response shape confirmed (session 26, real live response
        from chairstacker): RobotID, SerialNumber, built_as_sku,
        family_variant, is_raas, is_refurbished, is_smartcare,
        min_utc_reg_date, name (user-assigned robot name, e.g.
        "House_Bot"), sku, series (e.g. "G1"), family (e.g.
        "Roomba Combo" -- confirms a vacuum+mop combo device),
        serial_history.

        CORRECTED (session 53): actually parsed into RobotSerialInfo
        now -- same architectural gap as get_robot_parts(), see that
        method's docstring."""
        url = f"{self._http_base_auth}/v1/robots"
        data = await self._request("GET", url, query={"robot_id": blid})
        return RobotSerialInfo.from_json(data)

    async def poll_echo_value(self, blid: str) -> dict[str, Any]:
        """POST /v1/robots/{blid}/echo -- NEW (session 16). CONFIRMED
        from base_roomba_config.json (commandId "PollEchoValueCommand,Set",
        httpMethod=POST, urlPath="/v1/robots/%s/echo"). Matches the
        "echo" feature ("find my robot" -- audible chime/announcement)
        -- consistent with the SetRoombaEchoAwsIotSerializer finding
        from the native analysis. Body shape unknown -- presumably
        empty or a simple trigger, no payload needed for the simplest
        case. No body included, until proven otherwise."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/echo"
        return await self._request("POST", url)

    async def get_time_estimates(self, blid: str) -> dict[str, Any]:
        """POST /v1/time-estimates -- NEW (session 16). CONFIRMED from
        base_roomba_config.json (commandId "GetTimeEstimates",
        httpMethod=POST despite "read": true -- presumably POST because
        the request needs a body to know WHICH mission/rooms to
        estimate, not because it's a write).

        PARTIALLY CONFIRMED (parallel native-analysis track,
        MissionTimeEstimatesRepositoryImpl.java): the real call site is
        fetchTimeEstimatesWithAreasForAsset(assetId, mapId,
        commandDefRegions: ArrayList<String>, screen) --
        commandDefRegions is a LIST OF REGION-ID STRINGS specifically
        (not full region/command objects), and "screen" is an
        analytics-tracking parameter only, not part of the actual wire
        body. The exact JSON key names the body ultimately serializes
        to remain unconfirmed (native-level from here) -- body is
        still passed through as a raw dict, to be filled in by the
        caller themselves.

RESOLVED (30 July 2026) -- and the earlier "not determinable"
        note in this docstring was wrong.

        The body is a single field:

            {"robot_id": "<BLID>"}

        Confirmed by tracing the native call to StringUtils::vformat:
        the format string is `{ "%s": "%s" }` (length 0x1c>>1 = 14
        matches), x1 holds kRobotId and x2 the robot id itself. The
        csinc pairs around it are libc++'s standard short-string
        optimisation branch, so there is no ambiguity about which
        pointer is passed.

        THAT IS WHY NO SERIALIZER EXISTS. The body is assembled as a
        string with printf-style substitution -- there is no
        @Serializable class to find, which is what every earlier search
        was looking for. Worth remembering: absence of a serializer is
        not absence of a documented format.

        And an earlier abort was premature. This was closed as "native,
        therefore not determinable" on the grounds that native vtable
        reconstruction is unreliable. That rule is about reconstructing
        STRUCTURE from vtables; a format string with traced register
        arguments is direct evidence of a different kind.

        NOT IN THE BODY: mapId and the region list, despite both
        appearing in fetchTimeEstimatesWithAreasForAsset(). The server
        returns everything for the robot and the app filters
        client-side.

        RESPONSE SHAPE CONFIRMED 31 July 2026 (@DaRealGuGu, N185240):

            {"robot_id": "<BLID>",
             "api_version": "v1",
             "smart_maps": [
               {"smart_map_id": "<BLID>-<epoch>",
                "areas": [
                  {"area_id": "12", "area_type": "region",
                   "estimates": [
                     {"value": 533, "unit": "seconds", "deviation": 0.0,
                      "data_model_version": "app_prime",
                      "params": {"operatingMode": 512, "suctionLevel": 3,
                                 "swScrub": 0, "twoPass": false}},
                     ...
                   ]}
                ],
                "cleaning_rates": {"deep": 885.0, "light": 391.0,
                                   "standard": 479.0}}
             ]}

        THE SHAPE IS RICHER THAN EXPECTED. It is not one estimate per
        room -- it is one per room PER PARAMETER COMBINATION. A single
        room came back with 44 entries covering every mix of
        operatingMode, suctionLevel, swScrub and twoPass.

        So this answers "how long would this room take at these
        settings", not just "how long does this room take". Anything
        picking a single number has to select by params, and the sensible
        selection is the room's own last_operating_mode from the map
        metadata.

        `cleaning_rates` is per profile in area-per-hour terms and is
        map-wide rather than per room -- useful for a room that has no
        estimate yet.

        Note the parameter names are the camelCase wire keys used
        everywhere else in the command domain, not the snake_case of this
        library's models."""
        url = f"{self._http_base_auth}/v1/time-estimates"
        # Built here rather than taken as a raw dict. The old signature
        # made every caller invent the body, which meant every caller
        # could invent it differently -- and nobody could, because the
        # key was unknown.
        return await self._request("POST", url, body={"robot_id": blid})

    async def get_clean_score_raw(self, p2map_id: str) -> Any:
        """POST /v1/p2maps/clean-score -- a per-ROOM cleanliness value.

RESPONSE WIRE KEYS CONFIRMED (APK, 2 August 2026) -- as
        LITERALS in libdataModule.so, read out of jsonToCleanScoreData,
        not from the Kotlin property names:

            {
              "clean_score_ranges": [0.0, ...],
              "clean_scores": [{
                "p2map_id", "active_p2mapv_id", "user_p2mapv_id",
                "smart_clean_id", "mission_last_processed": {...},
                "regions": [{
                  "region_id", "clean_score", "updated_ts",
                  "last_updated_by", "smart_clean_prefs"
                }]
              }]
            }

        THE WIRE IS snake_case; the Kotlin side is camelCase
        (cleanScoreData, cleanScoreRegions, regionId, updatedTs). An
        earlier draft of this docstring wrote the Kotlin names as if
        they were the wire format -- the same confusion that once
        produced 21 wrong wire keys in this library.

        Ten of the thirteen keys are confirmed as literals. `p2map_id`,
        `active_p2mapv_id` and `regions` resolve through shared
        serialization constants rather than their own literals here, so
        their spelling is inherited from confirmed uses elsewhere rather
        than proven at this call site.

        Range is fixed: CleanScoreConst.MIN_CLEAN_SCORE = 0.0f,
        MAX_CLEAN_SCORE = 1.0f -- a float, so directly a percentage.

        NOT a mission result. The values hang off pmapId/regionId and
        `missionLastProcessed` only says how far the running value has
        been carried -- it is accumulated state per room. This is the
        data behind Smart Clean / "Dirt Detective".

        GET WITH A QUERY PARAMETER, not a POST body -- and that came
        from reading a second integration rather than from the APK.

        The APK could not supply it: the call goes through
        SmartCleanWrappedUseCase.fetchCleanScoreDataForMap() and across
        the Djinni boundary, exactly as /v1/time-estimates did. An
        earlier draft of this method guessed by analogy with that one --
        POST with `{"robot_id": ...}` there, so POST with
        `{"p2map_id": ...}` here. Plausible, and wrong.

        a-mavrides/roomba_v4 sends `GET .../clean-score?p2map_id=<id>`
        and, unlike its call to /v1/user/automations, actually CONSUMES
        the response -- it reads clean_scores[].regions[].region_id and
        smart_clean_prefs to derive per-room defaults. Code that reads
        what it fetched is evidence the fetch works; code that swallows
        errors and never looks is not.

        Still one integration's usage rather than a protocol document,
        so the check that calls this prints the request and the raw
        response.

        Returns the UNPARSED response. CleanScoreResponse.from_json()
        parses it and is built from the confirmed keys above -- the
        raw form stays available because a parsed result cannot
        distinguish "the server sent nothing" from "we failed to read
        what it sent", and this project has spent field rounds on
        exactly that ambiguity.
        """
        url = f"{self._http_base_auth}/v1/p2maps/clean-score"
        return await self._request("GET", url, query={"p2map_id": p2map_id})

    async def get_automations_raw(self) -> Any:
        """GET /v1/user/automations -- third-party triggers and
        geofencing. NOT the schedule endpoint.

        WHAT THIS SUBSYSTEM IS (APK, 2 August 2026). RoutineConstants
        holds 66 entries, and they settle the purpose: hard-coded
        service ids for August Home, Ecobee, Leviton, MyQ and Wyze;
        geofencing keys (kLatitude, kLongitude, kRadius,
        kEnterRegionLocationTriggerId); and behaviour options
        (kContinueCleaning, kPauseAndNotify, kEndJob). So: "when I leave
        the house, run favourite X", or "when Ecobee goes to Away".

        `favorite_id` and `robot_commands` in the payload show it drives
        the same command structures as favourites do.

        A DIFFERENT DATA MODEL FROM SCHEDULES, not an alternative to
        them -- automation_id vs schedule_id, time_window{hour,minute}
        vs start/end, plus service_id/trigger_id, which schedules have
        no counterpart for. Schedule management stays on
        /v1/households/{id}/settings/schedule, the only path active in
        the app's Kotlin.

        THE ENDPOINT MAY NOT BE LIVE, and that is the open question this
        method exists to settle. In liblegacyCore.so the string has
        exactly ONE reference -- a static initialiser, recognisable by
        the __cxa_atexit pattern, sitting between two IFTTT URLs. No
        reader. Same signature as `sec_message` and `koz-v1.0.0`, both
        of which turned out to be dead; actively used URLs show two or
        three real consumers.

        The app reaches the data through
        AutomationDataUseCaseImpl::fetchAllAutomations() and on across
        the Djinni boundary, so its real path is not resolvable
        statically. A second Home Assistant integration
        (a-mavrides/roomba_v4) calls this URL -- but reading its code,
        that is NOT evidence the endpoint answers. It swallows any
        exception into a debug log, defaults the result to {}, writes it
        to a debug file, and no entity ever reads it. A 404 would look
        identical to an empty response there, and nobody would notice.

        So there is currently NO evidence for or against, only an
        untested call in someone else's integration and a dead constant
        here.

        One read attempt on an account with automations configured
        settles it either way, and costs nothing if the answer is no.
        """
        url = f"{self._http_base_auth}/v1/user/automations"
        return await self._request("GET", url)

    async def reset_robot(self, blid: str) -> dict[str, Any]:
        """POST /v1/{blid}/reset -- NEW (session 16). CONFIRMED from
        base_roomba_config.json (commandId "ResetRobotCommand",
        httpMethod=POST, networkList contains both awsApiGateway and
        lss -- so it exists for both Classic AND Prime). WARNING:
        presumably a factory reset or at least a significant reset
        operation -- the name and "write": true suggest this triggers
        a REAL, potentially consequential action on the device. Never
        live-tested. Don't call this lightly."""
        url = f"{self._http_base_auth}/v1/{_path_segment(blid)}/reset"
        return await self._request("POST", url)

    async def get_notifications(self, blid: str, app_version: str = "2.2.4") -> dict[str, Any]:
        """GET /v1/robots/{blid}/timeline -- NEW (session 16). CONFIRMED
        from base_roomba_config.json (commandId "GetNotifications",
        urlPath="/v1/robots/%s/timeline?event_type=HKC&
        details_type_filter=all&app_version=%s&limit=50"). "HKC" as an
        event_type value not resolved (abbreviation unknown) -- carried
        over 1:1 from the configuration file, not guessed.

        KNOWN BUG, LIKELY CAUSE NOW IDENTIFIED (session 36): live against a
        real account (chairstacker, session 25), this call failed with
        HTTP 400 using the previous placeholder value ("1.0") -- a value
        with zero evidentiary basis, never anything but a guess. The
        analyzed APK's own `com.irobot.home.BuildConfig.VERSION_NAME`
        and the `AndroidManifest.xml`'s `android:versionName` both
        confirm the real app build used for this analysis was "2.2.4" --
        a strong candidate for what `app_version` is actually meant to
        carry (the calling app's own version string), now used as the
        default here instead of the old placeholder. NOT yet live-tested
        with this corrected value -- the real Prime app in the field may
        since have moved to a newer version than "2.2.4", so this
        remains a best-effort default, not a guaranteed-correct one. If
        this call still fails with the corrected value, the cause lies
        elsewhere (missing header/parameter not visible in the
        configuration file, or a version the server no longer accepts).
        Do NOT treat this as working until this is
        resolved."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/timeline"
        return await self._request(
            "GET",
            url,
            query={
                "event_type": "HKC",
                "details_type_filter": "all",
                "app_version": app_version,
                "limit": "50",
            },
        )

    @staticmethod
    def _favorite_from_json(data: dict[str, Any]) -> FavoriteV1:
        """Builds a FavoriteV1 from raw JSON. Deliberately tolerant
        (.get() everywhere) -- never seen a real server response to
        know which fields are truly always present.

        NEW (this session, parallel native-analysis track): Favorite's
        own Kotlin/Java field is typed List<String>, not a list of
        already-structured objects -- meaning each entry may arrive as
        a JSON-ENCODED STRING (a serialized RoutineCommand) rather than
        a dict directly. CONFIRMED (same track, follow-up): the real
        app deserializes each string to a full RoutineCommand object
        (bytecode shows `check-cast ... RoutineCommand`, not a plain
        string being used opaquely) -- a favorite genuinely carries
        complete command definitions (including regions/params), not
        just a reference. Defensively handles both wire shapes here:
        json.loads() first if an entry is a str, used as-is if it's
        already a dict. Without this, a real string-shaped response
        would crash outright on c["command"] (subscripting a string
        by a non-integer key)."""
        command_defs_raw = data.get("commanddefs") or []
        command_defs_raw = [
            json.loads(c) if isinstance(c, str) else c for c in command_defs_raw
        ]
        return FavoriteV1(
            favorite_id=data.get("favorite_id"),
            name=data.get("name"),
            color=data.get("color"),
            icon=data.get("icon"),
            order=data.get("order"),
            display_order=data.get("display_order"),
            is_default=bool(data.get("default", False)),
            is_deleted=bool(data.get("deleted", False)),
            is_hidden=bool(data.get("hidden", False)),
            modification_secs=data.get("modification_secs"),
            version=data.get("version"),
            command_defs=[
                RoutineCommand(
                    command_type=MissionCommandType(c["command"]),
                    asset_id=c.get("robot_id", ""),
                    map_id=c.get("p2map_id"),
                    ordered=c.get("ordered", 0),
                    id_multipolys=c.get("id_multipolys"),
                    params=c.get("params"),
                    regions=c.get("regions"),
                    pmap_version_id=c.get("user_p2mapv_id"),
                    clean_all=bool(c.get("select_all", False)),
                    spot_geometry=c.get("geom"),
                    favorite_id=c.get("favorite_id"),
                    # Passthrough only -- see RoutineCommand.command_id's
                    # own comment. Preserving what the server sent beats
                    # dropping it, even while its meaning is unknown.
                    command_id=c.get("id"),
                )
                for c in command_defs_raw
            ],
            creation_timestamp=data.get("creation_timestamp"),
            last_user_modified=data.get("last_user_modified"),
            last_modified=data.get("last_modified"),
            time_estimates=None,
        )

    def _signer(self) -> AwsSigV4Signer:
        return AwsSigV4Signer(
            self._credentials.access_key_id,
            self._credentials.secret_key,
            self._credentials.session_token,
        )

    async def _request(
        self,
        method: str,
        url: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        _retry: bool = True,
    ) -> Any:
        parsed = urllib.parse.urlparse(url)
        body_str = json.dumps(body) if body is not None else ""
        headers = self._signer().signed_headers(
            method=method,
            service="execute-api",
            region=self._credentials.region,
            host=parsed.netloc,
            path=parsed.path,
            query_params=query or {},
            body=body_str,
        )

        request_kwargs: dict[str, Any] = {"params": query, "headers": headers}
        if body is not None:
            # NOTE: must send the EXACT same bytes we hashed for the
            # signature -- aiohttp's json= would re-serialize
            # independently (possibly different key order/whitespace)
            # and invalidate the signature. data= sends our own string
            # verbatim.
            request_kwargs["data"] = body_str.encode()

        method_fn = getattr(self._session, method.lower())
        try:
            async with method_fn(url, **request_kwargs) as resp:
                if resp.status == 403 and _retry and self._relogin is not None:
                    _LOGGER.debug("roombapy-prime REST: 403 -- reauthenticating")
                    login_result = await self._relogin()
                    self._credentials = login_result.credentials
                    return await self._request(method, url, query, body, _retry=False)
                return await self._parse_response(resp)
        except aiohttp.ClientSSLError as exc:
            _raise_clear_ssl_error(exc)
        except aiohttp.ServerTimeoutError as exc:
            _raise_clear_timeout_error(exc)
        except aiohttp.ClientConnectorError as exc:
            _raise_clear_connection_error(exc)

    async def _parse_response(self, resp: aiohttp.ClientResponse) -> Any:
        text = await resp.text()
        if resp.status >= 400:
            raise RestError(f"HTTP {resp.status} from {resp.url}", status=resp.status, raw_response=text)
        if not text:
            return {}
        try:
            return json.loads(text)
        except JSONDecodeError as exc:
            raise RestError(
                f"Non-JSON response from {resp.url}: {text[:300]}", status=resp.status, raw_response=text
            ) from exc
