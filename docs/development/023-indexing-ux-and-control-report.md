# Phase 023 — Indexing UX & control center

Status: implementation complete; final task commit is recorded in the handoff
report.

## Scope delivered

Phase 023 adds a service-owned operational boundary and a separate Tk window;
the main search window remains a small query surface. The implementation is in
`src/universal_search/gui/control_center.py` and is re-exported through
`universal_search.gui.services`.

`ControlCenterService` now owns:

- configured source lifecycle (including a temporarily disconnected root);
- provider key/kind/availability and capability metadata;
- document counts, indexed metadata bytes and supported extensions;
- last local scan, current worker state and the worker's non-invented pending
  description;
- scan failures, extraction/scan errors and inaccessible roots;
- health and corrupt-database status;
- document-intelligence and relationship-graph state;
- database/WAL/SHM plus nearby local storage measurements;
- exclusions from `AppConfig`.

Every mutating or lifecycle method returns `ActionResult`. Results carry an
explicit `data_scope` (`configuration`, `indexed_records` or `derived_data`),
separate indexed/derived counters, and `physical_files=0`. A busy action fails
closed with `code="busy"` instead of starting a concurrent mutation.

## Safety boundary

- `add_source(path)` validates that an existing path is a directory, but allows
  a missing/disconnected path to be configured so it can be retried later.
- `remove_source(path)` removes the root from configuration and context roots;
  it retains indexed rows by default.
- `remove_source(path, delete_indexed=True)` removes only SQLite canonical,
  FTS, usage, intelligence and graph rows below the root. It never calls
  `unlink` on a source path. The test monkeypatches `Path.unlink` and verifies
  the physical file survives.
- FTS, relationship and full rebuilds require `confirm=True`; full rebuild
  states its indexed-record scope, and relationship rebuild states derived-data
  scope. Derived metadata rebuild remains additive and rebuildable.
- `control-center.json` stores only bounded counters, source paths and
  sanitized failure messages. It is included in the privacy inventory and is
  not an index or a source-file store.

## Diagnostics and source-file preservation

`diagnostics.stats` adds `collect_sources`, `collect_derived` and
`collect_storage`; these are aggregate/read-only views and never read document
content. `diagnostics.repair.remove_indexed_source` uses the existing FK-safe
Indexer deletion path, scrubs direct/reverse graph references and dirty
markers, deletes usage rows in the same transaction, and leaves the source
filesystem untouched. Corrupt databases and missing roots are represented as
snapshot facts or typed action failures rather than uncaught UI exceptions.

## GUI and service behavior

The search window adds only one menu route,
`Diagnóstico → Centro de control de indexación…`. The child window has a
source table, human-readable state, storage/failure summary, an optional
technical-details checkbox, source/rescan/failure/pause controls and clearly
separated maintenance actions. Snapshot and action work runs on daemon worker
threads and is rendered by a generation-aware Tk queue; the Tk thread never
performs a scan or rebuild. The existing theme and `Theme`/`fonts` helpers are
reused. Closing the search window still does not stop the independent worker.

`SearchService.control_center()` and
`SearchService.control_snapshot()` provide a no-Tk service path for tests and
other frontends. `BackgroundService` remains the sole reader of worker lock,
status and pause markers; the control center does not duplicate worker state
or touch platform APIs.

## TDD and exact commands/outcomes

The service tests were written before production code. The first RED command
was exactly:

```text
.venv\Scripts\python -m pytest tests\test_control_center.py -q -o addopts=
```

Outcome: collection failed with
`ModuleNotFoundError: No module named 'universal_search.gui.control_center'`,
which was the expected missing-service failure.

Focused GREEN and regression commands run during implementation:

| Command | Outcome |
|---|---|
| `.venv\Scripts\python -m pytest tests\test_control_center.py -q -o addopts=` | **11 passed** (initial service GREEN) |
| `.venv\Scripts\python -m pytest tests\test_control_center.py tests\test_diagnostics.py tests\test_privacy.py tests\test_gui.py tests\test_gui_services.py -q -o addopts=` | **104 passed, 1 skipped** |
| `.venv\Scripts\python -m pytest tests\test_control_center.py tests\test_diagnostics.py tests\test_gui_services.py -q -o addopts=` | **58 passed** after the safety/path refinements |
| `.venv\Scripts\python -m pytest tests\test_control_center.py tests\test_diagnostics.py tests\test_privacy.py tests\test_gui.py tests\test_gui_services.py -q -o addopts=` | **105 passed, 1 skipped** (final focused run) |
| `.venv\Scripts\python -m pyflakes src tests benchmarks evaluation` | exit 0, no output |

