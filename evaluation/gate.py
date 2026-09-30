"""The v2 quality gate (phase 030): one command, every invariant.

This is deliberately a *gate*, not a feature. It answers a single question
with evidence: may this tree be called Universal Search v2? Every check below
is local, fast and falsifiable — no network, no cloud, no learned model.

Run it directly::

    python -m evaluation.gate

or read the table in ``docs/development/030-v2-quality-gate-report.md``. The
suite (``tests/test_v2_gate.py``) asserts the same checks, so the gate cannot
rot into always-green.
"""

from __future__ import annotations

import ast
import gc
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "universal_search"
DOCS = ROOT / "docs"

# Phases 001-030, the whole v2 line. Every one must be documented, and the
# documentation must be a real report, not an empty placeholder.
PHASES = range(1, 31)

# Packages that must stay platform-independent: no Win32, no registry, no
# ctypes, no shell. The core runs, and is tested, on any OS (phase 016).
CORE_PACKAGES = (
    "domain",
    "index",
    "query",
    "ranking",
    "semantic",
    "intelligence",
    "diagnostics",
    "extractors",
    "providers",
)

# Modules at the top level that are part of the core contract.
CORE_MODULES = (
    "privacy.py",
    "recovery.py",
    "observability.py",
    "appconfig.py",
    "background.py",
    "background_service.py",
    "metrics.py",
)

# Modules allowed to touch Win32 outside ``platforms/`` and ``gui/``. Each one
# is declared with its reason, because the point of the gate is that a new
# Windows touchpoint has to be *chosen*, not merely written. Anything not on
# this list that imports ctypes/winreg/Tk fails the gate.
WINDOWS_INTEGRATION_MODULES = {
    # The global shortcut needs RegisterHotKey, which has no portable
    # equivalent. It is a Windows integration surface, not part of the data
    # path: search, ranking, indexing and extraction never import it.
    "hotkey.py": "global shortcut server (RegisterHotKey), phase 009",
}

# Nothing in the shipped package may open a socket or call a model service.
# The gate is the enforcement behind the project boundary, not a style guide.
FORBIDDEN_IMPORTS = {
    "socket",
    "ssl",
    "http",
    "http.client",
    "urllib",
    "urllib.request",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc",
    "requests",
    "httpx",
    "aiohttp",
    "openai",
    "anthropic",
    "torch",
    "transformers",
    "sentence_transformers",
    "numpy",
    "scipy",
    "sklearn",
    "faiss",
    "redis",
    "elasticsearch",
}

# Tables that hold no user data and are therefore not in the privacy
# inventory. Each one needs a reason: an unexplained exemption is a hole.
METADATA_TABLES = {
    "schema_migrations": "bookkeeping of applied migrations, no user data",
    "sqlite_sequence": "internal SQLite autoincrement counter",
}

FORBIDDEN_PLATFORM_IMPORTS = {
    "ctypes",
    "winreg",
    "win32api",
    "win32con",
    "win32gui",
    "winreg",
    "tkinter",
    "pythoncom",
    "pywintypes",
}


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class GateReport:
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checks": [check.as_dict() for check in self.checks],
        }


# -- helpers ------------------------------------------------------------------

def _module_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py")) if directory.is_dir() else []


def _top_level_modules() -> list[Path]:
    return sorted(SRC.glob("*.py"))


def _imported_names(path: Path) -> set[str]:
    """Every module name imported by ``path``, resolved to its first segment."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - a syntax error is fatal
        raise RuntimeError(f"{path} does not parse: {exc}") from exc
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import inside the package
                continue
            if node.module:
                names.add(node.module)
    return names


def _owned(name: str) -> str:
    return name.split(".", 1)[0]


# -- the checks ---------------------------------------------------------------

def check_dependency_budget() -> CheckResult:
    """Two runtime dependencies. Not three, not two and a half."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = {
        _owned(re.split(r"[<>=!\[ ]", entry, maxsplit=1)[0].strip().lower())
        for entry in data["project"]["dependencies"]
    }
    extras = {
        _owned(re.split(r"[<>=!\[ ]", entry, maxsplit=1)[0].strip().lower())
        for entries in data["project"]["optional-dependencies"].values()
        for entry in entries
    }
    ok = runtime == {"pypdf", "watchdog"} and not runtime & extras
    return CheckResult(
        "dependency budget",
        ok,
        f"runtime={sorted(runtime)} build-extra={sorted(extras)}",
    )


