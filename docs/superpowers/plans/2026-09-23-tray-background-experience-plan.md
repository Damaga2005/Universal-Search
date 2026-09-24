# Phase 021 Tray and Background Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native, optional Windows notification-area experience for the existing background indexer without changing indexing or search semantics.

**Architecture:** A platform-independent `BackgroundService` derives truthful worker state from the existing coordination files. A platform-independent `TrayController` owns menu commands, notifications, and tray ownership. `platforms/tray.py` contains the Win32 implementation behind injectable DLLs, while the CLI and existing GUI signaling provide the two entry points. Autostart remains `indexer run`.

**Tech Stack:** Python 3.12, standard-library `ctypes`, `dataclasses`, `pathlib`, `argparse`, Tkinter for the existing GUI, pytest, pyflakes, PyInstaller for the existing packaging smoke.

## Global Constraints

- Keep domain, indexing, search, database, and provider logic platform-independent.
- Keep all Windows API code inside `src/universal_search/platforms/`.
- Add no runtime dependency; keep the existing `pypdf` and `watchdog` dependencies unchanged.
- Do not add a Windows service, cloud backend, network client, or multi-user daemon.
- Keep autostart registered as `indexer run`; do not make indexing depend on the tray.
- Do not let the tray read SQLite internals; use the existing GUI/diagnostics service path.
- Use one final `feat:` commit for phase 021 and do not push.
- Preserve existing PID locks and worker lifecycle semantics.
- Keep user-facing output safe on a legacy Windows console.

---

## File Map

- Create `src/universal_search/background_service.py`: immutable status model, state derivation, worker actions, and notification decision helpers.
- Create `src/universal_search/platforms/tray.py`: `MenuItem`, `TrayBackend`, `NullTray`, and native `WindowsTray` adapter.
- Create `src/universal_search/tray.py`: tray lock, menu model, command controller, and blocking entry point.
- Modify `src/universal_search/platforms/tray.py`: add a platform-neutral termination request used by native Exit, with the Win32 implementation kept in the adapter.
- Modify `tests/test_windows_tray.py`: cover native termination signaling.
- Modify `src/universal_search/appconfig.py`: add the tray PID and GUI diagnostics-request paths.
- Modify `src/universal_search/background.py`: retain the last successful pass in status JSON without changing the current `stats` field contract.
- Modify `src/universal_search/hotkey.py`: add a file-based diagnostics request that reuses the existing GUI signaling pattern.
- Modify `src/universal_search/gui/services.py`: re-export and wrap the diagnostics request/consume helpers.
- Modify `src/universal_search/gui/app.py`: consume the diagnostics request and call the existing diagnostics view.
- Modify `src/universal_search/cli.py`: add the `tray` subcommand and route it to the tray entry point.
- Create `tests/test_background_service.py`: deterministic state and action tests.
- Create `tests/test_tray.py`: controller, lock, command, notification, and exit tests.
- Create `tests/test_windows_tray.py`: Win32 protocol fakes and Windows-only API smoke test.
- Modify `tests/test_background.py`: last-pass persistence regression tests.
- Modify `tests/test_hotkey.py`: diagnostics request signaling tests.
- Modify `tests/test_gui.py`: diagnostics request consumption test when Tk is available.
- Modify `tests/test_cli.py`: `tray` parser/dispatch test.
- Modify documentation listed in Task 6.

## Shared Interfaces

`BackgroundService` exposes:

```python
status() -> BackgroundStatus
start() -> tuple[str, str]
stop(*, timeout: float = 8.0) -> tuple[str, str]
pause() -> tuple[str, str]
resume() -> tuple[str, str]
autostart_enabled() -> bool
```

`BackgroundStatus` exposes the fields `state`, `pid`, `updated_at`, `last_scan_at`, `last_scan_stats`, `roots`, `pending`, `error`, `hotkey_error`, `stale_lock`, `paused`, `autostart`, and `problem`, plus `running`, `can_start`, `can_stop`, `can_pause`, `can_resume`, and `summary`.

`TrayController` exposes:

```python
claim() -> int
release() -> None
refresh() -> BackgroundStatus
menu() -> list[MenuItem]
run_command(command: int) -> CommandResult
exit() -> CommandResult
```

