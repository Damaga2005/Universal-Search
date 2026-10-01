# 047 — Storage & Data Lifecycle

## Objective
Define and enforce a transparent lifecycle for every local dataset Universal Search persists, especially as corpus size and derived data grow.

This phase complements the existing diagnostics and maintenance work from phases 015, 023 and 028.

## Scope
Audit lifecycle semantics for:

- SQLite index;
- FTS data;
- derived metadata and snippets;
- semantic-search derived data;
- relationship graph;
- usage history;
- logs/events;
- backups;
- migration artefacts.

Provide diagnostics showing storage usage by category where technically reliable.

## Lifecycle contract
Every persisted dataset must have:

- purpose;
- owner/component;
- schema/version;
- rebuild path;
- deletion path;
- retention policy;
- migration behavior.

Derived data must remain reconstructible where possible.

## Maintenance
Provide safe, explicit operations for:

- compact/optimize;
- rebuild;
- purge derived data;
- clear history;
- clear logs;
- backup/restore;
- migration cleanup.

Never delete or modify original user documents.

## Recovery
Maintenance must be safe under interruption and must leave the application able to recover deterministically.

## Tests
Test:

- storage accounting;
- deletion;
- rebuild;
- migration;
- interrupted maintenance;
- recovery;
- privacy/forget semantics.

## Acceptance
A user can understand and control Universal Search's storage footprint and derived-data lifecycle without manually manipulating its database.

## Ready-to-copy implementation prompt
Implement Phase 047 — Storage & Data Lifecycle. Audit every persisted dataset and establish explicit purpose, version, retention, rebuild and deletion semantics. Add reliable storage diagnostics and safe maintenance actions for index, FTS, derived data, semantic data, graph, history, logs and backups. Never touch original files. Add migration and failure-recovery tests and document the lifecycle. Do not push unless explicitly instructed.
