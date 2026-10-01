# 043 — Settings & Personalization

## Objective
Create a coherent settings system for all user-configurable behavior without scattering configuration across files, environment variables and UI code.

## Scope
Centralize settings for:
- indexed sources;
- include/exclude rules;
- index scheduling;
- startup;
- tray behavior;
- global hotkey;
- theme;
- language;
- search behavior;
- result count;
- history;
- local learning;
- semantic search;
- privacy;
- diagnostics/logging;
- resource limits.

## Configuration
Define:
- typed settings;
- defaults;
- validation;
- migration;
- persistence;
- reset-to-default;
- import/export where justified.

Do not store secrets in ordinary settings.

## UX
Group settings by user intent rather than implementation module.

Every advanced setting must explain its effect.

Dangerous/destructive operations require explicit confirmation.

## Tests
Test default settings, migration, invalid values, reset, persistence, privacy toggles and concurrent configuration changes.

## Acceptance
A user can understand and control application behavior from one coherent settings experience.

## Ready-to-copy implementation prompt
Implement Phase 043 — Settings & Personalization. Audit existing configuration paths and consolidate them into a typed, validated, migrated settings system with a modern Windows UI. Cover sources, indexing, startup, tray, hotkey, appearance, search, history, local learning, semantic search, privacy and diagnostics. Preserve backward compatibility where practical, add migration and reset behavior, and test persistence and privacy controls. Do not introduce account/cloud settings. Do not push unless explicitly instructed.
