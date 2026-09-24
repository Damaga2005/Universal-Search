"""Contract tests for the optional Windows notification-area adapter."""

import ctypes
import os
import sys
import threading
import time

import pytest

from universal_search.platforms.tray import MenuItem, NullTray, WindowsTray


# Win32 values are repeated here instead of imported from the implementation so
# the tests assert the native protocol rather than mirroring private constants.
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_CONTEXTMENU = 0x007B
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
TRAY_EVENT = WM_APP + 1

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001

MF_GRAYED = 0x0001
MF_SEPARATOR = 0x0800


class FakeShell32:
    def __init__(self, *, add_result=True) -> None:
        self.add_result = add_result
        self.calls = []

    def Shell_NotifyIconW(self, message, data_pointer):  # noqa: N802 - Win32 name
        data = data_pointer._obj
        self.calls.append(
            {
                "message": int(message),
                "size": ctypes.sizeof(data),
                "flags": int(data.uFlags),
                "callback": int(data.uCallbackMessage),
                "hwnd": data.hWnd,
                "icon": data.hIcon,
                "tip": data.szTip,
                "info": data.szInfo,
                "info_title": data.szInfoTitle,
                "info_flags": int(data.dwInfoFlags),
            }
        )
        if int(message) == NIM_ADD:
            return self.add_result
        return True


