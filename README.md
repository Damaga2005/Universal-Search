# Universal Search

Local-first universal search for Windows.

Universal Search indexes local files and cloud-backed locations such as OneDrive, then provides fast full-text and metadata search from a lightweight desktop application.

## Principles

- Local-first and privacy-preserving.
- No AI or paid API required.
- Fast incremental indexing.
- Provider-agnostic architecture.
- Search ranking based on filename, content, context and later optional local usage signals.
- Optional local semantic fallback (character n-gram TF-IDF) that answers
  queries the lexical engine misses — synonyms, paraphrases, morphological
  variants — without reordering exact matches. Dependency-free and removable.
- Windows desktop application with a background indexer.

## Initial scope

1. Local filesystem indexing.
2. SQLite + FTS5 search index.
3. Incremental updates.
4. Text/Markdown/JSON/CSV/XML extraction.
5. CLI for development and diagnostics.
6. Desktop GUI.
7. OneDrive providers.

## Status

Phases 001–010 delivered: foundation, incremental indexing, document
extractors (PDF/DOCX/XLSX/PPTX), ranking engine, Windows desktop GUI,
background indexer, OneDrive providers, personal context + local usage
learning, global search (hotkey, filters, recent queries) and the
Windows release **v1.0.0** (installer + uninstaller). A profiled audit &
optimization pass cut indexing 16× (2000 docs: 45.3s → 2.8s) and mean
query latency 48.6 → 34.4 ms (`docs/development/optimization-report.md`);
phase 011 added the reproducible benchmark suite, local metrics and
bounded ranking caches; phase 012 added the advanced query language
(phrases, `AND`/`OR`, negation and `name:`/`path:`/`type:`/`source:`/
`after:`/`before:`/`size:` filters) shared by the CLI and the GUI; phase
013 added a labelled evaluation corpus with Precision@K/Recall@K/MRR and
measured the headroom of every ranking weight instead of asserting it;
phase 014 added deterministic local document intelligence (language,
headings, bounded keyword vectors, co-occurrence and related documents,
rebuildable on demand); phase 015 added index diagnostics, twelve health
checks and five repair operations with confirmation enforced in code;
phase 016 isolated every Windows touchpoint behind a `platforms` adapter,
enforced single-instance window behaviour and added per-user Start Menu
and Explorer integration scripts; phase 017 moved search off the UI thread,
added loading / empty / error states, a central light-dark theme, scalable
type and focused results rows; phase 018 documented the threat model and
data inventory, added `privacy forget`, and fixed a real availability bug:
FTS5's `snippet()` walked every phrase instance, so one document repeating
a term 10 000 times took 1.9 s per search; phase 019 formalised provider
and extractor contracts with capability-based registration, and documented
why there is deliberately no runtime third-party plugin loading; phase 020
added downgrade refusal, a migration ledger, consistent backups before
destructive repairs, Windows CI with a packaged smoke test, and a
reproducible release procedure; phase 021 added an optional native Windows
notification-area controller, an application state service, GUI show/diagnostics
signalling and generation-aware worker ownership without adding a runtime
dependency. Startup claims survive Windows launcher PID differences; owner-scoped
stop markers and a held OS lease prevent replacement/PID-reuse handoff races;
phase 022 added a versioned, bounded local relationship graph with explainable
signals, incremental maintenance and a small related-documents list in the GUI;
phase 023 adds a separate indexing control center with typed source actions,
health/storage/derived-data state and explicit safety confirmations. The
phase 024 formalised the provider contract (streaming `iter_files`, bounded errors, cancellation, capability/interface negotiation), made the provider key the canonical source discriminator with a `(source, path)` uniqueness migration, and added mounted-path NAS/removable providers plus mixed-provider indexing with per-provider failure isolation. Phase 025 added a versioned extraction contract with bounded PDF/Office resources and visible truncation diagnostics. Phase 026 measured a fixed lexical baseline and shipped a dependency-free, versioned n-gram fallback only where the evidence gate justified it; phase 027 added DPI awareness, `open`/`reveal` commands and reversible per-user Explorer integration. Phase 028 added bounded redacted JSON events, a seven-area `diagnose self-test`, a support bundle that declares what it does not contain, and four named recovery cases that never touch source files. Phase 029 made the CI gates a verified contract and the packaged smoke a real gate; that smoke found and fixed a semantic-layer defect (a zero idf on a one-document index, and a precision gate that rejected morphological variants), re-measured with no metric regression. Phase 030 closed the line with an executable gate: `python -m evaluation.gate` runs local invariants, including a behavioural proof that no repair can touch a user's files. Phases 031–040 then added the features the gate could not yet check, and phase 040 extended it to twenty-three: one invariant per promise the programme made, from removable optional layers to WCAG AA contrast and the strings catalogue. Phases 041–049 of the 3.x programme are implemented, each with a report in `docs/development/`; **050, the 3.x product gate, is the one still open**.

