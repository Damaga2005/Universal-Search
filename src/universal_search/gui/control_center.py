"""Phase 023 indexing control center.

The module has two deliberately separate halves:

* :class:`ControlCenterService` is a platform-independent operational
  boundary.  It owns source configuration, safe scans, worker controls and
  explicit maintenance actions; it can be tested without Tk.
* :class:`ControlCenterWindow` is a small optional Tk presentation.  The
  search window opens it from the diagnostics menu, but no indexing or
  database logic lives in the widgets.

All derived data is local and rebuildable.  The service never calls ``unlink``
on a source path: source removal stops future indexing by default, while
``delete_indexed=True`` removes only SQLite/indexed records and derived rows.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from universal_search.appconfig import AppConfig, AppPaths
from universal_search.background_service import (
    STATE_ERROR,
    STATE_STOPPED,
    STATE_STOPPING,
    BackgroundService,
    BackgroundStatus,
)
from universal_search.context import configured_roots
from universal_search.diagnostics import (
    ConfirmationRequired,
    DerivedStatistics,
    HealthCheck,
    HealthReport,
    RepairBlocked,
    SourceStatistics,
    StorageStatistics,
    check,
    collect,
    collect_derived,
    collect_sources,
    collect_storage,
    rebuild_all,
    rebuild_fts,
    rebuild_intelligence,
    remove_indexed_source,
)
from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import IndexStats, Indexer
from universal_search.intelligence import clear as clear_intelligence
from universal_search.intelligence.graph import GraphStore
from universal_search.providers.base import ENUMERATE
from universal_search.platforms.worker import FileLease
from universal_search.providers.registry import ProviderRegistry, register_builtins

log = logging.getLogger("universal_search.control_center")

MAX_FAILURES = 100
MAX_SCAN_HISTORY = 20
MAX_ERROR_CHARS = 500


# -- small, serializable operational models ---------------------------------


@dataclass(frozen=True, slots=True)
class SourceFailure:
    """A human-readable failure tied to a source root, never file content."""

    path: str
    message: str
    kind: str = "indexing"
    count: int = 1

    @property
    def inaccessible(self) -> bool:
        return self.kind in {"inaccessible", "unavailable", "provider-unavailable"}

    @property
    def error(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "message": self.message,
            "kind": self.kind,
            "count": self.count,
            "inaccessible": self.inaccessible,
        }


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Typed result for every mutating or lifecycle action.

    ``data_scope`` is the safety contract: it says what category the action
    can affect.  ``physical_files`` is always zero for built-in maintenance
    actions; a user document is never deleted by this service.
    """

    action: str
    ok: bool = True
    message: str = ""
    changed: int = 0
    skipped: int = 0
    indexed_records: int = 0
    derived_records: int = 0
    physical_files: int = 0
    application_files: int = 0
    data_scope: str = "none"
    code: str = "ok"
    requires_confirmation: bool = False
    confirmed: bool = False
    confirmation_provenance: str | None = None
    errors: tuple[str, ...] = ()
    detail: str = ""
    retained_indexed_records: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.ok

    @property
    def status(self) -> str:
        return "ok" if self.ok else self.code

    @property
    def confirmation_source(self) -> str | None:
        return self.confirmation_provenance

    @property
    def indexed(self) -> int:
        return self.indexed_records

    @property
    def derived(self) -> int:
        return self.derived_records

    @property
    def source_files_deleted(self) -> int:
        return self.physical_files

    @property
    def source_files(self) -> int:
        return self.physical_files

    @property
    def application_owned_files(self) -> int:
        return self.application_files

    @property
    def physical_files_deleted(self) -> int:
        return self.physical_files

    @property
    def files_deleted(self) -> int:
        return self.physical_files

    @property
    def deleted_indexed_records(self) -> int:
        return self.indexed_records

    @property
    def deleted_derived_data(self) -> int:
        return self.derived_records

    @property
    def indexed_data(self) -> int:
        return self.indexed_records

    @property
    def derived_data(self) -> int:
        return self.derived_records

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "ok": self.ok,
            "message": self.message,
            "changed": self.changed,
            "skipped": self.skipped,
            "indexed_records": self.indexed_records,
            "derived_records": self.derived_records,
            "physical_files": self.physical_files,
            "source_files": self.source_files,
            "application_files": self.application_files,
            "data_scope": self.data_scope,
            "code": self.code,
            "requires_confirmation": self.requires_confirmation,
            "confirmed": self.confirmed,
            "confirmation_provenance": self.confirmation_provenance,
            "errors": list(self.errors),
            "detail": self.detail,
            "retained_indexed_records": self.retained_indexed_records,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Human-readable source state used by the control-center view."""

    path: str
    provider: str = "local"
    provider_kind: str = "filesystem"
    available: bool = False
    accessible: bool = False
    configured: bool = True
    document_count: int = 0
    indexed_bytes: int = 0
    supported_types: tuple[tuple[str, int], ...] = ()
    by_source: tuple[tuple[str, int], ...] = ()
    stale_documents: int = 0
    ignored: int = 0
    pending: int = 0
    pending_work: str = ""
    last_scan_at: str | None = None
    last_scan_stats: dict[str, int] = field(default_factory=dict)
    failures: tuple[SourceFailure, ...] = ()
    error: str | None = None
    capabilities: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return self.document_count

    @property
    def indexed_count(self) -> int:
        return self.document_count

    @property
    def provider_available(self) -> bool:
        return self.available

    @property
    def availability(self) -> str:
        if not self.available:
            return "unavailable"
        if not self.accessible:
            return "inaccessible"
        return "available"

    @property
    def provider_capabilities(self) -> tuple[str, ...]:
        return self.capabilities

    @property
    def file_count(self) -> int:
        return self.document_count

    @property
    def types(self) -> dict[str, int]:
        return dict(self.supported_types)

    @property
    def last_scan(self) -> str | None:
        return self.last_scan_at

    @property
    def status(self) -> str:
        if not self.available:
            return "unavailable"
        if not self.accessible:
            return "inaccessible"
        if self.failures:
            return "error"
        if self.pending:
            return "pending"
        return "ready"

    @property
    def status_label(self) -> str:
        return {
            "unavailable": "Proveedor no disponible",
            "inaccessible": "No accesible",
            "error": "Con errores",
            "pending": "Pendiente",
            "ready": "Al día",
        }.get(self.status, self.status)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "provider": self.provider,
            "provider_kind": self.provider_kind,
            "available": self.available,
            "availability": self.availability,
            "accessible": self.accessible,
            "configured": self.configured,
            "document_count": self.document_count,
            "count": self.document_count,
            "file_count": self.document_count,
            "indexed_bytes": self.indexed_bytes,
            "supported_types": dict(self.supported_types),
            "by_source": dict(self.by_source),
            "stale_documents": self.stale_documents,
            "ignored": self.ignored,
            "pending": self.pending,
            "pending_work": self.pending_work,
            "last_scan_at": self.last_scan_at,
            "last_scan_stats": dict(self.last_scan_stats),
            "failures": [failure.as_dict() for failure in self.failures],
            "error": self.error,
            "capabilities": list(self.capabilities),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class DerivedSnapshot:
    """Current state of intelligence and relationship graph data."""

    intelligence_rows: int = 0
    intelligence_version: int | None = None
    graph_nodes: int = 0
    graph_edges: int = 0
    graph_terms: int = 0
    graph_metadata: int = 0
    graph_version: int | None = None
    graph_preprocessing_version: int | None = None
    graph_current: bool = False
    dirty: int = 0
    error: str | None = None

    @property
    def relationships(self) -> int:
        return self.graph_edges

    @property
    def ready(self) -> bool:
        return self.error is None and self.graph_current and self.dirty == 0

    @classmethod
    def from_statistics(cls, statistics: DerivedStatistics) -> "DerivedSnapshot":
        return cls(
            intelligence_rows=statistics.intelligence_rows,
            intelligence_version=statistics.intelligence_version,
            graph_nodes=statistics.graph_nodes,
            graph_edges=statistics.graph_edges,
            graph_terms=statistics.graph_terms,
            graph_metadata=statistics.graph_metadata,
            graph_version=statistics.graph_version,
            graph_preprocessing_version=statistics.graph_preprocessing_version,
            graph_current=statistics.graph_current,
            dirty=statistics.dirty,
            error=statistics.error,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "intelligence_rows": self.intelligence_rows,
            "intelligence_version": self.intelligence_version,
            "graph_nodes": self.graph_nodes,
            "graph_edges": self.graph_edges,
            "graph_terms": self.graph_terms,
            "graph_metadata": self.graph_metadata,
            "graph_version": self.graph_version,
            "graph_preprocessing_version": self.graph_preprocessing_version,
            "graph_current": self.graph_current,
            "dirty": self.dirty,
            "relationships": self.relationships,
            "ready": self.ready,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class StorageSnapshot:
    """Measured local storage; no source file is inspected for content."""

    database_bytes: int = 0
    wal_bytes: int = 0
    shm_bytes: int = 0
    application_files: int = 0
    source_files: int = 0
    config_bytes: int = 0
    log_bytes: int = 0
    metrics_bytes: int = 0
    control_state_bytes: int = 0
    error: str | None = None

    @property
    def total_bytes(self) -> int:
        return self.database_bytes + self.wal_bytes + self.shm_bytes

    @property
    def application_bytes(self) -> int:
        return (
            self.total_bytes
            + self.config_bytes
            + self.log_bytes
            + self.metrics_bytes
            + self.control_state_bytes
        )

    @classmethod
    def from_statistics(cls, statistics: StorageStatistics) -> "StorageSnapshot":
        return cls(
            database_bytes=statistics.database_bytes,
            wal_bytes=statistics.wal_bytes,
            shm_bytes=statistics.shm_bytes,
            application_files=statistics.application_files,
            source_files=statistics.source_files,
            config_bytes=statistics.config_bytes,
            log_bytes=statistics.log_bytes,
            metrics_bytes=statistics.metrics_bytes,
            control_state_bytes=statistics.control_state_bytes,
            error=statistics.error,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "database": self.database_bytes,
            "wal": self.wal_bytes,
            "shm": self.shm_bytes,
            "total": self.total_bytes,
            "application_files": self.application_files,
            "source_files": self.source_files,
            "config": self.config_bytes,
            "log": self.log_bytes,
            "metrics": self.metrics_bytes,
            "control_state": self.control_state_bytes,
            "application": self.application_bytes,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    """One coherent operational view for the control center and tests."""

    generated_at: str = ""
    sources: tuple[SourceSnapshot, ...] = ()
    health: dict[str, object] = field(default_factory=dict)
    health_report: HealthReport | None = None
    statistics: dict[str, object] = field(default_factory=dict)
    derived: DerivedSnapshot = field(default_factory=DerivedSnapshot)
    storage: StorageSnapshot = field(default_factory=StorageSnapshot)
    worker: BackgroundStatus = field(
        default_factory=lambda: BackgroundStatus(state=STATE_STOPPED)
    )
    failures: tuple[SourceFailure, ...] = ()
    exclusions: tuple[str, ...] = ()
    pending: str = ""
    pending_work: str = ""
    last_scan_at: str | None = None
    last_scan_stats: dict[str, int] = field(default_factory=dict)
    stale_worker: bool = False
    recovery_required: bool = False
    pending_operation: dict[str, object] | None = None

    @property
    def health_status(self) -> str:
        return str(self.health.get("status", "unknown"))

    @property
    def derived_data(self) -> DerivedSnapshot:
        return self.derived

    @property
    def storage_use(self) -> StorageSnapshot:
        return self.storage

    @property
    def worker_state(self) -> str:
        return self.worker.state

    @property
    def document_count(self) -> int:
        return sum(source.document_count for source in self.sources)

    @property
    def pending_count(self) -> int:
        return sum(source.pending for source in self.sources)

    @property
    def source_paths(self) -> tuple[str, ...]:
        return tuple(source.path for source in self.sources)

    @property
    def worker_status(self) -> BackgroundStatus:
        return self.worker

    @property
    def storage_bytes(self) -> int:
        return self.storage.total_bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "sources": [source.as_dict() for source in self.sources],
            "health": dict(self.health),
            "statistics": dict(self.statistics),
            "derived": self.derived.as_dict(),
            "storage": self.storage.as_dict(),
            "worker": self.worker.as_dict(),
            "failures": [failure.as_dict() for failure in self.failures],
            "exclusions": list(self.exclusions),
            "pending": self.pending,
            "pending_count": self.pending_count,
            "pending_work": self.pending_work,
            "last_scan_at": self.last_scan_at,
            "last_scan_stats": dict(self.last_scan_stats),
            "stale_worker": self.stale_worker,
            "recovery_required": self.recovery_required,
            "pending_operation": (
                dict(self.pending_operation) if self.pending_operation else None
            ),
        }


# -- service -----------------------------------------------------------------


class ControlCenterService:
    """Own operational state and explicit actions for the control center."""

    def __init__(
        self,
        paths: AppPaths | None = None,
        database_path: Path | str | None = None,
        *,
        database: SearchDatabase | None = None,
        service: Any | None = None,
        background_service: BackgroundService | None = None,
        registry: ProviderRegistry | None = None,
        indexer_factory: Callable[[SearchDatabase], Any] = Indexer,
    ) -> None:
        # Accept a SearchService-like object as a convenience for the GUI,
        # while keeping the service independently constructible in tests.
        if service is not None:
            paths = service.paths
            database = service.database
        if isinstance(paths, SearchDatabase):
            positional_database = paths
            if isinstance(database_path, AppPaths):
                paths, database_path = database_path, None
            else:
                paths = None  # type: ignore[assignment]
            if database is None:
                database = positional_database
        if isinstance(database_path, SearchDatabase) and database is None:
            database, database_path = database_path, None
        if isinstance(database, (str, Path)):
            database = SearchDatabase(Path(database))
        if paths is not None and not isinstance(paths, AppPaths):
            paths = AppPaths(Path(paths))
        self.paths = paths or AppPaths.discover()
        self.paths.ensure()
        selected_database = database or SearchDatabase(
            Path(database_path) if database_path is not None else self.paths.database
        )
        self.database = selected_database
        self.indexer_factory = indexer_factory
        self.background = background_service or BackgroundService(self.paths)
        self.registry = registry or register_builtins()
        self.config = AppConfig.load(self.paths)
        self._action_lock = threading.Lock()
        self._scan_history: deque[dict[str, object]] = deque(maxlen=MAX_SCAN_HISTORY)
        self._scan_by_root: dict[str, dict[str, object]] = {}
        self._failures: dict[str, SourceFailure] = {}
        self._pending_operation: dict[str, object] | None = None
        self._load_state()

    @property
    def database_path(self) -> Path:
        return Path(self.database.path)

    @property
    def state_file(self) -> Path:
        return self.paths.control_state_file

    @property
    def failures(self) -> tuple[SourceFailure, ...]:
        return tuple(self._failures.values())

    def _load_state(self) -> None:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        history = raw.get("scan_history")
        if isinstance(history, list):
            for item in history[-MAX_SCAN_HISTORY:]:
                if isinstance(item, dict):
                    self._scan_history.append(dict(item))
        roots = raw.get("roots")
        if isinstance(roots, dict):
            for path, item in roots.items():
                if isinstance(path, str) and isinstance(item, dict):
                    self._scan_by_root[_comparison_path(path)] = dict(item)
        pending = raw.get("pending_operation")
        if isinstance(pending, dict):
            self._pending_operation = dict(pending)
        failures = raw.get("failures")
        if isinstance(failures, list):
            for item in failures[:MAX_FAILURES]:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "")
                message = _bounded_error(item.get("message"))
                if path and message:
                    try:
                        count = max(1, int(item.get("count") or 1))
                    except (TypeError, ValueError, OverflowError):
                        count = 1
                    self._failures[_comparison_path(path)] = SourceFailure(
                        path=path,
                        message=message,
                        kind=str(item.get("kind") or "indexing"),
                        count=count,
                    )

    def _persist_state(self) -> None:
        payload = {
            "version": 1,
            "last_scan_at": (
                self._scan_history[-1].get("at") if self._scan_history else None
            ),
            "last_scan_stats": (
                self._scan_history[-1].get("stats", {})
                if self._scan_history
                else {}
            ),
            "scan_history": list(self._scan_history),
            "roots": self._scan_by_root,
            "pending_operation": self._pending_operation,
            "failures": [
                failure.as_dict() for failure in self._failures.values()
            ],
        }
        try:
            self.paths.ensure()
            temporary = self.state_file.with_name(
                self.state_file.name + f".{os.getpid()}.tmp"
            )
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self.state_file)
        except OSError:
            # Operational state is helpful but not authoritative.  Never turn
            # a successful scan into a failure merely because a sidecar could
            # not be written.
            log.warning("could not persist control-center state (%s)", type(_current_exception()).__name__)

    def _reload_config(self) -> AppConfig:
        self.config = AppConfig.load(self.paths)
        return self.config

    def _roots(self, config: AppConfig | None = None) -> tuple[str, ...]:
        roots: list[str] = []
        seen: set[str] = set()
        for raw_root in configured_roots(config or self.config):
            display = _root_text(raw_root) or str(raw_root)
            key = _comparison_path(display)
            if key in seen:
                continue
            seen.add(key)
            roots.append(display)
        return tuple(roots)

    @staticmethod
    def _without_root(config: AppConfig, target: str) -> AppConfig:
        target_key = _comparison_path(target)
        roots = tuple(
            root for root in config.roots if _comparison_path(root) != target_key
        )
        contexts: list[dict] = []
        for raw in config.contexts:
            if not isinstance(raw, dict):
                continue
            copied = dict(raw)
            context_roots = copied.get("roots", ())
            if isinstance(context_roots, (list, tuple)):
                copied["roots"] = [
                    root
                    for root in context_roots
                    if _comparison_path(str(root)) != target_key
                ]
            contexts.append(copied)
        return replace(config, roots=roots, contexts=tuple(contexts))

    def _run_locked(
        self,
        action: str,
        operation: Callable[[], ActionResult],
        *,
        coordinate_worker: bool = True,
    ) -> ActionResult:
        if not self._action_lock.acquire(blocking=False):
            return ActionResult(
                action=action,
                ok=False,
                message="Otra operación de indexación está en curso",
                code="busy",
                errors=("operation-busy",),
                data_scope="none",
            )
        worker_lease: FileLease | None = None
        if coordinate_worker:
            try:
                worker_status = self.background.status()
                worker_active = worker_status.state not in {
                    STATE_STOPPED,
                    STATE_STOPPING,
                }
            except Exception:
                # The OS lease below is authoritative; a status probe failure
                # must not make a safe mutation possible by accident.
                worker_active = True
            if worker_active:
                self._action_lock.release()
                return ActionResult(
                    action=action,
                    ok=False,
                    message="El indexador está activo; pausa o detén el trabajador antes de continuar",
                    code="worker-busy",
                    errors=("worker-busy",),
                    data_scope="none",
                )
            worker_lease = FileLease(self.paths.worker_lease_file)
            if not worker_lease.acquire():
                self._action_lock.release()
                return ActionResult(
                    action=action,
                    ok=False,
                    message="El indexador está activo; pausa o detén el trabajador antes de continuar",
                    code="worker-busy",
                    errors=("worker-busy",),
                    data_scope="none",
                )
        try:
            return operation()
        except Exception as exc:  # every UI action returns a typed failure
            message = _bounded_error(f"{type(exc).__name__}: {exc}")
            log.warning("control-center action %s failed (%s)", action, type(exc).__name__)
            return ActionResult(
                action=action,
                ok=False,
                message=message,
                code="error",
                errors=(message,),
            )
        finally:
            if worker_lease is not None:
                worker_lease.release()
            self._action_lock.release()

    def _provider_for(self, path: str) -> tuple[str, str, bool, tuple[str, ...]]:
        try:
            from universal_search.providers.onedrive import is_onedrive_path

            key = "onedrive" if is_onedrive_path(path) else "local"
        except Exception:
            key = "local"
        try:
            info = next(
                (item for item in self.registry.infos() if item.key == key), None
            )
        except Exception:
            info = None
        if info is None:
            return key, "filesystem", False, ()
        return info.key, info.kind, bool(info.available), tuple(info.capabilities)

    def _source_failures(self, path: str, statistics: SourceStatistics) -> tuple[SourceFailure, ...]:
        key = _comparison_path(path)
        failures: list[SourceFailure] = []
        stored = self._failures.get(key)
        if stored is not None:
            failures.append(stored)
        if not statistics.accessible:
            message = statistics.error or "la carpeta no existe o no es accesible"
            if not any(item.message == message for item in failures):
                failures.append(
                    SourceFailure(
                        path=path,
                        message=message,
                        kind="inaccessible",
                    )
                )
        elif statistics.error:
            message = statistics.error
            if not any(item.message == message for item in failures):
                failures.append(
                    SourceFailure(path=path, message=message, kind="database")
                )
        return tuple(failures)

    def snapshot(self) -> ControlSnapshot:
        """Return a read-only, coherent view without repairing the index."""
        config = self._reload_config()
        roots = self._roots(config)
        try:
            worker = _coerce_worker_status(self.background.status())
        except Exception as exc:
            worker = BackgroundStatus(
                state=STATE_ERROR,
                error=_bounded_error(f"estado ilegible ({type(exc).__name__})"),
            )
        try:
            source_stats = collect_sources(self.database, roots)
        except Exception as exc:
            log.warning("could not collect source statistics (%s)", type(exc).__name__)
            source_stats = tuple(
                SourceStatistics(
                    path=root,
                    accessible=False,
                    available=False,
                    error=_bounded_error(f"{type(exc).__name__}: {exc}"),
                )
                for root in roots
            )
        sources: list[SourceSnapshot] = []
        for statistics in source_stats:
            scan = self._scan_by_root.get(_comparison_path(statistics.path), {})
            failures = self._source_failures(statistics.path, statistics)
            provider, kind, available, capabilities = self._provider_for(
                statistics.path
            )
            if not available or ENUMERATE not in capabilities:
                provider_message = (
                    f"proveedor no disponible para: {statistics.path}"
                    if not available
                    else f"el proveedor no puede enumerar: {statistics.path}"
                )
                if not any(item.message == provider_message for item in failures):
                    failures = failures + (
                        SourceFailure(
                            path=statistics.path,
                            message=provider_message,
                            kind="provider-unavailable",
                        ),
                    )
            last_stats = scan.get("stats")
            if not isinstance(last_stats, dict):
                last_stats = worker.last_scan_stats or {}
            last_scan_at = scan.get("at") or worker.last_scan_at
            sources.append(
                SourceSnapshot(
                    path=statistics.path,
                    provider=provider,
                    provider_kind=kind,
                    available=available,
                    accessible=statistics.accessible,
                    configured=True,
                    document_count=statistics.documents,
                    indexed_bytes=statistics.indexed_bytes,
                    supported_types=statistics.by_type,
                    by_source=statistics.by_source,
                    stale_documents=statistics.stale_documents,
                    ignored=int(scan.get("ignored", 0) or 0),
                    pending=1 if worker.state in {"starting", "indexing"} else 0,
                    pending_work=worker.pending,
                    last_scan_at=(
                        str(last_scan_at) if last_scan_at is not None else None
                    ),
                    last_scan_stats=(
                        dict(last_stats) if isinstance(last_stats, dict) else {}
                    ),
                    failures=failures,
                    error=statistics.error,
                    capabilities=capabilities,
                )
            )
        try:
            report = check(self.database, self.paths)
        except Exception as exc:
            report = HealthReport(
                checks=(
                    HealthCheck(
                        "control-center",
                        "fatal",
                        _bounded_error(f"no se pudo comprobar el índice: {exc}"),
                    ),
                )
            )
        try:
            statistics_payload = collect(self.database, self.paths).as_dict()
        except Exception as exc:
            statistics_payload = {
                "error": _bounded_error(f"{type(exc).__name__}: {exc}")
            }
        try:
            derived = DerivedSnapshot.from_statistics(collect_derived(self.database))
        except Exception as exc:
            derived = DerivedSnapshot(error=_bounded_error(f"{type(exc).__name__}: {exc}"))
        try:
            storage = StorageSnapshot.from_statistics(
                collect_storage(self.database, self.paths)
            )
        except Exception as exc:
            storage = StorageSnapshot(error=_bounded_error(f"{type(exc).__name__}: {exc}"))
        all_failures = tuple(
            failure for source in sources for failure in source.failures
        )
        last = self._scan_history[-1] if self._scan_history else {}
        last_stats = last.get("stats") if isinstance(last, dict) else {}
        health_payload = (
            report.as_dict()
            if hasattr(report, "as_dict")
            else dict(report) if isinstance(report, dict) else {"status": "unknown"}
        )
        return ControlSnapshot(
            generated_at=_now(),
            sources=tuple(sources),
            health=health_payload,
            health_report=report if isinstance(report, HealthReport) else None,
            statistics=statistics_payload,
            derived=derived,
            storage=storage,
            worker=worker,
            failures=all_failures,
            exclusions=tuple(
                [*(f"dir:{item}" for item in config.ignore_dirs),
                 *(f"pattern:{item}" for item in config.ignore_patterns)]
            ),
            pending=worker.pending,
            pending_work=worker.pending,
            last_scan_at=(
                str(last.get("at"))
                if last and last.get("at")
                else worker.last_scan_at
            ),
            last_scan_stats=(
                dict(last_stats)
                if isinstance(last_stats, dict)
                else dict(worker.last_scan_stats or {})
            ),
            stale_worker=worker.stale_lock,
            recovery_required=self._pending_operation is not None,
            pending_operation=(
                dict(self._pending_operation)
                if self._pending_operation is not None
                else None
            ),
        )

    def add_source(
        self, path: Path | str, *, scan: bool = False
    ) -> ActionResult:
        """Validate and configure a source root; never indexes implicitly."""
        target = _root_text(path)
        if not target:
            return _failure("add-source", "invalid-source", "Ruta de fuente vacía")
        if Path(target).exists() and not Path(target).is_dir():
            return _failure(
                "add-source",
                "invalid-source",
                f"La ruta no es una carpeta: {target}",
                errors=(f"source-not-directory:{target}",),
            )
        operation = lambda: self._add_source_locked(target, scan=scan)
        return self._run_locked("add-source", operation)

    def _add_source_locked(self, target: str, *, scan: bool) -> ActionResult:
        config = self._reload_config()
        if any(
            _comparison_path(root) == _comparison_path(target)
            for root in self._roots(config)
        ):
            return ActionResult(
                action="add-source",
                message="La carpeta ya está configurada",
                changed=0,
                data_scope="configuration",
            )
        updated = replace(config, roots=config.roots + (target,))
        try:
            updated.save(self.paths)
        except OSError as exc:
            return _failure(
                "add-source",
                "config-error",
                f"No se pudo guardar la configuración: {exc}",
                errors=(type(exc).__name__,),
            )
        self.config = updated
        self._failures.pop(_comparison_path(target), None)
        self._persist_state()
        if scan:
            result = self._rescan_paths((target,), action="add-source-scan")
            return replace(
                result,
                action="add-source",
                changed=1 + result.changed,
                message=f"Fuente añadida; {result.message}",
                data_scope="configuration",
            )
        if Path(target).is_dir():
            message = "Carpeta añadida; el indexador la tendrá en cuenta en la próxima pasada"
        else:
            message = "Carpeta añadida, pero ahora no es accesible; se podrá reintentar más tarde"
        return ActionResult(
            action="add-source",
            message=message,
            changed=1,
            data_scope="configuration",
            metadata={"accessible": Path(target).is_dir()},
        )

    def remove_source(
        self, path: Path | str, delete_indexed: bool = False
    ) -> ActionResult:
        """Stop a source, optionally removing only its indexed records."""
        target = _root_text(path)
        if not target:
            return _failure("remove-source", "invalid-source", "Ruta de fuente vacía")
        operation = lambda: self._remove_source_locked(target, delete_indexed)
        return self._run_locked("remove-source", operation)

    def _remove_source_locked(
        self, target: str, delete_indexed: bool
    ) -> ActionResult:
        config = self._reload_config()
        current_roots = self._roots(config)
        target_key = _comparison_path(target)
        if not any(_comparison_path(root) == target_key for root in current_roots):
            return _failure(
                "remove-source",
                "unknown-source",
                f"La carpeta no está configurada: {target}",
            )
        retained = 0
        if not delete_indexed:
            try:
                stats = collect_sources(self.database, current_roots)
                retained = next(
                    statistics.documents
                    for statistics in stats
                    if _comparison_path(statistics.path) == target_key
                )
            except (StopIteration, OSError, ValueError):
                retained = 0
        updated = self._without_root(config, target)
        previous_roots = tuple(config.roots)
        previous_contexts = tuple(config.contexts)
        self._pending_operation = {
            "kind": "remove-source",
            "phase": "prepared",
            "target": target,
            "target_key": target_key,
            "delete_indexed": bool(delete_indexed),
            "previous_roots": list(previous_roots),
            "previous_contexts": list(previous_contexts),
        }
        self._persist_state()
        # Configuration is written first.  If persistence fails, the index is
        # untouched; this is the important half of the cross-resource
        # transaction and is directly testable by injecting a save failure.
        try:
            updated.save(self.paths)
        except OSError as exc:
            self._pending_operation = None
            self._persist_state()
            return _failure(
                "remove-source",
                "config-error",
                f"No se pudo guardar la configuración: {exc}",
                errors=(type(exc).__name__,),
            )
        self.config = updated
        self._pending_operation["phase"] = "config-saved-db-pending"
        self._persist_state()

        indexed = 0
        derived = 0
        if delete_indexed:
            try:
                removed = remove_indexed_source(
                    self.database,
                    target,
                    configured_roots=self._roots(updated),
                )
            except Exception as exc:
                # A collaborator can fail after its transaction committed.  Do
                # not blindly restore the old config in that case: inspect the
                # target-owned rows first and keep the already-consistent
                # updated state when the deletion is visibly complete.
                try:
                    post_state = collect_sources(self.database, (target,))[0]
                except Exception:
                    post_state = None
                if post_state is not None and post_state.error is None and post_state.documents == 0:
                    self._pending_operation = None
                    self._failures.pop(target_key, None)
                    self._scan_by_root.pop(target_key, None)
                    self._persist_state()
                    return ActionResult(
                        action="remove-source",
                        ok=True,
                        message=(
                            "Fuente quitada; la eliminación indexada se confirmó "
                            "aunque el proveedor informó de un error"
                        ),
                        changed=1,
                        data_scope="indexed_records",
                        code="ok",
                        metadata={"post_commit_error": _bounded_error(str(exc))},
                    )
                # Restore the old source configuration before reporting failure.
                # If that restore itself fails, retain the journal marker so a
                # later service instance can recover instead of silently
                # disagreeing with SQLite.
                try:
                    config.save(self.paths)
                    self.config = config
                    self._pending_operation = None
                    self._persist_state()
                    return _failure(
                        "remove-source",
                        "database-error",
                        f"No se pudieron quitar los registros indexados: {exc}",
                        errors=(type(exc).__name__,),
                        data_scope="indexed_records",
                    )
                except Exception as restore_error:
                    self._pending_operation["phase"] = "restore-required"
                    self._pending_operation["restore_error"] = _bounded_error(
                        f"{type(restore_error).__name__}: {restore_error}"
                    )
                    self._persist_state()
                    return _failure(
                        "remove-source",
                        "recovery-required",
                        "La configuración y el índice requieren recuperación; no se borraron archivos",
                        errors=(
                            f"database:{type(exc).__name__}",
                            f"config:{type(restore_error).__name__}",
                        ),
                        data_scope="indexed_records",
                    )
            indexed = removed.documents
            derived = removed.derived_rows + removed.graph_rows
        self._pending_operation = None
        self._failures.pop(target_key, None)
        self._scan_by_root.pop(target_key, None)
        self._persist_state()
        if delete_indexed:
            message = (
                f"Fuente quitada; se eliminaron {indexed} registro(s) indexado(s) "
                "y sus datos derivados. Los archivos originales no se tocaron."
            )
            scope = "indexed_records"
        else:
            message = (
                "Fuente quitada; los registros indexados se conservaron y "
                "los archivos originales no se tocaron."
            )
            scope = "configuration"
        return ActionResult(
            action="remove-source",
            message=message,
            changed=(1 + indexed + derived) if delete_indexed else 1,
            indexed_records=indexed,
            derived_records=derived,
            retained_indexed_records=retained,
            data_scope=scope,
            code="ok",
        )

    def rescan(self, path: Path | str | None = None) -> ActionResult:
        """Run a safe reconciliation pass for one or all configured roots."""
        target = None if path is None else _root_text(path)
        if path is not None and not target:
            return _failure("rescan", "invalid-source", "Ruta de fuente vacía")
        operation = lambda: self._rescan_requested(target)
        return self._run_locked("rescan", operation)

    def _rescan_requested(self, target: str | None) -> ActionResult:
        config = self._reload_config()
        roots = self._roots(config)
        if target is not None:
            matching = next(
                (root for root in roots if _comparison_path(root) == _comparison_path(target)),
                None,
            )
            if matching is None:
                return _failure(
                    "rescan",
                    "unknown-source",
                    f"La carpeta no está configurada: {target}",
                )
            roots = (matching,)
        return self._rescan_paths(roots, action="rescan")

    def _rescan_paths(
        self, roots: Iterable[str], *, action: str = "rescan"
    ) -> ActionResult:
        config = self._reload_config()
        raw_roots = tuple(roots)
        provider_blocked: set[str] = set()
        for root in raw_roots:
            _provider, _kind, available, capabilities = self._provider_for(root)
            if not available or ENUMERATE not in capabilities:
                provider_blocked.add(_comparison_path(root))
        roots = _outermost_accessible_roots(raw_roots, provider_blocked)
        rules = config.ignore_rules()
        totals = IndexStats()
        errors: list[str] = []
        scanned: list[str] = []
        provider_unavailable = False
        at = _now()
        for raw_root in roots:
            root = str(raw_root)
            _provider, _kind, available, capabilities = self._provider_for(root)
            if not available or ENUMERATE not in capabilities:
                provider_unavailable = True
                message = (
                    f"proveedor no disponible para: {root}"
                    if not available
                    else f"el proveedor no puede enumerar: {root}"
                )
                errors.append(message)
                self._failures[_comparison_path(root)] = SourceFailure(
                    path=root,
                    message=message,
                    kind="provider-unavailable",
                )
                continue
            root_path = Path(root)
            if not root_path.is_dir():
                message = f"carpeta no accesible: {root}"
                errors.append(message)
                self._failures[_comparison_path(root)] = SourceFailure(
                    path=root, message=message, kind="inaccessible"
                )
                continue
            try:
                indexer = self.indexer_factory(self.database)
                stats = indexer.index_root(
                    root_path,
                    rules=rules,
                    delay=0.0,
                    onedrive_download_mb=config.onedrive_download_max_mb,
                )
                try:
                    indexer.close()
                except AttributeError:
                    pass
                if not isinstance(stats, IndexStats):
                    stats = _coerce_stats(stats)
                totals.merge(stats)
                scanned.append(root)
                stats_error_count = stats.errors + stats.extraction_errors
                if stats_error_count:
                    message = (
                        f"{stats_error_count} error(es) de lectura durante la exploración"
                    )
                    errors.append(f"{root}: {message}")
                    self._failures[_comparison_path(root)] = SourceFailure(
                        path=root,
                        message=message,
                        kind="scan",
                        count=stats_error_count,
                    )
                else:
                    self._failures.pop(_comparison_path(root), None)
                self._scan_by_root[_comparison_path(root)] = {
                    "at": at,
                    "stats": dict(stats.as_dict()),
                    "ignored": stats.ignored,
                }
            except Exception as exc:
                message = _bounded_error(f"{type(exc).__name__}: {exc}")
                errors.append(f"{root}: {message}")
                self._failures[_comparison_path(root)] = SourceFailure(
                    path=root, message=message, kind="indexing"
                )
        if scanned:
            record = {"at": at, "stats": totals.as_dict(), "roots": list(scanned)}
            self._scan_history.append(record)
        self._persist_state()
        indexed = totals.created + totals.updated + totals.deleted
        detail = totals.summary()
        if errors:
            message = "La exploración terminó con errores: " + "; ".join(errors[:3])
        elif scanned:
            message = f"Exploración completada: {indexed} cambio(s)"
        else:
            message = "No hay carpetas configuradas para explorar"
        return ActionResult(
            action=action,
            ok=not errors and bool(scanned),
            message=message,
            changed=indexed,
            skipped=totals.unchanged,
            indexed_records=indexed,
            data_scope="indexed_records",
            code=(
                "ok"
                if not errors
                else "provider-unavailable"
                if provider_unavailable and not scanned
                else "partial"
                if scanned
                else "error"
            ),
            errors=tuple(errors),
            detail=detail,
            metadata={"roots": list(scanned), "stats": totals.as_dict()},
        )

    def retry_failures(self) -> ActionResult:
        """Retry roots that previously failed, without touching other roots."""
        operation = lambda: self._retry_failures_locked()
        return self._run_locked("retry-failures", operation)

    def _retry_failures_locked(self) -> ActionResult:
        pending = self._pending_operation
        if pending is not None:
            target = str(pending.get("target") or "")
            target_key = _comparison_path(target) if target else ""
            config = self._reload_config()
            still_configured = any(
                _comparison_path(root) == target_key for root in self._roots(config)
            )
            if still_configured:
                # The journal was written before the config replace; the
                # source is still authoritative and the stale marker is safe to
                # discard.
                self._pending_operation = None
                self._persist_state()
            elif bool(pending.get("delete_indexed")) and target:
                try:
                    removed = remove_indexed_source(
                        self.database,
                        target,
                        configured_roots=self._roots(config),
                    )
                except Exception as exc:
                    return _failure(
                        "retry-failures",
                        "recovery-required",
                        _bounded_error(f"No se pudo completar la recuperación: {exc}"),
                        errors=(type(exc).__name__,),
                        data_scope="indexed_records",
                    )
                self._pending_operation = None
                self._failures.pop(target_key, None)
                self._scan_by_root.pop(target_key, None)
                self._persist_state()
                return ActionResult(
                    action="retry-failures",
                    message="Se completó la recuperación del índice",
                    changed=removed.changed,
                    indexed_records=removed.documents,
                    derived_records=removed.derived_rows + removed.graph_rows,
                    data_scope="indexed_records",
                )
            else:
                self._pending_operation = None
                self._persist_state()
        configured = {
            _comparison_path(root): root for root in self._roots()
        }
        selected: list[str] = []
        selected_keys: set[str] = set()
        stale_keys: list[str] = []
        for key, failure in tuple(self._failures.items()):
            current = configured.get(key)
            if current is None:
                stale_keys.append(key)
                continue
            if key not in selected_keys:
                selected.append(current)
                selected_keys.add(key)
        for key, root in configured.items():
            if not Path(root).is_dir() and key not in selected_keys:
                selected.append(root)
                selected_keys.add(key)
        for key in stale_keys:
            self._failures.pop(key, None)
        if stale_keys:
            self._persist_state()
        if not selected:
            return ActionResult(
                action="retry-failures",
                message="No hay fallos registrados para reintentar",
                data_scope="indexed_records",
            )
        return self._rescan_paths(selected, action="retry-failures")

    def pause(self) -> ActionResult:
        return self._lifecycle("pause", lambda: self.background.pause(), {"paused"})

    def resume(self) -> ActionResult:
        return self._lifecycle("resume", lambda: self.background.resume(), {"resumed"})

    def start(self) -> ActionResult:
        return self._lifecycle("start", lambda: self.background.start(), {"started", "already-running"})

    def stop(self) -> ActionResult:
        return self._lifecycle("stop", lambda: self.background.stop(), {"stopped", "not-running"})

    def set_paused(self, paused: bool) -> ActionResult:
        return self.pause() if paused else self.resume()

    def _lifecycle(
        self, action: str, operation: Callable[[], tuple[str, str]], success: set[str]
    ) -> ActionResult:
        def run() -> ActionResult:
            try:
                state, message = operation()
            except Exception as exc:
                return _failure(
                    action,
                    "error",
                    f"No se pudo completar la operación: {exc}",
                    errors=(type(exc).__name__,),
                )
            return ActionResult(
                action=action,
                ok=state in success,
                message=message,
                code=state,
                data_scope="configuration",
                skipped=0 if state in success else 1,
            )

        return self._run_locked(action, run, coordinate_worker=False)

    def rebuild(self, kind: str, confirm: bool = False) -> ActionResult:
        """Rebuild one maintenance layer with an explicit safety scope."""
        selected = _rebuild_kind(kind)
        if selected is None:
            return _failure(
                "rebuild",
                "unknown-kind",
                f"Tipo de reconstrucción desconocido: {kind}",
            )
        scope, requires = _rebuild_scope(selected)
        if requires and not confirm:
            return ActionResult(
                action=f"rebuild-{selected}",
                ok=False,
                message=(
                    "Se requiere confirmación explícita; esta operación no borra "
                    "archivos físicos"
                ),
                code="confirmation-required",
                requires_confirmation=True,
                confirmation_provenance="required",
                data_scope=scope,
                errors=("confirmation-required",),
            )

        def operation() -> ActionResult:
            try:
                if selected == "fts":
                    result = rebuild_fts(self.database, confirm=True)
                    changed = result.changed
                    detail = result.detail
                    derived = 0
                elif selected == "intelligence":
                    result = rebuild_intelligence(self.database)
                    changed = result.changed
                    detail = result.detail
                    derived = changed
                elif selected == "relationships":
                    stats = GraphStore(self.database).rebuild_from_database()
                    changed = stats.updated
                    detail = (
                        f"{stats.nodes_written} nodo(s), {stats.edges_written} relación(es)"
                    )
                    derived = changed
                elif selected == "clear-derived":
                    changed = clear_intelligence(self.database)
                    detail = f"{changed} análisis eliminado(s); índice intacto"
                    derived = changed
                else:  # full/all
                    roots = []
                    for root in self._roots():
                        _provider, _kind, available, capabilities = self._provider_for(root)
                        if available and ENUMERATE in capabilities:
                            roots.append(Path(root))
                    result = rebuild_all(
                        self.database,
                        roots,
                        confirm=True,
                    )
                    changed = result.changed
                    detail = result.detail
                    derived = 0
                return ActionResult(
                    action=f"rebuild-{selected}",
                    ok=True,
                    message=detail,
                    changed=changed,
                    indexed_records=changed if selected in {"fts", "full"} else 0,
                    derived_records=derived,
                    application_files=(
                        result.application_files if selected == "full" else 0
                    ),
                    data_scope=scope,
                    confirmed=confirm,
                    confirmation_provenance="caller" if confirm else None,
                    detail=detail,
                    code="ok",
                )
            except ConfirmationRequired as exc:
                return _failure(
                    f"rebuild-{selected}",
                    "confirmation-required",
                    str(exc),
                    requires_confirmation=True,
                    confirmation_provenance="required",
                    data_scope=scope,
                )
            except RepairBlocked as exc:
                return _failure(
                    f"rebuild-{selected}",
                    "blocked",
                    str(exc),
                    data_scope=scope,
                )
            except Exception as exc:
                return _failure(
                    f"rebuild-{selected}",
                    "error",
                    _bounded_error(f"{type(exc).__name__}: {exc}"),
                    data_scope=scope,
                )

        return self._run_locked(f"rebuild-{selected}", operation)


# -- optional Tk presentation ------------------------------------------------

try:  # Importing the service must remain safe in headless worker processes.
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # pragma: no cover - only a Python build without Tk
    tk = None  # type: ignore[assignment]
    filedialog = messagebox = ttk = None  # type: ignore[assignment]


if tk is not None:

    class ControlCenterWindow(tk.Toplevel):
        """Separate operational window; the search surface stays small."""

        def __init__(
            self,
            master: Any | None = None,
            *,
            service: ControlCenterService | None = None,
            paths: AppPaths | None = None,
            database_path: Path | str | None = None,
        ) -> None:
            super().__init__(master)
            self.service = service or ControlCenterService(
                paths=paths, database_path=database_path
            )
            self.closed = False
            self.snapshot: ControlSnapshot | None = None
            self._queue: queue.Queue[tuple[int, str, object, str | None]] = queue.Queue()
            self._generation = 0
            self._inflight = 0
            self._polling = False
            self._refresh_after_stale = False
            self._poll_id: str | None = None
            self.theme = self._resolve_theme()
            self.title("Centro de control de indexación")
            self.geometry("980x650")
            self.minsize(760, 480)
            self._build_ui()
            self.protocol("WM_DELETE_WINDOW", self.close)
            self.refresh()

        def _resolve_theme(self):
            from universal_search.gui import theme as theme_module

            return theme_module.resolve(getattr(self.service.config, "theme", "system"))

        def _build_ui(self) -> None:
            from universal_search.gui.theme import fonts

            self.configure(background=self.theme.background)
            style = ttk.Style(self)
            for name in ("vista", "winnative", "clam"):
                if name in style.theme_names():
                    style.theme_use(name)
                    break
            self.font_map = fonts(getattr(self.service.config, "ui_scale", 1.0))
            top = ttk.Frame(self, padding=(12, 12, 12, 6))
            top.pack(fill="x")
            ttk.Label(top, text="Indexación y fuentes", font=self.font_map["body"]).pack(
                side="left"
            )
            ttk.Button(top, text="Actualizar", command=self.refresh).pack(side="right")
            self.technical_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                top,
                text="Detalles técnicos",
                variable=self.technical_var,
                command=self._render_details,
            ).pack(side="right", padx=(0, 12))

            self.summary_var = tk.StringVar(value="Cargando estado…")
            ttk.Label(top, textvariable=self.summary_var).pack(anchor="w", pady=(8, 0))

            tree_frame = ttk.Frame(self, padding=(12, 0, 12, 0))
            tree_frame.pack(fill="both", expand=True)
            columns = ("state", "provider", "count", "types", "updated")
            self.sources_tree = ttk.Treeview(
                tree_frame,
                columns=columns,
                show="headings",
                selectmode="browse",
                height=9,
            )
            headings = {
                "state": "Estado",
                "provider": "Proveedor",
                "count": "Documentos",
                "types": "Tipos",
                "updated": "Última exploración",
            }
            widths = {"state": 130, "provider": 100, "count": 90, "types": 180, "updated": 190}
            for column in columns:
                self.sources_tree.heading(column, text=headings[column])
                self.sources_tree.column(column, width=widths[column], anchor="w")
            scrollbar = ttk.Scrollbar(
                tree_frame, orient="vertical", command=self.sources_tree.yview
            )
            self.sources_tree.configure(yscrollcommand=scrollbar.set)
            self.sources_tree.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            self.sources_tree.bind("<<TreeviewSelect>>", self._update_details)

            self.details_var = tk.StringVar(value="")
            ttk.Label(
                self,
                textvariable=self.details_var,
                foreground=self.theme.muted,
                padding=(12, 6),
                wraplength=900,
                justify="left",
            ).pack(fill="x")

            actions = ttk.Frame(self, padding=(12, 4, 12, 8))
            actions.pack(fill="x")
            ttk.Button(actions, text="Añadir carpeta…", command=self.add_source).pack(
                side="left"
            )
            ttk.Button(actions, text="Quitar fuente", command=self.remove_source).pack(
                side="left", padx=(6, 0)
            )
            ttk.Button(actions, text="Reexplorar", command=self.rescan).pack(
                side="left", padx=(6, 0)
            )
            ttk.Button(actions, text="Reintentar fallos", command=self.retry_failures).pack(
                side="left", padx=(6, 0)
            )
            ttk.Button(actions, text="Pausar", command=self.pause).pack(
                side="left", padx=(6, 0)
            )
            ttk.Button(actions, text="Reanudar", command=self.resume).pack(
                side="left", padx=(6, 0)
            )

            maintenance = ttk.Frame(self, padding=(12, 0, 12, 8))
            maintenance.pack(fill="x")
            ttk.Label(maintenance, text="Mantenimiento:").pack(side="left")
            ttk.Button(
                maintenance,
                text="Reconstruir FTS",
                command=lambda: self.rebuild("fts"),
            ).pack(side="left", padx=(6, 0))
            ttk.Button(
                maintenance,
                text="Reconstruir metadatos",
                command=lambda: self.rebuild("intelligence"),
            ).pack(side="left", padx=(6, 0))
            ttk.Button(
                maintenance,
                text="Reconstruir relaciones",
                command=lambda: self.rebuild("relationships"),
            ).pack(side="left", padx=(6, 0))
            ttk.Button(
                maintenance,
                text="Reconstruir todo",
                command=lambda: self.rebuild("all"),
            ).pack(side="left", padx=(6, 0))

            self.status_var = tk.StringVar(value="")
            ttk.Label(
                self,
                textvariable=self.status_var,
                foreground=self.theme.muted,
                padding=(12, 4, 12, 8),
            ).pack(fill="x")

        def _submit(self, kind: str, operation: Callable[[], object]) -> None:
            self._generation += 1
            generation = self._generation
            self._inflight += 1

            def work() -> None:
                try:
                    value = operation()
                except Exception as exc:
                    self._queue.put((generation, kind, None, str(exc)))
                else:
                    self._queue.put((generation, kind, value, None))

            threading.Thread(target=work, name=f"control-{kind}", daemon=True).start()
            if not self._polling:
                self._polling = True
                self._poll()

        def refresh(self) -> None:
            if not self.closed:
                self._submit("snapshot", self.service.snapshot)

        def _poll(self) -> None:
            if self.closed:
                self._polling = False
                return
            while True:
                try:
                    generation, kind, value, error = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._inflight = max(0, self._inflight - 1)
                if generation != self._generation:
                    if kind == "action":
                        self._refresh_after_stale = True
                    continue
                if error is not None:
                    self.status_var.set(f"No se pudo completar: {error}")
                    continue
                if kind == "snapshot" and isinstance(value, ControlSnapshot):
                    self.snapshot = value
                    self._render(value)
                elif isinstance(value, ActionResult):
                    self.status_var.set(value.message)
                    self.refresh()
            if self._refresh_after_stale and self._inflight == 0 and not self.closed:
                self._refresh_after_stale = False
                self.refresh()
            if not self.closed:
                self._poll_id = self.after(50, self._poll)
            else:
                self._polling = False

        def pump(self, timeout: float = 2.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.update()
                if self._inflight == 0 and self._queue.empty():
                    self.update()
                    return True
                time.sleep(0.005)
            return False

        def _render(self, snapshot: ControlSnapshot) -> None:
            self.summary_var.set(
                f"{snapshot.document_count} documento(s) · "
                f"{snapshot.health_status} · {_format_bytes(snapshot.storage.total_bytes)}"
            )
            selected = self.sources_tree.selection()
            selected_path = selected[0] if selected else None
            self.sources_tree.delete(*self.sources_tree.get_children())
            selected_id = None
            for source in snapshot.sources:
                item = self.sources_tree.insert(
                    "",
                    "end",
                    iid=source.path,
                    values=(
                        source.status_label,
                        source.provider,
                        source.document_count,
                        ", ".join(
                            f"{extension or '(none)'}: {count}"
                            for extension, count in source.supported_types[:5]
                        ),
                        source.last_scan_at or "Nunca",
                    ),
                )
                if source.path == selected_path:
                    selected_id = item
            if selected_id is not None:
                self.sources_tree.selection_set(selected_id)
            self._render_details()

        def _render_details(self, _event=None) -> None:
            snapshot = self.snapshot
            if snapshot is None:
                return
            source = self._selected_source()
            if source is None:
                self.details_var.set(
                    f"Almacenamiento: {_format_bytes(snapshot.storage.total_bytes)} · "
                    f"Derivados: {snapshot.derived.intelligence_rows} análisis, "
                    f"{snapshot.derived.graph_edges} relaciones"
                )
                return
            if self.technical_var.get():
                text = (
                    f"{source.path}\nProveedor: {source.provider} "
                    f"({', '.join(source.capabilities)})\n"
                    f"Fallos: {len(source.failures)} · "
                    f"Obsoletos: {source.stale_documents} · "
                    f" ignorados: {source.ignored}"
                )
            else:
                first_failure = source.failures[0].message if source.failures else ""
                suffix = f" Primero: {first_failure}." if first_failure else ""
                text = (
                    f"{source.document_count} documento(s), "
                    f"{len(source.failures)} fallo(s);{suffix} "
                    "Los archivos originales nunca se eliminan al quitar una fuente."
                )
            self.details_var.set(text)

        def _selected_source(self) -> SourceSnapshot | None:
            if self.snapshot is None:
                return None
            selection = self.sources_tree.selection()
            if not selection:
                return None
            return next(
                (source for source in self.snapshot.sources if source.path == selection[0]),
                None,
            )

        def _update_details(self, _event=None) -> None:
            self._render_details()

        def add_source(self) -> None:
            chosen = filedialog.askdirectory(title="Añadir carpeta a indexar")
            if chosen:
                self._submit("action", lambda: self.service.add_source(chosen))

        def remove_source(self) -> None:
            source = self._selected_source()
            if source is None:
                self.status_var.set("Selecciona una fuente")
                return
            delete_rows = messagebox.askyesno(
                "Quitar fuente",
                "¿También eliminar los registros indexados de esta carpeta?\n\n"
                "Los archivos originales no se eliminarán en ningún caso.",
            )
            if messagebox.askyesno(
                "Confirmar quitting",
                f"Quitar {source.path} de las fuentes?",
            ):
                self._submit(
                    "action",
                    lambda: self.service.remove_source(source.path, delete_rows),
                )

        def rescan(self) -> None:
            source = self._selected_source()
            target = source.path if source is not None else None
            self._submit("action", lambda: self.service.rescan(target))

        def retry_failures(self) -> None:
            self._submit("action", self.service.retry_failures)

        def pause(self) -> None:
            self._submit("action", self.service.pause)

        def resume(self) -> None:
            self._submit("action", self.service.resume)

        def rebuild(self, kind: str) -> None:
            labels = {
                "fts": "Se repararán filas de búsqueda; no se borran archivos.",
                "intelligence": "Se reconstruirán metadatos derivados; el índice no se toca.",
                "relationships": "Se reconstruirá el grafo local; no se borran archivos.",
                "all": "Se eliminará todo el índice y se reconstruirá. No se borran archivos.",
            }
            if not messagebox.askyesno(
                "Confirmar reconstrucción",
                f"{labels.get(kind, 'Se reconstruirá el índice.')}\n\n¿Continuar?",
            ):
                self.status_var.set("Reconstrucción cancelada")
                return
            self._submit("action", lambda: self.service.rebuild(kind, confirm=True))

        def close(self) -> None:
            self.closed = True
            self._polling = False
            if self._poll_id is not None:
                try:
                    self.after_cancel(self._poll_id)
                except Exception:
                    pass
            self.destroy()


else:  # pragma: no cover - exercised only on a Python build without Tk

    class ControlCenterWindow:  # type: ignore[no-redef]
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("Tkinter is not available")


def show_control_center(
    parent: Any | None = None,
    *,
    service: ControlCenterService | None = None,
    paths: AppPaths | None = None,
    database_path: Path | str | None = None,
) -> ControlCenterWindow:
    """Open or return a new control-center window for a parent."""
    return ControlCenterWindow(
        parent, service=service, paths=paths, database_path=database_path
    )


# -- helpers -----------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root_text(value: Path | str | None) -> str:
    if value is None:
        return ""
    try:
        expanded = os.path.expanduser(os.fspath(value))
        absolute = os.path.abspath(expanded)
    except (TypeError, ValueError, OSError):
        return ""
    return str(Path(absolute))


def _comparison_path(value: Path | str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(value))).rstrip("\\/")
    except (TypeError, ValueError, OSError):
        return str(value).casefold()


def _bounded_error(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return text[:MAX_ERROR_CHARS]


def _failure(
    action: str,
    code: str,
    message: str,
    *,
    errors: tuple[str, ...] = (),
    data_scope: str = "none",
    requires_confirmation: bool = False,
    confirmation_provenance: str | None = None,
) -> ActionResult:
    safe = _bounded_error(message)
    return ActionResult(
        action=action,
        ok=False,
        message=safe,
        code=code,
        errors=errors or (safe,),
        data_scope=data_scope,
        requires_confirmation=requires_confirmation,
        confirmation_provenance=confirmation_provenance,
    )


def _outermost_accessible_roots(
    roots: Iterable[str],
    blocked_roots: Iterable[str] = (),
) -> tuple[str, ...]:
    """Avoid scanning a configured child again under its configured parent."""
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_root in roots:
        display = _root_text(raw_root) or str(raw_root)
        key = _comparison_path(display)
        if key in seen:
            continue
        seen.add(key)
        unique.append((display, key))
    blocked = {
        _comparison_path(value) for value in blocked_roots
    }
    selected: list[str] = []
    for display, key in unique:
        nested_under_accessible_parent = False
        for other_display, other_key in unique:
            if other_key == key or not key.startswith(other_key + os.sep):
                continue
            # The longer path is the parent; keep the child when that parent
            # cannot currently be scanned so a disconnected mount remains
            # independently retryable.
            if _comparison_path(other_display) not in blocked and Path(other_display).is_dir():
                nested_under_accessible_parent = True
                break
        if not nested_under_accessible_parent:
            selected.append(display)
    return tuple(selected)


def _coerce_worker_status(value: object) -> BackgroundStatus:
    if isinstance(value, BackgroundStatus):
        return value
    if isinstance(value, dict):
        payload = value
        return BackgroundStatus(
            state=str(payload.get("state") or STATE_STOPPED),
            pid=payload.get("pid") if isinstance(payload.get("pid"), int) else None,
            generation=(
                str(payload.get("generation"))
                if payload.get("generation") is not None
                else None
            ),
            updated_at=(
                str(payload.get("updated_at"))
                if payload.get("updated_at") is not None
                else None
            ),
            last_scan_at=(
                str(payload.get("last_scan_at"))
                if payload.get("last_scan_at") is not None
                else None
            ),
            last_scan_stats=(
                dict(payload["last_scan_stats"])
                if isinstance(payload.get("last_scan_stats"), dict)
                else None
            ),
            roots=int(payload.get("roots") or 0),
            pending=str(payload.get("pending") or ""),
            error=(
                str(payload.get("error")) if payload.get("error") is not None else None
            ),
            stale_lock=bool(payload.get("stale_lock")),
            paused=bool(payload.get("paused")),
        )
    raise TypeError("background status is not a BackgroundStatus")


def _coerce_stats(value: object) -> IndexStats:
    if isinstance(value, IndexStats):
        return value
    result = IndexStats()
    for name in (
        "created",
        "updated",
        "unchanged",
        "deleted",
        "ignored",
        "errors",
        "extraction_errors",
        "cloud_only",
    ):
        try:
            setattr(result, name, max(0, int(getattr(value, name, 0) or 0)))
        except (TypeError, ValueError):
            continue
    return result


def _rebuild_kind(value: str) -> str | None:
    normalised = str(value or "").strip().casefold().replace("_", "-")
    return {
        "fts": "fts",
        "search": "fts",
        "intelligence": "intelligence",
        "derived": "intelligence",
        "metadata": "intelligence",
        "relationships": "relationships",
        "relationship": "relationships",
        "graph": "relationships",
        "full": "full",
        "all": "full",
        "index": "full",
        "clear-derived": "clear-derived",
        "clear": "clear-derived",
    }.get(normalised)


def _rebuild_scope(kind: str) -> tuple[str, bool]:
    if kind in {"fts", "full"}:
        return "indexed_records", True
    if kind in {"relationships", "clear-derived"}:
        return "derived_data", True
    return "derived_data", False


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def _current_exception() -> BaseException:
    return sys.exc_info()[1] or RuntimeError("unknown")


__all__ = [
    "ActionResult",
    "ControlCenterService",
    "ControlCenterWindow",
    "ControlSnapshot",
    "DerivedSnapshot",
    "SourceFailure",
    "SourceSnapshot",
    "StorageSnapshot",
    "show_control_center",
]
