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


def test_error_subclass_hierarchy_is_importable() -> None:
    """NEW (this session, ha_roomba_plus translation-key prep). Every
    typed error subclass must be reachable at the top level, and each
    must actually be a subclass of its untyped base -- callers that
    only catch the base (AuthError/RestError/ShadowError) must keep
    working unchanged."""
    assert issubclass(roombapy_prime.AuthCredentialsError, roombapy_prime.AuthError)
    assert issubclass(roombapy_prime.AuthRateLimitedError, roombapy_prime.AuthError)
    assert issubclass(roombapy_prime.AuthSSLError, roombapy_prime.AuthError)
    assert issubclass(roombapy_prime.AuthConnectionError, roombapy_prime.AuthError)
    assert issubclass(roombapy_prime.AuthTimeoutError, roombapy_prime.AuthError)
    assert issubclass(roombapy_prime.RestSSLError, roombapy_prime.RestError)
    assert issubclass(roombapy_prime.RestConnectionError, roombapy_prime.RestError)
    assert issubclass(roombapy_prime.RestTimeoutError, roombapy_prime.RestError)
    assert issubclass(roombapy_prime.ShadowSSLError, roombapy_prime.ShadowError)
    assert issubclass(roombapy_prime.ShadowConnectionError, roombapy_prime.ShadowError)


def test_all_matches_actual_exports() -> None:
    """__all__ is the documented stability contract -- keep it in sync
    with what's actually importable, rather than letting it drift."""
    expected = {
        "AuthConnectionError",
        "AuthCredentialsError",
        "AuthError",
        "AuthRateLimitedError",
        "AuthSSLError",
        "AuthTimeoutError",
        "LoginResult",
        "PrimeFactory",
        "PrimeRobot",
        "RestConnectionError",
        "RestError",
        "RestSSLError",
        "RestTimeoutError",
        "RobotLoginEntry",
        "ShadowConnectionError",
        "ShadowError",
        "ShadowResponse",
        "ShadowSSLError",
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
