# Phase 007 — OneDrive Integration: Implementation Report

## What was implemented

- **`providers/onedrive.py`** (new module) — all OneDrive knowledge lives in the
  provider layer, next to the local provider:
  - **Phase A — synced files**: `is_onedrive_path()` / `source_for_path()`
    classify any file under a detected OneDrive root as `SourceKind.ONEDRIVE`
    so it runs through the **same local pipeline** but is labelled `onedrive`
    everywhere (search results, CLI, GUI preview).
    Detection order (deterministic across worker/CLI/GUI processes):
    1. environment variables `OneDrive`, `OneDriveConsumer`, `OneDriveCommercial`;
    2. `%USERPROFILE%\OneDrive*` directories;
    3. fallback: any path component starting with `OneDrive`.
    Comparison is purely textual (no `resolve()` per file), so classifying a
    large tree costs nothing measurable.
  - **Phase B — cloud-only files**: `OneDriveProvider` (satisfies the
    `DocumentProvider` protocol) discovers files that are **not physically
    downloaded** by reading the Windows placeholder attributes
    (`FILE_ATTRIBUTE_OFFLINE`, `RECALL_ON_OPEN`, `RECALL_ON_DATA_ACCESS`)
    from the same `stat()` the scan already performs — zero extra syscalls,
    zero network traffic. It exposes metadata + availability state
    (`available / cloud_only / unavailable`) via `OneDriveFile`.
  - `allow_content_read()` is the **single decision point** for reading
    content: locally available files always; cloud-only files only when the
    user configured an explicit `onedrive_download_max_mb > 0` **and** the
    file fits the limit (huge files are never silently downloaded);
    `unavailable` never.
  - `OneDriveProvider.read_content()` mirrors that rule with explicit,
    human-readable refusals instead of implicit reads.
- **Indexer integration** (`index/indexer.py`):
  - source is classified per file (`source_for_path`) and **availability is
    part of the unchanged-check** (size + mtime + source + availability), so
    reclassifying a folder (e.g. `local → onedrive`) rebuilds the row instead
    of skipping it, and hydration ("free up space" reversed) re-reads content;
  - cloud-only placeholders are indexed as **metadata only**: content never
    read, `content_hash = NULL`, availability stored, counted as
    `stats.cloud_only` (new counter, shown in summaries/status);
  - explicit download converts the row to `available` on success;
  - **latent bug fixed**: `_delete_missing` filtered `source = local`, which
    would have leaked stale OneDrive rows forever — reconciliation now deletes
    by path range regardless of source (a path belongs to exactly one source).
- **Database**: `documents.availability TEXT NOT NULL DEFAULT 'available'`
  added both to the fresh schema and to the additive `MIGRATIONS` list, so
  pre-existing databases upgrade in place with data preserved.
- **Search/GUI**: `SearchResult` carries `availability` (and `document_id`);
  the GUI preview shows `☁ solo en OneDrive (sin descargar)` for cloud-only
  results alongside the existing `[onedrive]` source label.
- **Config**: `onedrive_download_max_mb` (default `0.0` = never download),
  persisted in `config.json`, honored by the background worker and
  `index --onedrive-download-mb`.
- **CLI**: `universal-search onedrive [--root PATH]` prints each detected
  root with file / cloud-only / unreadable counts.

## Technical decisions

1. **No cloud backend, no SDK, no API** (spec: never upload, no proprietary
   backend): discovery and availability come purely from the local sync
   namespace the OneDrive client already maintains. The module performs no
   network I/O of any kind.
2. **Content protection at the read boundary**: opening a placeholder would
   trigger a download, so the gate lives *before* any file open, in one
   shared predicate — not scattered checks.
3. **Classification is textual and env-driven**, never machine- or
   process-specific, so the worker, CLI and GUI always agree on a file's
   source (disagreement would corrupt document identity).
4. **Ranking untouched** — `SOURCE_SCORES` already weights `onedrive`
   identically to `local`; a test asserts every ranking signal is equal for
   both sources ("OneDrive-specific code must not leak into ranking").
5. **The unchanged-check must include source and availability** — otherwise a
   reclassified file keeps its old source (and its old document id) forever,
   and a hydrated placeholder stays content-less forever.

## Dependencies

None added (zero new packages for this phase).

## Limitations

- Cloud-only discovery only covers files that exist in the local namespace as
  placeholders (this is exactly what the sync client exposes offline). Files
  that never synced to this machine would require the Microsoft Graph API —
  an external API, explicitly out of scope.
- Placeholder attribute detection requires Windows (on other platforms
  `st_file_attributes` is absent → everything is `available`; tests cover the
  pure attribute mapping directly).
- Detection is heuristic by nature: a non-OneDrive folder whose name starts
  with `OneDrive` is classified as OneDrive (documented, deliberate fallback).
- An `unavailable` (stat fails) file surfaces as a `ScanError` during
  indexing; the three-state availability is exposed by the provider API.

## Tests

`tests/test_onedrive.py` (**15 tests**) + 1 GUI test in `tests/test_gui.py`:

- detection via env var / path component / `USERPROFILE` glob; cache follows
  environment changes;
- attribute→availability mapping for every Windows bit; `availability_of` on
  a real file;
- `allow_content_read` rules (available / no-limit / covered / over-limit /
  unavailable);
- phase A: synced tree indexed with `onedrive` label and correct search
  result; **local → onedrive reclassification rebuilds the row** (source and
  document id change, search label flips); onedrive rows (and FTS rows) are
  deleted on reconcile — regression for the `_delete_missing` fix;
- phase B: cloud-only file → metadata only, reader **never called**, hash
  `NULL`, availability stored, second pass `unchanged`, search still returns
  the file flagged `cloud_only`; explicit limit enables exactly one bounded
  read; 50 MB file never downloaded even with a 10 MB limit;
- provider: discovery yields `OneDriveFile` with availability, protocol
  compliance, explicit read refusals (no limit / over limit / unavailable)
  and the allowed read;
- ranking: every signal identical for `local` vs `onedrive` sources;
- GUI: cloud-only result preview shows the `☁` marker.

Full suite: **145 passed**.

## Acceptance criteria

- Synced OneDrive files are searchable — phase A tests + demo (below).
- Search results identify OneDrive — `[onedrive]` label + GUI marker + tests.
- Cloud-only files represented safely — metadata index + availability state +
  tests (reader never invoked implicitly).
- Indexing handles offline/unavailable content gracefully — placeholder skip,
  `unavailable` refusals, `cloud_only` counter, no exceptions (tests).
- OneDrive-specific code does not leak into ranking — `test_onedrive_source_does_not_leak_into_ranking`.

## How to run / manual verification performed

```bash
universal-search onedrive                     # detected roots + availability
universal-search onedrive --root "C:\...\OneDrive - X"
universal-search index "C:\...\OneDrive - Demo" --database idx.db
universal-search search examen --database idx.db   # -> [onedrive] examen.md
.venv\Scripts\python -m pytest tests/test_onedrive.py -v
```

Real-session demo output: index of `Temp\od-demo\OneDrive - Demo` printed
`created=1 … cloud_only=0`; `search "examen"` returned
`[onedrive] examen.md` with a highlighted snippet; `onedrive --root` reported
`1 file(s), 0 cloud-only, 0 unreadable`.
