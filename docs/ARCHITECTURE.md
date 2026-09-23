# Architecture

    Provider → Extractor → Document → Indexer → Database/FTS5 → Ranking → SearchEngine → CLI / GUI / Background indexer

The same core serves all three frontends; no indexing, search or ranking
logic lives in the UI.

## Layers

- **Providers** (`universal_search/providers/`): enumerate files and read
  content. The local filesystem provider (`local.py`, ignore rules in
  `ignore.py`) plus `onedrive.py` (phase 007): synced OneDrive folders run
  through the same pipeline labelled `onedrive`, and `OneDriveProvider`
  enumerates cloud-only placeholders with availability state — no network
  I/O, content reads gated by `onedrive_download_max_mb`.
- **Extractors** (`universal_search/extractors/`): extension-keyed registry
  (`extract()`, never raises). Text-like files are read bounded (2 M chars)
  with `utf-8-sig`; PDF via `pypdf`; DOCX/XLSX/PPTX via stdlib zip +
  ElementTree; unsupported binaries are never opened (no text, no error);
  failures come back as `ExtractionResult(text, error)`.
- **Domain** (`universal_search/domain/`): `Document` and the stable
  identity `document_id_for(source, path)`.
- **Index** (`universal_search/index/`):
  - `indexer.py` — reconciliation: skip when size + `mtime_ns` (+ source +
    availability) unchanged, batched transactions (`COMMIT_EVERY = 200`)
    over a single reused connection (WAL), deletes reconciled per root,
    optional `delay` between writes as a cooperative resource limit.
  - `database.py` — SQLite metadata + FTS5 (`unicode61`); per-connection
    pragmas (WAL, `synchronous=NORMAL`, 16 MB page cache, 256 MB mmap) and
    a schema-present gate so reconnects skip DDL (migrations still run).
  - `ranking.py` — composite score normalized to [0,1] (filename exact/
    tokens, phrase, BM25, term frequency, proximity, path, doc type, source,
    recency, plus the bounded usage/context boosts of phase 008) — see
    `docs/RANKING.md`. The hot path fuses phrase/counts/positions into one
    pass and caches content/name/path tokens per distinct value, because
    the same candidates are re-scored on every keystroke.
  - `search.py` — candidate pool (`max(limit×5, 50)`) computed first
    (`MATCH` + bm25 + `LIMIT`), then `documents` joined only over the
    pool; source/type filters join inside the pool subquery so a filtered
    search still ranks the whole filtered set before the limit. Scoring,
    context/usage/explain (phase 008), snippets with FTS highlight markers
    stripped for display.
- **Presentation**:
  - `cli.py` — `index | search | gui | onedrive | context | usage | hotkey
    | recent | indexer …` (diagnostics and control) plus `--version`.
  - `gui/` — Tk window (`app.py`, view only) + service layer (`services.py`,
    testable without Tk). `appconfig.py` provides paths/config/logging.
  - `hotkey.py` — global shortcut server (phase 009), hosted by the worker.
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
    gui.pid               live GUI window (phase 009 fast launch)
    gui-show.flag         hotkey pressed → lift and focus the open window

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
- Delivered later phases (OneDrive provider, personal/university contexts,
  usage learning, hotkey + filters, Windows installer, v1.0.0) all plugged
  into the layering above without rewriting the core. Still open: the tray
  indexer and the related-document graph.