`TrayBackend` exposes `available()`, `run(on_command, menu, tooltip, tick=None)`, `request_exit()`, `notify(title, message)`, `update_tooltip(text)`, and `describe()`.

`WindowsTray` constructor arguments are `icon_path=None`, `user32=None`, `shell32=None`, `kernel32=None`, and `platform=None`; all OS calls must be injectable.

---

### Task 1: Preserve coordination data and add GUI action signaling

**Files:**
- Modify: `src/universal_search/appconfig.py:65-81`
- Modify: `src/universal_search/background.py:164-189`
- Modify: `src/universal_search/hotkey.py:217-256`
- Modify: `src/universal_search/gui/services.py:14-40, 213-279`
- Modify: `src/universal_search/gui/app.py:268-280, 544-590`
- Test: `tests/test_background.py`
- Test: `tests/test_hotkey.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- `AppPaths.tray_pid_file -> Path` returns `<home>/tray.pid`.
- `AppPaths.diagnostics_request_file -> Path` returns `<home>/gui-diagnostics.flag`.
- `hotkey.request_diagnostics(paths) -> bool` requires a live GUI and touches the request file.
- `hotkey.consume_diagnostics_request(paths) -> bool` consumes the file exactly once.
- `background.write_status(...)` adds `last_scan_at` and `last_scan_stats` only after a completed idle pass, while retaining the existing rule that an `indexing` status has no current `stats` key.

- [ ] **Step 1: Write failing tests for path and last-pass behavior.**

Add assertions equivalent to:

```python
def test_status_retains_last_completed_pass_while_indexing(tmp_path):
    paths = AppPaths(tmp_path / "home")
    background.write_status(paths, "idle", stats={"created": 2}, roots=1)
    first = background.read_status(paths)
    background.write_status(paths, "indexing", roots=1)
    current = background.read_status(paths)
    assert "stats" not in current
    assert current["last_scan_at"] == first["updated_at"]
    assert current["last_scan_stats"] == {"created": 2}


def test_tray_and_diagnostics_paths_are_in_application_home(tmp_path):
    paths = AppPaths(tmp_path / "home")
    assert paths.tray_pid_file == paths.home / "tray.pid"
    assert paths.diagnostics_request_file == paths.home / "gui-diagnostics.flag"
```

- [ ] **Step 2: Run the focused tests and verify they fail for the missing paths/fields.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_background.py::test_status_retains_last_completed_pass_while_indexing tests\test_hotkey.py -q
```

Expected: failures because `tray_pid_file`, `diagnostics_request_file`, and the last-pass fields do not exist yet.

- [ ] **Step 3: Implement the minimal path and status changes.**

In `write_status`, read the prior status before replacing it, copy existing `last_scan_at` and `last_scan_stats`, and update those fields only when `state == STATE_IDLE and stats is not None`. Use one UTC timestamp for the current status and the completed-pass timestamp.

In `hotkey.py`, add:

```python
def request_diagnostics(paths: AppPaths) -> bool:
    pid = read_gui_pid(paths)
    if pid is None or not _process_alive(pid):
        return False
    paths.ensure()
    paths.diagnostics_request_file.touch()
    return True


def consume_diagnostics_request(paths: AppPaths) -> bool:
    try:
        paths.diagnostics_request_file.unlink()
        return True
    except OSError:
        return False
```

Re-export both functions from `gui.services`. In the existing GUI show-request poll, consume the diagnostics flag and call `_show_diagnostics()` when present. Do not change the existing global-hotkey request format.

