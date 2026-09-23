# 020 — Production Release & Reliability

## Objective

Prepare Universal Search for a real production release after the preceding phases, regardless of which optional features were ultimately implemented.

## Versioning

Introduce one authoritative application version exposed through:

- CLI
- GUI
- diagnostics
- packaged executable
- logs where appropriate

Define database/schema compatibility.

## Database migrations

Implement explicit migration support with:

- schema version
- migration history
- safe forward migrations
- safe failure
- backup before destructive changes where appropriate
- no silent data loss
- rebuild fallback where safe

Test upgrades from representative prior schemas.

## Packaging

Produce a Windows release build and document:

- build environment
- dependencies
- PyInstaller/build configuration
- output layout
- installer strategy
- signing status
- version metadata

If an installer is introduced, test install/uninstall/shortcuts/startup and make user-data removal explicit.

## Updates

Design a safe update strategy or explicitly document manual updates.

Never execute arbitrary downloaded update code without authenticity/integrity verification.

## Reliability

Test:

- missing/corrupt DB
- interrupted indexing
- process termination
- stale locks
- duplicate worker startup
- extraction failures
- deleted sources
- upgrade
- downgrade refusal where necessary
- clean shutdown
- crash recovery

## CI

Add CI for:

- unit/integration tests
- packaging/build
- lint/type checks where adopted
- migration tests
- smoke tests

Keep platform-specific tests isolated appropriately.

## Release checklist

Create a reproducible checklist covering:

- version bump
- changelog
- tests
- build
- smoke test
- installer
- clean install
- upgrade
- uninstall
- migrations
- documentation
- artifact hashes
- release notes

## Final quality gate

Run:

- complete test suite
- packaged Windows smoke test
- clean-install test
- upgrade/migration test
- background-indexer test
- search-quality regression suite
- security regression suite
- performance benchmark suite

Record exact results.

## Acceptance

A clean Windows machine should be able to install Universal Search, launch it, configure/index a directory, search, close/reopen it, retain the index and update safely.

The release process must be reproducible by another developer following the documentation.

## Ready-to-copy implementation prompt

Implement Phase 020 — Production Release & Reliability. Audit the complete repository and make the application releasable: authoritative versioning, database migrations, reliable packaging, Windows installation/uninstallation, safe update strategy, crash recovery, CI and reproducible release documentation. Run tests, benchmarks, security checks and packaged smoke tests. Document known limitations. Do not push unless explicitly instructed.
