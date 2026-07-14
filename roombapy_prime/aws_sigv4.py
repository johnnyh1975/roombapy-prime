"""AWS Signature Version 4 request signing.

Ported (renamed, no logic changes to the core signing) from
ha_roomba_plus's already-production cloud_api.py::_AWSSignatureV4 --
used there since version 3.x to sign the Classic-protocol REST
endpoints (/v1/{blid}/pmaps, /v1/{blid}/missionhistory, etc.). SigV4
itself is a public, AWS-documented standard mechanism (not iRobot-
specific reverse engineering) -- so it carries over with high
confidence.

IMPORTANT LIMITATION: the original (`_aws_get`) only ever signs GET
requests with no body (payload_hash there is always sha256("")).
p2maps also needs POST with a JSON body (edit_map, set_map_name,
etc.) -- the body parameter below and the payload_hash computed from
it are MY extension, not part of the original. The algorithm itself
(SigV4) requires exactly this, but it has never been tested against a
real POST call with a body -- neither Classic nor Prime/V4.

Also never tested: whether p2maps needs SigV4 signing at all (vs. e.g.
one of the tokens already included at login as a simple Bearer
header). This carry-over is a plausible assumption by analogy to
other /v1/ endpoints in the same cloud API family, not a confirmed
fact for p2maps itself.
"""
from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime

_USER_AGENT_AWS = "aws-sdk-iOS/2.27.6 iOS/18.0.1 en_US"


class AwsSigV4Signer:
    """Minimal AWS SigV4 signer. Credentials come from
    auth.CloudCredentials (access_key_id, secret_key, session_token)."""

    def __init__(self, access_key_id: str, secret_key: str, session_token: str) -> None:
        self._key_id = access_key_id
        self._secret = secret_key
        self._token = session_token

    @staticmethod
    def _hmac_sha256(key: bytes, data: str) -> bytes:
        return hmac.new(key, data.encode(), hashlib.sha256).digest()

    @staticmethod
    def _sha256_hex(data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()

    def _signing_key(self, date_stamp: str, region: str, service: str) -> bytes:
        k = self._hmac_sha256(f"AWS4{self._secret}".encode(), date_stamp)
        k = self._hmac_sha256(k, region)
        k = self._hmac_sha256(k, service)
        return self._hmac_sha256(k, "aws4_request")

    def signed_headers(
        self,
        method: str,
        service: str,
        region: str,
        host: str,
        path: str,
        query_params: dict[str, str] | None = None,
        body: str = "",
    ) -> dict[str, str]:
        """Returns a header dict including the AWS SigV4 Authorization
        header.

        body: empty for GET (original behavior unchanged). For POST,
        body MUST be exactly the string that's actually sent as the
        request body -- the signature and the sent body must be
        byte-identical, or the signature check will fail server-side
        (see rest_client.py for how this is ensured)."""
        now = datetime.now(tz=UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        canonical_uri = urllib.parse.quote(path, safe="/")
        qp = query_params or {}
        canonical_qs = "&".join(
            f"{urllib.parse.quote(k, safe='~')}={urllib.parse.quote(str(v), safe='~')}"
            for k in sorted(qp)
            for v in [qp[k]]
        )

        base_headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "host": host,
            "user-agent": _USER_AGENT_AWS,
            "x-amz-date": amz_date,
        }
        sorted_keys = sorted(base_headers)
        canonical_headers = "".join(f"{k}:{base_headers[k]}\n" for k in sorted_keys)
        signed_hdrs = ";".join(sorted_keys)

        payload_hash = self._sha256_hex(body)
        canonical_req = "\n".join([
            method.upper(), canonical_uri, canonical_qs,
            canonical_headers, signed_hdrs, payload_hash,
        ])

        algorithm = "AWS4-HMAC-SHA256"
        scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = "\n".join([
            algorithm, amz_date, scope, self._sha256_hex(canonical_req),
        ])

        sig = hmac.new(
            self._signing_key(date_stamp, region, service),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()

        authorization = (
            f"{algorithm} Credential={self._key_id}/{scope}, "
            f"SignedHeaders={signed_hdrs}, Signature={sig}"
        )

        return {
            **base_headers,
            "Authorization": authorization,
            "x-amz-security-token": self._token,
        }
