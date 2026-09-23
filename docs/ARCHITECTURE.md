# Architecture

    Provider → Extractor → Document → Indexer → Database/FTS5 → Query language → Ranking → SearchEngine → CLI / GUI / Background indexer

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
  - `query/` — the search query language in four stages (spec 012):
    `lexer.py` (tokens), `parser.py` (immutable AST), `validate.py` (value
    normalization + structural policy) and `translate.py` (`QueryPlan`:
    MATCH string, hoisted negations, ranking terms and bound SQL clauses).
    The MATCH string is assembled only from quoted word runs and
    whitelisted columns, so user text can never inject FTS5 syntax, and
    every SQL value stays a bound parameter.
  - `ranking.py` — composite score normalized to [0,1] (filename exact/
    tokens, phrase, BM25, term frequency, proximity, path, doc type, source,
    recency, plus the bounded usage/context boosts of phase 008) — see
    `docs/RANKING.md`. Weights live in one frozen `RankingWeights`; the hot
    path fuses phrase/counts/positions into one pass and caches
    content/name/path tokens per distinct value, because the same
    candidates are re-scored on every keystroke.
  - `search.py` — queries are translated by `query/` before any I/O, so a
    malformed query never opens the database nor reaches the metrics
    recorder. Candidate pool (`max(limit×5, 50)`) computed first
    (`MATCH` + bm25 + `LIMIT`), then `documents` joined only over the
    pool; source/type filters join inside the pool subquery so a filtered
    search still ranks the whole filtered set before the limit. A query
    made only of filters (`type:pdf`, `size:>10MB`) skips MATCH/bm25 and
    returns those rows newest first with score 0.0. Scoring,
    context/usage/explain (phase 008), snippets with FTS highlight markers
    stripped for display.
- **Presentation**:
  - `cli.py` — `index | search | gui | onedrive | context | usage | hotkey
    | recent | indexer …` (diagnostics and control) plus `--version`.
  - `gui/` — Tk window (`app.py`, view only) + service layer (`services.py`,
    testable without Tk). `appconfig.py` provides paths/config/logging.
    The service turns a `QueryError` into `last_query_error` so the window
    shows the reason instead of a bare empty list.
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

## Measurement instruments (development-only)

Two top-level packages sit outside the shipped application and are used to
justify changes instead of asserting them. They are not importable from the
wheel and never run at runtime:

- `benchmarks/` (phase 011) — `python -m benchmarks`: latency, indexing and
  memory baselines over a deterministic synthetic tree.
- `evaluation/` (phase 013) — `python -m evaluation`: a labelled corpus
  (no private document), Precision@K / Recall@K / MRR, the per-signal
  breakdown of every result, and `--flip WEIGHT QUERY` to report whether a
  weight is load-bearing and how much headroom it has.
  `evaluation/baseline.json` is the committed regression baseline.

Both are deterministic (no RNG, no clock in the data) and the test suite
imports them, so `pythonpath = ["."]` is set in the pytest configuration.

## Data, dependencies, non-goals

- Storage: local SQLite (FTS5). No Elasticsearch, Redis, Docker, cloud, AI
  or external APIs anywhere in the pipeline.
- Runtime dependencies: `pypdf` (PDF), `watchdog` (filesystem events).
  Optional: `pyinstaller` (packaging extra `[build]`).
- Delivered later phases (OneDrive provider, personal/university contexts,
  usage learning, hotkey + filters, Windows installer, v1.0.0) all plugged
  into the layering above without rewriting the core. Still open: the tray
  indexer and the related-document graph.
