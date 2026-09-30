"""Explicit, local recovery actions (phase 028).

Recovery is a small closed set of cases, never a generic "fix everything"
button. Each case states what it changes, refuses ambiguous ownership and
never deletes a source file.
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_search.appconfig import AppPaths
from universal_search.index.database import SearchDatabase

CASES = (
    "orphan-derived",
    "dirty-derived",
    "stale-coordination",
    "reset-derived",
)
DESTRUCTIVE_CASES = frozenset({"reset-derived"})


class RecoveryConfirmationRequired(RuntimeError):
    """The case deletes derived state and requires ``confirm=True``."""


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    case: str
    code: str
    changed: bool
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "case": self.case,
            "code": self.code,
            "changed": self.changed,
            "message": self.message,
        }


def recover(
    case: str,
    *,
    paths: AppPaths | None = None,
    confirm: bool = False,
) -> RecoveryResult:
    """Run one named recovery case and report exactly what changed."""
    if case not in CASES:
        raise ValueError(f"unknown recovery case: {case}")
    if case in DESTRUCTIVE_CASES and not confirm:
        raise RecoveryConfirmationRequired(f"{case} requires confirm=True")
    paths = paths or AppPaths.discover()
    paths.ensure()
    if case == "stale-coordination":
        return _stale_coordination(paths)
    if case == "orphan-derived":
        return _orphan_derived(paths)
    if case == "dirty-derived":
        return _dirty_derived(paths)
    return _reset_derived(paths)


def _stale_coordination(paths: AppPaths) -> RecoveryResult:
    from universal_search.background import process_alive, read_lock_pid

    pid = read_lock_pid(paths)
    if pid is not None and process_alive(pid):
        return RecoveryResult(
            "stale-coordination", "owner-alive", False,
            f"worker {pid} sigue vivo; no se toca su lock",
        )
    removed: list[str] = []
    for path in (
        paths.lock_file,
        paths.worker_owner_file,
        paths.status_file,
        paths.pause_file,
        paths.stop_file,
    ):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    for claim in paths.home.glob("indexer.starting.*.claim"):
        claim.unlink()
        removed.append(claim.name)
    for temporary in paths.home.glob("indexer.starting.*.tmp"):
        temporary.unlink(missing_ok=True)
    return RecoveryResult(
        "stale-coordination", "repaired", bool(removed),
        "sin owner vivo: " + (", ".join(removed) if removed else "nada que limpiar"),
    )


def _orphan_derived(paths: AppPaths) -> RecoveryResult:
    database = SearchDatabase(paths.database)
    with database.connect() as connection:
        removed = 0
        for table, column in (
            ("document_semantic_terms", "document_id"),
            ("document_semantic", "document_id"),
            ("document_graph_edges", "source_document_id"),
            ("document_graph_terms", "document_id"),
            ("document_graph_nodes", "document_id"),
        ):
            removed += connection.execute(
                f"DELETE FROM {table} WHERE {column} NOT IN"
                " (SELECT id FROM documents)"
            ).rowcount
        connection.commit()
    return RecoveryResult(
        "orphan-derived", "repaired", removed > 0,
        f"{removed} fila(s) derivada(s) sin documento",
    )


def _dirty_derived(paths: AppPaths) -> RecoveryResult:
    database = SearchDatabase(paths.database)
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO document_semantic_metadata(key, value) VALUES('dirty','1')"
            " ON CONFLICT(key) DO UPDATE SET value='1', updated_at=CURRENT_TIMESTAMP"
        )
        connection.execute(
            "INSERT INTO document_graph_metadata(key, value) VALUES('dirty-all','1')"
            " ON CONFLICT(key) DO UPDATE SET value='1', updated_at=CURRENT_TIMESTAMP"
        )
        connection.commit()
    return RecoveryResult(
        "dirty-derived", "marked-dirty", True,
        "graph y semantic index marcados para reconstruccion",
    )


def _reset_derived(paths: AppPaths) -> RecoveryResult:
    database = SearchDatabase(paths.database)
    with database.connect() as connection:
        removed = 0
        for table in (
            "document_semantic_terms",
            "document_semantic",
            "document_semantic_metadata",
            "document_graph_edges",
            "document_graph_terms",
            "document_graph_nodes",
            "document_graph_metadata",
            "document_intelligence",
        ):
            removed += connection.execute(f"DELETE FROM {table}").rowcount
        connection.commit()
    return RecoveryResult(
        "reset-derived", "repaired", removed > 0,
        f"{removed} fila(s) derivada(s) eliminadas; documentos y ficheros intactos",
    )


__all__ = [
    "CASES",
    "DESTRUCTIVE_CASES",
    "RecoveryConfirmationRequired",
    "RecoveryResult",
    "recover",
]