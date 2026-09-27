# Architecture

    Provider → Extractor → Document → Indexer → Database/FTS5 → Query language → Ranking → SearchEngine → CLI / GUI / Tray / Background indexer

The same core serves every frontend; no indexing, search or ranking logic
lives in the UI.

## Layers

- **Providers** (`universal_search/providers/`): enumerate files and read
  content. The local filesystem provider (`local.py`, ignore rules in
  `ignore.py`) plus `onedrive.py` (phase 007): synced OneDrive folders run
  through the same pipeline labelled `onedrive`, and `OneDriveProvider`
  enumerates cloud-only placeholders with availability state — no network
  I/O, content reads gated by `onedrive_download_max_mb`. Phase 024
  formalised the contract: streaming `iter_files` with bounded errors and a
  cancel token, a nine-member capability vocabulary, interface-version
  negotiation in the registry, and provider-namespaced identity — the
  canonical uniqueness contract is `(source, path)`, so two providers may
  own the same path. `network.py` and `removable.py` add NAS/removable
  sources as mounted-path providers only (UNC/mapped share or drive letter;
  no network client, root containment validated, disconnected states
  reported as errors). All declare their capabilities and register in
  `registry.py` (spec 019): `SearchEngine`, `ranking.py` and the GUI never
  learn which provider produced a document. OneDrive is a *layer* over the
  local scanner (Windows attributes), not a second scanner.
  `universal-search extensions` prints the registry; the full contract and
  the deliberate absence of runtime plugins are in `docs/EXTENDING.md`.
- **Extractors** (`universal_search/extractors/`): extension-keyed registry
  (`extract()`, never raises, `infos()` for inspection). Text-like files
  are read bounded (2 M chars) with `utf-8-sig`; PDF via `pypdf`;
  DOCX/XLSX/PPTX via stdlib zip + ElementTree; unsupported binaries are
  never opened (no text, no error); failures come back as
  `ExtractionResult(text, error)`.
- **Domain** (`universal_search/domain/`): `Document` and the stable
  identity `document_id_for(source, path)`.
- **Index** (`universal_search/index/`):
  - `indexer.py` — reconciliation: skip when size + `mtime_ns` (+ source +
    availability) unchanged, batched transactions (`COMMIT_EVERY = 200`)
    over a single reused connection (WAL), deletes reconciled per root,
    optional `delay` between writes as a cooperative resource limit. Phase
    024: `index_root(..., provider=)` enumerates through the provider and
    stores the provider key as `documents.source`; `index_sources` isolates
    per-provider failures (a provider that dies mid-enumeration costs only
    its own pass and never triggers the deletion pass); deletion is scoped to
    the provider's own rows.
  - `database.py` — SQLite metadata + FTS5 (`unicode61`); per-connection
    pragmas (WAL, `synchronous=NORMAL`, 16 MB page cache, 256 MB mmap) and
    a schema-present gate so reconnects skip DDL (migrations still run).
    Schema version 6: additive migrations, a `schema_migrations` ledger,
    consistent `backup()` and **downgrade refusal** — an index written by
    a newer build raises `UnsupportedSchemaVersion` instead of being
    silently re-stamped (spec 020).
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
- **Intelligence** (`universal_search/intelligence/`, specs 014 and 022): a
  pure, deterministic analysis pipeline over already-indexed text —
  `language.py` (function-word profiles, shared stop-word list),
  `structure.py` (title, headings, sections), `keywords.py` (bounded term
  vector and co-occurrence pairs), `analysis.py` (orchestration and the
  `DocumentRecord` boundary), and `store.py` (versioned
  `document_intelligence` rows and incremental rebuild). `graph.py` owns the
  optional derived relationship graph: bounded normalized terms, weighted
  keyword/title/heading/phrase/directory/reference/provider signals, stored
  evidence, graph and preprocessing versions, capped inverted postings,
  fixed-size alias/mention candidate aggregation, durable dirty markers,
  transactional FTS hash repair, cursor-paged batched orphan cleanup,
  deterministic full rebuilds and bounded incremental repair.
  `GraphStore.related()` is the
  only graph lookup; `SearchEngine` and ranking never read these tables.
  Derived data only: **search never reads it**, deleting it costs nothing, and
  document similarity is computed without the ranking formula.
- **Diagnostics** (`universal_search/diagnostics/`, specs 015 and 023): read-only
  by default — `stats.py` (`collect`: counts, sizes, schema, worker state;
  `collect_sources`/`collect_derived`/`collect_storage` for the control
  center), `health.py` (`check`: twelve checks, `ok`/`warning`/`fatal`) and
  `repair.py` (maintenance plus `remove_indexed_source`, which deletes only
  indexed/derived rows and never a source path). Destructive operations raise
  or return an explicit confirmation result. Reports read metadata only,
  never document content, and every connection is closed deterministically
  because Windows will not delete a locked database.