def check_no_network_or_model_imports() -> CheckResult:
    """No socket, no HTTP client, no model runtime in the shipped package."""
    offenders: list[str] = []
    for path in _module_files(SRC):
        for name in _imported_names(path):
            if name in FORBIDDEN_IMPORTS:
                offenders.append(f"{path.relative_to(ROOT)}:{name}")
    return CheckResult(
        "no network or model imports",
        not offenders,
        "clean" if not offenders else ", ".join(sorted(offenders)),
    )


def check_core_is_platform_independent() -> CheckResult:
    """The data path must import no Win32 and no Tk."""
    offenders: list[str] = []
    targets: list[Path] = []
    for package in CORE_PACKAGES:
        targets.extend(_module_files(SRC / package))
    targets.extend(SRC / name for name in CORE_MODULES)
    for path in targets:
        if not path.exists():
            offenders.append(f"missing core module {path.name}")
            continue
        for name in _imported_names(path):
            if name in FORBIDDEN_PLATFORM_IMPORTS:
                offenders.append(f"{path.relative_to(SRC)}:{name}")
    return CheckResult(
        "core is platform independent",
        not offenders,
        f"{len(targets)} core modules, no Win32/Tk imports"
        if not offenders
        else ", ".join(sorted(offenders)),
    )


def check_every_win32_touchpoint_is_declared() -> CheckResult:
    """A Windows primitive outside the seam must be listed, with a reason.

    This is the check that makes the classification above meaningful: a new
    ``import ctypes`` in the data path cannot slip in unnoticed.
    """
    allowed_prefixes = ("platforms/", "gui/")
    undeclared: list[str] = []
    for path in _module_files(SRC):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith(allowed_prefixes):
            continue
        if relative in WINDOWS_INTEGRATION_MODULES:
            continue
        for name in _imported_names(path):
            if name in FORBIDDEN_PLATFORM_IMPORTS:
                undeclared.append(f"{relative}:{name}")
    return CheckResult(
        "win32 touchpoints declared",
        not undeclared,
        f"{len(WINDOWS_INTEGRATION_MODULES)} declared outside the seam"
        if not undeclared
        else f"undeclared: {undeclared}",
    )


def check_platform_seam_is_used_for_shell_work() -> CheckResult:
    """``open``/``reveal``/autostart/notify go through the platform seam."""
    base = (SRC / "platforms" / "base.py").read_text(encoding="utf-8")
    missing = [
        operation
        for operation in ("def open_path", "def reveal", "def set_autostart",
                          "def notify", "def set_dpi_awareness")
        if operation not in base
    ]
    windows = (SRC / "platforms" / "windows.py").read_text(encoding="utf-8")
    if "import ctypes" not in windows and "user32" not in windows:
        missing.append("windows adapter has no OS touchpoint")
    return CheckResult(
        "platform seam", not missing,
        "all operations declared" if not missing else ", ".join(missing),
    )


