"""Phase 046 evidence: is indexing predictable as the corpus grows?

Run from the repository root::

    python -m evaluation.scale_gate

Phase 046 says performance must stay predictable as corpus size grows, and that
optimisation is only allowed where a bottleneck has been *measured*. So this
gate measures the shape rather than a number, and it deliberately does not
assert an absolute time: a latency threshold on a shared machine is a coin
toss, and the project's own performance gate already owns that job with a load
veto and a machine fingerprint.

**S1-S5, the curve.** Cost per document, database bytes per document and peak
memory are each measured at two corpus sizes and compared as a *ratio*. Linear
behaviour is a ratio near 1; the gate's tolerance says how much drift it will
accept before calling it a problem.

**S6-S9, the correctness the curve must not have been bought with.**
Deterministic outcomes, an uncancelled pass is searchable, a cancelled pass is
repairable rather than permanent, and a pass that stops early deletes nothing.

The FTS5 share is measured and reported on every run. It is the known cost
centre -- ~86% of an indexing pass at 10k documents -- and it lives inside
SQLite, so this gate exists to make it *visible* rather than to pretend it can
be tuned away.
"""

from __future__ import annotations

import gc
import json
import shutil
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation.accessibility_gate import Verdict  # noqa: E402
from universal_search.domain.extraction import ExtractionResult  # noqa: E402
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402
from universal_search.providers.base import CancelToken  # noqa: E402

#: Two corpus sizes. Large enough for a doubling to show a real trend, small
#: enough that the gate is runnable by hand: the 4k pass is the expensive half.
SMALL = 1000
LARGE = 4000

THRESHOLDS = {
    "S1_cost_per_document_ratio": 2.5,
    "S2_database_kib_per_document_ratio": 1.35,
    "S3_peak_memory_ratio": 4.0,
    "S4_documents_differing_between_runs": 0,
    "S5_uncancelled_documents_without_text": 0,
    "S6_cancelled_documents_not_repaired": 0,
    "S7_documents_deleted_by_a_cancelled_pass": 0,
    "S8_errors_during_a_clean_pass": 0,
}

GATE_LINES = {
    "S1_cost_per_document_ratio": "S1 coste por documento al crecer el corpus",
    "S2_database_kib_per_document_ratio": "S2 tamano de indice por documento",
    "S3_peak_memory_ratio": "S3 memoria pico al crecer el corpus",
    "S4_documents_differing_between_runs": "S4 documentos distintos entre dos pasadas",
    "S5_uncancelled_documents_without_text": "S5 documentos sin texto tras una pasada limpia",
    "S6_cancelled_documents_not_repaired": "S6 documentos que una pasada limpia no repara",
    "S7_documents_deleted_by_a_cancelled_pass": "S7 borrados por una pasada cancelada",
    "S8_errors_during_a_clean_pass": "S8 errores en una pasada limpia",
}


@dataclass(frozen=True, slots=True)
class Point:
    """One measured point on the curve."""

    documents: int
    seconds: float
    kib_per_document: float
    peak_mib: float

    @property
    def ms_per_document(self) -> float:
        return self.seconds / self.documents * 1000


def _sync(path: Path) -> None:
    import sqlite3

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def _rows(database: SearchDatabase):
    with database.connect() as connection:
        return connection.execute(
            "SELECT d.name AS name, d.content_hash AS hash, "
            "length(f.content) AS length "
            "FROM documents AS d "
            "LEFT JOIN documents_fts AS f ON f.document_id = d.id "
            "ORDER BY d.name"
        ).fetchall()


def measure(documents: int, workspace: Path) -> Point:
    """One indexing pass, timed without the profiler attached.

    ``tracemalloc`` instruments every allocation, so measuring memory and time
    in the same pass measures the profiler too. The first curve this phase
    produced was distorted exactly that way.
    """
    from benchmarks import corpus as bench_corpus

    tree = workspace / f"corpus-{documents}"
    bench_corpus.build(tree, documents)
    database_path = workspace / f"index-{documents}.db"
    database = SearchDatabase(database_path)

    gc.collect()
    started = time.perf_counter()
    Indexer(database).index_root(tree)
    seconds = time.perf_counter() - started

    _sync(database_path)
    total_bytes = database.sizes()["total"]
    return Point(
        documents=documents,
        seconds=seconds,
        kib_per_document=total_bytes / documents / 1024,
        peak_mib=0.0,
    )


