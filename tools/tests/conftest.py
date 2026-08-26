"""Test configuration for the tools distribution.

Deliberately delegates to the core suite's conftest rather than
duplicating it. An earlier version of this file WAS a byte-for-byte
copy -- introduced, with some irony, during the very refactor whose
purpose was removing duplicated code. Importing keeps the two suites
genuinely in step: a fixture added for the core is available here
without anyone remembering to copy it across.
"""


from __future__ import annotations

import io
import json
import tarfile
from typing import Any

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
"""Build a real map bundle, for tests that must not mock the parser.

WHY THIS EXISTS.

Every bundle test mocked `parse_map_bundle` as a robot METHOD --
`robot.parse_map_bundle = MagicMock(...)`. `PrimeRobot` has no such
attribute; it is a module function. So the mocks agreed with a call
site that could only ever raise, and two bugs shipped green behind
them: the dict-link bug, and the AttributeError it was masking.

@utkjmitch found the second one by being the first person to reach that
line after b16 made it reachable, and proposed this helper.

The same failure mode was named in b15's own notes for the
`zone_layers` fixture, one function further down. A mock that agrees
with the code proves the code agrees with itself -- so this builds a
real tar.gz and feeds it through the real parser.
"""



def make_bundle_bytes(files: dict[str, Any]) -> bytes:
    """A gzipped tar of `{filename: content}`, as the cloud serves it.

    JSON-serialisable values are written as JSON; anything else is
    written verbatim, so a test can also exercise the non-JSON path
    `parse_map_bundle` documents.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            if isinstance(content, (dict, list)):
                blob = json.dumps(content).encode("utf-8")
            elif isinstance(content, bytes):
                blob = content
            else:
                blob = str(content).encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


@pytest.fixture
def bundle_bytes():
    """The helper above, as a fixture.

    Two conftest files exist in this repo, so `from conftest import ...`
    resolves to whichever pytest imported first. A fixture is
    unambiguous.
    """
    return make_bundle_bytes
