# Development Prompt 009 — Global Search Experience

## Objective

Make Universal Search feel like an instant Windows utility rather than a conventional application.

## Requirements

- Global keyboard shortcut.
- Fast-launch search window.
- Search-as-you-type.
- Keyboard-first navigation.
- Recent queries, optionally disabled.
- Direct open.
- Reveal in Explorer.
- Copy path.
- Source/context indicators.
- Search filters.

## Performance target

The UI should not scan the filesystem during a query.

Queries must execute against the prepared index.

## Acceptance criteria

- Global shortcut opens search.
- First keystrokes are responsive.
- Results update without blocking the UI.
- Opening a result is immediate.
- The indexer continues running independently.

## Prompt

Implement the global search experience around the existing indexed search engine. Keep filesystem access out of the interactive query path. Add the global shortcut, keyboard navigation, filters and result actions. Measure startup and query latency and add regression checks for performance-sensitive code.