- **Platform** (`universal_search/platforms/`, specs 016 and 021): the seam
  between the platform-independent core and Windows. `Platform` declares the
  operations (open, reveal, autostart, notify); `WindowsPlatform` implements
  them with every OS touchpoint injectable (`startfile`, `popen`, `winreg`,
  `user32`); `NullPlatform` answers honestly elsewhere. `worker.py` owns the
  worker's standard-library OS lease and generation-safe process handle. The
  optional tray adds `TrayBackend`, `WindowsTray` and `NullTray`: the Windows
  adapter owns the hidden `ctypes` window, `Shell_NotifyIconW`, Explorer-restart
  recovery, menu, tooltip, balloon, timer and cleanup, while its DLL handles are
  injectable for deterministic tests. The core imports no platform
  implementation directly, so the suite runs and passes on any OS. On Windows,
  `worker.py` also queries the process creation FILETIME from the same handle used
  for termination; Linux uses a pidfd when the runtime exposes one.
- **Privacy** (`universal_search/privacy.py`, spec 018): the data inventory
  as data — what is stored, why, how long, and how to delete it — plus
  `forget()`, which removes a document and everything derived from it while
  leaving the file alone. See `docs/PRIVACY.md` and `docs/EXTENDING.md`
  (adding providers and extractors).
- **Presentation**:
  - `cli.py` — `index | search | gui | tray | onedrive | context | usage
    | hotkey | recent | indexer | intelligence | diagnose | privacy …`
    (diagnostics and control) plus `--version`.
  - `gui/` — Tk search window (`app.py`, view only) + service layer
    (`services.py`, testable without Tk) + the separate `control_center.py`
    operational window. `appconfig.py` provides paths/config/logging and a
    small `control-center.json` sidecar for scan counters and sanitized
    failures. The service turns a `QueryError` into `last_query_error` so
    the window shows the reason instead of a bare empty list. Searches and
    control-center snapshots/actions run on worker threads and are delivered
    through generation-aware queues; the Tk main loop only renders. The
    control center reuses `BackgroundService`, provider metadata,
    diagnostics, `Indexer`, intelligence and graph stores. It labels the
    safety scope of every result (`indexed_records`, `derived_data` or
    `configuration`) and never calls `unlink` on a source path. The main
    search window remains a small query surface; sources, health, storage,
    failures and maintenance live in the separate window. `theme.py` owns
    every colour and font size, `rows.py` the result-line format.
  - `hotkey.py` — global shortcut server (phase 009), hosted by the worker.
    A hotkey that cannot be registered is reported in the worker status
    file, so a dead shortcut is visible instead of silent. The window is
    single-instance: a second launch presents the first one. The same
    file-signalling path (`gui.pid`, `gui-show.flag` and
    `gui-diagnostics.flag`) lets the tray present Search, Settings or
    Diagnostics without creating a second window.
  - `background.py` — background-indexer lifecycle (below).
  - `background_service.py` — application-level state and actions. It
    reconciles the worker lock/PID, atomic status, pause and stop markers
    into an immutable `BackgroundStatus`; it never reads SQLite internals.
  - `tray.py` — platform-independent tray controller. It owns state-specific
    menu construction, command dispatch, notification transitions, the tray
    PID claim and exact startup-generation shutdown ownership. `run_tray()`
    selects `WindowsTray` only on Windows and `NullTray` elsewhere.

## Process model (GUI + indexer + optional tray)

The GUI search window, worker and optional tray are **independent per-user
processes**. The control center is a same-process `Toplevel` over the same
service objects; it is not a second worker and acquires no source-file
ownership.
Closing the search window never stops indexing, and exiting the tray does not
stop a worker that it did not start. They coordinate through files in the
per-user application home (`%LOCALAPPDATA%\Universal Search`, overridable with
`UNIVERSAL_SEARCH_HOME`):

    indexer.lock                          worker PID; preserved phase-006 text contract
    indexer.lock.lease                    persistent sidecar with an OS-held worker lease
    indexer.lock.owner                    worker PID + generation + creation identity
    indexer.starting.<pid>.<hash>.claim   one atomic PID-scoped starter claim
    indexer.starting.<pid>.<hash>.tmp     recoverable pre-replace claim, if interrupted
    indexer.starting.lease                persistent OS-held claim-recovery lease
    indexer-status.json                   worker state + PID/generation, written atomically
    indexer-paused.flag                   pause marker
    indexer-stop.<hash>.flag              one generation's graceful stop request
    indexer-stop.flag                     legacy generationless stop request
    gui.pid                  live GUI singleton identity
    gui-show.flag            present Search/Settings/Quick Search
    gui-diagnostics.flag     present the existing Diagnostics view
    control-center.json      scan counters and sanitized source failures
    tray.pid                 last tray PID, retained for diagnostics
    tray.pid.lock            OS-backed exclusive tray ownership

