# Development Prompt 007 — OneDrive Integration

## Objective

Make OneDrive a first-class search source.

## Phase A — synced files

Support OneDrive folders already present locally on the Windows machine.

These should use the same local filesystem pipeline while being classified as OneDrive source/context.

## Phase B — cloud-only files

Add a provider abstraction capable of discovering OneDrive files that are not physically downloaded.

The cloud provider must expose metadata and availability state.

## Important distinction

A cloud-only file may have searchable metadata without having local content.

If content must be downloaded to index it, this must be explicit and controlled.

## Requirements

- Never upload user files to our own service.
- No proprietary cloud backend.
- Clear source labels.
- Handle unavailable/offline files.
- Do not silently download huge files.
- Preserve provider independence.

## Acceptance criteria

- Synced OneDrive files are searchable.
- Search results identify OneDrive.
- Cloud-only files can be represented safely.
- Indexing handles offline/unavailable content gracefully.
- OneDrive-specific code does not leak into the core ranking engine.

## Prompt

Implement OneDrive support in two stages. Start with synced OneDrive folders using the existing local pipeline and explicit source classification. Then design and implement a cloud-only provider using the appropriate supported Windows/OneDrive integration mechanism. Preserve privacy, avoid unnecessary downloads and add tests for availability states.
