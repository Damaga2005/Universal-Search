# Development Prompt 002 — Incremental Indexing

## Objective

Make indexing efficient enough to run continuously in the background without rescanning and reprocessing unchanged files.

## Requirements

Implement:

- persistent file identity;
- modification detection;
- size and timestamp comparison;
- content hashing where appropriate;
- detection of newly created files;
- detection of modified files;
- detection of deleted files;
- safe reconciliation of an indexed root;
- configurable ignore rules;
- protection against inaccessible files and directories;
- transactional database updates.

## Important behavior

A second indexing pass over an unchanged tree must avoid unnecessary content extraction.

A changed file must replace its previous indexed representation.

A deleted file must disappear from search results.

Ignore rules must be explicit and testable.

## Constraints

Do not implement the Windows GUI or OneDrive yet.

Do not use filesystem watchers as the only source of truth. The system must support a complete reconciliation scan; watchers can be added later as an optimization.

## Acceptance criteria

- Indexing an unchanged tree is substantially cheaper than the first pass.
- Added, modified and deleted files are reflected correctly.
- Tests cover all three cases.
- Database state remains consistent after errors.
- A future background indexer can call the same indexing API.

## Prompt

Implement Incremental Indexing for Universal Search. First inspect the existing provider, document and database architecture. Add persistent indexing state, reconciliation, ignore rules and deletion handling without coupling the core to a GUI or Windows-specific watcher. Add comprehensive tests, run the full test suite, and document the resulting API.