The complete suite was also run after the implementation and before final
report synchronization:

```text
.venv\Scripts\python -m pytest tests\ -q -o addopts=
```

Outcome at the final verification checkpoint: **696 passed, 2 skipped in
127.43 s**. A preceding complete run had one known Windows tray-lock timing
flake (`test_process_death_releases_the_tray_process_lock`); the immediately
repeated full run was clean, and the focused test passed 10/10 repetitions.

## Measured service/UI behavior

The deterministic fixtures used for the control-center tests contain two
Markdown/text documents in one source. A snapshot reports two documents,
`.md=1`, `.txt=1`, positive indexed metadata bytes, two intelligence rows and
a positive database size. An added-then-removed source leaves the row count and
physical file unchanged by default. An explicit indexed-data removal reports
one indexed row, derived rows, and zero physical files. A missing root makes
`rescan()` and `retry_failures()` return partial/error `ActionResult`s and
appears as `inaccessible` with a failure in the next snapshot. Holding the
service action lock produces a `busy` result without invoking the indexer.

The real Tk smoke test opened the child window, verified the title
`Centro de control de indexación`, verified it was not the search root, and
closed it cleanly. The search-window suite remained green, including the
existing asynchronous related-document test.

## Files

Runtime:

- `src/universal_search/gui/control_center.py` (new service, models and
  optional Tk view)
- `src/universal_search/gui/services.py` (service-layer re-exports and facade)
- `src/universal_search/gui/app.py` (control-center menu route)
- `src/universal_search/appconfig.py` (control-state path)
- `src/universal_search/diagnostics/stats.py` (source/derived/storage views)
- `src/universal_search/diagnostics/repair.py` (indexed-only source removal)
- `src/universal_search/diagnostics/__init__.py` (exports)
- `src/universal_search/privacy.py` (control-state inventory)

Tests:

- `tests/test_control_center.py` (new)
- `tests/test_diagnostics.py`
- `tests/test_gui.py`
- `tests/test_gui_services.py`
- `tests/test_privacy.py`

Documentation:

- this report
- `docs/README.md`
- `docs/ROADMAP.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/PRIVACY.md`
- `CHANGELOG.md`

## Self-review

- [x] All service actions return `ActionResult`, including busy, inaccessible,
  corrupt-database and confirmation-refusal paths.
- [x] No ordinary source-removal path calls `unlink` on a user file; the
  explicit safety regression passes with `Path.unlink` instrumented.
- [x] Full/FTS/relationship confirmations happen before their underlying
  destructive operation is called.
- [x] Source, derived and physical-file effects are separately represented in
  results and user-facing messages.
- [x] Background worker state is delegated to `BackgroundService`; stale lock,
  pause, hotkey problem and generation filtering remain intact.
- [x] Provider registry metadata is inspected defensively; a provider failure
  does not crash a snapshot.
- [x] Health, corrupt DB, inaccessible roots, stale workers, concurrent actions
  and persisted operational state are covered by focused tests.
- [x] GUI work is queued off the Tk thread; stale generations cannot replace a
  newer render.
- [x] No runtime dependency, network client, source-file deletion, Windows
  service or phases 024–030 code was added.
- [x] Privacy inventory covers the new sidecar and contains no document text or
  query text.
- [x] Focused tests, GUI-safe tests, full tests and pyflakes were run.

## Concerns and limitations

1. A source scan is currently driven by the existing `Indexer`/local scanner;
   provider capability metadata is surfaced, but Phase 024 will formalize
   provider-specific enumeration, identity and cancellation contracts.
2. Source counts perform existence checks for indexed paths to show stale
   entries. They are bounded by the rows belonging to each configured root but
   can still be more expensive than the aggregate health sample on a very large
   disconnected tree.
3. The worker has no durable per-file failure queue. The control center
   persists bounded root-level failures and the latest counters; extraction
   and scan errors are reported as root-level issues until Phase 028 adds
   structured recovery events.
4. The operational sidecar is intentionally best effort. If it cannot be
   written, the canonical index/config operation remains authoritative and a
   warning is logged without document text.
5. Tk rendering is intentionally a small operational surface, not a graph
   visualization. Related-document evidence remains in its existing window.
6. The full rebuild follows the existing repair implementation and may require
   the GUI/worker connections to be released on Windows; the result explains
   `blocked` rather than retrying unsafely.

Commit: the final Phase 023 feature commit is recorded in the task handoff
report and will be squashed by the controller if a temporary task commit is
used.
