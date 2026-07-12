"""AWS Signature Version 4 request signing.

Portiert (Umbenennung, keine Logikaenderung an der Kernsignierung) aus
ha_roomba_plus's bereits produktiv laufender cloud_api.py::
_AWSSignatureV4 -- dort seit Version 3.x genutzt, um die
Classic-Protokoll-REST-Endpunkte (/v1/{blid}/pmaps, /v1/{blid}/
missionhistory, etc.) zu signieren. SigV4 selbst ist ein oeffentlicher,
von AWS dokumentierter Standardmechanismus (nicht iRobot-spezifisch
reverse-engineered) -- daher mit hoher Zuversicht uebertragbar.

WICHTIGE EINSCHRAENKUNG: Das Original (`_aws_get`) signiert ausschliesslich
GET-Anfragen ohne Body (payload_hash ist dort immer sha256("")). p2maps
braucht aber auch POST mit JSON-Body (edit_map, set_map_name, etc.) --
der body-Parameter unten und die daraus berechnete payload_hash sind
MEINE Erweiterung, nicht Teil des Originals. Der Algorithmus selbst
(SigV4) verlangt das exakt so, aber es ist nie gegen einen echten
POST-Aufruf mit Body getestet worden -- weder Classic noch Prime/V4.

Ebenfalls nie getestet: ob p2maps ueberhaupt SigV4-Signierung braucht
(vs. z.B. eines der bereits im Login enthaltenen Tokens als simplen
Bearer-Header). Diese Uebertragung ist eine plausible Annahme aus der
Analogie zu anderen /v1/-Endpunkten derselben Cloud-API-Familie, keine
bestaetigte Tatsache fuer p2maps selbst.
"""
from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime

_USER_AGENT_AWS = "aws-sdk-iOS/2.27.6 iOS/18.0.1 en_US"


class AwsSigV4Signer:
    """Minimaler AWS-SigV4-Signierer. Zugangsdaten kommen aus
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
        """Gibt ein Header-Dict inkl. AWS-SigV4-Authorization zurueck.

        body: leer fuer GET (Original-Verhalten unveraendert). Fuer
        POST MUSS body exakt der String sein, der tatsaechlich als
        Request-Body gesendet wird -- Signatur und gesendeter Body
        muessen byte-identisch sein, sonst schlaegt die Signaturpruefung
        serverseitig fehl (siehe rest_client.py, wie das sichergestellt
        wird)."""
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
