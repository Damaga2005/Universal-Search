# Phase 002 — Incremental Indexing: Implementation Report

## What was implemented

- **Stable document identity**: `document_id_for(source, canonical path)` (SHA-256). Identity no longer depends on size or mtime, so content changes never change who a document is.
- **Change detection**: the `documents` table stores `size` and `mtime_ns`. Files with unchanged size + mtime are skipped without reading their content at all.
- **`content_hash` in use**: when size/mtime changed but the extracted text hashes to the stored `content_hash`, only metadata is refreshed — the FTS row is untouched.
- **Reconciliation of deletions**: `Indexer.index_root()` stamps every visited row with `last_seen_run`; rows under the reconciled root that were not seen are removed from `documents` **and** `documents_fts`.
- **Ignore rules**: `providers/ignore.py` — `IgnoreRules.defaults()` (VCS, dependency caches, `AppData`, `$RECYCLE.BIN`, `Thumbs.db`, `~$*` Office locks, …) plus user extras via `IgnoreRules.defaults(directories=…, patterns=…)`.
- **Inaccessible paths**: `scan_local()` yields `ScanError` instead of aborting; a failing content reader is caught per-file, the file is still indexed by metadata, and the failure is counted.
- **Indexer statistics**: `IndexStats` with `created / updated / unchanged / deleted / ignored / errors / extraction_errors`, plus `scanned`, `merge()`, `as_dict()` and `summary()`.
- **Transactional updates**: WAL mode, one commit per file, deletions committed at the end of the pass — a crash can never leave a document without its FTS row.
- **The index database never indexes itself** (`search.db`, `-wal`, `-shm` are excluded).

## Technical decisions

1. Discovery is split from extraction: `scan_local()` yields metadata (`FileEntry`), `read_local_content()` is invoked only for changed files. `discover_local()` remains as the eager, full-extraction API used by Foundation tests.
2. Deletion scoping uses index-friendly range predicates (`path > root\` and `path < root` + `U+FFFF`) with `source = 'local'`, so reconciling one root can never touch another root or another provider's documents.
3. `last_seen_run` (a monotonic `time_ns` token per pass) is used instead of an in-memory set of paths — constant memory regardless of tree size.
4. Symlinked directories are never descended into (cycle protection); symlinked files are indexed at their own path.
5. No filesystem watchers yet — a full reconciliation scan is the source of truth (per spec 002).

## Dependencies

None added.

## Limitations

- The first pass after upgrading an existing Foundation-era database re-extracts once (old rows lack `mtime_ns`), then self-heals.
- `discover_local()` still extracts eagerly; incremental callers must use `index_root()`.

## Tests

`tests/test_incremental.py` (18 tests) + `tests/conftest.py` fixtures:
create → index; modify → reindex; rename; delete; two runs → same document count; second run performs zero content reads (tracking reader); ignored directory/file; configurable ignore rules; identical content with new mtime; changed content updates hash; index DB never indexed; unreadable file; inaccessible directory; second root survives first root's reconcile; other-provider documents survive local reconcile; stats summary; stable identity; `discover_local`/`index_root` identity agreement.

## Acceptance criteria

- Unchanged tree: cheap (no content reads) — verified by the tracking-reader test.
- Added / modified / deleted reflected correctly — verified end-to-end with the CLI (`created=1 → unchanged=1 → updated=1 → deleted=1`).
- Tests cover all three cases — yes.
- DB state consistent after errors — per-file commits plus error tests.
- A future background indexer can call the same API — `index_root()` is exactly the API used by phase 006.

## How to run

```bash
universal-search index <folder> [--database <path>]
universal-search search "query" [--database <path>]
.venv\Scripts\python -m pytest tests/test_incremental.py -v
```
