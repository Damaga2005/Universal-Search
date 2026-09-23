# 015 — Index Management & Diagnostics

## Objective

Make Universal Search able to explain, validate, repair and maintain its own index.

## Diagnostics

Expose:

- indexed document count
- counts by type/source
- index/database size
- last successful scan/update
- pending work
- extraction failures
- inaccessible/ignored files
- stale entries
- schema version
- indexer/background-worker state
- application version

## Health checks

Check:

- database accessibility
- schema consistency
- FTS consistency
- orphan metadata
- orphan FTS rows
- duplicate identities
- stale documents
- invalid paths
- failed extraction records
- lock/process state

Distinguish warnings from fatal corruption.

## Repair

Provide safe operations for:

- reconcile
- rebuild FTS
- re-extract a document
- rebuild derived metadata
- complete index rebuild

Destructive operations require explicit confirmation in GUI and explicit flags in CLI.

## CLI and GUI

Add diagnostic commands following existing CLI conventions and a useful GUI status/diagnostics view.

Logs must be local, bounded/rotated, timestamped and severity-tagged, and must avoid document-content leakage by default.

## Tests

Cover healthy state, missing DB, orphan rows, duplicate entries, failed extraction, repair/rebuild, log rotation and CLI/GUI diagnostics.

## Acceptance

- user can determine index health
- repair/rebuild is safe and tested
- full rebuild is possible
- diagnostics do not leak content by default
- degraded states have tests

## Ready-to-copy implementation prompt

Implement Phase 015 — Index Management & Diagnostics. Audit DB/indexer/background state, add health checks, statistics, repair/rebuild operations, safe bounded logging and a useful GUI diagnostics surface. Make destructive actions explicit and tested. Preserve search semantics. Update docs and tests. Do not push.
