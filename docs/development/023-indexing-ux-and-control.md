# 023 — Indexing UX & Control Center

## Objective
Create a coherent user-facing control center for sources, indexing, health, exclusions and derived-data maintenance.

## Scope
Expose:
- configured sources
- provider and availability
- file counts and supported types
- last/current scan
- pending work
- failures and inaccessible paths
- ignored/stale entries
- database health
- derived-data state
- storage usage

Provide actions:
- add/remove source
- rescan
- pause/resume
- retry failures
- rebuild FTS
- rebuild derived metadata
- rebuild relationships
- full rebuild

## Safety
Clearly distinguish stopping a source, deleting indexed records, deleting derived data and deleting physical files. Ordinary source removal must never delete user documents.

## UX
Keep the main search window uncluttered. Put operational detail in the control center. Show human-readable errors and optional technical diagnostics.

## Tests
Cover source lifecycle, rescan, pause/resume, retry, destructive confirmations, inaccessible sources, corrupt DB, concurrent operations and stale workers.

## Acceptance
A non-technical user can see whether files are indexed, identify failures, rescan a source and remove indexed data without risking original files.

## Ready-to-copy implementation prompt
Implement Phase 023 — Indexing UX & Control Center. Reuse existing diagnostics, provider and indexer abstractions. Build a coherent Windows control surface for sources, indexing state, failures, exclusions, health and derived-data maintenance. Make destructive actions explicit and guarantee that indexed-data deletion never deletes source files. Add service and GUI tests, Windows smoke coverage and documentation. Do not push unless explicitly instructed.
