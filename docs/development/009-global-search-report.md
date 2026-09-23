# Phase 009 — Global Search Experience: Implementation Report

## What was implemented

- **Global keyboard shortcut** (`hotkey.py`, new module):
  - `parse_hotkey("ctrl+alt+s")` → `(modifiers, virtual-key)` — pure and
    strict: case/space-insensitive, letters/digits/`F1..F12`/named keys,
    aliases (`ctrl`/`control`/`win`/`windows`/`super`/`meta`), and **at
    least one modifier is mandatory** so a bare key can never swallow
    normal typing. Every bad spec raises a readable `ValueError`.
  - `HotkeyServer`: dedicated daemon thread with its own Win32 message
    queue (`PeekMessage` first — `PostThreadMessageW` fails against a
    thread that has not pumped), `RegisterHotKey` → `GetMessageW` loop →
    `WM_HOTKEY` dispatch → `UnregisterHotKey` on shutdown
    (`PostThreadMessageW(WM_QUIT)` + join). All Win32 calls go through
    **injectable DLL objects**, so the whole lifecycle is unit-tested
    without grabbing a real hotkey.
  - Worker integration (`BackgroundIndexer._start_hotkey/_stop_hotkey`):
    started in `run()`, stopped in `finally`. **Failures never stop
    indexing**: invalid config → warning + no thread; hotkey already
    taken by another application → `start()` returns `False` + warning;
    handler exceptions are swallowed by the dispatch guard.
  - Trigger behavior (`_on_hotkey_press`): if `gui.pid` names a **live**
    window, touch `gui-show.flag` (the window lifts itself within
    250 ms); otherwise **launch a new GUI process** — packaged builds use
    the windowed `UniversalSearch.exe` sibling (no console flash), source
    runs use `python -m universal_search.cli gui`.
- **Fast-launch window**: the desktop window writes `gui.pid` on start,
  clears it on close, polls the show-request flag every 250 ms and on
  consumption restores/deiconifies, lifts with a 300 ms topmost hint and
  focuses the query box (`_present`).
- **Search-as-you-type** and **keyboard-first navigation** (existing
  from phase 005, still covered by tests) — spec items confirmed present.
- **Recent queries, optionally disabled**:
  - Config: `recent_queries` (capped at `MAX_RECENT_QUERIES = 20`) and
    `recent_queries_enabled` (default on). Shared policy lives in the
    pure `remember_query()` — whitespace-normalized, case-insensitive
    dedupe, newest first, empty/disabled inputs returned untouched.
  - GUI: *Recientes ▾* menubutton (right side of the toolbar) rebuilt on
    every search; clicking an entry runs it immediately (no queued
    duplicate); **disabled configuration hides the button entirely**.
  - Recorded **when the user commits to a result** (`_on_open`), never on
    every intermediate keystroke — a session of typing cannot flood the
    list. Managed from the CLI: `recent show|on|off|clear`.
- **Direct open / Reveal in Explorer** (existing) plus **Copy path**:
  `Ctrl+C` on the results list and *Archivo → Copiar ruta del resultado*,
  verified in the status bar and the actual clipboard.
- **Source/context indicators**: every row now carries a `[local]` /
  `[onedrive]` prefix (plus the existing preview/☁ marker) and the
  *Contexto* combo from phase 008.
- **Search filters**:
  - Engine: `search(..., source=, doc_type=)` adds parameterized SQL
    conditions over indexed columns (`d.source`, `d.extension`) — **still
    pure index queries**; `doc_type` accepts `pdf` or `.pdf`.
  - CLI: `search --source {local,onedrive} --type pdf` (unknown source →
    argparse exit 2).
  - GUI: *Fuente* and *Tipo* readonly combos; changing either re-runs the
    current query immediately.
- **Performance measurement + regression checks** (`test_performance.py`):
  - `test_query_never_touches_the_filesystem` — `os.scandir`,
    `os.listdir`, `Path.rglob/glob/iterdir/resolve` are patched to raise;
    plain and filtered searches must still succeed (spec target).
  - `test_query_latency_budget` — mean < 150 ms, worst < 500 ms over 24
    measured queries on a 600-document index.
  - `test_service_startup_and_first_query_budget` — paths + schema + first
    query < 2 s on a cold index.

## Technical decisions

1. **Signaling stays file-based**, exactly like the worker's existing
   status/stop/pause protocol — no sockets, no named pipes, one new
   dependency on nothing. The flag is touched only for a *live* PID, and
   consumed exactly once (tested), so no orphan popups appear.
2. **The hotkey is presentation sugar**: if registration fails for any
   reason the worker logs and keeps indexing; the application is fully
   usable without it (spec constraint: indexer keeps running
   independently).
3. **One modifier is mandatory** in `parse_hotkey` — a bare-letter global
   hotkey that registers successfully would hijack typing everywhere.
4. **Recents record on commit** (opening a result), not per keystroke:
   "recent queries" should mean queries the user actually acted on; the
   list stays useful and the config is written at most once per open.
5. **Filters live in SQL**, not in Python post-filtering: filtering after
   ranking would break `LIMIT` semantics and waste the candidate pool.
6. **Hotkey changes apply after an indexer restart** (the server is built
   once in `run()`); `hotkey set/on` prints that hint — simple and
   honest instead of re-registering mid-loop.

## Dependencies

