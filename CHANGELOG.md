# Changelog

All notable changes to Universal Search. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project version is
`src/universal_search/__init__.py::__version__`, single-sourced into the
PyInstaller resource and the installer (enforced by `test_release.py`).

## [Unreleased]

### Phase 031 - Robust search: typos and partial words
- Typo and partial-word queries now resolve. `transisto`, `transsistor`,
  `transistorr`, `transltor`, `polarisacion` and two-word queries with one
  misspelled word retrieve nothing on the lexical engine today and the right
  document after this phase.
- The design is blocking plus verification, and the separation is the whole
  point: a bounded trigram fingerprint (at most 64 trigrams per document,
  from its 32 most distinctive words, round-robin, keyed on a small integer
  surrogate) only *proposes* candidates, and every match is decided against
  the document's real text by substring or bounded Damerau-Levenshtein. A
  document the filter loves and the verifier rejects is never returned.
- A second FTS5 `trigram` table was rejected on measured storage cost, not on
  availability: SQLite 3.50.4 does support it, and it emits one term per
  character position, which is 2 million rows for a 2 MB document.
- The layer is fallback-only and filter-disabling, like the semantic layer: any
  non-empty lexical result is returned untouched, `source`/`type` switches it
  off, `--no-fuzzy` opts out, and `explain` reports the token, the rule and the
  distance.
- Schema 9 -> 10. The new tables are derived, versioned, rebuildable lazily
  and removable; `privacy forget` deletes a document's fingerprints and the
  privacy inventory declares all three tables.
- Fixed a real gap found by inspection: `Indexer._delete` removed the document,
  its FTS rows and the graph, but left the phase-026 semantic vectors behind,
  so a document deleted from disk survived as an orphan until someone ran
  `diagnose recover orphan-derived`. Both optional derived tables are now
  scrubbed at delete time, with tests.
- Evidence gate `python -m evaluation.fuzzy_gate`: **6/6 PASS**. Recall@5 of
  0,90 on the typo/prefix set, zero leaks on the must-retrieve-nothing set, no
  change to any lexical metric, 5,8 % extra index size and +5,13 ms of added
  p95 latency. The gate failed twice first, on real defects: a global overlap
  threshold that discarded candidates the verifier had already accepted
  (T1 0,40 -> 0,90 once blocking became per token), 64-character hash keys in
  every posting (T4 34,7 % -> 5,8 % with an integer surrogate), and four
  sources of waste worth 36 ms (one connection per operation, unbounded content
  fetches, per-token accent folding, and a distance computation per word). The
  gate's own latency measurement was also wrong - it took the p95 of paired
  differences, which on a loaded machine measures noise - and now reports each
  engine's p95 separately plus the machine's CPU load.
- Documented out of scope with reasons rather than hidden: typos three edits
  away, words that appear only in a path, and mid-word transpositions in words
  of seven characters or fewer.
- Phase 031 gate: **976 passed, 3 skipped** (979 collected), clean pyflakes,
  `python -m evaluation.gate` PASS (13/13).

## [2.0.0] - 2026-09-30

Major release covering phases 011-030. Version 1.0.0 shipped phases 001-010 on
a schema-3 index with a single-purpose CLI; this release moves the index to
schema 9, adds a background worker with its own lifecycle, a notification-area
controller, a control centre, a document graph, an optional local semantic
fallback, a Windows shell integration, observability with named recovery
cases, and a verified CI plus an executable v2 quality gate.

Upgrade: installing 2.0.0 over 1.0.0 migrates the existing index in place
(schema 3 to 9, additive migrations only). Downgrades are refused on purpose:
an older build will not touch a newer index.

### Phase 030 - Universal Search v2 quality gate
- Added `evaluation/gate.py`, runnable as `python -m evaluation.gate`: thirteen
  local invariants that answer one question with evidence. Dependency budget
  (exactly `pypdf` and `watchdog` at runtime), no socket/HTTP/model import in
  the shipped package, a platform-independent data path, every Windows
  touchpoint declared with a reason, a complete privacy inventory for all 12
  tables, single-sourced versioning, no stray `breakpoint`/`pdb`, every phase
  documented, changelog coverage, documented counts equal to what pytest
  collects, and a closed roadmap.
