# 021 — Tray & Background Experience

## Objective
Turn the existing background indexer into a reliable Windows notification-area experience without introducing a Windows service, cloud backend, or multi-user daemon.

## Scope
Implement:
- Windows system-tray integration.
- Start, stop, pause and resume indexing.
- Current state, last successful scan and pending work when available.
- Open Universal Search.
- Quick search action.
- Settings/diagnostics access.
- Clean Exit that terminates all owned workers.
- Startup behavior consistent with autostart configuration.
- Single-instance coordination between GUI, tray and indexer.

## Architecture
Keep Windows tray code inside the platform adapter. Domain, indexing, search, database and provider logic remain platform-independent.

Define application-level states such as stopped, starting, indexing, paused, idle, error and stopping. Expose state through an application service rather than making the tray read SQLite internals.

## Reliability
Handle worker disappearance, stale locks/PIDs, database failure, inaccessible sources, termination during indexing and shutdown races. Duplicate indexers must never be created.

## UX
Keep the tray minimal. Avoid notification spam. Notify only about meaningful failures, long-running completion or configuration problems.

## Tests
Add deterministic tests for state transitions, worker coordination, tray commands, shutdown, stale-state recovery and autostart. Add real Windows smoke tests for the platform layer.

## Acceptance
Universal Search can remain in the tray, index in the background, expose current state, pause/resume work, open search instantly and exit without residual workers.

## Ready-to-copy implementation prompt
Implement Phase 021 — Tray & Background Experience. Audit the existing GUI, indexer dispatcher, process coordination and Windows adapter before changing code. Add a native tray experience with reliable state reporting, pause/resume/start/stop controls, single-instance behavior, graceful shutdown and meaningful notifications. Keep the core platform-independent. Add unit/integration tests and Windows smoke tests, run the complete suite, verify packaged behavior, document the architecture and known limitations, and do not push unless explicitly instructed.
