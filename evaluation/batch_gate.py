"""Phase 035 evidence: batch operations that never overstate what they did.

Run from the repository root::

    python -m evaluation.batch_gate

A batch feature fails in a way no unit test can see if the status line says
"done" after opening three files out of two hundred. So the gates here measure
the report's honesty directly: a partial batch must be distinguishable from a
complete one, and the counts must be exact.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_search.gui.batch import (  # noqa: E402
    MAX_BATCH_OPERATIONS,
    BatchOperations,
)
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402


class FailingPlatform:
    """Opens everything except the paths it was told to fail on."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.opened: list[str] = []
        self.fail_on = fail_on or set()

    def open_path(self, target: str) -> None:
        if target in self.fail_on:
            raise FileNotFoundError(target)
        self.opened.append(target)

    def reveal(self, target: str) -> None:  # pragma: no cover - not measured
        self.open_path(target)


class ImportedAtRuntime:
    """Installs a platform only while the batch module asks for one."""

    def __init__(self, platform: FailingPlatform) -> None:
        self.platform = platform

    def __enter__(self) -> FailingPlatform:
        from universal_search import platforms

        self._previous = platforms.get_platform
        platforms.get_platform = lambda: self.platform
        return self.platform

    def __exit__(self, *_exc) -> None:
        from universal_search import platforms

        platforms.get_platform = self._previous


THRESHOLDS = {
    "T1_complete_batch_reports_complete": 1,
    "T2_partial_batch_detected": 1,
    "T3_batch_is_bounded": 1,
    "T4_one_failure_isolated": 1,
    "T5_counts_are_exact": 1,
    "T6_forget_requires_confirmation": 1,
    "T7_forget_removes_from_index": 1,
    "T8_no_display_dependency": 1,
    "T9_guessed_word_for_integration": 1,
}


@dataclass
class Verdict:
    gate: str
    measured: float
    threshold: float
    passed: bool
    detail: str

    def line(self) -> str:
        return (
            f"{'PASS' if self.passed else 'FAIL'}  {self.gate:<40} "
            f"{self.measured:>8.2f}  (umbral {self.threshold})  {self.detail}"
        )


def _hundreds(count: int) -> list[Path]:
    return [Path(f"doc{index}.md") for index in range(count)]