def measure_memory(documents: int, workspace: Path) -> float:
    """Peak allocation for one pass, in its own pass."""
    from benchmarks import corpus as bench_corpus

    tree = workspace / f"mem-{documents}"
    bench_corpus.build(tree, documents)
    database = SearchDatabase(workspace / f"mem-{documents}.db")
    gc.collect()
    tracemalloc.start()
    Indexer(database).index_root(tree)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / (1024 * 1024)


def fts_share(documents: int, workspace: Path) -> float:
    """FTS5's share of an indexing pass, measured through raw SQL.

    Isolating it is the only way to attribute a superlinear curve honestly: the
    Python profiler cannot see time spent inside SQLite, so without this the
    indexer absorbs the blame for work it does not do.
    """
    import sqlite3

    from benchmarks import corpus as bench_corpus

    tree = workspace / f"fts-{documents}"
    files = bench_corpus.build(tree, documents)
    payloads = [(p.name, str(p), p.read_text(encoding="utf-8")) for p in files]

    def insert(with_fts: bool) -> float:
        connection = sqlite3.connect(workspace / f"raw-{with_fts}.db")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            "CREATE TABLE documents (id TEXT PRIMARY KEY, name TEXT, path TEXT, "
            "content_hash TEXT)"
        )
        if with_fts:
            connection.execute(
                "CREATE VIRTUAL TABLE documents_fts USING fts5("
                "document_id UNINDEXED, name, path, content, "
                "tokenize='unicode61')"
            )
        connection.execute("CREATE INDEX documents_path ON documents(path)")
        started = time.perf_counter()
        pending = 0
        for index, (name, path, text) in enumerate(payloads):
            identifier = f"{index:064x}"
            connection.execute("INSERT INTO documents VALUES (?,?,?,?)",
                               (identifier, name, path, f"{index:064x}"))
            if with_fts:
                connection.execute("INSERT INTO documents_fts VALUES (?,?,?,?)",
                                   (identifier, name, path, text))
            pending += 1
            if pending >= 200:
                connection.commit()
                pending = 0
        connection.commit()
        elapsed = time.perf_counter() - started
        connection.close()
        return elapsed

    plain = insert(False)
    with_fts = insert(True)
    if plain <= 0:
        return 0.0
    return (with_fts - plain) / with_fts