Current test count: 1479 tests collected.

## Release state

`python -m evaluation.product_gate` is the 3.x product gate. It produces no
score and no ranking: fifteen audit areas, each mapped to the gate that owns its
evidence, plus the claims a release would want to make and whether this machine
can prove them.

**Verdict: RETENIDO (HOLD).** All fifteen areas have passing evidence. What
blocks a release is one unexplained measurement and six limitations this machine
cannot close -- see `docs/development/050-product-v3-gate-report.md`.

The blocker is `install.ps1 -Repair`: **940 s inside the end-to-end scenario and
19 s on a fresh directory**, for the same command. The process accumulates CPU
rather than blocking, it is past the file copy, and the two candidates that fit
were measured and ruled out (recursive enumeration 0.26 s, manifest
serialisation 0.14 s). An upgrade that takes fifteen minutes for reasons nobody
has identified is not shippable.

The eighteen-step scenario runs for real against the built executable --
`python evaluation/product_scenario.py` -- with `UNIVERSAL_SEARCH_HOME` pointed
at a temporary directory so nothing real is touched. Sixteen of eighteen steps
execute; the two that need a desktop session are recorded NOT RUN with the
reason, which is not a pass.

## Known limitations

Read this before expecting more than the program does.

- **Typo tolerance is bounded, not free.** Words within one edit (up to seven characters) or two edits (longer) are found, as are prefixes. A typo three edits away, or a transposition in the middle of a word of seven characters or fewer, is out of reach. Nothing is invented: every fuzzy match is verified against the real text.
- **No learned semantic embeddings.** The semantic layer (phase 026) is a
  local character n-gram TF-IDF fallback, not a trained embedding model. It
  catches morphological variants, accent-folded overlaps and partial term
  overlap. Pure synonyms with no shared surface form (`BJT` vs
  `transistor de union bipolar`) stay out of reach: that would need a model
  download, a runtime dependency and a license.
- **The semantic layer is optional and local.** It only runs when the lexical
  engine returns nothing, it never reorders a result, and
  `search --no-semantic` turns it off entirely.
- **No external APIs, no cloud AI, no telemetry.** Nothing about your
  documents or your queries leaves the machine. The dependency budget is
  `pypdf` and `watchdog`; `python -m evaluation.gate` fails if a third one
  appears.
- **No latency figure in this repository was measured on a machine en reposo.** The five latency gates correctly refuse to conclude here, because a neighbour is running: another project's test suite and a game. They can conclude on a runner with `--require-conclusive`, and that has never been run.
- **The CI matrix has never been run.** It now declares 3.12, 3.13 and
  3.14 (phase 048) and `requires-python` is bounded to `<3.15` so the
  promise matches the proof, but the workflow has only ever executed 3.12
  in anger. Treat 3.13 and 3.14 as configured, not proven.
- **Nineteen gates, and until phase 050 the build ran four of them.** Fifteen
  evidence gates existed as code and had never been executed by CI, including
  `distribution_gate`, which had carried eleven invariants since phase 037 and
  was the only one that ran a real artefact. All nineteen now run, in three jobs
  split by measured cost, and `tests/test_ci_gates.py` derives the gate list from
  the tree so a twentieth cannot be added without being wired up.
- **Windows only.** The core is platform-independent and the Ubuntu job is a
  non-gating probe, but the GUI, the registry, the global hotkey and the
  installer are Windows by design. `hotkey.py` is the one declared exception
  to the "no Win32 in the core" rule, because `RegisterHotKey` has no
  portable equivalent.