- [ ] **Step 4: Run the focused tests and the existing background/hotkey tests.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_background.py tests\test_hotkey.py tests\test_gui.py -q
```

Expected: all selected tests pass; Tk tests may be skipped by the existing `tk_guard` fixture.

- [ ] **Step 5: Checkpoint the task without committing.**

Run `git diff --check` and keep the changes uncommitted until the final phase commit.

---

### Task 2: Implement the platform-independent background state service

**Files:**
- Create: `src/universal_search/background_service.py`
- Test: `tests/test_background_service.py`

**Interfaces:**
- Consumes the existing `background.read_status`, `background.read_lock_pid`, `background.process_alive`, pause/stop marker helpers, and `background.start/stop/pause/resume`.
- Produces the `BackgroundStatus` and `BackgroundService` interfaces in the Shared Interfaces section.

- [ ] **Step 1: Write failing tests for every state and stale-lock case.**

Use an injected `registry` and monkeypatch `background.process_alive`/`background.get_autostart` so tests never touch a real Windows registry. Cover:

```python
def test_status_is_starting_when_live_worker_has_not_written_state(...): ...
def test_status_is_indexing_and_keeps_last_scan(...): ...
def test_status_is_paused_when_marker_exists(...): ...
def test_status_is_stopping_when_stop_marker_and_live_worker(...): ...
def test_status_is_error_from_worker_payload(...): ...
def test_dead_pid_is_stopped_with_stale_lock_problem(...): ...
def test_corrupt_status_does_not_raise(...): ...
def test_actions_delegate_and_refuse_when_stopped(...): ...
def test_notification_policy_is_quiet_for_user_actions(...): ...
```

The assertions must check the concrete state, `can_*` properties, pending text, and notification result, not private implementation details.

- [ ] **Step 2: Run the new tests and verify they fail because the service is absent.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_background_service.py -q
```

Expected: collection or import failure for `universal_search.background_service`.

- [ ] **Step 3: Implement the minimal state model and service.**

Use frozen dataclasses and these precedence rules:

```python
if stopping and alive:
    state = "stopping"
elif not alive:
    state = "stopped"
elif paused:
    state = "paused"
elif worker_state in {"indexing", "idle", "error"}:
    state = worker_state
else:
    state = "starting"
```

Set `stale_lock` only when a lock PID exists and is not alive. Read autostart through the injected registry and catch platform import/registry errors into `False`. Keep all file reads inside defensive `try/except` blocks. The notification helper must return text only for a new error, a newly visible hotkey problem, or a worker disappearing from `indexing`/`starting`; it must return `None` for start/stop/pause/resume transitions.

- [ ] **Step 4: Run the focused tests and verify green.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_background_service.py -q
```

Expected: all state-service tests pass.

- [ ] **Step 5: Add a small long-pass test and implement the timing helper.**

Create two ISO timestamps two minutes apart, transition `indexing -> idle`, and assert the helper returns `True` at the default 120-second threshold and `False` below it. Parse timestamps with `datetime.fromisoformat`; malformed timestamps must return `False`.

- [ ] **Step 6: Run the service tests again and checkpoint.**

Run the focused test file and `git diff --check`; do not commit yet.

---

### Task 3: Implement and test the Win32 tray adapter

**Files:**
- Create: `src/universal_search/platforms/tray.py`
- Test: `tests/test_windows_tray.py`

**Interfaces:**
- `MenuItem(command, label, enabled=True, separator=False)`.
- `TrayBackend`, `NullTray`, and `WindowsTray` with the exact methods listed in the Shared Interfaces section.
- Windows constants and callback command IDs are private implementation details.

- [ ] **Step 1: Write failing tests with fake DLL objects.**

The fakes must record `Shell_NotifyIconW`, `CreatePopupMenu`, `AppendMenuW`, `TrackPopupMenu`, `DestroyMenu`, and notification calls. Cover:

```python
def test_null_tray_reports_unavailable(): ...
def test_windows_tray_adds_updates_and_removes_icon(): ...
def test_windows_tray_disables_menu_items(): ...
def test_windows_tray_notification_uses_info_flag(): ...
def test_windows_tray_maps_right_click_command(): ...
def test_windows_tray_cleanup_removes_icon_after_error(): ...
```

For the message-loop test, inject a `GetMessageW` fake that returns one `WM_QUIT`-equivalent result and records `DestroyWindow`/`Shell_NotifyIconW(NIM_DELETE)`.

- [ ] **Step 2: Run the adapter tests and verify they fail.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_windows_tray.py -q
```

Expected: import failure because `platforms.tray` does not yet exist.

- [ ] **Step 3: Implement the adapter without a new dependency.**

Keep module import safe on non-Windows by importing `ctypes` only inside Windows-specific methods. Use a real `NOTIFYICONDATAW` structure for the native path, `LoadIconW(None, IDI_APPLICATION)` as the fallback icon, and a `WNDCLASSW` callback kept alive on the `WindowsTray` instance.

