# 018 — Privacy & Security Hardening

## Objective

Harden Universal Search because its index may contain metadata and extracted content from private documents.

## Threat model

Assess realistic local threats:

- malformed/malicious files
- untrusted paths
- path traversal
- symbolic links/reparse points
- SQL/FTS injection
- corrupt databases
- concurrent processes
- log leakage
- accidental sensitive directories
- multi-user Windows environments
- update/package integrity

Prioritise plausible threats and document assumptions.

## Data inventory

Document exactly what is stored:

- paths/names
- metadata
- extracted content
- hashes
- derived terms
- usage signals if present
- logs

For each: purpose, retention, deletion/rebuild method and whether it ever leaves the machine.

## Hardening

Verify:

- parameterised SQLite access
- safe FTS handling
- path normalisation
- symlink/reparse policy
- safe file reads
- extraction/resource limits
- safe temporary files
- conservative logging
- failure isolation

## Privacy controls

Provide controls to:

- exclude directories
- remove indexed data
- rebuild
- clear optional usage-derived data
- disable optional learning
- inspect storage location

No document contents are sent externally.

## Logging

Default logs must not contain full document contents or sensitive query text unless explicit debug mode is enabled.

## Tests

Add security regressions for SQL/FTS injection, traversal-like paths, inaccessible files, symlink/reparse cases where available, corrupt/oversized documents, log redaction and deletion/rebuild.

## Acceptance

- threat model documented
- local-only data flow verified
- safe logging defaults
- index deletion/rebuild works
- malformed input cannot kill the service
- security regression tests exist
- no unnecessary network dependency

## Ready-to-copy implementation prompt

Implement Phase 018 — Privacy & Security Hardening. Audit the actual application against a realistic local threat model, harden file handling, SQLite/FTS input, extraction, paths, logs, process coordination and privacy controls, document the data inventory/retention model, add security regression tests, and do not introduce telemetry or cloud processing. Do not push.
