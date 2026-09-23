# Phase 005 — Windows Desktop Application: Implementation Report

## Technology choice

**Tkinter + ttk** (Python's standard GUI toolkit), justified briefly:

| Option | Verdict |
|---|---|
| **Tkinter/ttk** | ✅ Zero new dependencies, ships with python.org Windows builds, fast cold start (~100 ms), first-class PyInstaller support, stable API, trivially testable. Theme `vista` gives native Windows look. |
| Qt (PySide/PyQt) | ❌ +60–120 MB dependency, licensing complexity (PyQt GPL/commercial), overkill for a search-first window. |
| WinUI/pywebview/CEF | ❌ Native look but heavyweight runtimes or web stack; hurts launch time and packaging. |

The requirement is a minimal, search-first, quickly launching window — Tkinter
is the most maintainable choice for a real single-purpose Windows utility, and
the GUI stays fully decoupled from the core.

## What was implemented

- **`appconfig.py`** (shared with phase 006) —
  - `AppPaths`: per-user home under `%LOCALAPPDATA%\Universal Search`
    (overridable via `UNIVERSAL_SEARCH_HOME`); dedicated files for database,
    config, logs, indexer status/pause/lock.
  - `AppConfig`: persistent JSON config (roots, ignore rules, autostart flag,
    indexer interval/delay, window geometry) with atomic saves, tolerant
    loading (missing/corrupt/wrong-typed/unknown fields → defaults, logged).
  - `setup_logging()`: rotating file handler (1 MB × 3), idempotent.
- **`gui/services.py`** — testable service layer, no Tk imports:
  `SearchService` (owns `SearchDatabase` + `SearchEngine` + config),
  `open_path()` (shell open), `reveal_in_explorer()` (`explorer /select,`).
  The UI contains **zero database logic**.
- **`gui/app.py`** — `SearchWindow` (view only):
  search box with 150 ms debounce, results list (name + context line),
  preview pane (filename — type — source / full path / snippet), status bar;
  `Enter` open, `Ctrl+Enter` reveal in Explorer, `Esc` clear → `Esc` close,
  `↑ ↓ PgUp PgDn` selection, double-click open; menu *Archivo → Añadir
  carpeta a indexar…*; geometry persisted to config; friendly status messages
  on errors (tracebacks only go to the log, never on screen); `run()` entry
  that can never surface a traceback.
- **CLI** `universal-search gui` + script `universal-search-gui`.
- **Product identity**: `packaging/make_icon.py` generates
  `packaging/universal_search.ico` (magnifier on a blue rounded tile) using
  **only the standard library** (custom PNG encoder + ICO container), sizes
  16/32/48/64/256.
- **Packaging** (build executed and verified): `packaging/entry-gui.py` is a
  dispatcher — with arguments it runs the CLI, without them it opens the
  window — so a frozen deployment still supports `indexer start/autostart`.
  `packaging/universal-search.spec` (PyInstaller, icon attached) produces one
  folder with **two executables sharing a single runtime**:
  `UniversalSearch.exe` (windowed, `console=False`) and
  `universal-search.exe` (console, for CLI + background indexer). Optional
  extra `.[build] = pyinstaller`; `packaging/make-shortcut.ps1` creates a
  Start Menu shortcut via `WScript.Shell`.

## Technical decisions

1. **No new runtime dependencies** — the GUI adds none (pypdf from 003 only).
2. **Service layer instead of logic-in-widgets** — every behavior (search,
   open, reveal, config) is callable without Tk, which is what makes the
   window tests real yet fast.
3. **Debounced search** (150 ms) so typing never blocks and each keystroke
   does not hit SQLite.
4. **Snippet display strips FTS highlight brackets** — implementation detail
   stays out of the product UI.
5. **One Tk root per test module** — creating/destroying several `Tk()`
   instances in one process trips a known Tkinter bug
   (`invalid command name "tcl_findLibrary"`, reproduced here intermittently);
   tests reset window state instead of recreating the root. Documented in the
   fixture.
6. **Keyboard events tested via binding registration + direct handler
   invocation** — synthetic `event_generate` key events are not delivered in
   non-interactive sessions (verified empirically), so tests assert both the
   wiring (`widget.bind(...)`) and the behavior of each handler.

## Dependencies

None added at runtime. `pyinstaller>=6.0` available via the optional `build`
extra (packaging only).

## Limitations

- No system tray yet (phase 006 keeps it out of scope; control lives in the
  CLI + GUI status bar).
- No installer (MSI/Inno) — distribution is the PyInstaller folder + a
  Start-Menu shortcut script.
- Window state persists (geometry); result history does not (by design).
- Synthetic keyboard events cannot be exercised in headless CI (bindings and
  handlers are covered instead).

## Tests

`tests/test_gui_services.py` (15) + `tests/test_gui.py` (8) = **23 tests**:
paths/env override, defaults under `LOCALAPPDATA`, service start with a
missing database, service search over a real index, `open_path`/`reveal`
mocked at the OS boundary, config defaults/round-trip/corruption/unknown
fields/ignore-rules drive, logging idempotence + file content, ICO structure
validation, Start-Menu shortcut script executed end-to-end; real window:
rendering + preview + status, keyboard navigation and open (binding asserted),
Enter without selection, two-stage Escape, `Ctrl+Enter` reveal, friendly error
without traceback, add-folder persistence, empty-query ready state.

## Acceptance criteria

- Launched as a Windows application — `universal-search gui`, verified alive
  in an end-to-end smoke run (log: `Universal Search GUI starting`, no errors).
- Results appear quickly — debounced search over the indexed DB; rendering
  tested.
- Opening a result works — service + handler tests.
- Keyboard-only navigation works — bindings registered + handlers tested.
- Core search tests remain independent of the GUI — all 86 previous tests
  unchanged and green (total now 109).

## How to run

```bash
universal-search gui                # or: universal-search-gui
.venv\Scripts\python -m pytest tests/test_gui.py tests/test_gui_services.py -v

# build the .exe (verified steps, see final report)
.venv\Scripts\python -m pip install ".[build]"
.venv\Scripts\python -m PyInstaller packaging/universal-search.spec
# → dist/UniversalSearch/UniversalSearch.exe
.\\packaging\\make-shortcut.ps1 -TargetExe "dist\\UniversalSearch\\UniversalSearch.exe"
```
