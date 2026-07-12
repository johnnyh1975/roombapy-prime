# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Nothing has been tagged/released yet — everything below is "Unreleased"
against the eventual `0.1.0`. For the detailed, session-by-session
reverse-engineering trail behind any of this (what was tried, what's
still uncertain, why), see
[`docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md`](docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md).
This file only tracks what changed from a user's point of view.

## [Unreleased]

### Added

- Account login (Gigya + AWS Custom Authorizer), MQTT shadow connection with automatic token refresh
- Live state: `get_state()`, `get_settings()`, `watch_state()` for continuous updates
- Mission control: `send_mission_command()` with the full command vocabulary (`MissionCommandType`, 30 values) and parameter surface (`CommandParams`, 37 fields covering suction, mop wetness, carpet boost, room confinement, timeboxing, drive speed, and more)
- Favorites: list/create/update/delete/reorder, backed by the fully-confirmed `FavoriteV1` model
- Maps: read metadata and active versions, edit rooms/zones/furniture/walls (`edit_map()`, 9 command types), watch the live map while cleaning, download and unpack the full map bundle (`download_map_bundle()` + `parse_map_bundle()`)
- Schedules: list/create/update/delete recurring cleaning schedules per household
- Mission history: `get_mission_history()` plus `parse_mission_history()` for typed results (duration, coverage, end reason via the 19-value `DoneCode` enum), including all 20 mission-timeline sub-event types (`MissionTimelineEvent` — room/zone/travel/plan/error events and more)
- Settings: Do Not Disturb windows, cleaning profiles (`DEEP`/`LIGHT`/`NORMAL`/`SMART`), per-map default routine suggestions
- Parts & device info: consumable part status (`get_robot_parts()`), reset after replacement (`reset_robot_parts()`), serial number data (`get_serial_number_data()`) — confirmed from the actual APK-bundled configuration file, not decompiled logic
- Find-my-robot echo/chirp (`poll_echo_value()`), time estimates (`get_time_estimates()`), full device reset (`reset_robot()`, destructive — see docstring), notification/timeline feed (`get_notifications()`) — same primary-source confirmation
- `roombapy_prime.diagnostics` — a live validation script (`roombapy-prime-validate`) that runs the library's read paths against a real account and reports what works; includes an opt-in reversible favorite create/verify/delete round trip, a credential-redaction pass, and a one-click pre-filled GitHub issue link for sharing results
- Full API reference (`docs/API_REFERENCE.md`) organized by feature area with per-item confidence markers
- MIT license, CI (test matrix across Python 3.11–3.13, lint, package build+install verification)

### Known limitations

See the README's "Confidence & known gaps" section for the current
list — the short version is: reading data rests on a solid,
source-confirmed wire format; anything that *sends* something to the
robot (mission commands, map edits) has the right shape on paper but
has never been confirmed against a real server. This library has never
been run against a real Prime/V4 account.
