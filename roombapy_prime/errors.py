"""The one base every error from iRobot's cloud derives from, and why it
was raised.

WHY IT EXISTS (0.4.0). Login failures (auth.AuthError), REST failures
(rest_client.RestError) and MQTT/shadow failures (mqtt_client.ShadowError,
SubscriptionRejectedError) grew as separate hierarchies, each rooted
directly in Exception. A caller that only needs "the cloud did not
answer as hoped" had to name all of them, and a caller that forgot one
let it escape -- ha_roomba_plus' own Classic cloud client worked around
that with a vocabulary of its own.

CloudError sits above all of them. It changes no existing `except`:
every class keeps its name, its own subclasses and its position
relative to the others.

WHY `reason` (0.4.0). The class says which part failed; it does not say
what to tell a person. AuthSSLError alone covers three causes with three
different pieces of advice -- the machine's own certificate store, an
expired certificate on iRobot's side, or neither known -- and until now
only the English message told them apart. A caller showing the error in
another language had nothing to translate from but that English text,
and ha_roomba_plus passed it into its German, French and Polish messages
as it was.

`reason` is the answer to translate from: a fixed name from a closed
set, CloudErrorReason. The library does not translate; it states the
cause, and the application says it in its user's language. The English
message stays, for logs. Data a translation needs rides along as
attributes, never inside the message: `status` on REST errors,
`retry_after` on RestRateLimitedError.

The names are part of the interface. Renaming one breaks every
translation keyed on it, so a name is added, never changed.
"""
from __future__ import annotations

from enum import StrEnum


class CloudErrorReason(StrEnum):
    """Why a CloudError was raised. One name per cause a person would be
    told something different about."""

    # ── getting there ─────────────────────────────────────────────────
    CONNECTION_FAILED = "connection_failed"
    """No connection at all: DNS, refused, unreachable."""
    CONNECTION_BROKEN = "connection_broken"
    """A connection was made and broke off before the answer was complete."""
    TIMEOUT = "timeout"
    """Sent, and no complete answer in time."""
    SSL_LOCAL_TRUST_STORE = "ssl_local_trust_store"
    """This machine cannot check iRobot's certificate -- a local setup
    problem that waiting will not fix."""
    SSL_CERTIFICATE_EXPIRED = "ssl_certificate_expired"
    """iRobot's certificate has expired -- on their side, usually fixed
    within hours."""
    SSL_UNVERIFIED = "ssl_unverified"
    """The certificate could not be verified, and the cause is not known."""

    # ── the account ───────────────────────────────────────────────────
    CREDENTIALS_REJECTED = "credentials_rejected"
    """Wrong username or password, or the login was refused."""
    ACCOUNT_LOCKED = "account_locked"
    """Locked after too many attempts. Clears by itself; re-entering the
    password extends it."""
    TOO_MANY_SESSIONS = "too_many_sessions"
    """Too many active app sessions for this account."""
    NO_ROBOTS = "no_robots"
    """The account has no robots."""
    ROBOT_AMBIGUOUS = "robot_ambiguous"
    """Several robots on the account and none was named."""

    # ── the answer ────────────────────────────────────────────────────
    REQUEST_REFUSED = "request_refused"
    """HTTP 4xx other than 429: the request itself was refused. `status`."""
    RATE_LIMITED = "rate_limited"
    """HTTP 429: too many requests. `retry_after` when the server gave one."""
    SERVER_ERROR = "server_error"
    """HTTP 5xx: a problem on iRobot's side. `status`."""
    RESPONSE_MALFORMED = "response_malformed"
    """An answer arrived and was not what the call expects: not JSON, not
    the documented shape, or missing a field the next step needs."""

    # ── MQTT ──────────────────────────────────────────────────────────
    NOT_CONNECTED = "not_connected"
    """An MQTT operation before connect() -- a caller's mistake, not the cloud's."""
    CONNECT_REFUSED = "connect_refused"
    """The broker refused the MQTT connection after TLS succeeded."""
    PUBLISH_NOT_DELIVERED = "publish_not_delivered"
    """A message was refused locally or never confirmed sent."""
    SUBSCRIPTION_NOT_SENT = "subscription_not_sent"
    """A subscription failed before it reached the broker."""
    SUBSCRIPTION_REJECTED = "subscription_rejected"
    """The broker's policy denied a subscription."""
    SHADOW_REJECTED = "shadow_rejected"
    """The device shadow refused a read or a write."""

    UNKNOWN = "unknown"
    """Never raised by this library -- every raise names its cause, and a
    test holds it to that. The value an error gets when code elsewhere
    constructs one without saying why."""


def reason_for_status(status: int) -> CloudErrorReason:
    """The reason for an HTTP answer with this status, the same way for
    login and REST: 429, other 4xx, 5xx -- and anything below 400 is an
    answer that arrived in a shape nobody expected."""
    if status == 429:
        return CloudErrorReason.RATE_LIMITED
    if 400 <= status < 500:
        return CloudErrorReason.REQUEST_REFUSED
    if status >= 500:
        return CloudErrorReason.SERVER_ERROR
    return CloudErrorReason.RESPONSE_MALFORMED


class CloudError(Exception):
    """Base of every error raised for iRobot's cloud -- login, REST, MQTT.

    `reason` says why, as a name from CloudErrorReason. Each subclass
    carries the reason its name implies; a raise with a more specific
    cause passes `reason=`."""

    reason: CloudErrorReason = CloudErrorReason.UNKNOWN

    def __init__(self, *args: object, reason: CloudErrorReason | None = None) -> None:
        super().__init__(*args)
        if reason is not None:
            self.reason = reason
