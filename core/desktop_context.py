"""Low-cost, local desktop activity sensing.

On Windows this reads only the last-input timestamp and foreground-window
geometry. When requested, it also reads the foreground process name. It never
captures window titles, screenshots, keystrokes, clipboard contents or text.
"""
from __future__ import annotations

import ntpath
import os
import re

MIN_DESKTOP_IDLE_SECONDS = 120


def _idle_seconds_from_ticks(last_input_tick: int, current_tick: int) -> float:
    """Return elapsed milliseconds using DWORD arithmetic (handles 49-day wrap)."""
    elapsed_ms = ((int(current_tick) - int(last_input_tick)) & 0xFFFFFFFF)
    return elapsed_ms / 1000.0


def _clean_process_name(value: object) -> str:
    """Keep only a short executable basename; discard control characters."""
    raw = str(value or "").replace("/", "\\")
    name = ntpath.basename(raw)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:80]


def _is_fullscreen_rect(left: int, top: int, right: int, bottom: int,
                        screen_width: int, screen_height: int) -> bool:
    """Treat a window covering at least 95% of the display as fullscreen."""
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    sw, sh = int(screen_width), int(screen_height)
    if sw <= 0 or sh <= 0:
        return False
    return (
        int(left) <= 5 and int(top) <= 5
        and width >= sw * 0.95 and height >= sh * 0.95
    )


def _is_fullscreen_monitor_rect(
    left: int, top: int, right: int, bottom: int,
    monitor_left: int, monitor_top: int, monitor_right: int, monitor_bottom: int,
) -> bool:
    """Detect borderless fullscreen against one monitor, including negative origins."""
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    monitor_width = int(monitor_right) - int(monitor_left)
    monitor_height = int(monitor_bottom) - int(monitor_top)
    if monitor_width <= 0 or monitor_height <= 0:
        return False
    return (
        int(left) <= int(monitor_left) + 5
        and int(top) <= int(monitor_top) + 5
        and int(right) >= int(monitor_right) - 5
        and int(bottom) >= int(monitor_bottom) - 5
        and width >= monitor_width * 0.95
        and height >= monitor_height * 0.95
    )


def _rect_within_primary_display(
    left: int, top: int, right: int, bottom: int,
    screen_width: int, screen_height: int,
) -> bool:
    """Only use primary-screen fallback when the window is actually on it."""
    width, height = int(screen_width), int(screen_height)
    if width <= 0 or height <= 0:
        return False
    return (
        int(left) >= -5 and int(top) >= -5
        and int(right) <= width + 5 and int(bottom) <= height + 5
    )


def get_foreground_window_identity() -> tuple[int, int] | None:
    """Return the current Windows foreground HWND/PID without reading its content."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        handle = user32.GetForegroundWindow()
        if not handle:
            return None
        process_id = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id)):
            return None
        if not process_id.value:
            return None
        return int(handle), int(process_id.value)
    except Exception:
        return None


def get_desktop_context(include_app: bool = True) -> dict:
    """Read idle time and foreground-window geometry, optionally process name.

    Returns available=False on platforms without this Windows-specific sensor.
    Failure to read Windows input state is reported as available with
    idle_seconds=None so callers can fail closed rather than interrupting
    someone whose activity cannot be measured.
    """
    if os.name != "nt":
        return {
            "available": False, "idle_seconds": None,
            "foreground_app": "", "fullscreen": False, "fullscreen_known": False,
        }

    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("dwTime", wintypes.DWORD),
            ]

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        user32.GetLastInputInfo.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        kernel32.GetTickCount.restype = wintypes.DWORD

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return {
                "available": True, "idle_seconds": None,
                "foreground_app": "", "fullscreen": False, "fullscreen_known": False,
            }

        idle_seconds = _idle_seconds_from_ticks(
            info.dwTime, kernel32.GetTickCount()
        )
        hwnd = user32.GetForegroundWindow()
        fullscreen = False
        fullscreen_known = False
        app = ""
        if hwnd:
            rect = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                # GetSystemMetrics(0/1) describes only the primary display.
                # Ask Windows for the monitor containing this window so a
                # borderless game on a secondary/negative-origin display is
                # recognised too.
                monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
                monitor_info = MONITORINFO()
                monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
                    bounds = monitor_info.rcMonitor
                    fullscreen_known = True
                    fullscreen = _is_fullscreen_monitor_rect(
                        rect.left, rect.top, rect.right, rect.bottom,
                        bounds.left, bounds.top, bounds.right, bounds.bottom,
                    )
                else:
                    screen_width = user32.GetSystemMetrics(0)
                    screen_height = user32.GetSystemMetrics(1)
                    if _rect_within_primary_display(
                            rect.left, rect.top, rect.right, rect.bottom,
                            screen_width, screen_height,
                    ):
                        fullscreen_known = True
                        fullscreen = _is_fullscreen_rect(
                            rect.left, rect.top, rect.right, rect.bottom,
                            screen_width, screen_height,
                        )
            if include_app:
                pid = wintypes.DWORD()
                if user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) and pid.value:
                    try:
                        import psutil
                        app = _clean_process_name(psutil.Process(pid.value).name())
                    except Exception:
                        # Activity sensing still works if process metadata is denied.
                        app = ""
        return {
            "available": True,
            "idle_seconds": idle_seconds,
            "foreground_app": app,
            "fullscreen": fullscreen,
            "fullscreen_known": fullscreen_known,
        }
    except Exception:
        return {
            "available": True, "idle_seconds": None,
            "foreground_app": "", "fullscreen": False, "fullscreen_known": False,
        }


def desktop_is_idle(context: object,
                    minimum_seconds: float = MIN_DESKTOP_IDLE_SECONDS):
    """Return True/False when measurable, or None when this OS has no sensor."""
    if not isinstance(context, dict) or not context.get("available"):
        return None
    if context.get("fullscreen"):
        return False
    try:
        seconds = float(context.get("idle_seconds"))
    except (TypeError, ValueError):
        return False
    return seconds >= max(0.0, float(minimum_seconds))

def should_run_proactive_checkin(
    session_available: bool,
    communication_active: bool,
    phone_active: bool,
    context: object,
) -> bool:
    """Allow a spoken check-in only with a live session and verified idle desktop.

    Wake-word sleep closes only the microphone path. It must not suppress an
    explicitly enabled proactive check-in; the local idle/context gates still
    prevent speaking during active work or a communication session.
    """
    return (
        bool(session_available)
        and not communication_active
        and not phone_active
        and should_allow_proactive_interrupt(context)
    )

def should_allow_proactive_interrupt(context: object) -> bool:
    """Allow unsolicited voice only when the desktop sensor positively confirms idle."""
    return (
        isinstance(context, dict)
        and context.get("fullscreen_known") is True
        and desktop_is_idle(context) is True
    )