- One of those checks is behavioural rather than textual: it indexes a real
  tree, runs all four recovery cases plus `privacy forget` and two destructive
  diagnostic repairs, and verifies every user file is byte-identical
  afterwards. A regex cannot tell an application-data `unlink` from a user's
  document; this can.
- `tests/test_v2_gate.py` gates the gate, including negative tests that break
  a temporary tree and assert each check can still say no.
- Found and fixed while writing it: `recovery.py` and `observability.py` used
  `with connection`, which commits but never closes; on Windows the database
  stayed locked after a repair. Both now close deterministically, and dead
  `winreg` code in `background.py` is gone.
- Phase 030 gate: **916 passed, 3 skipped**, clean pyflakes, `python -m
  evaluation.gate` → `VERDICT: PASS` (13/13).

### Phase 029 - Release engineering and CI
- Made the release gates a verified contract: `tests/test_ci_gates.py` fails
  when a gating job tolerates failure, when a mandatory gate disappears from
  the workflow, when the workflow is not read-only/serialized, or when a third
  runtime dependency appears. Runtime dependencies stay exactly `pypdf` and
  `watchdog`; the `build` extra is optional and disjoint.
- Strengthened the packaged gate: the smoke now checks **both** executables,
  runs `diagnose self-test`, `diagnose export` and
  `diagnose recover orphan-derived`, asserts that a destructive recovery is
  refused without `--yes`, and starts the windowed GUI to catch a build that
  dies on import. Hashes and the sanitized support bundle upload with
  `if: always()`.
- Added named per-phase steps to the `quality` job so a regression names
  itself instead of hiding inside a single large run.
- **Fixed two real semantic-layer defects found by that smoke** (test-first,
  `NGRAM_VERSION` 1 -> 2 so incompatible vectors are rebuilt):
  - smoothed idf (`log((n+1)/(df+1)) + 1`). The previous `log(n/df)` is
    exactly zero for every n-gram present in all documents, which silently
    disabled the whole layer on a one-document index.
  - the precision gate now accepts a morphological variant (`receta` /
    `recetas`) when both words are at least five characters, instead of
    requiring exact token equality. Short fragments still count for nothing,
    so the "must retrieve nothing" contract is unchanged.
  - Re-measured on the labelled corpus: hybrid failure R@5 0.762, exact-match
    correctness 1.0, zero top-1 regressions, both nonsense queries still
    empty — no metric moved. `evaluation/semantic_baseline.json` now records
    the model version, the idf formula and the gate rule, and a test fails if
    the committed record describes a model that is no longer shipped.
- Phase 029 quality gate: **891 passed, 3 skipped**, clean pyflakes, and a
  green packaged smoke over the real `.exe` files.

### Phase 028 — Local observability and recovery
- Added `EventRecorder`: bounded JSON Lines with a fixed schema
  (`at`, `component`, `event_id`, `severity`), size-based rotation (1 MB x 3)
  and field-name redaction for credentials, content and query fields. Every
  control-center action now leaves one such event; telemetry failures can never
  break an action.
- Added `universal-search diagnose self-test`, which exercises the database,
  FTS, schema, providers, extractors, worker and free disk space and returns
  the worst verdict (exit 0/1/2).
- Added `universal-search diagnose export --output PATH`, a sanitized support
  bundle that explicitly declares it contains no document content, no query
  text and no credentials.
- Added `universal-search diagnose recover CASE` with exactly four named cases
  (`orphan-derived`, `dirty-derived`, `stale-coordination`, `reset-derived`).
  A live worker owner is never disowned; destructive cases require `--yes`; no
  case can delete a user's source files.