There are **three independent PID-based role identities**, one per role. The
worker keeps the plain-PID `indexer.lock` contract, but holds a separate OS
lease for its entire lifetime. Every stale check and replacement happens only
while that lease is held, so two starters cannot both delete and recreate the
same stale PID file. `indexer.lock.owner` adds the generation presented through
the child environment and, on Windows, the process creation FILETIME.
`Popen.pid` is never ownership proof, which matters when a Windows venv launcher
PID differs from the worker PID. Modern startup claims are written to a
PID/generation-scoped temporary path and atomically replaced into a final claim.
If publication fails, rollback targets only that PID/generation's final and
temporary paths. Cleanup retries deletion three times; a remaining path produces
an actionable fail-closed result rather than a silent orphan. Recovery under
`indexer.starting.lease` removes only a path whose embedded PID is demonstrably
dead; malformed legacy state with no recoverable identity fails closed. The
GUI's record remains a liveness-checked identity. The tray combines
its diagnostic PID file with a held OS lock. No role claims another role.

`BackgroundService` derives `stopped`, `starting`, `indexing`, `paused`,
`idle`, `error` and `stopping` from those worker signals. A dead PID is stopped
(with stale-lock detail), and an owner-scoped stop marker outranks worker state
while that exact generation lives. Current status fields are trusted only when
PID and generation both match the live lock owner; stale diagnostics disappear,
while explicitly named `last_scan_*` values remain historical. Pending work is
described by mode, never as an invented queue count.

`TrayController` polls that immutable snapshot and asks `TrayBackend` to update
the tooltip/menu. It can start, stop, pause and resume the existing worker. A
tray claims a started worker only after the child presents the exact generation
that the tray generated. Exit and Stop pass both PID and generation. Modern
workers use a different `indexer-stop.<hash>.flag` path per generation, so an old
worker can never unlink a replacement's request; the generic marker remains only
for generationless legacy workers. If graceful stop times out, termination is
allowed only after the opened OS identity matches the owner generation and its
creation identity (Windows FILETIME) or is a kernel-bound pidfd, and the owner
sidecar is rechecked immediately before termination. A replacement is reported
and left running. On a non-Windows runtime without pidfd, cooperative stop is
preserved and forced termination deliberately fails after the bounded wait.
The native adapter removes the icon, timer, hidden window and registered class
and releases the tray lock. Settings and diagnostics remain in the existing GUI:
tray commands signal it or launch it, and the GUI consumes the request file.

The worker runs an initial reconciliation, then loops: filesystem notifications
(watchdog) mark the state dirty for responsiveness, while periodic
reconciliation (`indexer_interval_seconds`) guarantees correctness; events on the
application's own files are filtered so the indexer cannot trigger itself.
Stop escalates an owner-scoped marker → a process-handle termination only if
ignored and only after creation identity plus owner-sidecar verification. The tray
adapter handles `TaskbarCreated` by re-adding its icon after
Explorer restarts. Autostart still writes
`HKCU\…\CurrentVersion\Run` with `indexer run`; it does not start the optional
tray. Indexing logic is never moved into the GUI or tray: controllers only
delegate lifecycle actions and read the service snapshot.

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

## Continuous integration and release

`.github/workflows/ci.yml` gates Windows on quality (pyflakes, the full
suite, migrations and reliability, search-quality baseline, security
regressions), then builds the package with PyInstaller, runs a smoke test
against the frozen executables and publishes their SHA-256 hashes. A
**non-gating** Ubuntu job runs the platform-independent core as a probe of
the seam described above.

`docs/RELEASE.md` is the reproducible release procedure: single-sourced
versioning, the migration and downgrade rules, the build, the installer,
the manual update strategy (no auto-updater, by decision), the release
checklist, the final quality gate with recorded results, and the known
issues. `CHANGELOG.md` records what changed in phases 011–022.

## Data, dependencies, non-goals

- Storage: local SQLite (FTS5). No Elasticsearch, Redis, Docker, cloud, AI,
  external API, telemetry or Windows service anywhere in the pipeline.
- Runtime dependencies: `pypdf` (PDF), `watchdog` (filesystem events). The
  native tray uses `ctypes` from the standard library and added no dependency.
  Optional: `pyinstaller` (packaging extra `[build]`).
- The tray is an optional per-user UI over the independent worker, not a
  privileged daemon. Settings and diagnostics stay in the existing window;
  autostart remains the worker's `indexer run` command.
- Delivered phases plug into this layering without moving core logic into a
  frontend. Still open: planned phases 023–030.