class FakeUser32:
    def __init__(
        self,
        *,
        messages=(),
        get_message_results=None,
        register_result=1,
        window_handle=101,
        post_quit_results=None,
    ) -> None:
        self.messages = list(messages)
        self.get_message_results = list(get_message_results or [])
        self.post_quit_results = list(post_quit_results or [])
        self.register_result = register_result
        self.window_handle = window_handle
        self.get_message_calls = []
        self.dispatch_calls = []
        self.window_class = None
        self.timer_id = 77
        self.menu_handle = 901
        self.track_result = 0
        self.on_get_message = None
        self.on_unregister = None
        self.raise_from_get_message = None
        self.calls = {
            "load_icon": [],
            "load_image": [],
            "destroy_icon": [],
            "register_window_message": [],
            "register_class": [],
            "create_window": [],
            "set_timer": [],
            "kill_timer": [],
            "destroy_window": [],
            "unregister_class": [],
            "post_quit": [],
            "create_popup": [],
            "append_menu": [],
            "get_cursor": [],
            "set_foreground": [],
            "track_popup": [],
            "post_message": [],
            "destroy_menu": [],
            "default_window": [],
        }

    def LoadIconW(self, instance, name):  # noqa: N802 - Win32 name
        self.calls["load_icon"].append((instance, name))
        return 32512

    def LoadImageW(self, instance, name, image_type, width, height, flags):  # noqa: N802
        self.calls["load_image"].append(
            (instance, name, image_type, width, height, flags)
        )
        return 555

    def DestroyIcon(self, icon):  # noqa: N802 - Win32 name
        self.calls["destroy_icon"].append(icon)

    def RegisterWindowMessageW(self, name):  # noqa: N802 - Win32 name
        self.calls["register_window_message"].append(name)
        return 0xABCD

    def RegisterClassW(self, window_class):  # noqa: N802 - Win32 name
        self.calls["register_class"].append(window_class)
        if self.register_result:
            self.window_class = window_class._obj
        return self.register_result

    def UnregisterClassW(self, class_name, instance):  # noqa: N802 - Win32 name
        if self.on_unregister is not None:
            self.on_unregister()
        self.calls["unregister_class"].append(
            (class_name, instance, self.window_class)
        )
        return True

    def CreateWindowExW(  # noqa: N802 - Win32 name
        self,
        extended_style,
        class_name,
        window_name,
        style,
        x,
        y,
        width,
        height,
        parent,
        menu,
        instance,
        parameter,
    ):
        self.calls["create_window"].append(
            (
                extended_style,
                class_name,
                window_name,
                style,
                x,
                y,
                width,
                height,
                parent,
                menu,
                instance,
                parameter,
            )
        )
        return self.window_handle

    def GetMessageW(self, message_pointer, window, minimum, maximum):  # noqa: N802
        self.get_message_calls.append((window, minimum, maximum))
        if self.on_get_message is not None:
            self.on_get_message()
        if self.raise_from_get_message is not None:
            error = self.raise_from_get_message
            self.raise_from_get_message = None
            raise error
        if self.get_message_results:
            result = self.get_message_results.pop(0)
        elif self.messages:
            result = 1
        else:
            result = 0
        if result <= 0:
            message_pointer._obj.message = int(result)
            return result
        message, wparam, lparam = self.messages.pop(0)
        message_pointer._obj.message = message
        message_pointer._obj.wParam = wparam
        message_pointer._obj.lParam = lparam
        return 1

    def TranslateMessage(self, message_pointer):  # noqa: N802 - Win32 name
        return 0

    def DispatchMessageW(self, message_pointer):  # noqa: N802 - Win32 name
        self.dispatch_calls.append(message_pointer._obj.message)
        message = message_pointer._obj
        callback = self.window_class.lpfnWndProc
        result = callback(message.hwnd, message.message, message.wParam, message.lParam)
        return result

    def DefWindowProcW(self, window, message, wparam, lparam):  # noqa: N802
        self.calls["default_window"].append((window, message, wparam, lparam))
        return 0

    def SetTimer(  # noqa: N802 - Win32 name
        self, window, timer_id, elapsed, callback
    ):
        self.calls["set_timer"].append((window, timer_id, elapsed, callback))
        return self.timer_id

    def KillTimer(self, window, timer_id):  # noqa: N802 - Win32 name
        self.calls["kill_timer"].append((window, timer_id))

    def DestroyWindow(self, window):  # noqa: N802 - Win32 name
        self.calls["destroy_window"].append(window)

    def PostQuitMessage(self, exit_code):  # noqa: N802 - Win32 name
        self.calls["post_quit"].append(exit_code)
        if self.post_quit_results:
            return self.post_quit_results.pop(0)
        return True

    def CreatePopupMenu(self):  # noqa: N802 - Win32 name
        self.calls["create_popup"].append(())
        return self.menu_handle

    def AppendMenuW(self, menu, flags, command, label):  # noqa: N802 - Win32 name
        self.calls["append_menu"].append((menu, flags, command, label))
        return True

    def GetCursorPos(self, point_pointer):  # noqa: N802 - Win32 name
        self.calls["get_cursor"].append(point_pointer)
        point_pointer._obj.x = 17
        point_pointer._obj.y = 23
        return True

    def SetForegroundWindow(self, window):  # noqa: N802 - Win32 name
        self.calls["set_foreground"].append(window)
        return True

    def TrackPopupMenu(  # noqa: N802 - Win32 name
        self,
        menu,
        flags,
        x,
        y,
        reserved,
        window,
        rectangle,
    ):
        self.calls["track_popup"].append(
            (menu, flags, x, y, reserved, window, rectangle)
        )
        return self.track_result

    def PostMessageW(self, window, message, wparam, lparam):  # noqa: N802
        self.calls["post_message"].append((window, message, wparam, lparam))
        return True

    def DestroyMenu(self, menu):  # noqa: N802 - Win32 name
        self.calls["destroy_menu"].append(menu)


class FakeKernel32:
    def __init__(self) -> None:
        self.calls = []
        self.instance = 606

    def GetModuleHandleW(self, name):  # noqa: N802 - Win32 name
        self.calls.append(name)
        return self.instance


def run_until_destroyed(user32, *messages):
    user32.messages = list(messages)
    user32.get_message_results = [1] * len(messages) + [0]


def test_null_tray_reports_unavailable():
    tray = NullTray()

    assert tray.available() is False
    assert tray.request_exit() is False