- Exposed the self-test and the support bundle in the control center with
  typed `ActionResult`s (`data_scope="none"`), and declared `events.jsonl` in
  the privacy inventory and `docs/PRIVACY.md`.
- No new dependencies, no network, no telemetry leaving the machine.
- Phase 028 quality gate: **874 passed, 3 skipped**, clean pyflakes.

### Phase 027 — Windows shell integration
- Added `universal-search open PATH` and `universal-search reveal PATH`, both
  delegating to the platform adapter and failing with an actionable code.
- Added per-monitor DPI awareness (`SetProcessDpiAwarenessContext`, with the
  legacy `SetProcessDPIAware` fallback) before Tk creates a window.
- `install.ps1` now registers the reversible per-user Explorer verb and records
  it in the install manifest; `uninstall.ps1 -Remove` removes exactly that verb.
  `-NoExplorer` keeps test/silent installs free of HKCU writes.
- No administrator privileges, new dependencies or duplicated core logic.
- Phase 027 quality gate: **859 passed, 3 skipped**, clean pyflakes.

### Phase 026 — Evidence-first local semantic search
- Extended the fixed evaluation corpus (20 → 27 documents, 13 → 18 labelled
  queries) with the failure classes a semantic layer must fix: a BJT
  synonym that never says "BJT", a voltaje/tension synonym pair, an accented
  variant, a stopword-heavy paraphrase, a malformed binary `.md`, and two
  unrelated-domain distractors.
- Added a deterministic corpus hash, per-query latency, exact-match
  correctness and a failure inventory to the evaluation runner, and a
  `--semantic-baseline` record (`evaluation/semantic_baseline.json`).
- Measured the lexical baseline: MRR 0.833, exact-match correctness 1.0,
  with three total failures (synonym, paraphrase, morphological) and one
  partial (the BJT synonym document is unreachable).
- Shipped an optional, dependency-free, fallback-only local semantic layer
  (`src/universal_search/semantic/`): a versioned character 3-gram TF-IDF
  embedder, a versioned/rebuildable/removable vector index (schema v9) and
  a hybrid engine that consults the layer only when the lexical engine
  returns nothing. A shared-content-word precision gate rejects nonsense
  queries that a fixed cosine threshold cannot.
- The layer cleared a priori evidence gates (material gain, exact-match
  authority, no top-1 regression, no new dependency): hybrid MRR 0.833 →
  0.944, R@5 0.817 → 0.947, exact-match correctness 1.0. A bounded semantic
  boost on a non-empty pool was measured and rejected (it flipped the
  exact-token query "CMOS" and gained nothing on the failure subset).
- Exact filenames, phrases, filters and query operators stay authoritative;
  with the layer disabled or removed, search is exactly the lexical engine.
  No cloud, network, telemetry, external API or runtime dependency.
- Phase 026 quality gate: **852 passed, 3 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 025 — Content extraction v2
- Added a versioned extraction contract: `ExtractionResult` now carries
  `contract_version`, a machine-readable `status` (`ok`, `truncated`,
  `partial`, `no_content`, `error`, `cancelled`), sanitized `warnings`,
  a bounded `structure` summary (title, headings, sheets, slides), a
  `resource_usage` measurement and a `truncated` flag. `text`/`error`
  keep their meaning, so older construction sites are unaffected.
- Added `ExtractionLimits` (input bytes, characters, pages, sheets, slides,
  time, ZIP members, per-part bytes, decompression expansion) with one
  shared enforcement module (`extractors/base.py`): character-budget
  accumulation, chunked bounded ZIP reads, header-level rejection of
  traversal names / oversized parts / expansion ratios, DTD entity
  rejection, and sanitized bounded warnings on every result.
- Hardened PDF extraction: input limit before opening, encrypted files
  rejected (owner-only readable with a warning), page/character/time
  limits with visible truncation, per-page error isolation, empty text
  layers reported as `no_content` instead of empty success, Info title
  preserved, cooperative cancellation between pages.