The callback must handle:

- `WM_APP + 1` tray events: right-click/context-menu opens the current menu, double-click emits `CMD_OPEN`;
- `WM_COMMAND`: emit the low-word command ID;
- `WM_TIMER`: call the injected `tick` callback;
- `WM_DESTROY`: call `PostQuitMessage(0)`.

`run()` must create the window, add the icon, install a timer, pump messages until `WM_QUIT`, then kill the timer, destroy the window, and delete the icon in `finally`. It must return a non-zero result when the notification area is unavailable or refuses the icon.

- [ ] **Step 4: Run adapter tests and a Windows-only smoke test.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_windows_tray.py -q
```

On Windows, additionally run a smoke test that instantiates `WindowsTray(platform="win32")`, checks `available()` and the native `NOTIFYICONDATAW` size, and does not call `Shell_NotifyIconW(NIM_ADD, ...)`.

- [ ] **Step 5: Checkpoint without committing.**

Run pyflakes on the adapter and `git diff --check`.

---

### Task 4: Implement the platform-independent tray controller

**Files:**
- Create: `src/universal_search/tray.py`
- Modify: `src/universal_search/platforms/tray.py` (native termination request required by clean Exit)
- Test: `tests/test_tray.py`
- Test: `tests/test_windows_tray.py` (termination signaling regression)

**Interfaces:**
- Consumes `BackgroundService`, `TrayBackend`, existing `hotkey.request_show`/`launch_gui`, and the new diagnostics request helper.
- Produces `TrayController`, `CommandResult`, `build_menu`, and `run_tray`.

- [ ] **Step 1: Write failing tests for menu and command dispatch.**

Use a fake service and backend. Cover the exact menu rules:

```python
def test_menu_contains_state_open_quick_and_exit(): ...
def test_menu_offers_resume_only_when_paused(): ...
def test_menu_offers_start_only_when_stopped(): ...
def test_menu_offers_stop_and_pause_when_running(): ...
def test_open_reuses_live_gui_before_launching(): ...
def test_quick_search_uses_same_activation_path(): ...
def test_diagnostics_requests_existing_gui_view(): ...
def test_settings_opens_existing_window(): ...
def test_pause_resume_start_stop_map_to_service_actions(): ...
def test_unknown_command_is_rejected_without_side_effects(): ...
```

- [ ] **Step 2: Write failing tests for lock, ownership, and shutdown.**

Cover a live PID, a stale PID, atomic claim failure, idempotent release, stopping a worker started by this tray, and leaving an externally started worker running. Assert the service call sequence, not private fields.

- [ ] **Step 3: Run the controller tests and verify they fail.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_tray.py -q
```

Expected: import failure because `universal_search.tray` is not implemented.

- [ ] **Step 4: Implement the controller.**

Use a tray PID lock with stale-state recovery that cannot delete a replacement owner's lock during a race. `run_command` must return a `CommandResult` for known and unknown commands. `refresh` must call the notification policy and tooltip update without raising. `exit` must be idempotent, stop only the exact worker PID started by this tray, report stop failure, release the lock, and never touch a GUI PID. The native backend must expose `request_exit()` so the Windows message loop actually terminates after Exit; unexpected backend failure cleanup may release the tray lock but must not stop indexing.

- [ ] **Step 5: Implement `run_tray` with safe backend selection.**

Select `WindowsTray` on `win32` and `NullTray` elsewhere. Return an actionable non-zero code when no notification area exists. Claim before entering the backend loop and release in `finally`. Pass `controller.refresh` as the timer callback so the platform loop stays free of application policy.

