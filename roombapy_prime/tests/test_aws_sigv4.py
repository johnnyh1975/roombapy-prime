"""Tests for roombapy_prime.aws_sigv4.

The frozen-clock test below pins the EXACT signature output that was
manually verified (during development, not as an automated test
dependency) to be byte-for-byte identical to ha_roomba_plus's original
cloud_api.py::_AWSSignatureV4 for the same inputs -- see
docs/FINDINGS_2026-07-11.md / README for how that comparison was done.
This test doesn't re-run that comparison (would require importing a
sibling project's source, not appropriate for this repo's test suite)
-- it just locks in the known-good output so a future refactor can't
silently change the signing algorithm without a failing test.
"""
from __future__ import annotations

import datetime as real_datetime

from roombapy_prime.aws_sigv4 import AwsSigV4Signer


class _FrozenDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return real_datetime.datetime(2026, 7, 11, 12, 0, 0, tzinfo=tz)


def test_signed_headers_matches_verified_reference_output(monkeypatch) -> None:
    """Regression pin -- see module docstring. Any change to this output
    must be a deliberate, understood change to the signing algorithm."""
    import roombapy_prime.aws_sigv4 as aws_sigv4_module

    monkeypatch.setattr(aws_sigv4_module, "datetime", _FrozenDatetime)

    signer = AwsSigV4Signer("AKIDEXAMPLE", "secretkey123", "sessiontoken456")
    headers = signer.signed_headers(
        method="GET",
        service="execute-api",
        region="us-east-1",
        host="example.execute-api.us-east-1.amazonaws.com",
        path="/v1/BLID123/pmaps",
        query_params={"visible": "true"},
    )

    assert headers == {
        "accept": "application/json",
        "content-type": "application/json",
        "host": "example.execute-api.us-east-1.amazonaws.com",
        "user-agent": "aws-sdk-iOS/2.27.6 iOS/18.0.1 en_US",
        "x-amz-date": "20260711T120000Z",
        "Authorization": (
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260711/us-east-1/execute-api/aws4_request, "
            "SignedHeaders=accept;content-type;host;user-agent;x-amz-date, "
            "Signature=8321b809e76b0a698978321a02821539c904883d786a8903ee9e700d0701014c"
        ),
        "x-amz-security-token": "sessiontoken456",
    }


def test_signed_headers_with_body_differs_from_empty_body(monkeypatch) -> None:
    """SYNTHETIC extension beyond the original (which is GET-only, no
    body) -- confirms a non-empty body actually changes the signature
    (i.e. the payload really is part of what gets signed), without
    asserting a specific value (no reference output exists for the
    POST-with-body case)."""
    import roombapy_prime.aws_sigv4 as aws_sigv4_module

    monkeypatch.setattr(aws_sigv4_module, "datetime", _FrozenDatetime)

    signer = AwsSigV4Signer("AKIDEXAMPLE", "secretkey123", "sessiontoken456")
    empty_body_headers = signer.signed_headers(
        method="POST", service="execute-api", region="us-east-1",
        host="h", path="/p",
    )
    with_body_headers = signer.signed_headers(
        method="POST", service="execute-api", region="us-east-1",
        host="h", path="/p", body='{"command": "merge_rooms"}',
    )

    assert empty_body_headers["Authorization"] != with_body_headers["Authorization"]


def test_signed_headers_query_params_are_sorted_deterministically(monkeypatch) -> None:
    """Canonical query string construction must sort params -- otherwise
    the same logical request signs differently depending on dict
    iteration order, which would make signatures non-reproducible."""
    import roombapy_prime.aws_sigv4 as aws_sigv4_module

    monkeypatch.setattr(aws_sigv4_module, "datetime", _FrozenDatetime)

    signer = AwsSigV4Signer("AKIDEXAMPLE", "secretkey123", "sessiontoken456")
    headers_a = signer.signed_headers(
        method="GET", service="execute-api", region="us-east-1", host="h", path="/p",
        query_params={"b": "2", "a": "1"},
    )
    headers_b = signer.signed_headers(
        method="GET", service="execute-api", region="us-east-1", host="h", path="/p",
        query_params={"a": "1", "b": "2"},
    )

    assert headers_a == headers_b
