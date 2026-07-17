"""Tests for roombapy_prime's top-level public API surface.

NEW (session 57). Previously the package's __init__.py exported
nothing at all -- every consumer had to reach into internal submodules
directly. These tests exist so a future refactor can't silently drop
one of these names from the top level without a test failing."""

from __future__ import annotations

import roombapy_prime


def test_public_api_is_importable_from_top_level() -> None:
    """Every name a consumer (e.g. ha_roomba_plus) needs for the
    login -> connect -> control flow must be reachable as
    `roombapy_prime.<name>`, not just via internal submodule paths."""
    assert roombapy_prime.PrimeFactory is not None
    assert roombapy_prime.PrimeRobot is not None
    assert roombapy_prime.login is not None
    assert roombapy_prime.LoginResult is not None
    assert roombapy_prime.RobotLoginEntry is not None
    assert roombapy_prime.AuthError is not None
    assert roombapy_prime.ShadowResponse is not None


def test_all_matches_actual_exports() -> None:
    """__all__ is the documented stability contract -- keep it in sync
    with what's actually importable, rather than letting it drift."""
    expected = {
        "AuthError",
        "LoginResult",
        "PrimeFactory",
        "PrimeRobot",
        "RobotLoginEntry",
        "ShadowResponse",
        "login",
    }
    assert set(roombapy_prime.__all__) == expected
    for name in expected:
        assert hasattr(roombapy_prime, name)


def test_version_still_accessible() -> None:
    """diagnostics.py does `from . import __version__ as lib_version`
    -- must keep working regardless of what else __init__.py exports."""
    assert isinstance(roombapy_prime.__version__, str)
    assert roombapy_prime.__version__