- Hardened Office extraction: member-count cap, per-member vetting before
  reading, malformed optional parts (shared strings, workbook, single
  sheets/slides) warn and continue instead of failing the whole document,
  empty text layers are `no_content`, docx headings/title, xlsx sheet names
  and pptx slide text preserved as bounded structure, sheet/slide limits
  truncate visibly.
- Preserved text-extractor behavior (UTF-8-sig, replacement, NUL/BOM strip,
  2 M-char cut) — the cut is now flagged instead of silent — and registry
  compatibility: pre-contract single-argument extractors keep working.
- Persisted extraction status/warnings/truncation/contract in the derived
  `document_intelligence` table (schema v8, idempotent migrations): an
  intelligence rebuild preserves the columns and `privacy forget` deletes
  the row, so diagnostics are explainable without outliving the document.
  Diagnostics-only rows (`version = 0`) are not counted as stale analyses
  and do not make `analysis_for`/`related` claim an analysis exists.
- The indexer forwards the cooperative cancel to the content reader and
  never lets one document stop a pass; cloud-only placeholders no longer
  reference an unassigned outcome.
- Phase 025 quality gate: **813 passed, 3 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 024 — Provider expansion
- Formalized the provider contract: streaming `iter_files` with bounded
  error reporting (`MAX_PROVIDER_ERRORS`), a cooperative `CancelToken`, a
  nine-member capability vocabulary (adding `errors`, `watch`, `streaming`)
  and interface-version negotiation in `ProviderRegistry.register`.
- Made the provider key the canonical source discriminator: the uniqueness
  contract is now `(source, path)` via a safe additive schema migration
  (v6 → v7, lossless table rebuild), so two providers may own the same path
  without changing search or ranking semantics. `SourceKind` gains `network`
  and `removable`; the query language, CLI and GUI source filters accept
  them.
- Added `NetworkProvider` (NAS/share) and `RemovableProvider` (USB/SD) as
  mounted-path providers only — standard-library filesystem access,
  configured roots with containment validation, disconnected states
  reported as errors, no network client or credential handling.
- Integrated mixed-provider indexing: `index_root(..., provider=)` and
  `index_sources(...)` with per-provider failure isolation; a provider that
  dies mid-enumeration costs only its own pass and never triggers the
  deletion pass; deletion is scoped to the provider's own rows.
- Phase 024 quality gate: **765 passed, 3 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 023 — Indexing UX & control center
- Added a separate, asynchronous control-center window for configured sources,
  provider availability, counts and supported types, scan/pending state,
  failures and inaccessible roots, exclusions, health, derived-data state and
  measured storage.
- Added typed `ControlCenterService` actions for source add/remove, safe rescan,
  retry, pause/resume and FTS/derived/relationship/full rebuilds. Mutating
  actions fail closed as busy when another action is already running.
- Made the safety boundary explicit in every result: configuration,
  indexed records, derived data and physical files are separate scopes.
  Ordinary source removal never unlinks a user file; indexed-row removal is
  opt-in, and FTS/relationship/full rebuilds require confirmation.
- Added `control-center.json` operational state and privacy inventory coverage;
  it contains bounded counters and sanitized failure text only.
- Phase 023 quality gate: **695 passed, 2 skipped**, clean pyflakes; exact
  commands, measured behavior and limitations are in the phase report.

### Phase 022 — Related-document graph
- Added a versioned local graph of document nodes, bounded term postings and
  explainable relationship edges. Candidate generation uses inverted terms,
  phrases, safe references and directory hints with explicit per-document,
  minimum-similarity and stored-edge caps; it never performs an unbounded
  all-pairs scan.
- Added deterministic full rebuild, graph/preprocessing-version invalidation,
  incremental update refresh and deletion cleanup. The graph is derived beside
  document intelligence and is never read by `SearchEngine` or query ranking.
- Added a service-layer related list and a small GUI evidence window; the
  existing intelligence CLI remains compatible and can remove all graph data.