- **95% of a built index is derived data** that only exists after a fallback
  feature has been used, so the footprint jumps between two searches without
  anything announcing it (`047-storage-and-data-lifecycle-report.md`).
- **`scan_local` has no visited set**, so a Windows junction is the one resource
  in this project without a bound (`046`, `047`).
- **`install.ps1` copies over the top on repair** and does not remove files that
  a new build no longer ships. The Inno Setup script does, but it has never been
  compiled (`049-install-report.md`).
- **`installer.iss` has never been compiled** -- ISCC.exe is not on the build
  machine -- and `install.ps1` is the installer of record for that reason.
- **The CI runs on Windows Server**, not on the Windows 10 or 11 a user has, so
  no installation has ever been validated on a user's operating system
  (`docs/SUPPORT.md`).
- **Two measurements are unexplained and recorded as such**: a 16 KiB index-size
  spread between two identical runs, and WAL run-to-run variance from 1.5 s to
  5.6 s on identical input. Three theories were tested and refuted for the
  first; neither has a mechanism, and neither has a workaround.
- **Four tests are flaky** and were documented in phase 041 rather than hidden:
  `test_worker_keeps_index_current_and_stops_cleanly`,
  `test_start_stop_and_no_duplicate_process`,
  `test_process_death_releases_the_tray_process_lock` and
  `test_concurrent_starts_only_claim_the_generation_they_presented`.
- **No digital signature.** The executables ship unsigned.
- **No auto-updater.** Upgrades are manual by decision, not by omission.
- **The index is local to one machine.** It is not synchronised anywhere.

| Fase | Entrega | Estado |
|------|---------|--------|
| 001 | Foundation (SQLite + FTS5, CLI) | ✅ |
| 002 | Incremental indexing + ignore rules | ✅ |
| 003 | Extractors (PDF/DOCX/XLSX/PPTX) | ✅ |
| 004 | Ranking engine | ✅ |
| 005 | Windows desktop GUI | ✅ |
| 006 | Background indexer | ✅ |
| 007 | OneDrive providers | ✅ |
| 008 | Personal context | ✅ |
| 009 | Global search (hotkey, filters, recents) | ✅ |
| 010 | Release (v1.0.0) | ✅ |
| 011 | Performance & scalability (benchmarks, métricas) | ✅ |
| 012 | Advanced search (lenguaje de consultas) | ✅ |
| 013 | Ranking v2 (corpus etiquetado, P@K/R@K/MRR) | ✅ |
| 014 | Inteligencia documental local (reconstruible) | ✅ |
| 015 | Diagnóstico y mantenimiento del índice | ✅ |
| 016 | Integración con Windows (adaptador, shell) | ✅ |
| 017 | UX y accesibilidad (hilo, tema, estados) | ✅ |
| 018 | Privacidad y seguridad (inventario, forget) | ✅ |
| 019 | Arquitectura de proveedores y extensiones | ✅ |
| 020 | Release de producción y fiabilidad (CI, migraciones, copias) | ✅ |
| 021 | Bandeja de notificación y experiencia en segundo plano | ✅ |
| 022 | Grafo local de documentos relacionados | ✅ |
| 023 | UX de indexación y centro de control | ✅ |
| 024 | Expansión de proveedores (NAS/extraíble, identidad `(source, path)`) | ✅ |
| 025 | Extracción de contenido v2 (contrato versionado, límites, diagnósticos) | ✅ |
| 026 | Búsqueda semántica local con evidencia primero (capa opcional sin dependencias) | ✅ |
| 027 | Integración con el shell de Windows (DPI, Explorer, open/reveal) | ✅ |
| 028 | Observabilidad y recuperación local (eventos, self-test, casos nombrados) | ✅ |
| 029 | Release engineering y CI (puertas verificadas por test) | ✅ |
| 030 | Puerta de calidad v2 (gate ejecutable, 13 invariantes) | ✅ |
| 031 | Búsqueda robusta: erratas y palabras parciales (verificada) | ✅ |
| 032 | Sugerencias de consulta, cada una verificada (032) | ✅ |
| 033 | Correo como fuente: asunto, remitente y cuerpo (033) | ✅ |
| 034 | Contenido dentro de `.zip`, con rechazo de zip-slip (034) | ✅ |
| 035 | Operaciones por lotes sobre la selección (035) | ✅ |
| 036 | Agrupar, ordenar y búsquedas guardadas (036) | ✅ |
| 037 | Distribución: modo portable y ejecutable único (037) | ✅ |
| 038 | Puerta de rendimiento reproducible: sabe cuándo no concluir (038) | ✅ |
| 039 | Accesibilidad: teclado, nombres, contraste medido y catálogo de textos (039) | ✅ |
| 040 | Puerta de calidad v3: 23 invariantes, y la decisión de publicar (040) | ✅ |
| 041 | Experiencia de producto: resultados con columnas, estados reales (041) | ✅ | [informe](docs/development/041-product-experience-report.md) |
| 042 | Búsqueda como flujo: ordenar, agrupar, guardar, historial, explicaciones (042) | ✅ | [informe](docs/development/042-search-experience-report.md) |
| 043 | Un sistema de ajustes tipado, validado y migrado (043) | ✅ | [informe](docs/development/043-settings-report.md) |
| 044 | Aprendizaje local v2 | ✅ | [informe](docs/development/044-local-learning-report.md) |
| 045 | Calidad y relevancia de búsqueda | ✅ | [informe](docs/development/045-search-quality-report.md) |
| 046 | Escalabilidad y rendimiento de indexación | ✅ | [informe](docs/development/046-indexing-scale-report.md) |
| 047 | Almacenamiento: `compact()` verificado, contrato de ciclo de vida completo | ✅ | [informe](docs/development/047-storage-and-data-lifecycle-report.md) |
| 048 | Matriz de soporte: rango acotado, CI alineado, clasificadores | ✅ | [informe](docs/development/048-support-matrix-report.md) |
| 049 | Instalación: ciclo real, `repair` y hashes verificables | ✅ | [informe](docs/development/049-install-report.md) |
| 050 | Puerta de producto Universal Search 3.x | ⬜ | [roadmap](docs/development/050-product-v3-gate.md) |

