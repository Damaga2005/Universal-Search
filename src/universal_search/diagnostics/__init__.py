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
    SourceRemovalResult,
    rebuild_all,
    rebuild_fts,
    rebuild_intelligence,
    re_extract,
    reconcile,
    remove_indexed_source,
)
from universal_search.diagnostics.stats import (
    DerivedStatistics,
    IndexStatistics,
    SourceStatistics,
    StorageStatistics,
    collect,
    collect_derived,
    collect_sources,
    collect_storage,
)

__all__ = [
    "FATAL",
    "OK",
    "WARNING",
    "ConfirmationRequired",
    "HealthCheck",
    "HealthReport",
    "IndexStatistics",
    "DerivedStatistics",
    "SourceStatistics",
    "StorageStatistics",
    "SourceRemovalResult",
    "RepairBlocked",
    "RepairResult",
    "check",
    "collect",
    "collect_derived",
    "collect_sources",
    "collect_storage",
    "re_extract",
    "remove_indexed_source",
    "rebuild_all",
    "rebuild_fts",
    "rebuild_intelligence",
    "reconcile",
]