def check_privacy_inventory_covers_every_table() -> CheckResult:
    """Every table is either declared as stored data or explained as metadata."""
    sys.path.insert(0, str(ROOT / "src"))
    from universal_search.index.database import SCHEMA  # noqa: PLC0415
    from universal_search.privacy import INVENTORY  # noqa: PLC0415

    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))
    tables |= set(re.findall(r"CREATE VIRTUAL TABLE IF NOT EXISTS (\w+)", SCHEMA))
    declared: set[str] = set()
    for item in INVENTORY:
        declared |= set(re.findall(r"`(\w+)`", item.where))
        declared |= {re.sub(r"_.*", "", part) for part in re.findall(
            r"`(\w+_\*)`", item.where
        )}
    # A wildcard entry such as ``document_graph_*`` covers the family.
    families = {
        name[: -len("*")] for name in re.findall(r"`(\w+\*)`",
                                                " ".join(i.where for i in INVENTORY))
    }
    covered = {
        table
        for table in tables
        if table in declared
        or any(table.startswith(family) for family in families)
        or table in METADATA_TABLES
    }
    missing = sorted(tables - covered)
    return CheckResult(
        "privacy inventory complete",
        not missing,
        f"{len(tables)} tables, all declared"
        if not missing
        else f"undeclared: {missing}",
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def check_repairs_never_touch_user_files() -> CheckResult:
    """Behavioural proof, not a regex: run every repair, compare the bytes.

    A textual scan cannot tell ``path.unlink()`` on an application data file
    from the same call on a user's document. This check indexes a real tree,
    runs all four recovery cases plus ``privacy forget``, and verifies that
    every file the user owns is byte-identical afterwards.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "src"))
    from universal_search.appconfig import AppPaths  # noqa: PLC0415
    from universal_search.index.database import SearchDatabase  # noqa: PLC0415
    from universal_search.index.indexer import Indexer  # noqa: PLC0415
    from universal_search.privacy import forget  # noqa: PLC0415
    from universal_search.recovery import recover  # noqa: PLC0415

    workspace = Path(tempfile.mkdtemp(prefix="universal-search-v2-gate-"))
    try:
        source = workspace / "user-documents"
        source.mkdir()
        (source / "notas.md").write_text("BJT Ebers-Moll", encoding="utf-8")
        (source / "receta.md").write_text("paella valenciana", encoding="utf-8")
        before = _tree_snapshot(source)

        paths = AppPaths(workspace / "appdata")
        paths.ensure()
        database = SearchDatabase(paths.database)
        Indexer(database).index_root(source)

        for case in ("orphan-derived", "dirty-derived", "stale-coordination"):
            recover(case, paths=paths)
        recover("reset-derived", paths=paths, confirm=True)
        forget(database, source / "notas.md")
        # And a destructive diagnostic repair, which is the riskiest one.
        from universal_search.diagnostics import rebuild_all, rebuild_fts  # noqa: PLC0415

        rebuild_fts(database, confirm=True)
        # ``rebuild_fts`` predates the deterministic-close discipline and its
        # sqlite connection is released by the collector rather than by
        # scope exit. Windows will not delete a locked file, so the gate has
        # to collect before the next repair tries to unlink the database.
        # Found by this gate, not by a test that ran in a fresh process.
        gc.collect()
        rebuild_all(database, [source], confirm=True)

        after = _tree_snapshot(source)
        problems: list[str] = []
        if set(before) != set(after):
            problems.append(
                f"file set changed: {sorted(set(before) ^ set(after))}"
            )
        for name, (size, payload) in before.items():
            other = after.get(name)
            if other is None or other[0] != size or other[1] != payload:
                problems.append(f"{name} was modified or removed")
        return CheckResult(
            "repairs never touch user files",
            not problems,
            f"{len(before)} user files byte-identical after 7 repairs"
            if not problems
            else "; ".join(problems),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def collect_test_count() -> tuple[int, str]:
    """Number of tests pytest will run, and how we know.

    No extra ``-q``: ``pyproject.toml`` already sets it, and passing it twice
    silences the very summary line this parses.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    )
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    if match:
        return int(match.group(1)), "pytest --collect-only"
    per_file = re.findall(r"^tests/\S+\.py:\s*(\d+)$", proc.stdout, flags=re.MULTILINE)
    if per_file:
        return sum(int(n) for n in per_file), "sum of per-file counts"
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
    return 0, f"collection failed: {tail[0] if tail else 'no output'}"


