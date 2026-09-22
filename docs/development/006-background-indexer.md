# Development Prompt 006 — Background Indexer

## Objective

Keep the Universal Search index continuously up to date without requiring the GUI to remain open.

## Architecture

Separate:

    Universal Search GUI
    Universal Search Indexer

The indexer should be able to start with Windows and operate independently.

## Requirements

- Initial reconciliation scan.
- Efficient incremental updates.
- Filesystem change notifications as an optimization.
- Periodic reconciliation as a correctness mechanism.
- Graceful shutdown.
- Crash-safe database writes.
- Logging.
- Configurable indexed locations.
- Pause/resume indexing.
- Resource limits for CPU, disk and memory.

## Product behavior

Closing the search window must not stop indexing.

The indexer should run quietly in the background.

The user must be able to see index status:

- indexing;
- idle;
- paused;
- error.

## Acceptance criteria

- Index remains current after files are created, modified and deleted.
- Restarting the indexer does not corrupt state.
- GUI and indexer can run independently.
- Resource usage is bounded and configurable.

## Prompt

Implement the Windows background indexer using the existing provider/indexing APIs. Use filesystem notifications for responsiveness but retain periodic reconciliation for correctness. Add lifecycle management, logging, resource controls and tests. Do not move indexing logic into the GUI.
