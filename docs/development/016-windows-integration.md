# 016 — Windows Integration

## Objective

Integrate Universal Search naturally into Windows while keeping the core platform-independent.

## Features

Evaluate and implement as appropriate:

- configurable global hotkey, preferably `Ctrl + Space`
- Start Menu integration
- application shortcuts
- Explorer integration
- "Search with Universal Search"
- open/reveal file
- keyboard-first launch
- single-instance search window
- startup behaviour
- useful Windows notifications

If Windows Search protocol/index integration is attempted, isolate it behind a platform adapter and document limitations.

## Global hotkey

It must:

- be configurable
- fail gracefully on conflicts
- unregister cleanly
- not create duplicate windows
- work when GUI is not foreground
- degrade gracefully if registration is unavailable

## Shell operations

Implement robust open/reveal-folder operations and handle missing/deleted files.

## Architecture

Keep:

```
Core
  -> platform-independent services
  -> Windows adapter
```

Do not put Windows APIs into domain/search classes.

## Tests

Use mocks/fakes for Windows integration and Windows-only smoke tests where practical. Non-Windows tests must not require Windows APIs.

## Acceptance

- Windows features are isolated
- GUI works without optional shell integration
- hotkey failure is graceful
- normal usage does not create duplicate instances
- shell operations handle errors cleanly
- Windows-only behaviour is documented

## Ready-to-copy implementation prompt

Implement Phase 016 — Windows Integration. Audit the actual GUI/packaging architecture, create a clean Windows adapter, implement/configure the global hotkey and useful shell integration, add Start Menu/startup support where appropriate, enforce single-instance behaviour, and provide mocks plus Windows smoke tests. Keep the core platform-neutral and do not push.
