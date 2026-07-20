from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    """NEW (this session): auth.py's login() now caches the discovery
    response in a module-level dict, keyed by country_code (see
    auth.py's _DISCOVERY_CACHE for why this specific response -- no
    per-user data in or out -- is safe to cache at all, unlike
    credentials). Without this fixture, tests that share a
    country_code (most use "US") would silently see the FIRST test's
    mocked discovery response instead of their own -- a real test-
    pollution bug this fixture found directly (17 failures) before
    being added. autouse=True so every test gets a clean cache
    automatically, not just the ones in test_auth.py that happen to
    remember to ask for it."""
    from roombapy_prime import auth

    auth._DISCOVERY_CACHE.clear()
    yield
    auth._DISCOVERY_CACHE.clear()
