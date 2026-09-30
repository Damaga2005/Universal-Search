"""Structured local observability and sanitized support bundles (phase 028).

Everything here is local, bounded and content-free. Events are JSON lines with
an explicit schema; fields whose name suggests credentials or content are
redacted before they reach the sink. The support bundle declares exactly what
it contains instead of asking the user to trust a zip file.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from universal_search import __version__
from universal_search.appconfig import AppPaths

REDACTED = "[redacted]"
MAX_FIELD_CHARS = 300
DEFAULT_EVENT_BYTES = 1_000_000
DEFAULT_EVENT_BACKUPS = 3
_SENSITIVE_KEY_PARTS = (
    "password", "token", "secret", "credential", "content", "query",
)


def _sanitize_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return REDACTED
    if isinstance(value, str):
        return value[:MAX_FIELD_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_FIELD_CHARS]


class EventRecorder:
    """Bounded JSONL event sink with one-field redaction."""

    def __init__(
        self,
        path: Path | str,
        *,
        max_bytes: int = DEFAULT_EVENT_BYTES,
        backups: int = DEFAULT_EVENT_BACKUPS,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max(1, int(max_bytes))
        self.backups = max(0, int(backups))

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            size = 0
        if size and size + incoming > self.max_bytes:
            if self.backups == 0:
                self.path.unlink(missing_ok=True)
            else:
                backup = self.path.with_suffix(self.path.suffix + ".1")
                backup.unlink(missing_ok=True)
                os.replace(self.path, backup)

    def emit(
        self,
        component: str,
        event_id: str,
        severity: str = "info",
        **fields: Any,
    ) -> dict[str, Any]:
        record = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "component": str(component)[:80],
            "event_id": str(event_id)[:120],
            "severity": str(severity)[:16],
            **{key: _sanitize_value(key, value) for key, value in fields.items()},
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed(len(encoded))
        with self.path.open("ab") as handle:
            handle.write(encoded)
        return record


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SelfTestReport:
    checks: tuple[CheckResult, ...]
    storage: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "checks": {check.name: check.as_dict() for check in self.checks},
            "storage": dict(self.storage),
        }

    @property
    def status(self) -> str:
        if any(check.status == "fatal" for check in self.checks):
            return "fatal"
        if any(check.status == "warning" for check in self.checks):
            return "warning"
        return "ok"


def _check(name: str, operation) -> CheckResult:
    try:
        detail = str(operation())
        return CheckResult(name, "ok", detail[:MAX_FIELD_CHARS])
    except Exception as exc:
        return CheckResult(name, "fatal", f"{type(exc).__name__}: {exc}"[:MAX_FIELD_CHARS])


def self_test(paths: AppPaths | None = None) -> SelfTestReport:
    """Exercise every local subsystem the support bundle reports on."""
    paths = paths or AppPaths.discover()
    paths.ensure()
    from universal_search import extractors
    from universal_search.background_service import BackgroundService
    from universal_search.index.database import SCHEMA_VERSION, SearchDatabase
    from universal_search.providers.registry import register_builtins

    database = SearchDatabase(paths.database)
    checks: list[CheckResult] = []

    def database_check() -> str:
        with database.connect() as connection:
            value = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
        return f"readable, documents={value}"

    def fts_check() -> str:
        with database.connect() as connection:
            value = connection.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
        return f"readable, rows={value}"

    def schema_check() -> str:
        with database.connect() as connection:
            value = connection.execute("PRAGMA user_version").fetchone()[0]
        return f"version={value}, app={SCHEMA_VERSION}"

    def providers_check() -> str:
        infos = register_builtins().infos()
        available = sum(1 for info in infos if info.available)
        return f"{available}/{len(infos)} available"

    def extractors_check() -> str:
        infos = extractors.infos()
        return f"{len(infos)} registered"

    def worker_check() -> str:
        status = BackgroundService(paths).status()
        return f"state={status.state}"

    checks.extend([
        _check("database", database_check),
        _check("fts", fts_check),
        _check("schema", schema_check),
        _check("providers", providers_check),
        _check("extractors", extractors_check),
        _check("worker", worker_check),
    ])
    usage = shutil.disk_usage(paths.home)
    storage = {"free_bytes": int(usage.free), "total_bytes": int(usage.total)}
    status = "ok" if storage["free_bytes"] > 0 else "fatal"
    checks.append(CheckResult("storage", status, f"free={storage['free_bytes']}"))
    return SelfTestReport(tuple(checks), storage)


@dataclass(frozen=True, slots=True)
class SupportBundleResult:
    path: Path
    checks: int


def support_bundle(paths: AppPaths | None, destination: Path | str) -> SupportBundleResult:
    """Write a bounded, explicitly sanitized JSON support bundle."""
    paths = paths or AppPaths.discover()
    destination = Path(destination)
    report = self_test(paths)
    payload = {
        "format": "universal-search-support-bundle",
        "format_version": 1,
        "application_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "declaration": {
            "contains_document_content": False,
            "contains_query_text": False,
            "contains_credentials": False,
            "note": "metadata and bounded diagnostics only",
        },
        "self_test": report.as_dict()["checks"],
        "storage": report.storage,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return SupportBundleResult(destination, len(report.checks))


__all__ = [
    "CheckResult",
    "EventRecorder",
    "SelfTestReport",
    "SupportBundleResult",
    "self_test",
    "support_bundle",
]