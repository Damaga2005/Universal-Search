# Phase 021: Tray and Background Experience Design

Date: 2026-09-23
Status: Approved design

## Goal

Turn the existing background indexer into a reliable Windows notification-area
experience without a Windows service, cloud backend, or multi-user daemon.

The tray is an optional user interface. The indexer remains an independent
worker, and indexing at Windows logon continues to use the existing autostart
command `indexer run`.

## Non-goals

- No Windows service or privileged background daemon.
- No network, cloud, telemetry, or third-party runtime plugins.
- No replacement search window, settings window, or diagnostics UI.
- No change to ranking, indexing, extraction, or database semantics.
- No automatic tray launch at logon.

## Architecture

The implementation has four boundaries:

1. `background_service.py` owns application-level state and actions. It reads
   the worker's coordination files, derives a truthful state, and never reads
   SQLite internals.
2. `platforms/tray.py` owns Win32 notification-area calls, the hidden window,
   message loop, icon lifetime, menus, tooltips, and balloon tips.
3. `tray.py` owns platform-independent menu construction, command dispatch,
   notification policy, tray single-instance coordination, and clean exit.
4. `cli.py` exposes `universal-search tray`; the existing GUI, CLI, and worker
   remain separate entry points with separate PID files.

The controller receives a `BackgroundService` and a `TrayBackend`. Both are
injectable so tests can exercise the complete behavior without Windows APIs,
Tk, a desktop session, or a real worker process.

## State model

The application states are:

- `stopped`: no live indexer process; a stale lock is reported separately.
- `starting`: a live worker owns the lock but has not published a usable state.
- `indexing`: the worker is reconciling roots.
- `paused`: the worker is alive and the pause marker is present.
- `idle`: the worker is alive and waiting for work.
- `error`: the worker published an error.
- `stopping`: a stop was requested and the worker is still alive.

State is derived from the lock PID, status JSON, pause marker, and stop marker.
A lock naming a dead process is not considered running. The status snapshot
also retains the last completed pass timestamp and counters, so the tray can
show useful information while a new pass is running.

Pending work is described by mode rather than an invented queue depth: the
worker reconciles a filesystem and does not expose a backlog count.

## Commands and lifecycle

The tray menu contains:

- disabled state summary;
- Open Universal Search;
- Quick search;
- Pause indexing or Resume indexing, according to state;
- Stop indexer or Start indexer, according to state;
- Diagnostics;
- Settings;
- Exit.

Open and Quick search reuse the existing GUI activation path. Settings opens
the existing window, whose Indexer menu is the single settings surface.
Diagnostics opens the existing diagnostics path. No tray-specific duplicate
settings or repair UI is introduced.

The tray has its own atomic PID lock. A live owner causes a second tray to
exit. A dead owner's PID file is stale and may be replaced. The indexer and
GUI keep their existing locks, so a tray never steals either role.

Exit removes the icon, releases the tray lock, and stops only a worker started
by this tray instance. A worker started by autostart, the GUI, the CLI, or
another tray is left running.

## Notifications

The tray stays silent for user-requested start, stop, pause, and resume
actions. It may show a balloon only for:

- a transition into a new indexer error;
- a newly visible global-hotkey configuration problem;
- a worker that disappeared while indexing or starting;
- completion of a pass that ran longer than the configured threshold.

The notification decision is a pure transition function and is tested without
a notification-area implementation.

## Windows adapter

`WindowsTray` uses the native `Shell_NotifyIcon` API through `ctypes`, without
adding a tray dependency. It creates one hidden window, posts the icon, owns a
Win32 message loop, maps tray callbacks to menu command IDs, refreshes state
on a timer, and removes the icon in every exit path. `NullTray` reports that
the feature is unavailable instead of pretending to show an icon.

All DLL handles and OS calls are injectable. Deterministic tests assert the
protocol and command mapping with fakes. A Windows smoke test checks the real
API/struct availability without posting a visible test icon.

## Error handling

- Corrupt or unreadable coordination files become a status plus a problem note.
- A dead worker PID is treated as stopped and its stale lock is recoverable.
- Database or diagnostics failures are shown as a failed command result, not a
  traceback from the tray loop.
- An unavailable notification area makes `tray` exit with an actionable code.
- Shutdown is idempotent and always releases owned resources.
- Starting a worker is delegated to the existing single-instance starter, so a
  duplicate indexer cannot be created.

## Tests

Add deterministic tests for state derivation, stale locks, last-pass data,
worker coordination, menu contents, all tray commands, notification policy,
single-instance behavior, clean exit, and unchanged autostart behavior. Add
Windows smoke coverage for the native adapter's API and struct layout without
creating a visible icon. Run the complete test suite, pyflakes, and packaging
smoke checks before the phase commit.

## Documentation and acceptance

Update the architecture, README, roadmap, development index, and changelog.
Document the optional nature of the tray, the unchanged indexer autostart, the
notification limits, and the fact that settings/diagnostics remain in the
existing window.

Acceptance is met when the tray can remain resident, report state, start/stop
and pause/resume the worker, open search, expose existing settings and
diagnostics, enforce one tray instance, and exit without leaving workers it
started behind.