Detail by phase (prompts + reports): [`docs/README.md`](docs/README.md) ·
by version: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Usage

```bash
pip install -e .
universal-search --version         # universal-search 2.0.0
python -m evaluation.gate          # the quality gate (23 invariants)
universal-search diagnose self-test # same checks, from the installed app
universal-search index C:\Users\me\Documents
universal-search search "meeting notes" --limit 20
universal-search search "notes" --source onedrive --type pdf   # filters (009)
universal-search search "notes" --context engineering --explain # context + scoring breakdown (008)
universal-search search "transisto"                    # typo / partial word (031)
universal-search search "bjt" --no-fuzzy              # lexical only (031)
universal-search search "bjt type:txt after:2026-01-01"        # query language (012)
universal-search search "bjt -cmos size:>10KB"                # negation + size filter (012)
universal-search search '"ebers moll" OR "gunn effect"'      # phrase + OR (012)
universal-search gui               # desktop window (alias: universal-search-gui)
universal-search tray              # optional Windows notification-area controller
universal-search open C:\Users\me\notes.pdf   # open with the default app (027)
universal-search reveal C:\Users\me\notes.pdf # show in its folder (027)

# personal layer (all local): contexts, usage learning, hotkey, recents
universal-search context list      # context add|remove|use|relate …
universal-search usage on          # usage show | clear
universal-search hotkey show       # hotkey set ctrl+alt+s | on | off
universal-search recent show       # recent on | off | clear

# local document intelligence (derived data, local-only, rebuildable)
universal-search intelligence rebuild        # language, headings, keywords and graph
universal-search intelligence show informe.pdf
universal-search intelligence related informe.pdf --limit 5

# index health and repair (destructive repairs need --yes)
universal-search diagnose summary
universal-search diagnose health             # exit 0 ok / 1 warnings / 2 fatal
universal-search diagnose repair reconcile C:\Users\me\Docs
universal-search diagnose repair all --root C:\Users\me\Docs --yes

# privacy: what is stored, and how to make it go away
universal-search privacy show
universal-search privacy forget C:\Users\me\Docs\informe.pdf

# install, upgrade and repair without knowing anything about PowerShell
universal-search install               # from a built dist/UniversalSearch
universal-search install --repair      # reinstall over an existing installation

# storage: why the index is the size it is, and how to shrink it
universal-search storage show      # measured footprint, rows per
                                  # category, and reclaimable pages
universal-search storage compact   # give the bytes back (pause the indexer)

# what this build can read and from where
universal-search extensions

# background indexer — runs independently; closing the GUI does not stop it
universal-search indexer start     # detached worker (single instance)
universal-search indexer status    # idle / indexing / paused / error
universal-search indexer pause     # universal-search indexer resume
universal-search indexer stop
universal-search indexer autostart on
```

