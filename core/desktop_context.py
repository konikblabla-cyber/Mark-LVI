"""Low-cost, local desktop activity sensing.

On Windows this reads only the last-input timestamp and, when explicitly
requested, the foreground process name. It never captures window titles,
screenshots, keystrokes, clipboard contents or document text.
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


def get_desktop_context(include_app: bool = True) -> dict:
    """Read desktop idle time and optionally the foreground executable.

    Returns available=False on platforms without this Windows-specific
    sensor. Failure to read Windows input state is reported as available with
    idle_seconds=None so callers can fail closed rather than interrupting
    someone whose activity cannot be measured.
    """
    if os.name != "nt":
        return {"available": False, "idle_seconds": None, "foreground_app": ""}

    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("dwTime", wintypes.DWORD),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        user32.GetLastInputInfo.restype = wintypes.BOOL
        kernel32.GetTickCount.restype = wintypes.DWORD

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return {"available": True, "idle_seconds": None, "foreground_app": ""}

        idle_seconds = _idle_seconds_from_ticks(
            info.dwTime, kernel32.GetTickCount()
        )
        app = ""
        if include_app:
            hwnd = user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            if hwnd and user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) and pid.value:
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
        }
    except Exception:
        return {"available": True, "idle_seconds": None, "foreground_app": ""}


def desktop_is_idle(context: object,
                    minimum_seconds: float = MIN_DESKTOP_IDLE_SECONDS):
    """Return True/False when measurable, or None when this OS has no sensor."""
    if not isinstance(context, dict) or not context.get("available"):
        return None
    try:
        seconds = float(context.get("idle_seconds"))
    except (TypeError, ValueError):
        return False
    return seconds >= max(0.0, float(minimum_seconds))