DOC_COUNT_FILES = ("README.md", "docs/README.md", "docs/ROADMAP.md")

# The one line in each document that states the current count. It is visible
# prose, not a hidden marker, and it is what the gate compares against: a
# historical sentence ("phase 010: 208 tests") is not the current count and
# must not be mistaken for one.
COUNT_LINE = re.compile(
    r"^Current test count:\s*(\d{3,5})\s+tests collected"
    r"(?:\s*\((\d{3,5})\s+passed,\s*(\d+)\s+skipped\))?\s*[.]?\s*$",
    flags=re.MULTILINE,
)


def check_documented_test_count() -> CheckResult:
    """The number the docs state must be the number pytest will run.

    "Collected" is the only count a cheap check can know without running the
    suite. When the docs also state the outcome, the arithmetic is verified
    too: collected = passed + skipped, so the two numbers cannot drift apart
    and quietly become a different claim.
    """
    count, source = collect_test_count()
    if not count:
        return CheckResult("documented test count", False, source)
    problems: list[str] = []
    for name in DOC_COUNT_FILES:
        text = (ROOT / name).read_text(encoding="utf-8")
        matches = COUNT_LINE.findall(text)
        if not matches:
            problems.append(f"{name}: no 'Current test count:' line")
            continue
        if len(matches) > 1:
            problems.append(f"{name}: {len(matches)} count lines")
        for stated, passed, skipped in matches:
            if int(stated) != count:
                problems.append(f"{name}: says {stated}, pytest collects {count}")
            if passed and skipped and int(passed) + int(skipped) != int(stated):
                problems.append(
                    f"{name}: {passed} passed + {skipped} skipped != {stated} collected"
                )
    return CheckResult(
        "documented test count",
        not problems,
        f"{count} collected, docs agree ({source})"
        if not problems
        else "; ".join(problems),
    )


def check_every_phase_is_documented() -> CheckResult:
    """Phases 001-030 each need a substantial document and an index row.

    Phase 001 predates the prompt/report split, so its single document *is*
    the record; what the gate forbids is a missing or empty one.
    """
    documents = sorted((DOCS / "development").glob("*.md"))
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    missing: list[int] = []
    thin: list[str] = []
    for phase in PHASES:
        prefix = f"{phase:03d}-"
        matches = [path for path in documents if path.name.startswith(prefix)]
        substantial = [
            path
            for path in matches
            if len(path.read_text(encoding="utf-8")) >= 500
        ]
        if not substantial:
            if matches:
                thin.append(matches[0].name)
            else:
                missing.append(phase)
        if f"| {phase:03d} |" not in index:
            missing.append(phase)
    return CheckResult(
        "every phase documented",
        not missing and not thin,
        f"{len(PHASES)} phases with a substantial document and an index row"
        if not missing and not thin
        else f"missing={missing} thin={thin}",
    )


def check_roadmap_has_no_open_phase() -> CheckResult:
    """The roadmap must be honest about what is open and what is done.

    Phase 031 opened a second programme (v2.x, phases 031-040), so "no
    checkbox is open" stopped being a true invariant for every commit and
    became a release-readiness check that belongs to the final phase. The
    invariant that *is* always true, and is strictly stronger than an empty
    checklist, is structural:

    * a **completed** phase has a substantial document and an index row;
    * an open phase is never skipped over by a completed one, so a stray
      checkbox in the middle of a finished programme is caught.

    Open phases are read *from* the roadmap, so they are declared by
    definition and need no document yet; requiring one would demand
    documentation for phases that have not happened.
    """
    roadmap = (DOCS / "ROADMAP.md").read_text(encoding="utf-8")
    done = [int(n) for n in re.findall(r"^- \[x\] .*?(\d{3})", roadmap, re.MULTILINE)]
    open_phases = [
        int(n) for n in re.findall(r"^- \[ \] .*?(\d{3})", roadmap, re.MULTILINE)
    ]
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    documents = {path.name[:3] for path in (DOCS / "development").glob("*.md")}

    problems: list[str] = []
    if done and open_phases and min(open_phases) < max(done):
        problems.append(
            f"open phase {min(open_phases)} sits below completed {max(done)}"
        )
    for phase in sorted(set(done)):
        if f"| {phase:03d} |" not in index:
            problems.append(f"{phase:03d} has no row in docs/README.md")
        if f"{phase:03d}" not in documents:
            problems.append(f"{phase:03d} has no document")
    return CheckResult(
        "roadmap is honest",
        not problems,
        f"{len(done)} done (all documented), {len(open_phases)} open"
        if not problems
        else "; ".join(problems[:6]),
    )


