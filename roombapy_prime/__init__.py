"""roombapy-prime — Cloud client for iRobot "Prime"/V4-generation robots.

STATUS (updated session 57 -- see CHANGELOG.md for the full history):
Alpha. Login, MQTT shadow connection, mission control
(start/stop/pause/resume/dock via send_simple_command()), and most REST
read endpoints are confirmed working live against two independent real
Prime/V4 accounts (chairstacker, jadestar1864 -- both a Roomba 405
Combo, SKU G185020). Map editing and region-based mission commands
remain unverified against a live server. See
docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the complete,
continuously updated audit status and README.md's "Confidence & known
gaps" section for the current, per-feature breakdown.

Why a separate library instead of extending `roombapy`:

`roombapy`'s RoombaRemoteClient sets `ssl.CERT_NONE` globally cached
(correct for local connections, unsafe for a real internet endpoint)
and expects (address, blid, password) as a local IP -- structurally
incompatible with a cloud client. It's not an adaptation problem, but
a fundamentally different trust and connection model. See
docs/internal/ROOMBAPY_COMPARISON.md for the full comparison.

The name "Prime" is iRobot's own designation (com.irobot.home.prime
app), not our informal "V4".

Module structure:
    auth.py           -- Gigya -> Custom Authorizer token
    mqtt_client.py     -- AWS IoT WebSocket connection, real cert verification
    rest_client.py     -- p2maps, favorites, schedules, DND, mission history, etc.
    models/            -- state/command payload types (package, split by feature area
                          since session 55: geometry, mission_control, map_bundle,
                          map_editing, favorites, schedules_dnd, mission_history,
                          robot_info, livemap, enums_common)
    prime_robot.py     -- public class (analogous to roomba.py in roombapy)
    prime_factory.py   -- factory: username/password/blid instead of a local IP
    diagnostics.py     -- live validation script against a real account

Public API (NEW, session 57): previously this package exported nothing
at all -- every consumer had to reach into internal submodules
directly (e.g. `from roombapy_prime.auth import login`), which
couples callers to internal module layout rather than to a stable
contract. The names below are now re-exported at the top level and are
the intended integration surface for external consumers (e.g.
ha_roomba_plus's planned V4/Prime support); everything else remains
reachable via its submodule but isn't part of the stability contract.
"""

from .auth import (
    AuthCredentialsError,
    AuthError,
    AuthRateLimitedError,
    AuthSSLError,
    AuthConnectionError,
    AuthTimeoutError,
    LoginResult,
    RobotLoginEntry,
    login,
)
from .mqtt_client import ShadowConnectionError, ShadowError, ShadowResponse, ShadowSSLError
from .prime_factory import PrimeFactory
from .prime_robot import PrimeRobot
from .rest_client import RestConnectionError, RestError, RestSSLError, RestTimeoutError

__version__ = "0.1.11a9"

__all__ = [
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
]

