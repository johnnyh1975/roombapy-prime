"""Test configuration for the tools distribution.

Deliberately delegates to the core suite's conftest rather than
duplicating it. An earlier version of this file WAS a byte-for-byte
copy -- introduced, with some irony, during the very refactor whose
purpose was removing duplicated code. Importing keeps the two suites
genuinely in step: a fixture added for the core is available here
without anyone remembering to copy it across.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The core suite's conftest lives outside this distribution's tree, so
# it has to be made importable explicitly. Both are developed and run
# from the same repository checkout; this is a test-time concern only
# and does not create any packaging dependency.
_CORE_TESTS = Path(__file__).resolve().parents[2] / "roombapy_prime" / "tests"
if str(_CORE_TESTS) not in sys.path:
    sys.path.insert(0, str(_CORE_TESTS))

import pytest  # noqa: E402
from conftest import *  # noqa: E402, F403

from roombapy_prime_tools import verify_region_commands  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Shrinks the send path's deliberate pauses to near-zero for tests.

    Those pauses are real and correct in production -- one lets a
    subscription reach the broker before publishing, the other lets a
    near-instant robot-side refusal land before it is read. But six
    tests were spending 3-4 seconds EACH sleeping through them, 21 of
    the suite's 23 seconds, and that cost would compound with every
    future test on this path.

    Patched here rather than in individual tests so it cannot be
    forgotten: a new test touching this code gets the speedup by
    default. Any test that genuinely needs the real timing can set the
    constants back explicitly.
    """
    monkeypatch.setattr(verify_region_commands, "SUBSCRIBE_SETTLE_SECONDS", 0.001)
    monkeypatch.setattr(verify_region_commands, "STATUS_SNAPSHOT_DELAY_SECONDS", 0.001)