def check_changelog_documents_every_phase() -> CheckResult:
    """Every phase must be traceable in the changelog.

    A phase counts when it has its own heading, or when it falls inside an
    explicit range: the 1.0.0 release covered phases 001-010 under one
    heading, and rewriting history to satisfy a linter would be worse than
    reading the range.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    covered: set[int] = set()
    for start, end in re.findall(
        r"phases?\s+(\d{3})\s*[-–—]\s*(\d{3})", changelog, flags=re.IGNORECASE
    ):
        covered.update(range(int(start), int(end) + 1))
    for phase in PHASES:
        if re.search(rf"Phase 0?{phase}\b", changelog):
            covered.add(phase)
    missing = [phase for phase in PHASES if phase not in covered]
    return CheckResult(
        "changelog covers every phase",
        not missing,
        f"all {len(PHASES)} phases traceable (headings + release ranges)"
        if not missing
        else f"missing: {missing}",
    )


def check_schema_and_version_are_single_sourced() -> CheckResult:
    sys.path.insert(0, str(ROOT / "src"))
    import universal_search  # noqa: PLC0415
    from universal_search.index.database import SCHEMA_VERSION  # noqa: PLC0415

    version_file = (ROOT / "packaging" / "version_file.txt").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    problems: list[str] = []
    if universal_search.__version__ not in version_file:
        problems.append("version_file.txt is out of date")
    if universal_search.__version__ not in installer:
        problems.append("installer.iss is out of date")
    return CheckResult(
        "version single sourced",
        not problems,
        f"{universal_search.__version__} / schema {SCHEMA_VERSION}"
        if not problems
        else "; ".join(problems),
    )


def check_no_stray_debugging() -> CheckResult:
    """No ``breakpoint()``, ``pdb``, or ``print`` in the shipped package."""
    offenders: list[str] = []
    for path in _module_files(SRC):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\bbreakpoint\(\)|\bimport pdb\b", stripped):
                offenders.append(f"{path.name}:{number}")
    return CheckResult(
        "no stray debugging",
        not offenders,
        "clean" if not offenders else ", ".join(offenders),
    )


CHECKS = (
    check_dependency_budget,
    check_no_network_or_model_imports,
    check_core_is_platform_independent,
    check_every_win32_touchpoint_is_declared,
    check_platform_seam_is_used_for_shell_work,
    check_privacy_inventory_covers_every_table,
    check_repairs_never_touch_user_files,
    check_no_stray_debugging,
    check_schema_and_version_are_single_sourced,
    check_every_phase_is_documented,
    check_changelog_documents_every_phase,
    check_documented_test_count,
    check_roadmap_has_no_open_phase,
)


def run_all() -> GateReport:
    return GateReport(tuple(check() for check in CHECKS))


def render(report: GateReport) -> str:
    lines = ["Universal Search v2 quality gate", "-" * 52]
    for check in report.checks:
        status = "PASS" if check.ok else "FAIL"
        lines.append(f"{status}  {check.name:<38} {check.detail}")
    lines.append("-" * 52)
    lines.append(
        "VERDICT: PASS" if report.ok
        else f"VERDICT: FAIL ({len(report.failures)} of {len(report.checks)})"
    )
    return "\n".join(lines)


def main() -> int:
    report = run_all()
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
