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


@pytest.fixture
def deployment_login():
    """Synthetic complete discovery for a login-time prefix comparison."""
    from roombapy_prime.auth import CloudCredentials, ConnectionToken, LoginResult, RobotLoginEntry

    def build(deployments, *, token='new'):
        return LoginResult(
            mqtt_endpoint='mqtt.example.invalid', http_base='https://base.example.invalid',
            http_base_auth='https://auth.example.invalid',
            credentials=CloudCredentials(access_key_id='A', secret_key='S',
                                         session_token=token, cognito_id='synthetic'),
            robots={blid: RobotLoginEntry(sku='W155020', svc_deployment_id=d)
                    for blid, d in deployments.items()},
            connection_tokens=[ConnectionToken(client_id='synthetic', iot_token=token,
                iot_signature='synthetic', iot_authorizer_name='synthetic', expires=None,
                devices=list(deployments))],
            raw={}, irbt_topic_prefix='v007-irbthbu',
            deployments={d: {'irbtTopics': f'{d}-irbthbu'} for d in ('v007', 'v011')},
        )
    return build