- Graph persistence was added to the privacy inventory. No cloud service,
  model, network client or new runtime dependency was introduced.
- Fix round: capped posting storage/retrieval and counters, durable dirty
  markers with recoverable lookup repair, FTS content-hash checks, bounded
  incremental maintenance, symmetric explicit references, and worker/queue
  GUI lookup.
- Fix round 2: bounded alias/mention/declared candidate aggregation,
  transactional FTS hash/dirty repair, and direct/reverse reference metadata
  scrubbing on privacy forget and canonical deletion.
- Fix round 3: cursor-paged, batch-capped FTS orphan cleanup with alias-aware
  reverse-reference and dirty-marker scrubbing in one transaction.
- Phase 022 quality gate: **681 passed, 2 skipped**, clean pyflakes; measured
  bounds and performance are recorded in the phase report.

### Phase 021 — Tray & background experience
- Added the optional `universal-search tray` command and a native Windows
  notification-area adapter implemented with standard-library `ctypes`; no new
  runtime dependency, Windows service, cloud component or automatic tray
  launch.
- Added an application-level background state service with `stopped`,
  `starting`, `indexing`, `paused`, `idle`, `error` and `stopping`. The tray
  reads that snapshot instead of SQLite internals.
- Tray menu: present Search / Quick Search / Settings, open the existing
  Diagnostics view, and start, stop, pause or resume the indexer according to
  its current state.
- Independent worker, GUI and tray PID identities. Worker startup now uses a
  unique generation passed to the child plus an OS-held lease; the tray claims
  only the exact generation it presented. Stop markers, cleanup and forced
  termination are owner-scoped, so a replacement or reused PID is never hit.
- Meaningful-only balloons for new errors or error text, new hotkey
  configuration problems, unexpected worker disappearance and long-pass
  completion. User-requested lifecycle actions stay silent; startup evaluation
  waits until the native icon is ready.
- The notification-area adapter restores its icon on Windows `TaskbarCreated`
  after Explorer restarts. Non-Exit command failures are bounded, retained as
  the controller's last result and logged without exception/document text.
- Autostart is unchanged: it still registers `indexer run`, not the tray.
- Current quality gate: **646 passed, 2 skipped**, clean
  pyflakes, successful PyInstaller build and frozen-package smoke. The opt-in
  native smoke posted and deleted a real notification-area icon in the final-fix
  verification; it is skipped by the normal suite.

### Phase 020 — Production release & reliability
- Schema version 5: `schema_migrations` ledger (one row per applied
  version, append-only) and **downgrade refusal** — a build older than the
  index refuses to open it instead of silently rewriting the stamp.
- `SearchDatabase.backup()`: consistent copy via SQLite's own backup API
  (safe while the worker writes), and `diagnose repair all --backup` takes
  one before dropping the index.
- Fixed: `background.start(paths)` passed only the working directory to the
  spawned worker, so a worker started with custom paths published its lock
  and status in the *default* user home. The child now inherits
  `UNIVERSAL_SEARCH_HOME`. Found by the phase-020 crash-recovery test.
- `background.start()` waits 10 s (was 5 s) for the worker handshake: on a
  loaded machine a correct worker was reported as failed.
- `diagnose summary` shows the migration ledger.
- CI: Windows quality + build/smoke gates, plus a non-gating Ubuntu probe
  for the platform-independent core.
- `docs/RELEASE.md` (reproducible checklist and update strategy) and this
  changelog.

### Phase 019 — Provider & extension architecture
- Explicit `DocumentProvider` contract: `key`, `version`, `capabilities`,
  `available()`; six named capabilities and an interface version.
- `ProviderRegistry`: capability negotiation, duplicate refusal, and
  failure isolation (a provider that cannot answer is reported
  unavailable, not propagated).
- `local` and `onedrive` registered with the capabilities they actually
  have — OneDrive deliberately does not claim `content`.
