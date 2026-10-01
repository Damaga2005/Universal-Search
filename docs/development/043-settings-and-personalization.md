# 043 — Settings & Configuration

## Objective
Create one coherent, typed settings system for user-configurable behavior that is currently distributed across configuration files, environment variables, CLI options and UI surfaces.

This phase is about configuration architecture and user control, not a new personalization engine.

## Scope
Centralize settings for:

- indexed sources;
- include/exclude rules;
- indexing behavior and scheduling;
- startup;
- tray behavior;
- global hotkey;
- theme and language;
- search behavior and result count;
- history;
- local learning;
- semantic search;
- privacy;
- diagnostics/logging;
- resource limits;
- portable-mode behavior where appropriate.

## Configuration contract
Define:

- typed settings;
- defaults;
- validation;
- persistence;
- migration;
- reset-to-default;
- safe import/export where justified.

Existing supported configuration paths must either remain compatible or have an explicit migration path.

Do not store secrets in ordinary settings.

Environment variables that remain as operational overrides must have documented precedence over persisted settings.

## UX
Group settings by user intent, not implementation module.

Every advanced setting must explain its effect.

Destructive operations require explicit confirmation.

## Tests
Test:

- defaults;
- invalid values;
- persistence;
- migrations;
- reset;
- precedence;
- privacy toggles;
- concurrent configuration changes;
- backward compatibility.

## Acceptance
A user can understand and control Universal Search behavior from one coherent settings experience without manually editing implementation-level files.

## Ready-to-copy implementation prompt
Implement Phase 043 — Settings & Configuration. Audit all existing configuration paths and consolidate user-facing behavior into a typed, validated and migrated settings system with a coherent Windows UI. Cover sources, indexing, startup, tray, hotkey, appearance, search, history, local learning, semantic search, privacy and diagnostics. Preserve compatibility where practical and document precedence for operational overrides. Add migration, reset and persistence tests. Do not introduce account/cloud settings. Do not push unless explicitly instructed.
