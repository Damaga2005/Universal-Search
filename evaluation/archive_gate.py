"""Phase 034 evidence: content inside ZIPs is searchable, and hostile ZIPs fail.

Run from the repository root::

    python -m evaluation.archive_gate

The gates that decide this phase are the hostile ones. An archive reader is
the kind of feature where "it works on my zip" says nothing, and where the
damage (path traversal, decompression bombs, a lying central directory) is
silent. Each hostile case is planted as a real archive and then checked twice:
the extractor must refuse it, *and* the resulting index must not contain the
planted token.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation import corpus as corpus_module  # noqa: E402
from universal_search.extractors.archive import read_archive  # noqa: E402
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402

SEARCHABLE_TOKEN = "represionrepartidorcompensador"
SLIP_TOKEN = "contenidotraviesoquelogue"
ABS_TOKEN = "raizdelrepositorioequis"
BOMB_TOKEN = "cargautilqueapretadmuchisima"

SEARCHABLE = {
    "notas/represion.txt": (
        "La represion del repartidor compensador esta calibrada a 4,5 bar."
    ),
    "notas/valvulas.txt": "Valvula de control sobre el colector principal.",
    "informes/resumen.txt": "Resumen anual de incidencias y presupuesto.",
}

EXPECTED: tuple[tuple[str, str], ...] = (
    ("compensador", "notas/represion.txt"),
    ("valvula", "notas/valvulas.txt"),
    ("incidencias", "informes/resumen.txt"),
    ("resumen.txt", "informes/resumen.txt"),
)


def build_corpus(root: Path) -> None:
    corpus_module.build(root)


def add_archive(root: Path) -> None:
    """One archive whose members are searchable and whose hostile ones are not.

    Every hostile case is planted in the *same* archive as the searchable
    content, so the gate proves the refusal rules do not cost the good members.
    """
    members = {name: text.encode("utf-8") for name, text in SEARCHABLE.items()}
    with zipfile.ZipFile(root / "manual.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
        archive.writestr("datos.exe", b"MZ\x90\x00binario\x00\x01\x02")
        archive.writestr(
            "../../windows/system32/travieso.txt",
            (SLIP_TOKEN + " " * 50).encode(),
        )
        archive.writestr("/raiz/equis.txt", (ABS_TOKEN + " " * 50).encode())
        archive.writestr("dentro.zip", zip_bytes({"secreto.txt": b"oculto"}))
        archive.writestr("bomba.txt", (BOMB_TOKEN + " ").encode() * 40_000)


def zip_bytes(members: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


THRESHOLDS = {
    "T1_archive_recall": 1.0,
    "T2_hostile_tokens_indexed": 0,
    "T3_refused_members_indexed": 0,
    "T4_binary_members_indexed": 0,
    "T5_nested_content_indexed": 0,
    "T6_labelled_documents_lost": 0,
    "T7_ms_per_archive": 250.0,
    "T8_new_runtime_dependencies": 0,
    "T9_peak_ratio_bound": 100.0,
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
            f"{'PASS' if self.passed else 'FAIL'}  {self.gate:<36} "
            f"{self.measured:>12.4f}  (umbral {self.threshold})  {self.detail}"
        )


def _declared_dependencies() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL
    )
    if not match:
        return ["<no dependency list found>"]
    names = []
    for entry in match.group(1).split(","):
        cleaned = re.match(
            r"[A-Za-z_][\w.-]*", entry.strip().strip("\"'")
        )
        if cleaned:
            names.append(cleaned.group(0).lower())
    return sorted(names)


def _stdlib_only(module: str) -> list[str]:
    declared = {"pypdf", "watchdog"}
    source = ROOT / "src" / "universal_search" / f"{module.replace('.', '/')}.py"
    if not source.exists():
        return [f"<missing {source.name}>"]
    known = {
        "__future__", "universal_search", "zipfile", "pathlib", "time",
        "dataclasses", "contextlib", "typing", "collections", "weakref",
        "re", "sqlite3", "json", "sys", "os", "io", "email", "html",
    }
    text = source.read_text(encoding="utf-8")
    found = set()
    for match in re.finditer(
        r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", text, re.MULTILINE
    ):
        root = match.group(1).split(".")[0]
        if root not in known and root not in declared:
            found.add(root)
    return sorted(found)


def main() -> int:
    # Windows consoles default to a legacy code page, and these gates print
    # the interface's own strings. Never crash while reporting (the CLI has
    # done this since phase 005; a gate that dies printing is worse than one
    # that reports a failure).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    corpus_module.assert_labels_are_consistent()
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-034-"))
    tree = workspace / "tree"
    build_corpus(tree)
    mapping = corpus_module.ids_by_path(tree)

    # The corpus first, on its own: the archive must not be able to push a
    # document out of the results, and the only way to know that is to have
    # measured what the results were before the archive existed. Measuring the
    # same index twice is a tautology, and it is what this gate did first.
    database = SearchDatabase(workspace / "index.db")
    Indexer(database).index_root(tree)
    lexical = SearchEngine(database)
    from evaluation import runner  # noqa: PLC0415

    def found_at(limit: int = 5) -> dict[str, bool]:
        answer: dict[str, bool] = {}
        for labelled in corpus_module.LABELLED_QUERIES:
            hits = {
                mapping.get(Path(result.path).resolve())
                for result in lexical.search(labelled.query, limit=limit)
            }
            answer[labelled.query] = bool(hits & labelled.relevant)
        return answer

    found_before = found_at()
    mrr_without = runner.measure(lexical, mapping).mrr()

    add_archive(tree)
    Indexer(database).index_root(tree)

    # -- T1: content inside the archive is searchable -------------------------
    found = 0
    misses: list[str] = []
    for query, member in EXPECTED:
        hits = {
            Path(result.path).as_posix()
            for result in lexical.search(query, limit=50)
        }
        if any(hit.endswith("manual.zip") for hit in hits):
            found += 1
        else:
            misses.append(f"{query!r} no devolvio manual.zip")
    recall = found / len(EXPECTED)

    # -- T2: hostile names, never searchable ----------------------------------
    hostile_hits = sum(
        len(lexical.search(token, limit=50))
        for token in (SLIP_TOKEN, ABS_TOKEN, BOMB_TOKEN)
    )

    # -- T3: refused members are named as refused -----------------------------
    archive_path = tree / "manual.zip"
    result = read_archive(archive_path)
    refused_named = sum(
        1 for warning in result.warnings
        if "unsafe member name" in warning or "refused" in warning
    )

    # -- T4: binary members never decoded -------------------------------------
    binary_hits = len(lexical.search("binario", limit=50))

    # -- T5: no recursion ------------------------------------------------------
    nested_hits = len(lexical.search("oculto", limit=50))

    # -- T6: the existing corpus did not lose anything -----------------------
    mrr_with = runner.measure(lexical, mapping).mrr()
    found_after = found_at()
    lost = [
        query for query, was_found in found_before.items()
        if was_found and not found_after.get(query, False)
    ]

    # -- T7: cost per archive --------------------------------------------------
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        read_archive(archive_path)
        timings.append((time.perf_counter() - started) * 1000)
    per_archive = sorted(timings)[-1]

    # -- T8: dependencies ------------------------------------------------------
    declared = _declared_dependencies()
    new_dependencies = [
        name for name in declared if name not in ("pypdf", "watchdog")
    ] + [f"import:{name}" for name in _stdlib_only("extractors.archive")]

    # -- T9: the expansion bound actually binds -------------------------------
    from universal_search.domain.extraction import (  # noqa: PLC0415
        DEFAULT_LIMITS,
        ExtractionLimits,
    )

    bomb_path = workspace / "bomba.zip"
    with zipfile.ZipFile(bomb_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomba.txt", (BOMB_TOKEN + " ").encode() * 40_000)
    strict = read_archive(
        bomb_path,
        limits=ExtractionLimits(
            max_expansion_ratio=10, max_part_bytes=DEFAULT_LIMITS.max_part_bytes
        ),
    )
    ratio_bound_holds = strict.text is None or BOMB_TOKEN not in strict.text

    verdicts = [
        Verdict("T1 archive recall", recall, THRESHOLDS["T1_archive_recall"],
                recall >= THRESHOLDS["T1_archive_recall"],
                f"{found}/{len(EXPECTED)} dentro de manual.zip"
                + ("" if not misses else "; " + "; ".join(misses))),
        Verdict("T2 hostile tokens indexed", hostile_hits,
                THRESHOLDS["T2_hostile_tokens_indexed"], not hostile_hits,
                "zip-slip, ruta absoluta y ratio de expansion"),
        Verdict("T3 refused members declared", refused_named, 1,
                refused_named >= 1,
                f"avisos: {[w for w in result.warnings if 'refused' in w or 'unsafe' in w][:2]}"),
        Verdict("T4 binary members indexed", binary_hits,
                THRESHOLDS["T4_binary_members_indexed"], not binary_hits,
                "un .exe dentro del zip no se decodifica"),
        Verdict("T5 nested content indexed", nested_hits,
                THRESHOLDS["T5_nested_content_indexed"], not nested_hits,
                "un zip dentro de un zip no se abre"),
        Verdict("T6 labelled documents lost", len(lost),
                THRESHOLDS["T6_labelled_documents_lost"], not lost,
                f"MRR {mrr_without:.4f} -> {mrr_with:.4f}"
                + ("" if not lost else f"; perdidos: {lost}")),
        Verdict("T7 ms per archive", per_archive,
                THRESHOLDS["T7_ms_per_archive"],
                per_archive <= THRESHOLDS["T7_ms_per_archive"],
                "peor de cinco, con un zip de 7 miembros"),
        Verdict("T8 new runtime dependencies", len(new_dependencies),
                THRESHOLDS["T8_new_runtime_dependencies"], not new_dependencies,
                f"declaradas: {declared}"
                if not new_dependencies else f"nuevas: {new_dependencies}"),
        Verdict("T9 expansion bound holds", 1.0 if ratio_bound_holds else 0.0,
                THRESHOLDS["T9_peak_ratio_bound"], ratio_bound_holds,
                "con ratio 10x el miembro bomba se rechaza entero"),
    ]

    print("=" * 104)
    print("PUERTA DE EVIDENCIA - FASE 034 (contenido dentro de comprimidos)")
    print("=" * 104)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 104)
    print(f"archivo: {archive_path.name} "
          f"({archive_path.stat().st_size} bytes en disco)")
    print(f"estado: {result.status}  avisos: {len(result.warnings)}")
    for warning in result.warnings:
        print(f"    - {warning}")
    print(f"miembros leidos: "
          f"{len(result.structure.headings) if result.structure else 0}")
    payload = {
        "phase": "034",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "warnings": list(result.warnings),
        "misses": misses,
    }
    out = ROOT / "evaluation" / "archive_baseline.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nregistro escrito en {out.relative_to(ROOT)}")
    failed = [v for v in verdicts if not v.passed]
    print("VEREDICTO:", "SHIP" if not failed else f"NO SHIP ({len(failed)} puertas)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
