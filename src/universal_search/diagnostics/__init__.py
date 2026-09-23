"""Index management and diagnostics (spec 015).

    from universal_search.diagnostics import check, collect, reconcile

    statistics = collect(database, paths)   # counts, sizes, worker state
    report = check(database, paths)         # ok / warning / fatal
    reconcile(database, root)               # safe repair

Read-only by default, local-only, no document content in any report.
"""

from universal_search.diagnostics.health import (
    FATAL,
    OK,
    WARNING,
    HealthCheck,
    HealthReport,
    check,
)
from universal_search.diagnostics.repair import (
    ConfirmationRequired,
    RepairBlocked,
    RepairResult,
    rebuild_all,
    rebuild_fts,
    rebuild_intelligence,
    re_extract,
    reconcile,
)
from universal_search.diagnostics.stats import IndexStatistics, collect

__all__ = [
    "FATAL",
    "OK",
    "WARNING",
    "ConfirmationRequired",
    "HealthCheck",
    "HealthReport",
    "IndexStatistics",
    "RepairBlocked",
    "RepairResult",
    "check",
    "collect",
    "re_extract",
    "rebuild_all",
    "rebuild_fts",
    "rebuild_intelligence",
    "reconcile",
]
