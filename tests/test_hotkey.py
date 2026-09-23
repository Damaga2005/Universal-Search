"""Phase 009: global hotkey, file-based show signaling, launch commands.

The Win32 calls are injected as fakes, so registration, dispatch, failure
and shutdown are all verified without grabbing a real hotkey.
"""

import sys
import threading
import time
from dataclasses import replace

import pytest

from universal_search.appconfig import (
    MAX_RECENT_QUERIES,
    AppConfig,
    AppPaths,
    remember_query,
)
from universal_search.background import BackgroundIndexer
from universal_search.hotkey import (
    HOTKEY_ID,
    MOD_ALT,
    MOD_CONTROL,
    WM_HOTKEY,
    WM_QUIT,
    HotkeyServer,
    clear_gui_pid,
    consume_show_request,
    gui_command,
    parse_hotkey,
    read_gui_pid,
    request_show,
    write_gui_pid,
)


# -- fake Win32 ----------------------------------------------------------------

class FakeUser32:
    def __init__(self, register_ok: bool = True, hotkeys: int = 0) -> None:
        self.register_ok = register_ok
        self.hotkeys = hotkeys  # WM_HOTKEY messages to deliver first
        self.calls: list = []
        self._quit = threading.Event()

    def PeekMessageW(self, *_args):
        self.calls.append("peek")
        return 0

    def RegisterHotKey(self, _hwnd, ident, modifiers, vk):
        self.calls.append(("register", ident, modifiers, vk))
        return 1 if self.register_ok else 0

    def GetMessageW(self, message, *_args):
        if self.hotkeys > 0:
            self.hotkeys -= 1
            message._obj.message = WM_HOTKEY
            return 1
        # Block like the real loop until the worker posts WM_QUIT.
        self._quit.wait(timeout=2.0)
        return 0

    def TranslateMessage(self, *_args):
        return 1

    def DispatchMessageW(self, *_args):
        return 0

    def UnregisterHotKey(self, _hwnd, ident):
        self.calls.append(("unregister", ident))
        return 1

    def PostThreadMessageW(self, thread_id, msg, _wparam, _lparam):
        self.calls.append(("post", thread_id, msg))
        self._quit.set()
        return 1


class FakeKernel32:
    def GetCurrentThreadId(self):
        return 4242


def make_server(on_pressed=None, **kwargs):
    user32 = FakeUser32(**kwargs)
    server = HotkeyServer(
        "ctrl+alt+s",
        on_pressed or (lambda: None),
        user32=user32,
        kernel32=FakeKernel32(),
    )
    return server, user32


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# -- spec parsing --------------------------------------------------------------

def test_parse_hotkey_valid_specs() -> None:
    modifiers, vk = parse_hotkey("ctrl+alt+s")
    assert modifiers == (MOD_CONTROL | MOD_ALT)
    assert vk == ord("S")
    # case- and space-insensitive
    modifiers, vk = parse_hotkey(" CTRL + Alt + S ")
    assert modifiers == (MOD_CONTROL | MOD_ALT)
    assert vk == ord("S")
    assert parse_hotkey("win+f5")[1] == 0x74
    assert parse_hotkey("ctrl+f12")[1] == 0x7B
    assert parse_hotkey("shift+1")[1] == ord("1")
    assert parse_hotkey("alt+space")[1] == 0x20
    assert parse_hotkey("control+enter")[1] == 0x0D


def test_parse_hotkey_rejects_unusable_specs() -> None:
    bad_specs = (
        "",
        "   ",
        "ctrl+",
        "+s",
        "ctrl+s+",
        "a",  # a bare key would swallow normal typing
        "banana+s",  # unknown modifier
        "ctrl+qwertz",  # unknown key
        "ctrl+alt+f13",  # out of range
        "ctrl + +s",  # empty segment
    )
    for spec in bad_specs:
        with pytest.raises(ValueError):
            parse_hotkey(spec)
    with pytest.raises(ValueError):
        parse_hotkey(None)  # type: ignore[arg-type]


# -- server lifecycle ----------------------------------------------------------

