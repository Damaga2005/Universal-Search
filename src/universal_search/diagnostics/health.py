"""Health checks: can this index be trusted? (spec 015)

Each check answers one question, returns one verdict and one sentence a
person can act on. The distinction that matters:

* ``ok`` — healthy, or informational.
* ``warning`` — degraded but usable. A document without extracted text, a
  stale lock, a derived table that was never built: search still works and
  the user may want to fix it later.
* ``fatal`` — the index cannot be trusted: no database, no ``documents``
  table, a schema that is not the one this build expects, or rows that
  contradict each other in a way that would return wrong results.

Checks never read document content, never write, and never raise: a
diagnostic that crashes on a damaged database is useless precisely when it
is needed.
"""

import os
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from universal_search.appconfig import AppPaths
from universal_search.index.database import SCHEMA_OBJECTS, SCHEMA_VERSION, SearchDatabase

OK = "ok"
WARNING = "warning"
FATAL = "fatal"

# Relative order used by the report and the CLI exit code.
SEVERITY = {OK: 0, WARNING: 1, FATAL: 2}


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One verdict, with the sentence that explains it."""

    name: str
    status: str
    detail: str

    @property
    def fatal(self) -> bool:
        return self.status == FATAL

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class HealthReport:
    checks: tuple[HealthCheck, ...] = ()

    @property
    def status(self) -> str:
        """Worst verdict in the report."""
        return max(
            (check.status for check in self.checks),
            key=lambda status: SEVERITY[status],
            default=OK,
        )

    @property
    def ok(self) -> bool:
        return self.status == OK

    def by_name(self, name: str) -> HealthCheck | None:
        for check in self.checks:
            if check.name == name:
                return check
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [check.as_dict() for check in self.checks],
        }

    def render(self) -> str:
        lines = [f"index health: {self.status}"]
        for check in self.checks:
            lines.append(f"  [{check.status:<7}] {check.name}: {check.detail}")
        return "\n".join(lines)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - alive but not ours
        return True
    except OSError:  # pragma: no cover - platform without kill(0)
        return True
    return True


def _database_check(database: SearchDatabase) -> HealthCheck:
    # Checked before connecting: sqlite3.connect() would *create* a missing
    # database, and a diagnostic must never write to the index it inspects.
    if not Path(database.path).exists():
        return HealthCheck(
            "database", FATAL,
            "no index yet — run `universal-search index <root>`",
        )
    try:
        with closing(database.connect()) as connection:
            connection.execute("SELECT 1 FROM documents LIMIT 1").fetchall()
    except Exception as exc:
        return HealthCheck(
            "database", FATAL,
            f"cannot be opened or read ({type(exc).__name__}: {exc})",
        )
    return HealthCheck("database", OK, "readable and queryable")


def _schema_check(database: SearchDatabase) -> list[HealthCheck]:
    try:
        with closing(database.connect()) as connection:
            version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            present = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN"
                    " ('table','index')"
                )
            }
    except Exception as exc:
        return [
            HealthCheck(
                "schema", FATAL,
                f"cannot be inspected ({type(exc).__name__}: {exc})",
            )
        ]
    checks: list[HealthCheck] = []
    if version != SCHEMA_VERSION:
        checks.append(
            HealthCheck(
                "schema", FATAL if "documents" not in present else WARNING,
                f"version {version}, this build expects {SCHEMA_VERSION}"
                + ("" if "documents" not in present else " — search still works"),
            )
        )
    else:
        checks.append(HealthCheck("schema", OK, f"version {version}"))
    missing = [name for name in SCHEMA_OBJECTS if name not in present]
    if missing:
        checks.append(
            HealthCheck(
                "schema.objects", WARNING,
                f"missing {', '.join(missing)} — a rebuild creates them",
            )
        )
    else:
        checks.append(
            HealthCheck("schema.objects", OK, "every expected object exists")
        )
    return checks


def _fts_checks(database: SearchDatabase) -> list[HealthCheck]:
    with closing(database.connect()) as connection:
        orphan_metadata = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents AS d"
                " WHERE NOT EXISTS (SELECT 1 FROM documents_fts AS f"
                " WHERE f.document_id = d.id)"
            ).fetchone()[0]
        )
        orphan_fts = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents_fts AS f"
                " WHERE NOT EXISTS (SELECT 1 FROM documents AS d"
                " WHERE d.id = f.document_id)"
            ).fetchone()[0]
        )
        duplicates = int(
            connection.execute(
                "SELECT COUNT(*) FROM (SELECT path FROM documents"
                " GROUP BY path HAVING COUNT(*) > 1)"
            ).fetchone()[0]
        )
    checks: list[HealthCheck] = []
    if orphan_metadata:
        checks.append(
            HealthCheck(
                "fts.coverage", WARNING,
                f"{orphan_metadata} document(s) have no search text —"
                " `diagnose repair fts` re-reads them",
            )
        )
    else:
        checks.append(
            HealthCheck("fts.coverage", OK, "every document is searchable")
        )
    if orphan_fts:
        checks.append(
            HealthCheck(
                "fts.orphans", WARNING,
                f"{orphan_fts} search row(s) without a document —"
                " `diagnose repair fts` removes them",
            )
        )
    else:
        checks.append(HealthCheck("fts.orphans", OK, "no orphaned search rows"))
    checks.append(
        HealthCheck(
            "documents.identities",
            WARNING if duplicates else OK,
            f"{duplicates} duplicate path(s)" if duplicates
            else "no duplicate identities",
        )
    )
    return checks


def _path_checks(database: SearchDatabase, sample_limit: int) -> list[HealthCheck]:
    with closing(database.connect()) as connection:
        rows = connection.execute(
            "SELECT path FROM documents ORDER BY path LIMIT ?",
            (max(sample_limit, 1),),
        ).fetchall()
        invalid = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE path = ''"
            ).fetchone()[0]
        )
    # Bounded probe: checking existence of every path in a 100k index is a
    # filesystem walk, not a diagnostic. A sample is enough to notice a
    # moved or unmounted drive, and the reconcile pass does the real work.
    missing = [str(row["path"]) for row in rows if not os.path.exists(row["path"])]
    checks: list[HealthCheck] = []
    if invalid:
        checks.append(
            HealthCheck(
                "paths.invalid", WARNING, f"{invalid} document(s) with an empty path"
            )
        )
    if missing:
        sample = ", ".join(missing[:3])
        checks.append(
            HealthCheck(
                "paths.stale", WARNING,
                f"{len(missing)} of {len(rows)} sampled path(s) no longer exist"
                f" (e.g. {sample}) — `universal-search index <root>` reconciles",
            )
        )
    elif rows:
        checks.append(
            HealthCheck("paths.stale", OK, "sampled paths exist on disk")
        )
    return checks


def _extraction_check(database: SearchDatabase) -> list[HealthCheck]:
    with closing(database.connect()) as connection:
        without_text = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents_fts WHERE content = ''"
            ).fetchone()[0]
        )
        stale_content = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents AS d JOIN documents_fts AS f"
                " ON f.document_id = d.id WHERE d.content_hash IS NULL"
                " AND f.content <> ''"
            ).fetchone()[0]
        )
    checks = [
        HealthCheck(
            "extraction", WARNING if without_text else OK,
            f"{without_text} document(s) have no extracted text"
            " (binary, cloud-only or failed extraction)"
            if without_text
            else "every document has extracted text",
        )
    ]
    if stale_content:
        checks.append(
            HealthCheck(
                "extraction.hash", WARNING,
                f"{stale_content} document(s) have text without a content hash",
            )
        )
    return checks


def _derived_check(database: SearchDatabase) -> list[HealthCheck]:
    with closing(database.connect()) as connection:
        analysed = int(
            connection.execute(
                "SELECT COUNT(*) FROM document_intelligence"
            ).fetchone()[0]
        )
        outdated = int(
            connection.execute(
                "SELECT COUNT(*) FROM document_intelligence WHERE version <> ?",
                (_intelligence_version(),),
            ).fetchone()[0]
        )
    if outdated:
        return [
            HealthCheck(
                "intelligence", WARNING,
                f"{outdated} derived row(s) from an older analysis version —"
                " `diagnose repair intelligence` rebuilds them",
            )
        ]
    return [
        HealthCheck(
            "intelligence", OK,
            f"{analysed} analysed document(s)" if analysed
            else "not built (optional: `diagnose repair intelligence`)",
        )
    ]


def _intelligence_version() -> int:
    from universal_search.intelligence import INTELLIGENCE_VERSION

    return INTELLIGENCE_VERSION


def _worker_checks(paths: AppPaths | None) -> list[HealthCheck]:
    if paths is None:
        return []
    from universal_search import background

    status = background.read_status(paths)
    lock_pid = background.read_lock_pid(paths)
    checks: list[HealthCheck] = []
    if lock_pid and _pid_alive(lock_pid):
        checks.append(
            HealthCheck("worker.lock", OK, f"indexer running (pid {lock_pid})")
        )
    elif lock_pid:
        checks.append(
            HealthCheck(
                "worker.lock", WARNING,
                f"stale lock from pid {lock_pid} — the process is gone",
            )
        )
    else:
        checks.append(
            HealthCheck("worker.lock", OK, "no indexer running")
        )
    if status is None:
        checks.append(
            HealthCheck("worker.state", OK, "no worker status recorded")
        )
    elif status.get("state") == "error":
        checks.append(
            HealthCheck(
                "worker.state", WARNING,
                f"last pass failed: {status.get('error') or 'unknown error'}",
            )
        )
    else:
        checks.append(
            HealthCheck(
                "worker.state", OK,
                f"{status.get('state')} (updated {status.get('updated_at')})",
            )
        )
    return checks


def check(
    database: SearchDatabase,
    paths: AppPaths | None = None,
    *,
    sample_limit: int = 200,
) -> HealthReport:
    """Run every health check and return the worst verdict.

    ``sample_limit`` bounds how many indexed paths are probed on disk.
    """
    checks: list[HealthCheck] = [_database_check(database)]
    if not checks[0].fatal:
        checks.extend(_schema_check(database))
        checks.extend(_fts_checks(database))
        checks.extend(_path_checks(database, sample_limit))
        checks.extend(_extraction_check(database))
        checks.extend(_derived_check(database))
    checks.extend(_worker_checks(paths))
    return HealthReport(checks=tuple(checks))
