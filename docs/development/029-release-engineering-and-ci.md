# 029 — Release Engineering & CI

## Objective
Make Universal Search reproducible enough that another developer can build, test and verify the same release without undocumented machine state.

## Version authority
Define one source of truth for application, package, executable, installer and database compatibility versions. Prevent drift.

## CI
Add appropriate gates for:
1. dependency installation
2. unit tests
3. integration tests
4. warnings-as-errors
5. static checks
6. migration tests
7. benchmark smoke tests
8. packaging
9. install/start/search smoke tests
10. artifact validation

Keep Windows-only checks on Windows runners.

## Reproducibility
Document supported Python/Windows versions, dependency constraints, build commands, artifact names, hashes, environment requirements and clean-build procedure.

## Release artifacts
Validate GUI executable, CLI/indexer executable, installer when present, uninstall behavior, documentation, release notes and checksums.

If signing is unavailable, state that fact. Never claim signatures or verification that were not actually performed.

## Regression gates
Fail release validation on test regressions, packaging failure, migration incompatibility, startup/search failure, version drift or security failure.

## Acceptance
A fresh checkout can be built by following documentation and produces the expected artifacts.

## Ready-to-copy implementation prompt
Implement Phase 029 — Release Engineering & CI. Audit build, packaging and versioning, then establish reproducible CI gates for tests, warnings, migrations, packaging, smoke checks and artifacts. Establish authoritative version metadata and documented supported environments. Validate clean builds and uninstall behavior, publish checksums where appropriate, and never claim signing or verification that has not occurred. Run the pipeline locally where possible and document platform-specific limitations. Do not push unless explicitly instructed.