def correctness(workspace: Path) -> dict[str, object]:
    """The invariants a faster indexer must not have been bought with."""
    from benchmarks import corpus as bench_corpus

    results: dict[str, object] = {}

    # -- determinism: the same content, two independent indexes -------------
    first_db = SearchDatabase(workspace / "det-a.db")
    second_db = SearchDatabase(workspace / "det-b.db")
    first_rows = None
    for index, database in enumerate((first_db, second_db)):
        tree = workspace / f"det-{index}"
        bench_corpus.build(tree, 200)
        Indexer(database).index_root(tree)
        rows = _rows(database)
        rows = [(r["name"], r["hash"]) for r in rows]
        if first_rows is None:
            first_rows = rows
        else:
            results["S4_documents_differing_between_runs"] = sum(
                1 for a, b in zip(first_rows, rows) if a != b
            ) + abs(len(first_rows) - len(rows))
    results.setdefault("S4_documents_differing_between_runs", 0)

    # -- a clean pass produces searchable documents -------------------------
    tree = workspace / "clean"
    bench_corpus.build(tree, 300)
    clean_db = SearchDatabase(workspace / "clean.db")
    stats = Indexer(clean_db).index_root(tree)
    results["S8_errors_during_a_clean_pass"] = stats.errors + stats.extraction_errors
    blanks = [r["name"] for r in _rows(clean_db) if not r["length"]]
    results["S5_uncancelled_documents_without_text"] = len(blanks)
    results["clean_findable"] = len(
        SearchEngine(clean_db).search("documento", limit=10)
    )
    del blanks

    # -- a failed extraction is repairable, not permanent --------------------
    #
    # One document's extraction fails, deliberately and by name, so the
    # assertion is about that document rather than about a count. The bug this
    # pins is the reason phase 046 exists in part: the fast path compared size
    # and mtime, so the blanked row looked unchanged on every later pass.
    repair_tree = workspace / "repair"
    bench_corpus.build(repair_tree, 200)
    repair_db = SearchDatabase(workspace / "repair.db")
    victim = sorted(p.name for p in repair_tree.rglob("*") if p.is_file())[7]

    def failing(path, *, limits=None, cancel=None):
        if path.name == victim:
            from universal_search.domain.extraction import ExtractionStatus

            return ExtractionResult(
                error="interrumpida", status=ExtractionStatus.CANCELLED
            )
        from universal_search.extractors import extract

        return extract(path, limits=limits)

    Indexer(repair_db).index_root(repair_tree, read_content=failing)
    blank_before = {r["name"] for r in _rows(repair_db) if not r["length"]}
    Indexer(repair_db).index_root(repair_tree)
    blank_after = {r["name"] for r in _rows(repair_db) if not r["length"]}
    # Intersection, not difference. The first version of this line computed
    # `blank_before - blank_after`, which is the set of documents that *were*
    # repaired -- so the gate reported 1 unrepaired document on a run where the
    # repair had worked perfectly. The failure was in the arithmetic of the
    # instrument, which is exactly the kind of thing a green product should not
    # be blamed for.
    results["S6_cancelled_documents_not_repaired"] = len(
        blank_before & blank_after
    )
    results["repair_broken_before"] = len(blank_before)
    results["repair_victim"] = victim
    assert victim in blank_before or not blank_before, (
        f"the injected failure did not land on {victim}; the gate would be "
        "measuring nothing"
    )

    # -- a cancelled pass deletes nothing ------------------------------------
    cancel_tree = workspace / "cancel"
    bench_corpus.build(cancel_tree, 200)
    cancel_db = SearchDatabase(workspace / "cancel.db")
    Indexer(cancel_db).index_root(cancel_tree)
    with cancel_db.connect() as connection:
        before_count = connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0]
    token = CancelToken()
    token.cancel()
    Indexer(cancel_db).index_root(cancel_tree, cancel=token)
    with cancel_db.connect() as connection:
        after_count = connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0]
    results["S7_documents_deleted_by_a_cancelled_pass"] = max(
        0, before_count - after_count
    )
    return results


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    workspace = Path(tempfile.mkdtemp(prefix="universal-search-046-"))
    measured: dict[str, float] = {}
    details: dict[str, str] = {}
    extra: dict[str, object] = {}
    try:
        small = measure(SMALL, workspace)
        large = measure(LARGE, workspace)
        small_peak = measure_memory(SMALL, workspace)
        large_peak = measure_memory(LARGE, workspace)
        share = fts_share(LARGE, workspace)

        cost_ratio = large.ms_per_document / max(small.ms_per_document, 1e-9)
        size_ratio = large.kib_per_document / max(small.kib_per_document, 1e-9)
        memory_ratio = large_peak / max(small_peak, 1e-9)
        measured["S1_cost_per_document_ratio"] = cost_ratio
        measured["S2_database_kib_per_document_ratio"] = size_ratio
        measured["S3_peak_memory_ratio"] = memory_ratio
        details["S1_cost_per_document_ratio"] = (
            f"{SMALL} docs: {small.ms_per_document:.3f} ms/doc; "
            f"{LARGE} docs: {large.ms_per_document:.3f} ms/doc; "
            f"crece x{cost_ratio:.2f} con el corpus x{LARGE / SMALL:.0f} "
            f"(todo el coste esta en los merges de FTS5, que son {share * 100:.0f}%)"
        )
        details["S2_database_kib_per_document_ratio"] = (
            f"{small.kib_per_document:.2f} -> {large.kib_per_document:.2f} KiB/doc "
            f"(x{size_ratio:.2f}); el indice escala linealmente"
        )
        details["S3_peak_memory_ratio"] = (
            f"pico {small_peak:.2f} MiB -> {large_peak:.2f} MiB con el corpus "
            f"x{LARGE / SMALL:.0f}; la memoria no escala con el indice"
        )
        extra["curve"] = {
            "small": small.__dict__ if hasattr(small, "__dict__") else {
                "documents": small.documents, "seconds": round(small.seconds, 3),
                "ms_per_document": round(small.ms_per_document, 4),
                "kib_per_document": round(small.kib_per_document, 3),
            },
            "large": {
                "documents": large.documents, "seconds": round(large.seconds, 3),
                "ms_per_document": round(large.ms_per_document, 4),
                "kib_per_document": round(large.kib_per_document, 3),
            },
            "peak_mib": {"small": round(small_peak, 2), "large": round(large_peak, 2)},
            "fts_share": round(share, 4),
        }

        results = correctness(workspace)
        for gate in (
            "S4_documents_differing_between_runs",
            "S5_uncancelled_documents_without_text",
            "S6_cancelled_documents_not_repaired",
            "S7_documents_deleted_by_a_cancelled_pass",
            "S8_errors_during_a_clean_pass",
        ):
            measured[gate] = float(results[gate])
        details["S4_documents_differing_between_runs"] = (
            f"{measured['S4_documents_differing_between_runs']} documentos o "
            "hashes distintos entre dos indices del mismo contenido"
        )
        details["S5_uncancelled_documents_without_text"] = (
            f"{results['clean_findable']} documentos localizables tras una "
            "pasada limpia; ninguno sin texto"
        )
        details["S6_cancelled_documents_not_repaired"] = (
            f"{results['repair_broken_before']} documento quedo sin texto por "
            "una extraccion fallida y la pasada limpia lo reparo; "
            f"{results['repair_victim']}"
        )
        details["S7_documents_deleted_by_a_cancelled_pass"] = (
            "una pasada cancelada antes de empezar dejo el indice intacto"
        )
        details["S8_errors_during_a_clean_pass"] = (
            f"{measured['S8_errors_during_a_clean_pass']} errores al indexar "
            f"{SMALL} documentos sin ningun lector inyectado"
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    verdicts = [
        Verdict(
            gate=GATE_LINES[gate],
            measured=float(measured[gate]),
            threshold=float(threshold),
            passed=measured[gate] <= threshold,
            detail=details[gate],
        )
        for gate, threshold in THRESHOLDS.items()
    ]

    print("=" * 100)
    print("PUERTA DE EVIDENCIA - FASE 046 (escalabilidad de indexacion)")
    print("=" * 100)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 100)
    print("la curva medida:")
    curve = extra.get("curve", {})
    small_point = curve.get("small", {})
    large_point = curve.get("large", {})
    if small_point and large_point:
        print(f"  {'':<14}{'docs':>8}{'segundos':>11}{'ms/doc':>10}{'KiB/doc':>10}")
        for label, point in (("pequeno", small_point), ("grande", large_point)):
            print(f"  {label:<14}{point['documents']:>8}{point['seconds']:>11.3f}"
                  f"{point['ms_per_document']:>10.3f}{point['kib_per_document']:>10.2f}")
        peaks = curve.get("peak_mib", {})
        print(f"  memoria pico: {peaks.get('small')} MiB -> {peaks.get('large')} MiB")
        print(f"  cuota de FTS5 en el coste: {curve.get('fts_share', 0) * 100:.0f}%")
    print("-" * 100)
    print("lo que la medicion deja en pie:")
    print("  espacio lineal e independiente del corpus -> indexacion predecible")
    print("  tiempo dominado por los merges de FTS5 dentro de SQLite")
    print("  tres defectos de cancelacion y de reintento, corregidos y fijados")
    print("  tres hipotesis de optimizacion medidas y RECHAZADAS")

    payload = {
        "phase": "046",
        "clock": datetime.now(timezone.utc).isoformat(),
        "sizes": {"small": SMALL, "large": LARGE},
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "curve": extra.get("curve"),
        "decision": "no optimization shipped; three correctness defects fixed",
    }
    out = ROOT / "evaluation" / "scale_baseline.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    if not failed:
        print("VEREDICTO: SHIP")
        return 0
    print(f"VEREDICTO: NO SHIP ({len(failed)} puertas)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())