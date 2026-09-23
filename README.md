# Universal Search

Local-first universal search for Windows.

Universal Search indexes local files and cloud-backed locations such as OneDrive, then provides fast full-text and metadata search from a lightweight desktop application.

## Principles

- Local-first and privacy-preserving.
- No AI or paid API required.
- Fast incremental indexing.
- Provider-agnostic architecture.
- Search ranking based on filename, content, context and later optional local usage signals.
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
and Explorer integration scripts — **447 passing tests**.

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

Detail by phase (prompts + reports): [`docs/README.md`](docs/README.md) ·
by version: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Usage

```bash
pip install -e .
universal-search --version         # universal-search 1.0.0
universal-search index C:\Users\me\Documents
universal-search search "meeting notes" --limit 20
universal-search search "notes" --source onedrive --type pdf   # filters (009)
universal-search search "notes" --context engineering --explain # context + scoring breakdown (008)
universal-search search "bjt type:txt after:2026-01-01"        # query language (012)
universal-search search "bjt -cmos size:>10KB"                # negation + size filter (012)
universal-search search '"ebers moll" OR "gunn effect"'      # phrase + OR (012)
universal-search gui               # desktop window (alias: universal-search-gui)

# personal layer (all local): contexts, usage learning, hotkey, recents
universal-search context list      # context add|remove|use|relate …
universal-search usage on          # usage show | clear
universal-search hotkey show       # hotkey set ctrl+alt+s | on | off
universal-search recent show       # recent on | off | clear

# local document intelligence (derived data, local-only, rebuildable)
universal-search intelligence rebuild        # language, headings, keywords
universal-search intelligence show informe.pdf
universal-search intelligence related informe.pdf --limit 5

# index health and repair (destructive repairs need --yes)
universal-search diagnose summary
universal-search diagnose health             # exit 0 ok / 1 warnings / 2 fatal
universal-search diagnose repair reconcile C:\Users\me\Docs
universal-search diagnose repair all --root C:\Users\me\Docs --yes

# background indexer — runs independently; closing the GUI does not stop it
universal-search indexer start     # detached worker (single instance)
universal-search indexer status    # idle / indexing / paused / error
universal-search indexer pause     # universal-search indexer resume
universal-search indexer stop
universal-search indexer autostart on
```

The CLI database defaults to `universal-search.db` (`--database <path>` to
change). The GUI and background indexer use the per-user application home
(`%LOCALAPPDATA%\Universal Search\index.db`, logs and status), overridable
with `UNIVERSAL_SEARCH_HOME`. Search results show source, path, file name
and a content snippet; ranking is documented in `docs/RANKING.md`.

### Build the Windows executables

```bash
pip install ".[build]"                       # pyinstaller
python -m PyInstaller packaging/universal-search.spec
# dist/UniversalSearch/UniversalSearch.exe   windowed GUI
# dist/UniversalSearch/universal-search.exe  console CLI + background indexer
powershell -File packaging/make-shortcut.ps1 -TargetExe "dist\UniversalSearch\UniversalSearch.exe"
```

## Development

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e . pytest
.venv\Scripts\python -m pytest

# measurement instruments (deterministic, development only)
python -m benchmarks --profile 1000     # latency / indexing / memory (011)
python -m evaluation                     # labelled corpus, P@K / R@K / MRR (013)
python -m evaluation --flip recency diagrama   # headroom of one ranking weight
```

Development prompts live in `docs/development/`, with a per-phase report for
each completed phase. Documentation hub with the roadmap status:
[`docs/README.md`](docs/README.md). Architecture: `docs/ARCHITECTURE.md`.
Ranking: `docs/RANKING.md`. Roadmap: `docs/ROADMAP.md`.
