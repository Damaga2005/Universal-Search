# Phase 006 — Background Indexer: Implementation Report

## What was implemented

- **`background.py`** — the entire lifecycle of a separate worker process:
  - **Single-instance lock** (`indexer.lock`): created with `O_CREAT|O_EXCL`,
    holds the PID; liveness checked through `OpenProcess` +
    `WaitForSingleObject` (proper ctypes signatures, no handle truncation);
    stale locks from dead processes are replaced; a second live worker gets
    `EXIT_ALREADY_RUNNING`.
  - **Status file** (`indexer-status.json`): `idle / indexing / paused / error`
    with PID, timestamp, roots and per-pass stats, written atomically
    (`os.replace`, no temporary left behind); corrupt/absent reads never raise.
  - **Pause marker** (`indexer-paused.flag`) and **stop marker**
    (`indexer-stop.flag`): presence-based control, honored within 250 ms.
  - **Autostart** in `HKCU\…\CurrentVersion\Run` with an **injected registry**
    (fully tested with a fake, no registry writes from tests); packaged builds
    register the exe, dev installs register `python -m … indexer run`.
  - **`BackgroundIndexer` worker**: initial reconciliation scan → loop of
    (stop/pause checks → filesystem-triggered or periodic reconciliation).
    Roots are re-read from config every pass, so adding a folder takes effect
    without restarting. Per-root failures are logged and reported as `error`
    state without abandoning the other roots.
  - **Watchers as an optimization** (watchdog, recursive, debounced to 1 s per
    pass) that only mark the state dirty; **periodic reconciliation remains
    the correctness mechanism** (`indexer_interval_seconds`, default 300 s).
    Events on the application's own files (DB/WAL/status/lock) are filtered so
    the indexer can never trigger itself.
  - **Graceful shutdown**: stop file or `SIGINT`/`SIGTERM` → clean exit code 0,
    observers joined, lock released, stop marker consumed, status cleared. A
    crash writes the `error` status (evidence) and still releases the lock.
  - **Control API**: `start()` (detached spawn + waits until the worker holds
    the lock, surfaces immediate-startup failures), `stop()` (marker →
    escalation to `SIGTERM` after 8 s), `status_report()`, `pause/resume`,
    `set/get_autostart`.
- **CLI**: `universal-search indexer run|start|stop|status|pause|resume
  autostart on|off|status`, plus a `__main__` guard so `python -m
  universal_search.cli …` works (used by the spawned worker).
- **Resource limits** (spec requirement):
  - *CPU/disk*: `Indexer.index_root(delay=…)` sleeps between changed-file
    writes (`config.indexer_interval_seconds`, `indexer_file_delay` — both
    persistent and configurable);
  - *memory*: already bounded by the extraction cap (2 M chars/file) and the
    ranking candidate pool;
  - *events*: watcher debounce (1 pass per second maximum).
- **GUI hooks** (presentation only, no indexing logic): *Indexador* menu
  (Iniciar/Detener/Pausar/Reanudar/Iniciar con Windows) and a live status
  indicator in the status bar refreshed every 2 s. Closing the window never
  touches the worker (asserted by a test): GUI and indexer are independent
  processes coordinated only through files.
- **Logging**: rotating file log shared with the GUI (`setup_logging`), now
  re-binding when the application home changes.

## Technical decisions

1. **File-based coordination, not sockets/RPC** — lock/status/pause/stop files
   in the per-user directory are inspectable, trivially testable and survive
   crashes; no ports, no extra dependencies.
2. **`watchdog` as the second runtime dependency** (pure-Python on Windows via
   `ReadDirectoryChangesW`; no compiled extension needed) — required by the
   spec for responsive updates; import is lazy so every other command works
   even if it is missing.
3. **Periodic reconciliation is authoritative** — watchers may be missed
   (buffer overflow, network mounts); the periodic pass guarantees eventual
   correctness, exactly as the spec demands.
4. **Stop escalation** — cooperative marker first (flushes everything), then
   `SIGTERM` (`TerminateProcess` on Windows) only if the worker ignores it.
5. **The worker never trusts its own in-memory config** — every pass reloads
   `config.json`, so GUI/CLI edits apply live.
6. **Tray icon remains out of scope** (v0.4 “Tray indexer” is a later roadmap
   item): control lives in the CLI + GUI menu/status bar. Documented as a
   limitation, consistent with the roadmap.

## Dependencies

| Added | Version | Why |
|---|---|---|
| `watchdog` | 6.0.0 | filesystem change notifications (declared `watchdog>=4.0`) |

## Limitations

- Status/pause/stop coordination is local-machine only (single user session).
- No tray icon yet (see above); no Windows *service* — the worker is a user
  process started manually, by the GUI menu, or by autostart at logon.
- Watcher debounce is a fixed 1 s; extremely fast change bursts are coalesced
  into one pass (correctness unaffected — reconciliation always runs).
- The stale-lock takeover has a tiny TOCTOU window between read and unlink;
  `O_EXCL` on the next create makes the worst case a retried attempt, never a
  double worker.

## Tests

`tests/test_background.py` (**17 tests**) + GUI-hook tests in
`tests/test_gui.py` (3 new; **11 total there**):
lock exclusivity/stale takeover/second-instance refusal/PID liveness;
status atomicity, full-payload semantics, corruption tolerance, tmp cleanup;
pause/resume/stop markers idempotence; status report with paused-when-stopped;
autostart via injected registry (set/get/delete, `REG_SZ`, frozen command);
**full worker lifecycle with real watchdog events**: create → modify → delete
each reflected in the index, then marker stop → exit 0, lock/status/marker
cleaned; **real subprocess E2E**: start → duplicate refused with same PID →
parent observes the child's indexed data → clean stop → restart without
corruption → final cleanup; `stop` when not running; `index_root(delay=…)`
timing as the configurable resource limit; CLI `indexer status/pause/resume/
autostart status/unknown-command` exit codes and messages; GUI menu presence,
live status refresh, friendly error without traceback, close never stops the
indexer.

## Acceptance criteria

- Index current after create/modify/delete — `test_worker_keeps_index_current_and_stops_cleanly` + CLI demo (below).
- Restarting does not corrupt state — restart half of `test_start_stop_and_no_duplicate_process`.
- GUI and indexer independent — separate processes; `test_closing_window_does_not_touch_the_indexer`.
- Resource usage bounded and configurable — `delay` + interval/delay config + debounce + extraction/pool caps.
- No duplicate processes — O_EXCL lock + `already-running` (asserted in tests and in the manual run).
- Status visible in all four states — status JSON + GUI indicator + CLI `indexer status`.
- App starts with missing DB, crash-safe writes, logging — inherited from phases 001/002 (WAL + per-file commit) and verified throughout.

## How to run / manual verification performed

```bash
universal-search indexer start      # background worker (detached)
universal-search indexer status     # idle/indexing/paused/error + stats
universal-search indexer pause      # pause marker (also honored at next start)
universal-search indexer resume
universal-search indexer stop       # graceful, escalates only if ignored
universal-search indexer autostart on
universal-search indexer run        # foreground (autostart / debugging)
.venv\Scripts\python -m pytest tests/test_background.py -v
```

Real-session demo output: `indexador iniciado (pid 6856)` → second `start`
answered `ya en marcha (pid 6856)` → file created in a watched folder →
`status` showed `idle … created=1` → `search "examen"` from another process
returned the file with a highlighted snippet → `stop` → `detenido`, **0
residual processes**.