def test_server_registers_and_stops_cleanly() -> None:
    server, user32 = make_server()
    try:
        assert server.start() is True
        assert server.registered is True
        # the registration carried the parsed modifier/key values
        assert ("register", HOTKEY_ID, MOD_CONTROL | MOD_ALT, ord("S")) in (
            user32.calls
        )
        # starting twice never creates a second thread
        thread = server._thread
        server.start()
        assert server._thread is thread
    finally:
        server.stop()
    assert ("unregister", HOTKEY_ID) in user32.calls
    assert ("post", 4242, WM_QUIT) in user32.calls
    assert server.registered is False
    server.stop()  # idempotent


def test_server_dispatches_hotkey_messages() -> None:
    presses: list[int] = []
    delivered = threading.Event()
    server, _user32 = make_server(
        lambda: (presses.append(1), delivered.set()), hotkeys=1
    )
    try:
        assert server.start() is True
        assert delivered.wait(2.0), "WM_HOTKEY never reached the handler"
        assert presses == [1]
    finally:
        server.stop()


def test_handler_exception_is_contained() -> None:
    calls: list[int] = []

    def boom() -> None:
        calls.append(1)
        raise RuntimeError("presentation exploded")

    server, _user32 = make_server(boom, hotkeys=1)
    try:
        assert server.start() is True
        assert wait_for(lambda: calls), "hotkey was never dispatched"
        assert server.registered is True  # worker keeps running regardless
    finally:
        server.stop()


def test_registration_failure_is_not_fatal() -> None:
    server, user32 = make_server(register_ok=False)
    assert server.start() is False  # hotkey already taken by another app
    assert server.registered is False
    assert server.error  # human-readable reason for the log
    server.stop()  # thread already finished: no hang, no crash
    assert ("unregister", HOTKEY_ID) not in user32.calls


def test_stop_without_start_is_safe() -> None:
    HotkeyServer("ctrl+alt+s", lambda: None).stop()


# -- file-based signaling ------------------------------------------------------

