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

Phases 001–006 delivered: foundation, incremental indexing, document
extractors (PDF/DOCX/XLSX/PPTX), ranking engine, Windows desktop GUI and
background indexer — 129 passing tests.

| Fase | Entrega | Estado |
|------|---------|--------|
| 001 | Foundation (SQLite + FTS5, CLI) | ✅ |
| 002 | Incremental indexing + ignore rules | ✅ |
| 003 | Extractors (PDF/DOCX/XLSX/PPTX) | ✅ |
| 004 | Ranking engine | ✅ |
| 005 | Windows desktop GUI | ✅ |
| 006 | Background indexer | ✅ |
| 007 | OneDrive providers | ⬜ |
| 008 | Personal context | ⬜ |
| 009 | Global search | ⬜ |
| 010 | Release | ⬜ |

Detail by phase (prompts + reports): [`docs/README.md`](docs/README.md) ·
by version: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Usage

```bash
pip install -e .
universal-search index C:\Users\me\Documents
universal-search search "meeting notes"
universal-search gui               # desktop window (alias: universal-search-gui)

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
```

Development prompts live in `docs/development/`, with a per-phase report for
each completed phase. Documentation hub with the roadmap status:
[`docs/README.md`](docs/README.md). Architecture: `docs/ARCHITECTURE.md`.
Roadmap: `docs/ROADMAP.md`.
