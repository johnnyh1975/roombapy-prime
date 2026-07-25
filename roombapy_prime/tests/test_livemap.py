"""Tests for live map decoding."""

from __future__ import annotations

import pytest

class TestLiveMapIsZlibCompressed:
    """CONFIRMED FROM A REAL FIELD CAPTURE (DaRealGuGu, and almost
    certainly the same cause as chairstacker's long-standing blank live
    map): the payload arrives zlib-compressed and was being fed to the
    protobuf parser as-is.

    The resulting "Unsupported protobuf wire type 4 at offset 7" reads
    like a protocol mismatch and is really just compressed data. The
    answer was in the first two bytes of the diagnostic hex dump added
    for exactly this purpose -- everything before that was guesswork
    about expired URLs and error pages."""

    def test_the_real_field_header_is_recognised_as_zlib(self):
        """First bytes taken verbatim from the field report."""
        from roombapy_prime.models.livemap import _maybe_decompress

        real_prefix = bytes.fromhex("789ced9d099414f59dc7a7ffba3a1986")

        assert real_prefix[:2] == b"\x78\x9c", "0x78 0x9c is zlib, default compression"
        with pytest.raises(ValueError, match="zlib header"):
            _maybe_decompress(real_prefix)   # a truncated stream, but recognised

    def test_a_compressed_payload_round_trips(self):
        import zlib

        from roombapy_prime.models.livemap import _maybe_decompress

        payload = b"\x08\x01\x10\x02" * 40

        assert _maybe_decompress(zlib.compress(payload)) == payload

    def test_an_uncompressed_payload_passes_through_untouched(self):
        """Not established that every firmware compresses, so this stays
        tolerant rather than assuming."""
        from roombapy_prime.models.livemap import _maybe_decompress

        payload = b"\x08\x01\x10\x02"

        assert _maybe_decompress(payload) is payload

    def test_a_corrupt_zlib_stream_names_the_header_rather_than_confusing_the_parser(self):
        """Without this, a bad stream would reach the protobuf parser and
        produce an offset error pointing at the wrong layer entirely --
        which is exactly how this bug hid for so long."""
        from roombapy_prime.models.livemap import _maybe_decompress

        with pytest.raises(ValueError, match="789c"):
            _maybe_decompress(b"\x78\x9c" + b"not actually deflate data")