def test_windows_tray_adds_updates_and_removes_icon():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(user32, (WM_DESTROY, 0, 0))
    updated = []

    def update_once():
        if not updated:
            updated.append(tray.update_tooltip("Indexing 12 files"))

    user32.on_get_message = update_once

    result = tray.run(lambda command: None, [], "Universal Search")

    assert result == 0
    assert [call["message"] for call in shell.calls] == [
        NIM_ADD,
        NIM_MODIFY,
        NIM_DELETE,
    ]
    added = shell.calls[0]
    assert added["size"] >= 956
    assert added["flags"] & NIF_MESSAGE
    assert added["flags"] & NIF_ICON
    assert added["flags"] & NIF_TIP
    assert added["callback"] == TRAY_EVENT
    assert shell.calls[1]["flags"] == NIF_TIP
    assert shell.calls[1]["tip"] == "Indexing 12 files"
    assert user32.calls["load_icon"] == [(None, 32512)]
    assert user32.calls["destroy_icon"] == []
    assert user32.calls["kill_timer"] == [(101, 77)]
    assert user32.calls["destroy_window"] == [101]


def test_windows_tray_readds_icon_after_taskbar_created():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(
        user32,
        (0xABCD, 0, 0),  # RegisterWindowMessageW("TaskbarCreated")
        (WM_DESTROY, 0, 0),
    )

    result = tray.run(lambda command: None, [], "Universal Search")

    assert result == 0
    assert user32.calls["register_window_message"] == ["TaskbarCreated"]
    assert [call["message"] for call in shell.calls] == [
        NIM_ADD,
        NIM_ADD,
        NIM_DELETE,
    ]
    assert shell.calls[1]["flags"] & NIF_MESSAGE
    assert shell.calls[1]["flags"] & NIF_ICON
    assert shell.calls[1]["flags"] & NIF_TIP


@pytest.mark.parametrize("tray_event", [WM_RBUTTONUP, WM_CONTEXTMENU])
def test_windows_tray_disables_menu_items(tray_event):
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(
        user32,
        (TRAY_EVENT, 1, tray_event),
        (WM_DESTROY, 0, 0),
    )
    menu = [
        MenuItem(10, "Enabled"),
        MenuItem(11, "Disabled", enabled=False),
        MenuItem(0, "", separator=True),
        MenuItem(12, "Exit"),
    ]

    result = tray.run(lambda command: None, menu, "Universal Search")

    assert result == 0
    assert user32.calls["append_menu"] == [
        (901, 0, 10, "Enabled"),
        (901, MF_GRAYED, 11, "Disabled"),
        (901, MF_SEPARATOR, 0, None),
        (901, 0, 12, "Exit"),
    ]
    assert user32.calls["track_popup"][0][0] == 901
    assert user32.calls["track_popup"][0][2:4] == (17, 23)
    assert user32.calls["track_popup"][0][5] == 101
    assert user32.calls["destroy_menu"] == [901]


def test_windows_tray_notification_uses_info_flag():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(
        user32,
        (WM_TIMER, 77, 0),
        (WM_DESTROY, 0, 0),
    )
    user32.on_get_message = lambda: tray.notify("Indexing problem", "One root is offline")

    result = tray.run(lambda command: None, [], "Universal Search")

    assert result == 0
    notification = shell.calls[1]
    assert notification["message"] == NIM_MODIFY
    assert notification["flags"] == NIF_INFO
    assert notification["info_flags"] == NIIF_INFO
    assert notification["info_title"] == "Indexing problem"
    assert notification["info"] == "One root is offline"


def test_windows_tray_maps_right_click_command():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    user32.track_result = 0x1234
    commands = []
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(
        user32,
        (WM_COMMAND, 0xABCD0017, 0),
        (TRAY_EVENT, 1, WM_RBUTTONUP),
        (WM_DESTROY, 0, 0),
    )

    result = tray.run(commands.append, [MenuItem(0x1234, "Selected")], "Tray")

    assert result == 0
    assert commands == [0x17, 0x1234]


def test_windows_tray_double_click_opens_application():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    commands = []
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(
        user32,
        (TRAY_EVENT, 1, WM_LBUTTONDBLCLK),
        (WM_DESTROY, 0, 0),
    )

    result = tray.run(commands.append, [], "Tray")

    assert result == 0
    assert commands == [1]


def test_windows_tray_exit_command_requests_native_quit():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    commands = []
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(
        user32,
        (WM_COMMAND, 0xABCD0009, 0),
        (WM_DESTROY, 0, 0),
    )

    result = tray.run(
        lambda command: commands.append(command) or True,
        [MenuItem(9, "Exit")],
        "Tray",
    )

    assert result == 0
    assert commands == [9]
    assert user32.calls["post_quit"] == [0, 0]


