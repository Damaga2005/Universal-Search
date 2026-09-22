# Development Prompt 001 — Foundation

## Objective

Establish the initial, provider-agnostic foundation of Universal Search.

## Context

Universal Search is a Windows desktop search application designed to index local files and, later, OneDrive and other sources. It must remain local-first and must not require AI, paid APIs, a cloud backend, or an external database server.

## Current scope

This phase establishes:

- Python package structure.
- Domain document model.
- Provider abstraction.
- Local filesystem provider.
- SQLite metadata database.
- SQLite FTS5 full-text index.
- Basic indexing operation.
- Basic CLI.
- Initial automated tests.

## Requirements

1. Keep domain models independent of SQLite and Windows UI.
2. Keep document providers independent of the search engine.
3. Use SQLite as the local persistence layer.
4. Use FTS5 for full-text search.
5. Do not introduce AI, embeddings, external APIs, cloud services, Elasticsearch, Redis, Docker, or microservices.
6. Preserve Python 3.12+ compatibility.
7. Every behavior introduced must have automated tests.
8. Public APIs should be small and explicit.

## Acceptance criteria

- A local directory can be indexed.
- Text-like files can be searched by content.
- File names and paths are searchable.
- Search results expose source, path, name and a useful snippet.
- Database creation is automatic.
- The project can be installed as a Python package.
- Tests pass.

## Prompt

Implement or review the Foundation milestone of Universal Search according to this document. Work incrementally, preserve the architecture, add tests for every behavior, and do not implement later milestones prematurely. At the end, report changed files, tests executed, and any architectural decisions.
