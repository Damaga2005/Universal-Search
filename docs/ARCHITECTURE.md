# Architecture

    Provider → Extractor → Document → Indexer → Database/FTS5 → Ranking → SearchEngine → CLI / GUI / Background indexer

The same core serves all three frontends; no indexing, search or ranking
logic lives in the UI.

## Layers

- **Providers** (`universal_search/providers/`): enumerate files and read
  content. The first provider is the local filesystem (`local.py`, ignore
  rules in `ignore.py`). OneDrive will be added later as another provider
  without changing the search engine.
- **Extractors** (`universal_search/extractors/`): extension-keyed registry
  (`extract()`, never raises). Text-like files are read bounded (2 M chars)
  with `utf-8-sig`; PDF via `pypdf`; DOCX/XLSX/PPTX via stdlib zip +
  ElementTree; unsupported binaries are never opened (no text, no error);
  failures come back as `ExtractionResult(text, error)`.
- **Domain** (`universal_search/domain/`): `Document` and the stable
  identity `document_id_for(source, path)`.
- **Index** (`universal_search/index/`):
  - `indexer.py` — reconciliation: skip when size + `mtime_ns` unchanged,
    one SQLite transaction per file (WAL), deletes reconciled per root,
    optional `delay` between writes as a cooperative resource limit.
  - `database.py` — SQLite metadata + FTS5 (`unicode61`); WAL for
    crash-safe, concurrent reader/writer access.
  - `ranking.py` — composite score normalized to [0,1] (filename exact/
    tokens, phrase, BM25, term frequency, proximity, path, doc type, source,
    recency; usage reserved) — see `docs/RANKING.md`.
  - `search.py` — candidate pool (`max(limit×5, 50)`), scoring, snippets
    with FTS highlight markers stripped for display.
- **Presentation**:
  - `cli.py` — `index | search | gui | indexer …` (diagnostics and control).
  - `gui/` — Tk window (`app.py`, view only) + service layer (`services.py`,
    testable without Tk). `appconfig.py` provides paths/config/logging.
  - `background.py` — background-indexer lifecycle (below).

## Process model (GUI + background indexer)

The GUI and the indexer are **independent processes**; closing the search
window never stops indexing. They coordinate exclusively through files in
the per-user application home (`%LOCALAPPDATA%\Universal Search`,
overridable with `UNIVERSAL_SEARCH_HOME`):

    indexer.lock          PID of the worker — single instance (O_EXCL)
    indexer-status.json   idle / indexing / paused / error, written atomically
    indexer-paused.flag   pause marker
    indexer-stop.flag     graceful stop marker

The worker runs an initial reconciliation, then loops: filesystem
notifications (watchdog) mark the state dirty for responsiveness, while
periodic reconciliation (`indexer_interval_seconds`) guarantees correctness;
events on the application's own files are filtered so the indexer can never
trigger itself. Stop escalates marker → terminate only if ignored. Autostart
writes `HKCU\…\CurrentVersion\Run` through an injected registry (testable).
Indexing logic is never moved into the GUI: the GUI only spawns/controls the
worker and reads its status.

## Data, dependencies, non-goals

- Storage: local SQLite (FTS5). No Elasticsearch, Redis, Docker, cloud, AI
  or external APIs anywhere in the pipeline.
- Runtime dependencies: `pypdf` (PDF), `watchdog` (filesystem events).
  Optional: `pyinstaller` (packaging extra `[build]`).
- Later phases (OneDrive provider, university profile, usage learning,
  installer) plug into the layering above without rewriting the core.