def test_windows_tray_request_exit_retries_after_failed_post():
    shell = FakeShell32()
    user32 = FakeUser32(post_quit_results=[False, True, True])
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(user32, (WM_DESTROY, 0, 0))
    results = []

    def request_twice():
        if results:
            return
        results.append(tray.request_exit())
        results.append(tray.request_exit())

    user32.on_get_message = request_twice

    result = tray.run(lambda command: None, [], "Tray")

    assert result == 0
    assert results == [False, True]
    assert user32.calls["post_quit"] == [0, 0, 0]


def test_windows_tray_runs_without_optional_menu_helpers():
    shell = FakeShell32()
    inner_user32 = FakeUser32()
    kernel32 = FakeKernel32()

    class User32WithoutMenuHelpers:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            if name in {"GetCursorPos", "SetForegroundWindow", "PostMessageW"}:
                raise AttributeError(name)
            return getattr(self.wrapped, name)

    user32 = User32WithoutMenuHelpers(inner_user32)
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(inner_user32, (WM_DESTROY, 0, 0))

    assert tray.run(lambda command: None, [], "Tray") == 0
    assert inner_user32.calls["post_quit"] == [0]


def test_windows_tray_message_loop_handles_destroy_and_quit():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    ticks = []
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    run_until_destroyed(user32, (WM_TIMER, 77, 0), (WM_DESTROY, 0, 0))

    result = tray.run(
        lambda command: None,
        [],
        "Tray",
        tick=lambda: ticks.append("tick"),
    )

    assert result == 0
    assert ticks == ["tick"]
    assert user32.dispatch_calls == [WM_TIMER, WM_DESTROY]
    assert user32.calls["post_quit"] == [0]
    assert [call["message"] for call in shell.calls] == [NIM_ADD, NIM_DELETE]
    assert user32.calls["kill_timer"] == [(101, 77)]
    assert user32.calls["destroy_window"] == [101]
    assert tray._wndproc is not None
    assert tray._wndclass is not None


def test_windows_tray_unregisters_each_repeated_window_class():
    shell = FakeShell32()
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )
    references_at_unregister = []

    def record_references():
        references_at_unregister.append(
            (tray._wndproc, tray._wndclass, tray._class_name_buffer)
        )

    user32.on_unregister = record_references

    for _ in range(2):
        run_until_destroyed(user32, (WM_DESTROY, 0, 0))
        assert tray.run(lambda command: None, [], "Tray") == 0

    created_names = [call[1] for call in user32.calls["create_window"]]
    unregistered = user32.calls["unregister_class"]
    assert len(user32.calls["register_class"]) == 2
    assert [(name, instance) for name, instance, _ in unregistered] == [
        (name, 606) for name in created_names
    ]
    assert all(window_class is not None for _, _, window_class in unregistered)
    assert len(references_at_unregister) == 2
    assert all(
        wndproc is not None and wndclass is not None and name_buffer is not None
        for wndproc, wndclass, name_buffer in references_at_unregister
    )
    assert user32.calls["destroy_window"] == [101, 101]
    assert [call["message"] for call in shell.calls] == [
        NIM_ADD,
        NIM_DELETE,
        NIM_ADD,
        NIM_DELETE,
    ]


def test_windows_tray_unregisters_class_when_window_creation_fails():
    shell = FakeShell32()
    user32 = FakeUser32(window_handle=0)
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )

    result = tray.run(lambda command: None, [], "Tray")

    assert result != 0
    assert len(user32.calls["register_class"]) == 1
    assert len(user32.calls["create_window"]) == 1
    registered_name = user32.calls["register_class"][0]._obj.lpszClassName
    assert user32.calls["unregister_class"] == [
        (registered_name, 606, user32.window_class)
    ]
    assert user32.calls["destroy_window"] == []
    assert shell.calls == []


def test_windows_tray_does_not_unregister_failed_registration():
    shell = FakeShell32()
    user32 = FakeUser32(register_result=0)
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )

    result = tray.run(lambda command: None, [], "Tray")

    assert result != 0
    assert len(user32.calls["register_class"]) == 1
    assert user32.calls["create_window"] == []
    assert user32.calls["unregister_class"] == []
    assert user32.calls["destroy_window"] == []
    assert shell.calls == []


