# 028 — Observability & Recovery

## Objective
Make failures diagnosable and recoverable without telemetry or accidental exposure of document contents.

## Diagnostics
Expose local information about:
- application/runtime version
- schema version
- index statistics
- provider states
- extractor failures
- worker state
- recent operations
- migration state
- derived-data versions
- useful performance counters

## Logging
Use structured, bounded local logs with timestamp, severity, component, event ID and sanitized errors.

By default never log document contents, credentials, access tokens or unnecessary sensitive paths. Query text should not be logged unless explicitly enabled for debugging.

Implement rotation and size limits.

## Recovery
Handle corrupt DB, incomplete migration, interrupted indexing, stale locks, worker crashes, provider disconnects, extractor failures and disk-full conditions.

Recovery must preserve source files and minimize derived-state loss.

## Self-test
Provide diagnostics for DB read/write, FTS integrity, schema compatibility, provider accessibility, extractor availability, worker coordination and storage capacity.

## Diagnostic export
Allow a sanitized support bundle with a clear description of included data.

## Tests
Add failure-injection tests and verify that default logs contain no document content or credentials.

## Acceptance
Users can diagnose and repair common failures without manually editing SQLite files or deleting arbitrary directories.

## Ready-to-copy implementation prompt
Implement Phase 028 — Observability & Recovery. Build a privacy-preserving local diagnostics and recovery layer for database, FTS, migrations, providers, extractors and worker coordination. Add structured bounded logs, self-tests, safe recovery and an explicitly sanitized diagnostic export. Add failure-injection tests and verify default logs contain no document content or credentials. Keep diagnostics local and deterministic. Do not push unless explicitly instructed.