None added — `ctypes` (stdlib) talks to `user32`/`kernel32`; no `pywin32`,
no new packages.

## Limitations

- The global keypress itself cannot be synthesized in this test
  environment (Tk/Win32 synthetic key events do not fire), so the spec's
  "global shortcut opens search" is verified at every testable stage —
  registration carries the right modifiers/key, `WM_HOTKEY` reaches the
  handler, live-window flag consumption presents the window, dead-window
  path launches the GUI — but an interactive press was not automated.
- If another application already owns `ctrl+alt+s`, registration fails
  with a logged warning; the shortcut stays silent until the conflict is
  resolved or another one is set with `hotkey set`.
- The 250 ms show-request poll adds one lightweight wakeup per second to
  the idle window (imperceptible; same pattern as the 2 s indexer poll).
- In frozen builds without the windowed sibling the fallback command runs
  from the console exe (console may flash); the standard `dist/` layout
  includes `UniversalSearch.exe`, so the normal path avoids this.
- Recent-queries are recorded by the GUI (and managed by the CLI); plain
   CLI `search` invocations do not write to them (shell history already
   records those).

## Tests

`tests/test_hotkey.py` (**20**), `tests/test_performance.py` (**3**), +2
in `tests/test_cli.py` (8 total) and +2 in `tests/test_gui.py` (16
total):

- spec parsing: valid aliases/case/spaces/F-keys/named keys; 10 invalid
  specs + `None` rejected with `ValueError`;
- server lifecycle: correct `RegisterHotKey(mods, vk)` payload, start
  twice → same thread, clean `WM_QUIT` + `UnregisterHotKey`, idempotent
  stop, stop-without-start;
- dispatch: `WM_HOTKEY` reaches the handler; handler exception is
  contained and the server stays registered; registration failure returns
  `False` without ever raising;
- signaling: no PID / dead PID → no flag; live PID → flag exists and is
  consumed **exactly once**; garbage/missing PID files parse as `None`;
  PID files round-trip and clear;
- launch: source command exact; frozen prefers the windowed sibling,
  falls back to `gui` argument;
- worker: injected fake server starts/stops, disabled config never
  starts it, invalid configured hotkey never raises and creates no
  server, refused registration logs and shuts down cleanly, press →
  `request_show` first / `launch_gui` only when there is no window;
- recents policy: normalize + case-insensitive dedupe + front-push, empty
  and disabled untouched, cap at 20; hotkey/recents config round-trip;
- CLI: `hotkey show/set/on/off` with invalid `set` → exit 1 + unchanged
  config; `recent show/on/off/clear` against real config state;
  `search --type/--source` filters + empty result for absent source +
  argparse exit 2 for an unknown source;
- GUI: filter combos forward `source`/`doc_type` to the service and
  re-run searches; `Ctrl+C` bound, status shows *Ruta copiada* and the
  clipboard returns the exact path; recents menu labels in order,
  applying one runs it immediately with no queued duplicate, disabling
  hides the button; show-flag → `_poll_show_request` → *Atajo global*
  status;
- performance: filesystem-ban during plain **and** filtered queries,
  latency budgets, cold-start budget.

Full suite: **197 passed** (was 170 after phase 008).

## Acceptance criteria

- **Global shortcut opens search** — worker registration + dispatch +
  live-window flag/`_present()` (focused query box) or GUI launch when no
  window (unit/lifecycle verified; physical press needs an interactive
  session — see Limitations).
- **First keystrokes are responsive** — search-as-you-type with 150 ms
  debounce against the index; measured mean query **48.6 ms**, p95
  **80.3 ms**, worst **82.6 ms** on 2000 documents (budget: mean < 150
  ms).
- **Results update without blocking the UI** — queries run against FTS5
  with a bounded candidate pool; no filesystem calls in the query path
  (regression test patches every enumerator to raise).
- **Opening a result is immediate** — `os.startfile` / `explorer
  /select,` without waiting (phase 005, unchanged, still tested).
- **The indexer continues running independently** — hotkey failures are
  non-fatal, GUI launch is a detached `Popen`, closing the window never
  touches the worker (existing test), and `BackgroundIndexer.run()` stops
  the hotkey thread in `finally`.

## How to run / manual verification performed

```bash
universal-search hotkey show                 # atajo global: ctrl+alt+s (activado)
universal-search hotkey set win+f3           # reinicia el indexador para aplicarlo
universal-search hotkey off
universal-search recent show|on|off|clear
universal-search search practica --type txt --source local
universal-search gui                         # then press Ctrl+Alt+S
.venv\Scripts\python -m pytest tests/test_hotkey.py tests/test_performance.py -v
```

Real-session demo output (reported above): `hotkey show` →
`atajo global: ctrl+alt+s (activado)`; `hotkey set win+f3` → confirmed by
`show`; `search practica` → both files; `--type txt` → only
`apuntes.txt`; `--source onedrive` → empty (correct: index has only
local); `recent show` → `(recientes activadas) / sin búsquedas
recientes`; `recent off` → `(recientes desactivadas)`.

Benchmark (2000 documents, `bench009.py`): `build=45.26s
cold_first_query=36.5ms mean_query=48.6ms p50=49.7ms p95=80.3ms
max=82.6ms` — the per-document commit cost visible in `build` is the
prime target of the optimization pass (transaction batching, connection
reuse, SQLite PRAGMAs).