def test_windows_tray_cleanup_removes_icon_after_error():
    shell = FakeShell32()
    user32 = FakeUser32()
    user32.raise_from_get_message = RuntimeError("message pump failed")
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        icon_path="custom.ico",
        user32=user32,
        shell32=shell,
        kernel32=kernel32,
        platform="win32",
    )

    with pytest.raises(RuntimeError, match="message pump failed"):
        tray.run(lambda command: None, [], "Tray")

    assert [call["message"] for call in shell.calls] == [NIM_ADD, NIM_DELETE]
    assert user32.calls["kill_timer"] == [(101, 77)]
    assert user32.calls["destroy_window"] == [101]
    assert user32.calls["destroy_icon"] == [555]
    assert user32.calls["load_image"][0][1] == "custom.ico"
    assert tray.update_tooltip("Too late") is False


def test_windows_tray_returns_nonzero_when_icon_is_refused():
    shell = FakeShell32(add_result=False)
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    tray = WindowsTray(
        user32=user32, shell32=shell, kernel32=kernel32, platform="win32"
    )

    result = tray.run(lambda command: None, [], "Tray")

    assert result != 0
    assert [call["message"] for call in shell.calls] == [NIM_ADD, NIM_DELETE]
    assert user32.get_message_calls == []
    assert user32.calls["set_timer"] == []
    assert user32.calls["kill_timer"] == []
    assert user32.calls["destroy_window"] == [101]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only native smoke test")
def test_windows_tray_native_structure_smoke_without_posting_icon():
    real_shell = ctypes.WinDLL("shell32", use_last_error=True)

    class NoPostShell:
        def __getattr__(self, name):
            return getattr(real_shell, name)

        def Shell_NotifyIconW(self, *args):  # noqa: N802 - Win32 name
            raise AssertionError("the smoke test must not post a notification icon")

    tray = WindowsTray(platform="win32", shell32=NoPostShell())

    assert tray.available() is True
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    expected_size = 956 + (5 * (pointer_size - 4))
    assert ctypes.sizeof(tray._notify_icon_data_type()) == expected_size


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only native tray smoke")
@pytest.mark.skipif(
    os.environ.get("UNIVERSAL_SEARCH_TRAY_NATIVE_SMOKE") != "1",
    reason="set UNIVERSAL_SEARCH_TRAY_NATIVE_SMOKE=1 for a visible native icon",
)
def test_windows_tray_native_add_command_exit_and_delete_smoke():
    real_shell = ctypes.WinDLL("shell32", use_last_error=True)
    real_user32 = ctypes.WinDLL("user32", use_last_error=True)
    real_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    real_user32.PostMessageW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    )
    real_user32.PostMessageW.restype = ctypes.c_int
    calls: list[int] = []

    class RecordingShell:
        def __getattr__(self, name):
            return getattr(real_shell, name)

        def Shell_NotifyIconW(self, message, data_pointer):  # noqa: N802
            calls.append(int(message))
            return bool(real_shell.Shell_NotifyIconW(message, data_pointer))

    tray = WindowsTray(
        user32=real_user32,
        shell32=RecordingShell(),
        kernel32=real_kernel32,
        platform="win32",
    )
    commands: list[int] = []

    def dispatch_exit_when_ready() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and tray._window_handle is None:
            time.sleep(0.01)
        if tray._window_handle is None:
            return
        window = tray._window_handle
        handle = window.value if hasattr(window, "value") else window
        real_user32.PostMessageW(handle, WM_COMMAND, 9, 0)

    poster = threading.Thread(target=dispatch_exit_when_ready, daemon=True)
    poster.start()
    result = tray.run(
        lambda command: commands.append(command) or True,
        [MenuItem(9, "Exit")],
        "Universal Search native smoke",
    )
    poster.join(timeout=5)

    assert result == 0
    assert commands == [9]
    assert calls == [NIM_ADD, NIM_DELETE]
    assert not poster.is_alive()