The related-document graph is derived, local and optional. Rebuild it with
`intelligence rebuild`; open the ranked evidence list from the GUI's
Diagnostic menu. It never changes normal search ranking, and all graph rows
can be removed with `intelligence clear`.

Phase 023 keeps search uncluttered: open **Diagnostic → Centro de control de
indexación** for configured sources, provider availability, counts, scan and
failure state, storage, exclusions, health and derived-data maintenance. Add
or remove a source there without deleting the user's files; indexed rows are
removed only when that separate option is explicitly selected. FTS,
relationship and full-index rebuilds have separate confirmations and never
delete physical source files.

The tray is optional and must be started explicitly. Windows autostart still
registers only the `indexer run` worker; it does not launch the tray. The
tray's Settings and Diagnostics commands open the existing search window, where
those surfaces already live. The adapter uses the Python standard library's
`ctypes`, so this phase added no runtime dependency. The normal suite does not
post an icon. A disposable Windows-only smoke is opt-in with
`UNIVERSAL_SEARCH_TRAY_NATIVE_SMOKE=1`; it exercises real `NIM_ADD`, a posted
Exit command and guaranteed `NIM_DELETE` cleanup.

The CLI database defaults to `universal-search.db` (`--database <path>` to
change). The GUI and background indexer use the per-user application home
(`%LOCALAPPDATA%\Universal Search\index.db`, logs and status), overridable
with `UNIVERSAL_SEARCH_HOME`. Search results show source, path, file name
and a content snippet; ranking is documented in `docs/RANKING.md`.

### Build the Windows executables

```bash
pip install ".[build]"                       # pyinstaller
powershell -File packaging\build.ps1          # both builds, then smoke-tests them
# dist/UniversalSearch/UniversalSearch.exe       windowed GUI
# dist/UniversalSearch/universal-search.exe      console CLI + background indexer + tray
# dist/UniversalSearch-onefile/UniversalSearch.exe   single file, no folder
powershell -File packaging/make-shortcut.ps1 -TargetExe "dist\UniversalSearch\UniversalSearch.exe"
```

Two builds, because they are two different products. **One-dir** is the
installed copy: it starts fast and carries both the window and the console
executable. **One-file** is the portable copy: one `.exe` you can put on a USB
stick, at the cost of unpacking its runtime on every launch.

`build.ps1` runs what it built — including copying the single-file
executable to an empty folder and running it there — because the 2.0.0
release shipped two bare `.exe` files that did not start
(`PYI-8: Failed to load Python DLL`): a one-dir build needs its `_internal/`
folder. Only run it if you meant to; use `-SkipSmoke` to build without testing.

### Portable mode

By default the index, configuration and logs live in
`%LOCALAPPDATA%\Universal Search`, so uninstalling never leaves your index
behind and two accounts never share one. **Portable mode** moves all of it
next to the executable instead, for the cases where that is the only sensible
layout: a USB stick that must carry the index to another PC, a corporate
machine where nothing may be written outside a network share, or an
environment where `%LOCALAPPDATA%` is managed by someone else.

```bash
universal-search portable status          # where do my data live, and why?
universal-search portable on              # write the marker, start using it next launch
universal-search portable off             # back to %LOCALAPPDATA%
```

Portable mode is switched on by either the marker file `portable.txt` beside
the executable or `UNIVERSAL_SEARCH_PORTABLE=1`, and the data goes to a
`UniversalSearch-data` folder next to it. Two things it deliberately does not
do:

