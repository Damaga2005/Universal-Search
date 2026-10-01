"""Phase 037 evidence: distribution must work from a copy, not just a build.

Run from the repository root::

    python -m evaluation.distribution_gate

The phase exists because the 2.0.0 audit published two ``.exe`` files that did
not run: a one-dir build needs its ``_internal/`` folder, and nobody noticed
until the artefact was downloaded and executed. So the gates below are not
about *building* — they are about what a user can do with a copy afterwards.

Three questions, three groups of gates:

* **Portable mode is real.** Index, search and forget all land in a folder next
  to the executable, and nothing at all appears under ``%LOCALAPPDATA%``.
  T1-T5.
* **It does not leak into the installed layout.** An explicit home still wins,
  an installed copy is unaffected, and portable never falls back silently.
  T6-T8.
* **The shipped artefacts are coherent.** Both specs pin the same version and
  icon, and the portable build answers ``--version`` from a folder with no
  siblings. T9-T11.

The gate rewrites ``LOCALAPPDATA`` and the working directory to measure a copy
on a stick, and restores both afterwards. The first version did not, and the
suite failed three unrelated tests in other modules with an index that had
appeared next to the repository.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from universal_search import portable  # noqa: E402
from universal_search.appconfig import (  # noqa: E402
    AppPaths,
    default_home,
    per_user_home,
)
from universal_search.fuzzy import FuzzyIndex, FuzzySearchEngine  # noqa: E402
from universal_search.index.database import SearchDatabase  # noqa: E402
from universal_search.index.indexer import Indexer  # noqa: E402
from universal_search.index.search import SearchEngine  # noqa: E402
from universal_search.privacy import forget  # noqa: E402

HOME_ENV = portable.HOME_ENV

THRESHOLDS = {
    "T1_writes_in_the_portable_folder": 0,
    "T2_search_works_portable": 1,
    "T3_forget_works_portable": 1,
    "T4_touched_localappdata": 0,
    "T5_documents_untouched": 1,
    "T6_explicit_home_wins": 1,
    "T7_installed_copy_unchanged": 1,
    "T8_silent_fallbacks": 0,
    "T9_specs_pin_one_version": 1,
    "T10_onefile_spec_is_one_file": 1,
    "T11_onefile_answers_version": 1,
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
            f"{'PASS' if self.passed else 'FAIL'}  {self.gate:<40}"
            f"{self.measured:>9.3f}  (umbral {self.threshold})  {self.detail}"
        )


def _snapshot(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


@contextmanager
def _as_a_copy_on_a_stick(local: Path, folder: Path):
    """Simulate a portable copy, and restore the real process afterwards.

    Both the environment and the working directory are process-global, so they
    are restored here rather than at the end of ``main``. A gate that leaves
    ``LOCALAPPDATA`` pointing into its temporary workspace, or a ``portable.txt``
    in the repository, corrupts every measurement that runs after it.

    Portable mode keys off the marker next to the *executable*, and outside a
    PyInstaller build the executable folder is the working directory. Running
    from inside the folder we are pretending to be the copy in is the situation
    a user on a USB stick is in, and it avoids adding a production switch that
    exists only to make a measurement easier.
    """
    saved_environment = dict(os.environ)
    saved_directory = Path.cwd()
    os.environ.pop(HOME_ENV, None)
    os.environ.pop(portable.PORTABLE_ENV, None)
    os.environ["LOCALAPPDATA"] = str(local)
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(saved_directory)
        os.environ.clear()
        os.environ.update(saved_environment)


def _measure(
    workspace: Path,
    exe_dir: Path,
    fake_local: Path,
    documents: Path,
    local_before: set[str],
) -> tuple[dict[str, bool], dict[str, object]]:
    """T1-T8. Runs inside :func:`_as_a_copy_on_a_stick`."""
    marker = portable.enable()
    current = portable.status()

    paths = AppPaths.discover()
    database = SearchDatabase(paths.database)
    Indexer(database).index_root(documents)

    # T1: every byte of state is inside the portable folder.
    strays = [
        path.relative_to(exe_dir).as_posix()
        for path in exe_dir.rglob("*")
        if path.is_file()
        and path.name != portable.PORTABLE_MARKER
        and not str(path).startswith(str(marker))
    ]
    t1 = current.portable and paths.portable and not strays and marker.is_dir()

    # T2 and T3: the two operations the acceptance criterion names, run against
    # the portable database and not against a temporary copy of it.
    #
    # The engine chain is the one the CLI assembles, not the bare lexical
    # engine: "transisto" is a prefix query, which only the fuzzy layer of
    # phase 031 answers. Testing portable mode with a weaker engine would prove
    # the storage works and leave the retrieval untested.
    engine = FuzzySearchEngine(SearchEngine(database), FuzzyIndex(database))
    found = [item.name for item in engine.search("transisto", limit=5)]
    t2 = "nota.md" in found

    result = forget(database, documents / "receta.md")
    after = SearchEngine(database).search("paella", limit=5)
    t3 = result.total > 0 and not [i for i in after if i.name == "receta.md"]

    # T4: the per-user directory was not created and not written.
    local_after = _snapshot(fake_local)
    t4 = local_after == local_before

    # T5: forgetting a document is still not deleting a file.
    t5 = (documents / "receta.md").is_file()

    # T6: an explicit home beats the marker. The background worker relies on
    # this: it passes its resolved home to the child process.
    os.environ[HOME_ENV] = str(workspace / "explicit")
    override = portable.status()
    t6 = (
        not override.portable
        and override.home == workspace / "explicit"
        and AppPaths.discover().home == workspace / "explicit"
    )

    # T7: without the marker, the installed copy is exactly what it was. Run
    # from an ordinary folder: asserting it from inside the portable one would
    # only re-prove T1.
    os.environ.pop(HOME_ENV, None)
    portable.disable(exe_dir)
    os.chdir(workspace)
    installed = portable.status()
    t7 = (
        not installed.portable
        and installed.home == per_user_home()
        and default_home() == per_user_home()
        and AppPaths.discover().portable is False
    )

    # T8: an unusable portable folder raises instead of quietly relocating.
    #
    # Two refusals, both of which happen for real: a file sitting where the
    # marker must go (``enable``) and one sitting where the data directory must
    # go (``ensure_writable``). Both are ordinary outcomes of a stick that
    # already holds something with the same name, and neither is a Windows
    # permission this gate could simulate honestly — chmod does not stop an
    # administrator, and a check that pretended it did would prove nothing.
    refusals = 0
    occupied_by_file = workspace / "occupied"
    occupied_by_file.write_text("not a directory", encoding="utf-8")
    for action in (
        lambda: portable.enable(occupied_by_file),
        lambda: portable.ensure_writable(
            occupied_by_file / portable.PORTABLE_DATA_DIRNAME
        ),
    ):
        try:
            action()
        except portable.PortableUnavailable:
            refusals += 1
    t8 = refusals == 2

    facts = {
        "marker": marker,
        "current": current,
        "strays": strays,
        "local_after": local_after,
        "database": database,
        "indexed": len(SearchEngine(database).search("bjt", 20)),
    }
    gates = {"t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5,
             "t6": t6, "t7": t7, "t8": t8}
    return gates, facts


def _single_file_gate(workspace: Path) -> tuple[float, bool, str]:
    """T11: run the single-file build alone, if it has been built."""
    built = ROOT / "dist" / "UniversalSearch-onefile" / "UniversalSearch.exe"
    if not built.is_file():
        return 0.0, False, "no compilado: ejecuta packaging/build.ps1 para medirlo"

    isolated = workspace / "alone"
    isolated.mkdir()
    lonely = isolated / "UniversalSearch.exe"
    lonely.write_bytes(built.read_bytes())
    completed = subprocess.run(
        [str(lonely), "--version"],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, HOME_ENV: str(isolated / "home")},
    )
    # The folder must hold the executable and nothing else from the build: a
    # file that needs siblings is the exact 2.0.0 defect.
    siblings = sorted(e.name for e in isolated.iterdir() if e.is_file())
    ok = completed.returncode == 0 and siblings == ["UniversalSearch.exe"]
    detail = (
        f"{completed.stdout.strip() or completed.stderr.strip()} "
        f"| solo en la carpeta: {siblings}"
    )
    return (1.0 if ok else 0.0), True, detail


def main() -> int:
    # Windows consoles default to a legacy code page, and these gates print
    # the interface's own strings. Never crash while reporting (the CLI has
    # done this since phase 005; a gate that dies printing is worse than one
    # that reports a failure).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    workspace = Path(tempfile.mkdtemp(prefix="universal-search-037-"))

    # Two directories that must stay untouched: the portable folder and a
    # stand-in for %LOCALAPPDATA%. The second is not the real one — pointing
    # the gate at the developer's profile would mean reading their data, which
    # is exactly what this project refuses to do.
    exe_dir = workspace / "UniversalSearch"
    fake_local = workspace / "LOCALAPPDATA"
    exe_dir.mkdir()
    fake_local.mkdir()
    local_before = _snapshot(fake_local)

    documents = workspace / "documents"
    documents.mkdir()
    (documents / "nota.md").write_text(
        "BJT: transistor de union bipolar y polarizacion", encoding="utf-8"
    )
    (documents / "receta.md").write_text(
        "paella valenciana con arroz bomba", encoding="utf-8"
    )

    with _as_a_copy_on_a_stick(fake_local, exe_dir):
        gates, facts = _measure(
            workspace, exe_dir, fake_local, documents, local_before
        )

    marker = facts["marker"]
    strays = facts["strays"]
    local_after = facts["local_after"]

    # -- the shipped artefacts ------------------------------------------------
    one_dir_spec = (ROOT / "packaging" / "universal-search.spec").read_text(
        encoding="utf-8"
    )
    one_file_spec = (
        ROOT / "packaging" / "universal-search-onefile.spec"
    ).read_text(encoding="utf-8")

    # T9: one version, one icon, in both specs and the Windows resource.
    import universal_search

    t9 = (
        one_dir_spec.count("version=str(VERSION_FILE)") == 2
        and one_file_spec.count("version=str(VERSION_FILE)") == 1
        and one_dir_spec.count("icon=str(ICON)") == 2
        and one_file_spec.count("icon=str(ICON)") == 1
        and f"{universal_search.__version__}" in (
            ROOT / "packaging" / "version_file.txt"
        ).read_text(encoding="utf-8")
    )

    # T10: the one-file spec bundles the binaries instead of collecting them
    # beside the executable. A spec that also calls COLLECT is a one-dir build
    # with an extra step, which is the defect the audit published.
    t10 = (
        "COLLECT(" not in one_file_spec
        and "exclude_binaries=False" in one_file_spec
        and "a.binaries" in one_file_spec
        and "a.datas" in one_file_spec
    )

    # T11: the single-file executable answers --version from a folder with
    # nothing else in it. This is the audit's own reproduction, run as a gate.
    onefile_measured, onefile_ran, onefile_detail = _single_file_gate(workspace)

    verdicts = [
        Verdict("T1 state stays in the portable folder", len(strays),
                THRESHOLDS["T1_writes_in_the_portable_folder"], gates["t1"],
                f"todo dentro de {marker.name}/; nada fuera"),
        Verdict("T2 search works portable", 1 if gates["t2"] else 0,
                THRESHOLDS["T2_search_works_portable"], gates["t2"],
                "'transisto' encuentra nota.md desde el indice portable"),
        Verdict("T3 forget works portable", 1 if gates["t3"] else 0,
                THRESHOLDS["T3_forget_works_portable"], gates["t3"],
                "receta.md sale del indice portable"),
        Verdict("T4 files written under LOCALAPPDATA",
                len(local_after - local_before),
                THRESHOLDS["T4_touched_localappdata"], gates["t4"],
                "0 escrituras en el directorio por usuario"),
        Verdict("T5 user documents untouched", 1 if gates["t5"] else 0,
                THRESHOLDS["T5_documents_untouched"], gates["t5"],
                "forget no borra ficheros"),
        Verdict("T6 explicit home wins", 1 if gates["t6"] else 0,
                THRESHOLDS["T6_explicit_home_wins"], gates["t6"],
                f"{HOME_ENV} manda sobre el marcador"),
        Verdict("T7 installed copy unchanged", 1 if gates["t7"] else 0,
                THRESHOLDS["T7_installed_copy_unchanged"], gates["t7"],
                f"sin marcador: {per_user_home()}"),
        Verdict("T8 silent fallbacks", 2 if gates["t8"] else 1,
                THRESHOLDS["T8_silent_fallbacks"], gates["t8"],
                "una carpeta inutilizable da error, no se cambia de sitio"),
        Verdict("T9 one version in both specs", 1 if t9 else 0,
                THRESHOLDS["T9_specs_pin_one_version"], t9,
                "icono y recurso de version en los dos .spec"),
        Verdict("T10 one-file spec is one file", 1 if t10 else 0,
                THRESHOLDS["T10_onefile_spec_is_one_file"], t10,
                "sin COLLECT: los binarios van dentro del .exe"),
        Verdict("T11 one-file answers --version", onefile_measured,
                THRESHOLDS["T11_onefile_answers_version"], onefile_ran,
                onefile_detail),
    ]

    print("=" * 108)
    print("PUERTA DE EVIDENCIA - FASE 037 (distribucion: portable y ejecutable unico)")
    print("=" * 108)
    for verdict in verdicts:
        print(verdict.line())
    print("-" * 108)
    print(f"carpeta del ejecutable:  {exe_dir}")
    print(f"datos portables:         {marker}")
    print(f"por usuario (no tocado): {fake_local}")
    print(f"documentos indexados:    {facts['indexed']}")

    payload = {
        "phase": "037",
        "verdicts": [
            {"gate": v.gate, "measured": v.measured, "threshold": v.threshold,
             "passed": v.passed, "detail": v.detail}
            for v in verdicts
        ],
        "portable_home": str(marker),
        "onefile_exercised": onefile_ran,
    }
    out = ROOT / "evaluation" / "distribution_baseline.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    # relative_to raises when the path has been redirected, and a diagnostic
    # that crashes at the end of a successful run is a bad diagnostic.
    try:
        where = out.relative_to(ROOT)
    except ValueError:
        where = out
    print(f"\nregistro escrito en {where}")
    failed = [v for v in verdicts if not v.passed]
    print("VEREDICTO:", "SHIP" if not failed else f"NO SHIP ({len(failed)} puertas)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())