- [ ] **Step 6: Run the controller tests and the complete new test set.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_tray.py tests\test_background_service.py tests\test_windows_tray.py -q
```

Expected: all phase-021 tests pass.

- [ ] **Step 7: Checkpoint without committing.**

Run pyflakes on `src` and `tests`; do not commit yet.

---

### Task 5: Expose the tray through the CLI and verify integration

**Files:**
- Modify: `src/universal_search/cli.py:156-167, 292-387`
- Test: `tests/test_cli.py`

**Interfaces:**
- New parser command: `universal-search tray`.
- `_tray_command(args) -> int` calls `setup_logging()`, constructs the platform backend, and returns `run_tray()`.

- [ ] **Step 1: Write a failing CLI test.**

Monkeypatch `universal_search.cli._tray_command` or the tray entry point and assert:

```python
def test_cli_tray_subcommand_dispatches(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_tray_command", lambda args: called.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["universal-search", "tray"])
    cli.main()
    assert len(called) == 1
```

Also assert `universal-search --help` contains `tray`.

- [ ] **Step 2: Run the CLI test and verify it fails.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_cli.py::test_cli_tray_subcommand_dispatches -q
```

Expected: argparse rejects the unknown `tray` command.

- [ ] **Step 3: Implement the parser and dispatcher.**

Add `sub.add_parser("tray", help="show the Windows notification-area controller")`, route `args.command == "tray"` before the search fallback, and return the tray exit code. `_tray_command` must use `AppPaths.discover()` and the real adapter selection, not open a second database.

- [ ] **Step 4: Run CLI and integration tests.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_cli.py tests\test_tray.py tests\test_background_service.py tests\test_windows_tray.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Verify no duplicate indexer process can be created.**

Run the existing single-instance tests together with the new tray command tests:

```powershell
.venv\Scripts\python -m pytest tests\test_background.py::test_lock_is_exclusive_and_released tests\test_background.py::test_run_refuses_a_second_live_instance tests\test_tray.py -q
```

Expected: all pass.

- [ ] **Step 6: Checkpoint without committing.**

Run `git diff --check` and preserve the one-final-commit rule.

---

### Task 6: Synchronize documentation and run the release-quality gate

**Files:**
- Create: `docs/development/021-tray-and-background-experience-report.md`
- Modify: `docs/README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/RELEASE.md` only if the new CLI command changes its command inventory.

**Interfaces:**
- Documentation must state that the tray is optional, autostart still starts `indexer run`, settings/diagnostics remain in the existing window, and no new runtime dependency was added.

- [ ] **Step 1: Update the phase index and roadmap.**

Add phase 021 to `docs/README.md`, mark the tray item complete in `docs/ROADMAP.md`, add the 021 report link, and leave phases 022-030 pending. Do not claim 022-030 are implemented.

- [ ] **Step 2: Document architecture and limitations.**

Update `docs/ARCHITECTURE.md` with the service/controller/platform boundary, the three independent PID locks, the native Win32 adapter, and the no-service/no-cloud decision. Add the exact `universal-search tray` command to the README and release command inventory if applicable.

- [ ] **Step 3: Write the phase report with measured results.**

Include files changed, state-transition table, notification policy, test counts from the actual run, pyflakes result, Windows smoke result, packaging result, and known limitations. Do not put unmeasured claims in the report.

- [ ] **Step 4: Run the complete quality gate.**

Run:

```powershell
.venv\Scripts\python -m pytest tests\ -q
.venv\Scripts\python -m pyflakes src tests benchmarks evaluation
```

Expected: the full suite passes and pyflakes is clean. Record the actual counts, including skips.

- [ ] **Step 5: Run the existing packaging smoke.**

Build with the repository's exact command:

```powershell
.venv\Scripts\python -m PyInstaller packaging\universal-search.spec --noconfirm --clean
```

Run the existing packaged smoke script at `C:\Users\dmart\AppData\Local\Temp\opencode\smoke020.py`, then invoke `dist\UniversalSearch\UniversalSearch.exe tray --help` and verify that the existing `indexer run` autostart command is unchanged. Do not leave build artifacts or credentials in the repository and do not push.

- [ ] **Step 6: Review the final diff and create the single phase commit.**

Run:

```powershell
git diff --check
git status --short
git diff --stat
git diff -- src tests docs README.md CHANGELOG.md
```

Confirm no temporary probe files, databases, logs, or unrelated user changes are staged. Then run exactly one phase commit:

```powershell
git add src tests docs README.md CHANGELOG.md packaging
git commit -m "feat: add tray and background experience phase (021)"
```

Do not run `git push`.

- [ ] **Step 7: Verify the commit and clean tree.**

Run:

```powershell
git status --short
git log -1 --oneline
git log origin/main..HEAD --oneline
```

Expected: the phase commit is present, the tree is clean except for any explicitly preserved user work, and no push has occurred.