- Extractors expose `infos()`; `universal-search extensions` prints the
  whole registry.
- **No runtime third-party plugins**, documented with reasons in
  `docs/EXTENDING.md`.

### Phase 018 — Privacy & security hardening
- `docs/PRIVACY.md`: prioritised threat model, explicit assumptions and a
  full data inventory; the inventory lives in code
  (`universal_search/privacy.py::INVENTORY`) so it cannot quietly drift.
- `universal-search privacy show | forget`: `forget` removes a document
  and everything derived from it (FTS row, intelligence, usage signals)
  without touching the file.
- **Fixed an availability bug**: FTS5's `snippet()` walks every phrase
  instance, so one document repeating a term 10 000 times took 1.9 s per
  search (>150 s at 2 MB). Snippets are now produced by the linear
  `build_snippet()`; measured cost is now independent of term frequency.
- Log messages capped at 500 characters by a handler filter; status writes
  retry the atomic replace on Windows.
- 24 security regressions, including one that fails if anyone imports
  `socket`/`http`/`urllib`/`requests`/`ftplib`/`smtplib`.

### Phase 017 — UX & accessibility
- Search runs on a worker thread; the Tk main loop drains results through
  a queue, with a generation number so a superseded keystroke can never
  overwrite a newer answer.
- Explicit states: loading (previous results stay visible), no results,
  query error with the reason, and failure.
- Central theme (`gui/theme.py`, light and dark) and user UI scale;
  no colour literals left in widget code.
- Result rows: name, type, last two folders, snippet — no absolute path,
  no "(local)" noise.
- Manual Windows UX/accessibility checklist.

### Phase 016 — Windows integration
- `universal_search.platforms`: every OS touchpoint behind an adapter
  (`WindowsPlatform`, `NullPlatform`) with injectable `startfile`, `popen`,
  `winreg` and `user32`.
- Single-instance window: a second launch presents the running one.
- A hotkey that could not be registered is reported in the worker status.
- Per-user, reversible Start Menu and Explorer integration scripts.
- Ctrl+Space is deliberately **not** the default hotkey: it toggles the
  IME on a large share of Windows installations.

### Phase 015 — Index management & diagnostics
- `universal-search diagnose summary | health | repair …`: twelve health
  checks (`ok`/`warning`/`fatal`) and five repair operations, with the
  destructive ones requiring `confirm=True` in code and `--yes` in the CLI.
- GUI "Diagnóstico" menu with a read-only report and a confirmed full
  rebuild.
- Fixed: `with connection` in sqlite3 commits but does not close, so a full
  rebuild failed on Windows; and `check()` on a missing database used to
  create it.

### Phase 014 — Local document intelligence
- Deterministic, local, rebuildable analysis: language (function-word
  profiles), title/headings/sections, a bounded 24-term vector, 16
  co-occurrence pairs, and related documents by cosine similarity.
- Versioned derived table, incremental rebuild, `intelligence clear`;
  search never reads it and similarity never touches the ranking formula.

### Phase 013 — Search quality & ranking v2
- Labelled evaluation corpus with Precision@K, Recall@K, MRR and a
  committed baseline (`evaluation/baseline.json`).
- Weight headroom measured instead of asserted: a path-only document
  needs `path_match` 7.2× higher to displace a content match; no weight
  was changed because no change was justified.

### Phase 012 — Advanced search language
- Query language in four stages (lexer, parser, validation, translation)
  with phrases, `AND`/`OR`, negation, and `name:`/`path:`/`type:`/
  `source:`/`after:`/`before:`/`size:` filters, shared by CLI and GUI.

### Phase 011 — Performance & scalability
- Reproducible benchmark suite, bounded local metrics, rotating logs and
  database maintenance.

## [1.0.0] — 2026-09 (phases 001–010)
Foundation, incremental indexing, document extractors, ranking engine,
Windows desktop GUI, background indexer, OneDrive providers, personal
context with local usage learning, global search with hotkey and filters,
and the Windows installer.
