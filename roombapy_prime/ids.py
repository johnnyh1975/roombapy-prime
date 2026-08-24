"""Mission and deployment ID validation.

iRobot's `missionId` / `deploymentId` are ULIDs: a 26-character
Crockford base32 string (48-bit timestamp + 80-bit randomness). The
alphabet is `0123456789ABCDEFGHJKMNPQRSTVWXYZ` -- the digits and
uppercase letters with I, L, O and U removed, so a human reading one
aloud cannot confuse it for 1, 1, 0 or V.

Source: the firmware 3.8.126 image names Crockford base32 as the ID
encoding. Confirmed against two real IDs (a mission and a config entry
from a live diagnostics download): both 26 chars, both inside the
alphabet.

WHICH IDS THIS APPLIES TO -- AND WHICH IT DOES NOT. Only mission and
deployment ids are ULIDs. Other ids in this protocol are different
formats entirely, verified against real field data:

    missionId    01M0J6GZJRNEX8TG873FAYEKGF   26 chars, Crockford  ✓
    p2map_id     DJkG17mVRx2lOkWefteHBg       22 chars, mixed case ✗
    p2map_id     BLID-1758329350              hyphen, blid-derived ✗
    p2mapv_id    260607T091458                a timestamp          ✗

Applying `is_valid_id` to a map id would report every real one as
malformed. The three ids above are not broken -- they are not ULIDs.
Check `missionId` and `deploymentId` with this; nothing else.

WHY VALIDATE AT ALL. Nothing here rejects an ID -- a malformed one is
still passed through, because the robot is the authority on its own IDs
and a format this library merely inferred is not grounds to drop data.
What this buys is *recognition*: a value that is meant to be a mission
ID but is empty, truncated, or lowercased (a common transcription slip,
since Crockford decoding is case-insensitive but the wire form is
upper) can be spotted in a diagnostic rather than silently used as a
dictionary key that never matches.
"""

from __future__ import annotations

#: Crockford base32: no I, L, O, U. Uppercase is the wire form.
_CROCKFORD = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

#: ULID length. A different length is not a ULID, whatever its
#: characters -- worth flagging separately from an alphabet violation.
_ULID_LENGTH = 26


def is_valid_id(value: object) -> bool:
    """True if `value` is a well-formed 26-char Crockford base32 ID.

    Case-sensitive on purpose: the wire form is uppercase, and a
    lowercased ID is a transcription artefact worth catching rather
    than quietly accepting. Use `normalise_id` first if a caller has a
    reason to accept mixed case.
    """
    return (
        isinstance(value, str)
        and len(value) == _ULID_LENGTH
        and all(char in _CROCKFORD for char in value)
    )


def id_problem(value: object) -> str | None:
    """None if `value` is a valid ID, else a short reason.

    For diagnostics: names *how* an ID is malformed rather than just
    that it is, so a capture with a bad ID says something useful. The
    order matters -- a wrong length is reported before an alphabet
    check, because "" and a truncated ID are the common cases and
    "empty"/"wrong length" is more informative than listing every
    missing character.
    """
    if not isinstance(value, str):
        return f"not a string ({type(value).__name__})"
    if not value:
        return "empty"
    if len(value) != _ULID_LENGTH:
        return f"wrong length ({len(value)}, expected {_ULID_LENGTH})"
    # Report the lowercase case distinctly: it is the one malformation
    # that is recoverable, and a caller may choose to normalise it.
    if value.upper() != value and all(c in _CROCKFORD for c in value.upper()):
        return "lowercase (wire form is uppercase)"
    stray = {c for c in value if c not in _CROCKFORD}
    if stray:
        return f"characters outside Crockford base32: {sorted(stray)}"
    return None


def normalise_id(value: str) -> str:
    """Uppercase an ID for comparison. Does not validate.

    Crockford decoding is case-insensitive, so `abc` and `ABC` are the
    same ID; the wire form is uppercase. A caller matching an ID it
    received against one it stored should normalise both ends rather
    than assume case.
    """
    return value.upper()
