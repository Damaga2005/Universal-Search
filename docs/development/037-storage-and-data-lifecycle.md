# 037 — Storage & Data Lifecycle

## Objective
Give users transparent control over how much local storage Universal Search consumes and how derived data evolves over time.

## Scope
Define lifecycle policies for:
- SQLite index;
- FTS data;
- snippets/derived metadata;
- semantic vectors;
- relationship graph;
- usage history;
- logs;
- backups.

Provide diagnostics showing storage usage by category.

## Retention
Every persisted derived dataset must have:
- purpose;
- version;
- rebuild path;
- deletion path;
- retention policy.

## Maintenance
Provide safe:
- compact/optimize;
- rebuild;
- purge derived data;
- clear history;
- clear logs;
- backup/restore.

Never delete original documents.

## Tests
Test storage accounting, deletion, rebuild, migrations, interrupted maintenance and recovery.

## Acceptance
A user can understand and control Universal Search's storage footprint without manually manipulating its database.

## Ready-to-copy implementation prompt
Implement Phase 037 — Storage & Data Lifecycle. Audit every persisted dataset and establish explicit purpose, version, retention, rebuild and deletion semantics. Add storage diagnostics and safe maintenance actions for index, FTS, derived data, vectors, graph, history, logs and backups. Never touch original files. Add migration and failure-recovery tests and document the lifecycle. Do not push unless explicitly instructed.
