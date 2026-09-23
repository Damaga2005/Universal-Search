# 027 — Windows Shell Integration

## Objective
Make Universal Search feel native to Windows without coupling search internals to Windows APIs.

## Features
Evaluate and implement:
- configurable global hotkey
- Start Menu integration
- desktop/taskbar shortcuts where useful
- Explorer context-menu action
- open containing folder
- copy path
- single-instance search window
- startup integration
- DPI/scaling support
- Windows notifications where useful

Prefer Ctrl+Space as a default only if it can be registered safely without interfering with common applications.

## Platform boundary
Windows APIs belong in the platform adapter. Query parsing, ranking, indexing, database, extraction and provider contracts remain platform-neutral.

## Installer
Installation and uninstall must create/remove shell entries cleanly. Prefer per-user integration and avoid administrator privileges where possible.

## Tests
Add platform-independent command/configuration tests and real Windows smoke tests for hotkey, Explorer action, open/reveal, startup, single instance and uninstall cleanup.

## Acceptance
A Windows user can invoke Universal Search naturally from normal desktop workflows without opening a terminal.

## Ready-to-copy implementation prompt
Implement Phase 027 — Windows Shell Integration. Audit the existing Windows adapter and packaging before adding native shell entry points. Add safe configurable global invocation, Start Menu/shortcut integration, Explorer actions, open/reveal commands, startup and single-instance behavior with clean uninstall. Keep Windows APIs isolated from the core and add platform-independent tests plus Windows smoke tests. Avoid administrator requirements where possible. Do not push unless explicitly instructed.