- **It never moves an existing index.** Switching mode writes a marker and
  tells you where to look. Relocating hours of indexing work silently would
  either duplicate it or lose it.
- **It never falls back.** If the portable folder cannot be written, the
  application says so and stops, instead of quietly writing to
  `%LOCALAPPDATA%` — an index in a place you do not know about is worse than
  one that refuses to start.

`UNIVERSAL_SEARCH_HOME` still overrides everything, which is how the
background worker passes its resolved location to the child process.

## Development

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e . pytest
.venv\Scripts\python -m pytest

# measurement instruments (deterministic, development only)
python -m benchmarks --profile 1000     # latency / indexing / memory (011)
python -m evaluation                     # labelled corpus, P@K / R@K / MRR (013)
python -m evaluation --flip recency diagrama   # headroom of one ranking weight
python -m evaluation.perf_gate           # performance gate: 0 pass 1 fail 2 inconclusive (038)
```

### The evidence gates

Sixteen phases ship one gate each. Every gate prints its invariants with the
measured value beside the threshold, and writes a record under `evaluation/`.

| puerta | fase | invariantes | veto de carga |
|---|---:|---:|---|
| `python -m evaluation.accessibility_gate` | 039 | 6 | — |
| `python -m evaluation.archive_gate` | 034 | 9 | — |
| `python -m evaluation.batch_gate` | 035 | 9 | — |
| `python -m evaluation.distribution_gate` | 037 | 11 | — |
| `python -m evaluation.fuzzy_gate` | 031 | 6 | sí |
| `python -m evaluation.interaction_gate` | 042 | 11 | sí |
| `python -m evaluation.learning_gate` | 044 | 11 | — |
| `python -m evaluation.mail_gate` | 033 | 7 | — |
| `python -m evaluation.organize_gate` | 036 | 9 | — |
| `python -m evaluation.perf_gate` | 038 | 9 | sí |
| `python -m evaluation.quality_gate` | 045 | 13 | — |
| `python -m evaluation.scale_gate` | 046 | 8 | — |
| `python -m evaluation.settings_gate` | 043 | 13 | — |
| `python -m evaluation.storage_gate` | 047 | 9 | — |
| `python -m evaluation.suggest_gate` | 032 | 6 | sí |
| `python -m evaluation.ux_gate` | 041 | 9 | sí |

Three exit codes, and the middle one is the point:

| code | meaning |
|---|---|
| 0 | **SHIP** — every invariant inside its threshold |
| 1 | **NO SHIP** — at least one invariant outside its threshold |
| 2 | **INCONCLUYENTE** — nothing was measured, and nothing was concluded |

**INCONCLUYENTE is a real answer, not a failure to run.** The four gates marked
above measure latency, and a latency number taken while another program is using
the machine measures that program. So they time a pure-arithmetic calibration
and read the OS's CPU figure *before* measuring and *again afterwards*; if the
machine was busy at either end, they exit 2 having published nothing and having
moved no threshold. Phase 047 added that second reading after a bursty neighbour
slipped past a single check.

The consequence worth knowing before you run them: **on a busy machine four
gates will report INCONCLUYENTE and that is the correct result.** Close whatever
else is running and run them again. Nothing here will pass by raising a
threshold to meet a number taken under conditions nobody declared.


Development prompts live in `docs/development/`, with a per-phase report for
each completed phase, and `CHANGELOG.md` summarises the releases.
**Supported platforms and versions: [`docs/SUPPORT.md`](docs/SUPPORT.md)** --
what is tested, what is merely probed, and what is neither. Windows 10 and 11 on
x86-64, Python 3.12 to 3.14, and Linux as a non-gating probe of the
platform-independent core. macOS is not tested and not claimed.

Documentation hub with the roadmap status:
[`docs/README.md`](docs/README.md). Architecture: `docs/ARCHITECTURE.md`.
Ranking: `docs/RANKING.md`. Privacy: `docs/PRIVACY.md`. Extending:
`docs/EXTENDING.md`. Release procedure: `docs/RELEASE.md`.
Roadmap: `docs/ROADMAP.md`.