def test_request_show_requires_a_live_window(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    assert request_show(paths) is False
    assert not paths.show_request_file.exists()

    write_gui_pid(paths, 999_999_999)  # nonexistent PID
    assert read_gui_pid(paths) == 999_999_999
    assert request_show(paths) is False
    assert not paths.show_request_file.exists()  # no orphan flag


def test_request_show_reaches_a_live_window_exactly_once(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    write_gui_pid(paths)  # our own PID: definitely alive
    assert read_gui_pid(paths) == __import__("os").getpid()

    assert request_show(paths) is True
    assert paths.show_request_file.exists()

    assert consume_show_request(paths) is True
    assert consume_show_request(paths) is False  # consumed exactly once

    clear_gui_pid(paths)
    assert read_gui_pid(paths) is None
    assert not paths.gui_pid_file.exists()


def test_read_gui_pid_garbage(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    paths.gui_pid_file.write_text("not-a-pid", encoding="utf-8")
    assert read_gui_pid(paths) is None
    clear_gui_pid(paths)  # missing file never raises
    assert read_gui_pid(paths) is None


# -- launching -----------------------------------------------------------------

def test_gui_command_from_source() -> None:
    assert not getattr(sys, "frozen", False)  # tests run from source
    assert gui_command() == [
        sys.executable,
        "-m",
        "universal_search.cli",
        "gui",
    ]


def test_gui_command_frozen_prefers_windowed_exe(tmp_path, monkeypatch) -> None:
    fake_exe = tmp_path / "universal-search.exe"
    fake_exe.write_text("console", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    # no windowed sibling: fall back to the explicit command
    assert gui_command() == [str(fake_exe), "gui"]
    # windowed sibling exists: use it so no console flashes
    sibling = tmp_path / "UniversalSearch.exe"
    sibling.write_text("windowed", encoding="utf-8")
    assert gui_command() == [str(sibling)]


# -- worker integration --------------------------------------------------------

class FakeServer:
    def __init__(self, start_result: bool = True) -> None:
        self.spec = "ctrl+alt+s"
        self.error = None
        self.start_result = start_result
        self.started = False
        self.stopped = False

    def start(self) -> bool:
        self.started = True
        return self.start_result

    def stop(self) -> None:
        self.stopped = True


def test_worker_starts_and_stops_injected_hotkey(tmp_path) -> None:
    fake = FakeServer()
    worker = BackgroundIndexer(
        paths=AppPaths(tmp_path / "home"), observers=False, hotkey_server=fake
    )
    worker._start_hotkey()
    assert fake.started
    worker._stop_hotkey()
    assert fake.stopped


def test_worker_respects_disabled_hotkey(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    AppConfig(hotkey_enabled=False).save(paths)
    fake = FakeServer()
    worker = BackgroundIndexer(paths=paths, observers=False, hotkey_server=fake)
    worker._start_hotkey()
    assert not fake.started


def test_worker_survives_invalid_hotkey_config(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure()
    AppConfig(hotkey="banana").save(paths)
    worker = BackgroundIndexer(paths=paths, observers=False)
    worker._start_hotkey()  # must not raise
    assert worker.hotkey_server is None  # never created, indexing continues
    worker._stop_hotkey()  # and shutdown stays safe


def test_worker_continues_when_registration_fails(tmp_path) -> None:
    fake = FakeServer(start_result=False)
    worker = BackgroundIndexer(
        paths=AppPaths(tmp_path / "home"), observers=False, hotkey_server=fake
    )
    worker._start_hotkey()  # logs a warning; never raises
    worker._stop_hotkey()
    assert fake.stopped


def test_hotkey_press_prefers_live_window_else_launches(tmp_path, monkeypatch) -> None:
    from universal_search import background

    paths = AppPaths(tmp_path / "home")
    worker = BackgroundIndexer(paths=paths, observers=False)
    launched: list[bool] = []
    requested: list[object] = []
    monkeypatch.setattr(background, "launch_gui", lambda: launched.append(True))
    monkeypatch.setattr(
        background, "request_show", lambda p: requested.append(p) or False
    )

    worker._on_hotkey_press()
    assert requested == [paths]
    assert launched == [True]  # no live window -> launch one

    requested.clear()
    monkeypatch.setattr(
        background, "request_show", lambda p: requested.append(p) or True
    )
    worker._on_hotkey_press()
    assert len(requested) == 1
    assert launched == [True]  # unchanged: live window presents itself


# -- recent-queries policy and configuration -----------------------------------

def test_remember_query_policy() -> None:
    config = AppConfig()
    config = remember_query(config, "  memoria   final ")
    assert config.recent_queries == ("memoria final",)

    # case-insensitive dedupe moves the repeat to the front
    config = remember_query(config, "MEMORIA FINAL")
    assert config.recent_queries == ("MEMORIA FINAL",)

    # a second, distinct query pushes the old one down
    config = remember_query(config, "fourier")
    assert config.recent_queries == ("fourier", "MEMORIA FINAL")

    # empty queries and disabled features are returned untouched
    assert remember_query(config, "   ") is config
    disabled = AppConfig(recent_queries_enabled=False)
    assert remember_query(disabled, "algo") is disabled


def test_remember_query_caps_the_list() -> None:
    config = AppConfig()
    for number in range(MAX_RECENT_QUERIES + 5):
        config = remember_query(config, f"consulta {number}")
    assert len(config.recent_queries) == MAX_RECENT_QUERIES
    assert config.recent_queries[0] == f"consulta {MAX_RECENT_QUERIES + 4}"


def test_hotkey_and_recent_settings_roundtrip(tmp_path) -> None:
    paths = AppPaths(tmp_path / "home")
    original = replace(
        AppConfig(),
        hotkey="win+f2",
        hotkey_enabled=False,
        recent_queries=("fourier", "memoria final"),
        recent_queries_enabled=False,
    )
    original.save(paths)
    loaded = AppConfig.load(paths)
    assert loaded.hotkey == "win+f2"
    assert loaded.hotkey_enabled is False
    assert loaded.recent_queries == ("fourier", "memoria final")
    assert loaded.recent_queries_enabled is False
