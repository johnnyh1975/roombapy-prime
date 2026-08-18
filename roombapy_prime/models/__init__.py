"""State/command payload types for roombapy-prime -- split into a
package (session 55) for navigability, after the single-file version
grew to 4213 lines / 154 classes across many sessions.

This re-exports everything from every submodule, so existing code
(`from roombapy_prime.models import X`) is completely unaffected by
this split -- see docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
full story behind this refactor and every individual model's
evidence trail.

Import order below follows the actual dependency graph (no cycles):
geometry/enums_common have no internal dependencies; everything else
builds on those and, in a few cases, on mission_control's
CommandParams/Region."""

from .geometry import *  # noqa: F401,F403
from .enums_common import *  # noqa: F401,F403
from .livemap import *  # noqa: F401,F403
from .map_bundle import *  # noqa: F401,F403
from .map_editing import *  # noqa: F401,F403
from .mission_control import *  # noqa: F401,F403
from .favorites import *  # noqa: F401,F403
from .schedules_dnd import *  # noqa: F401,F403
from .mission_history import *  # noqa: F401,F403
from .robot_info import *  # noqa: F401,F403

# EXPLICIT RE-EXPORT, because a `py.typed` package has to say so.
#
# The star imports above carry each module's own `__all__` and are
# re-exported implicitly. This line names two symbols, and under
# `--strict` a consumer importing them from here gets "does not
# explicitly export attribute TimeEstimates" -- which is what happened
# to ha_roomba_plus in CI, on three call sites, while a local editable
# install resolved them fine.
from .time_estimates import (  # noqa: E402
    TimeEstimate as TimeEstimate,
    TimeEstimates as TimeEstimates,
)
