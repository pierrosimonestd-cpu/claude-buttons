"""Win32 helpers to dock a window to the Claude desktop window."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
from collections.abc import Callable, Iterable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindow.restype = wt.HWND

DEFAULT_TARGETS = ("claude.exe",)
MUTEX_NAME = "Local\\ClaudeButtonsBar"
MIN_TARGET_SIZE = (480, 360)  # logical px: skips small popups such as Quick Entry
_GW_OWNER = 4
_GWLP_HWNDPARENT = -8
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WM_HOTKEY = 0x0312
_MOD_ALT, _MOD_CONTROL, _MOD_NOREPEAT = 0x1, 0x2, 0x4000

_EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_exe_cache: dict[int, str] = {}


# --- process / instance -----------------------------------------------------------


def set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def acquire_single_instance() -> bool:
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def is_running() -> bool:
    handle = kernel32.OpenMutexW(0x00100000, False, MUTEX_NAME)  # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


# --- finding the target window ------------------------------------------------------


def _exe_of(hwnd: int) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in _exe_cache:
        return _exe_cache[pid.value]
    name = ""
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value).lower()
        kernel32.CloseHandle(handle)
    if len(_exe_cache) > 512:
        _exe_cache.clear()
    _exe_cache[pid.value] = name
    return name


def _is_target(hwnd: int, targets: Iterable[str]) -> bool:
    if not (
        hwnd
        and user32.IsWindowVisible(hwnd)
        and not user32.GetWindow(hwnd, _GW_OWNER)
        and user32.GetWindowTextLengthW(hwnd) > 0
        and _exe_of(hwnd) in targets
    ):
        return False
    if is_minimized(hwnd):
        return True
    left, top, right, bottom = window_rect(hwnd)
    scale = dpi_scale(hwnd)
    return (right - left) >= MIN_TARGET_SIZE[0] * scale and (bottom - top) >= MIN_TARGET_SIZE[1] * scale


def find_target_window(targets: Iterable[str] = DEFAULT_TARGETS, previous: int | None = None) -> int | None:
    """The target window in the foreground, else the previous one, else the largest."""
    targets = {t.lower() for t in targets}
    fg = user32.GetForegroundWindow()
    if _is_target(fg, targets):
        return fg
    if previous and user32.IsWindow(previous) and _is_target(previous, targets):
        return previous
    found: list[tuple[int, int]] = []

    def callback(hwnd, _):
        if _is_target(hwnd, targets):
            left, top, right, bottom = window_rect(hwnd)
            found.append(((right - left) * (bottom - top), hwnd))
        return True

    user32.EnumWindows(_EnumProc(callback), 0)
    return max(found)[1] if found else None


# --- geometry -------------------------------------------------------------------------


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Visible bounds (without the invisible resize borders of Windows 10/11)."""
    rect = wt.RECT()
    if (
        dwmapi.DwmGetWindowAttribute(
            wt.HWND(hwnd), _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        != 0
    ):
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def dpi_scale(hwnd: int) -> float:
    try:
        return user32.GetDpiForWindow(hwnd) / 96 or 1.0
    except AttributeError:
        return 1.0


# --- our own windows ------------------------------------------------------------------


def toplevel_hwnd(tk_widget) -> int:
    return int(tk_widget.wm_frame(), 16)


def set_owner(hwnd: int, owner: int) -> None:
    """Owned windows stay above their owner and hide/minimize with it."""
    if user32.GetWindowLongPtrW(hwnd, _GWLP_HWNDPARENT) != owner:
        user32.SetWindowLongPtrW(hwnd, _GWLP_HWNDPARENT, owner)


def bring_above(hwnd: int) -> None:
    """Move the window to the top of its z-order band without activating it."""
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOSIZE|NOMOVE|NOACTIVATE


def round_corners(hwnd: int) -> None:
    pref = ctypes.c_int(2)  # DWMWCP_ROUND (Windows 11; ignored elsewhere)
    dwmapi.DwmSetWindowAttribute(
        wt.HWND(hwnd), _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref)
    )


def start_hotkey(key: str, callback: Callable[[], None]) -> None:
    """Register Ctrl+Alt+<key> on a thread with its own message loop."""

    def loop():
        if not user32.RegisterHotKey(None, 1, _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT, ord(key)):
            return  # already taken by another app
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY:
                callback()

    threading.Thread(target=loop, daemon=True).start()


# --- theme ----------------------------------------------------------------------------


def _claude_configs() -> list[str]:
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    paths = [os.path.join(appdata, "Claude", "config.json")]
    packages = os.path.join(local, "Packages")
    if os.path.isdir(packages):  # Microsoft Store (MSIX) install
        for name in os.listdir(packages):
            if name.startswith("Claude_"):
                paths.append(os.path.join(packages, name, "LocalCache", "Roaming", "Claude", "config.json"))
    return [p for p in paths if os.path.isfile(p)]


def claude_theme_light() -> bool:
    """Claude desktop theme (userThemeMode); 'system' or missing = Windows app theme."""
    for path in sorted(_claude_configs(), key=os.path.getmtime, reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                mode = json.load(f).get("userThemeMode")
        except (OSError, ValueError):
            continue
        if mode in ("light", "dark"):
            return mode == "light"
        break
    return windows_light_theme()


def theme_signature() -> tuple:
    return tuple((p, os.path.getmtime(p)) for p in _claude_configs())


def windows_light_theme() -> bool:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return False