def main() -> int:
    # Windows consoles default to a legacy code page, and these gates print
    # the interface's own strings. Never crash while reporting (the CLI has
    # done this since phase 005; a gate that dies printing is worse than one
    # that reports a failure).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    findings: dict[str, object] = {}

    # -- T1: a complete batch reports itself complete ------------------------
    platform = FailingPlatform()
    with ImportedAtRuntime(platform):
        report = BatchOperations().open_all(_hundreds(5))
    t1 = report.ok and "5 de 5" in report.summary()
    findings["T1"] = {"summary": report.summary(), "ok": report.ok}

    # -- T2: a partial batch is distinguishable from a complete one ----------
    paths = _hundreds(20)
    platform = FailingPlatform(fail_on={str(path) for path in paths[:5]})
    with ImportedAtRuntime(platform):
        partial = BatchOperations().open_all(paths)
    t2 = (not partial.ok) and "15 de 20" in partial.summary()
    findings["T2"] = {"summary": partial.summary(), "ok": partial.ok}

    # -- T3: a batch is bounded and says it was ------------------------------
    platform = FailingPlatform()
    with ImportedAtRuntime(platform):
        bounded = BatchOperations().open_all(_hundreds(MAX_BATCH_OPERATIONS + 30))
    t3 = (
        len(platform.opened) == MAX_BATCH_OPERATIONS
        and bounded.skipped == 30
        and "30 sin procesar" in bounded.summary()
    )
    findings["T3"] = {
        "opened": len(platform.opened),
        "skipped": bounded.skipped,
        "summary": bounded.summary(),
    }

    # -- T4: one failure does not stop the batch -----------------------------
    platform = FailingPlatform(fail_on={"doc3.md"})
    with ImportedAtRuntime(platform):
        isolated = BatchOperations().open_all(_hundreds(10))
    t4 = (
        len(platform.opened) == 9
        and isolated.attempted == 10
        and len(isolated.failures) == 1
        and len(isolated.succeeded) == 9
    )
    findings["T4"] = {"opened": len(platform.opened), "summary": isolated.summary()}

    # -- T5: the counts add up exactly ---------------------------------------
    platform = FailingPlatform(fail_on={f"doc{i}.md" for i in range(4)})
    with ImportedAtRuntime(platform):
        counted = BatchOperations(max_batch=7).open_all(_hundreds(10))
    t5 = (
        counted.requested == 10
        and counted.attempted == 7
        and counted.skipped == 3
        and len(counted.succeeded) + len(counted.failures) == counted.attempted
    )
    findings["T5"] = {"summary": counted.summary()}

    # -- T6: forgetting without confirmation does nothing ---------------------
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-035-"))
    tree = workspace / "tree"
    tree.mkdir()
    for index in range(4):
        (tree / f"doc{index}.md").write_text("contenido", encoding="utf-8")
    database = SearchDatabase(workspace / "index.db")
    Indexer(database).index_root(tree)

    unconfirmed = BatchOperations(database=database).forget_all(
        _hundreds(4)
    )
    with database.connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE path LIKE '%doc%'"
        ).fetchone()[0]
    t6 = unconfirmed.attempted == 0 and remaining == 4
    findings["T6"] = {
        "summary": unconfirmed.summary(),
        "still_indexed": remaining,
    }

    # -- T7: with confirmation they really leave the index --------------------
    confirmed = BatchOperations(database=database).forget_all(
        [tree / "doc0.md", tree / "doc1.md"], confirm=True
    )
    with database.connect() as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE path LIKE '%doc%'"
        ).fetchone()[0]
        texts = connection.execute(
            "SELECT COUNT(*) FROM documents_fts WHERE content LIKE '%contenido%'"
        ).fetchone()[0]
    t7 = confirmed.ok and after == 2 and texts == 2 and (tree / "doc0.md").exists()
    findings["T7"] = {
        "summary": confirmed.summary(),
        "still_indexed": after,
        "files_untouched": (tree / "doc0.md").exists(),
    }

    # -- T8: the batch core imports no display library -----------------------
    source = (ROOT / "src" / "universal_search" / "gui" / "batch.py").read_text(
        encoding="utf-8"
    )
    # Imports, not words: the first version of this gate searched for the word
    # "clipboard" and failed on a docstring that says the module is free of it.
    imports = [
        match.group(1).split(".")[0]
        for match in re.finditer(
            r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.MULTILINE
        )
    ]
    forbidden = sorted(
        {name for name in imports if name in ("tkinter", "ttk", "clipboard")}
    )
    t8 = not forbidden
    findings["T8"] = {"imports": sorted(set(imports)), "forbidden": forbidden}

    # -- T9: no report may claim "listo", and an unconfirmed forget must say so
    reports = [findings[key]["summary"] for key in findings
               if isinstance(findings.get(key), dict) and "summary" in findings[key]]
    no_listo = not any("listo" in text.lower() for text in reports)
    unconfirmed_reason = (
        "que no se confirmo" in unconfirmed.summary()
        and "limite" not in unconfirmed.summary()
    )
    t9 = no_listo and unconfirmed_reason
    findings["T9"] = {
        "reports_checked": len(reports),
        "unconfirmed_summary": unconfirmed.summary(),
    }

    verdicts = [
        Verdict("T1 complete batch says complete", 1 if t1 else 0,
                THRESHOLDS["T1_complete_batch_reports_complete"], t1,
                str(findings["T1"]["summary"])),
        Verdict("T2 partial batch is distinguishable", 1 if t2 else 0,
                THRESHOLDS["T2_partial_batch_detected"], t2,
                str(findings["T2"]["summary"])),
        Verdict("T3 batch is bounded", 1 if t3 else 0,
                THRESHOLDS["T3_batch_is_bounded"], t3,
                f"{findings['T3']['opened']} abiertos, "
                f"{findings['T3']['skipped']} saltados"),
        Verdict("T4 one failure is isolated", 1 if t4 else 0,
                THRESHOLDS["T4_one_failure_isolated"], t4,
                str(findings["T4"]["summary"])),
        Verdict("T5 counts are exact", 1 if t5 else 0,
                THRESHOLDS["T5_counts_are_exact"], t5,
                str(findings["T5"]["summary"])),
        Verdict("T6 forget needs confirmation", 1 if t6 else 0,
                THRESHOLDS["T6_forget_requires_confirmation"], t6,
                f"sin confirmar quedan {findings['T6']['still_indexed']} indexados"),
        Verdict("T7 forget really removes", 1 if t7 else 0,
                THRESHOLDS["T7_forget_removes_from_index"], t7,
                f"quedan {findings['T7']['still_indexed']}, "
                f"ficheros intactos: {findings['T7']['files_untouched']}"),
        Verdict("T8 no display dependency in the core", 1 if t8 else 0,
                THRESHOLDS["T8_no_display_dependency"], t8,
                f"importadas: {findings['T8']['imports']}"),
        Verdict("T9 no false 'listo' or wrong reason", 1 if t9 else 0,
                THRESHOLDS["T9_guessed_word_for_integration"], t9,
                f"{findings['T9']['reports_checked']} informes; "
                f"sin confirmar dice: {findings['T9']['unconfirmed_summary']}"),
    ]

    print("=" * 104)
    print("PUERTA DE EVIDENCIA - FASE 035 (operaciones por lotes)")
    print("=" * 104)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 104)
    print("informes tal y como los veria el usuario:")
    for key in ("T1", "T2", "T3", "T6", "T7"):
        if "summary" in findings.get(key, {}):
            print(f"  [{key}] {findings[key]['summary']}")
    payload = {
        "phase": "035",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "findings": {key: findings[key] for key in sorted(findings)},
    }
    out = ROOT / "evaluation" / "batch_baseline.json"
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    print("VEREDICTO:", "SHIP" if not failed else f"NO SHIP ({len(failed)} puertas)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
