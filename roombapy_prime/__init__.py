"""roombapy-prime — Cloud client for iRobot "Prime"/V4-generation robots.

STATUS (July 11, 2026, twelfth session): Draft, entirely produced
through static analysis (Kotlin/Java decompilation + native bytecode
inspection). Extensive feature coverage (auth, MQTT shadow, mission
control, p2maps map editing, favorites, schedules, DND, cleaning
profiles, mission history) -- but NEVER tested against a real server
or a real V4 device. See docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for
the complete, continuously updated audit status and README.md for the
Contributing section (roombapy_prime.diagnostics -- the live
validation script that could change this).

Why a separate library instead of extending `roombapy`:

`roombapy`'s RoombaRemoteClient sets `ssl.CERT_NONE` globally cached
(correct for local connections, unsafe for a real internet endpoint)
and expects (address, blid, password) as a local IP -- structurally
incompatible with a cloud client. It's not an adaptation problem, but
a fundamentally different trust and connection model. See
docs/ROOMBAPY_COMPARISON.md for the full comparison.

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
"""

__version__ = "0.1.10a0"
