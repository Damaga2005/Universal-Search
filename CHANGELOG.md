# Changelog

All notable changes to Universal Search. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project version is
`src/universal_search/__init__.py::__version__`, single-sourced into the
PyInstaller resource and the installer (enforced by `test_release.py`).

## [Unreleased]

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
