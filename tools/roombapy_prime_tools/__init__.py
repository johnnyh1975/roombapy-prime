"""Diagnostic and field-test tooling for roombapy-prime.

DELIBERATE ONE-WAY BOUNDARY: everything in here may import from the
library core (roombapy_prime.auth, .prime_robot, .models, ...), but the
core must NEVER import from here. That direction was already respected
before this package existed -- moving these modules into their own
subpackage makes it structural rather than a matter of discipline, and
test_tools_boundary.py enforces it mechanically.

Why the separation matters beyond tidiness: these modules register 11
console scripts, several of which MOVE A REAL ROBOT. Keeping them
distinct from the library is what makes it possible to ship the library
to consumers (Home Assistant installations, via ha_roomba_plus) without
putting robot-moving commands on their PATH.
"""
