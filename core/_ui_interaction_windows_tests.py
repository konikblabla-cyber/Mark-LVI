"""Windows-only integration test for MARK's real UI Automation click path.

This launches a tiny native Win32 test window in a child process, inspects it
through pywinauto's UIA backend on the worker thread, invokes a real Button
InvokePattern, and verifies the changed Static text. No real user application
or account is touched.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from actions.ui_interaction import (
    _click_and_verify,
    _matching_controls,
    _snapshot,
)


_CLASS_NAME = "MARK_UIA_Integration_Window"
_WINDOW_TITLE = "MARK UIA Integration Test"
_BUTTON_ID = 101

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
        ctypes.c_size_t, ctypes.c_ssize_t,
    )

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.LoadCursorW.restype = wintypes.HANDLE
    # The second argument accepts MAKEINTRESOURCE(IDC_ARROW), an integer
    # resource cast to a pointer rather than a real wide string.
    _user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
    ]
    _user32.SetWindowTextW.restype = wintypes.BOOL
    _user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
    ]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UpdateWindow.restype = wintypes.BOOL
    _user32.UpdateWindow.argtypes = [wintypes.HWND]
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND,
        wintypes.UINT, wintypes.UINT,
    ]
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def _serve_native_window() -> int:
    """Run a native, accessible Win32 window until WM_CLOSE."""
    if sys.platform != "win32":
        return 2

    hinstance = _kernel32.GetModuleHandleW(None)
    status_hwnd = [None]

    @_WNDPROC
    def wnd_proc(hwnd, message, wparam, lparam):
        if message == 0x0111 and (wparam & 0xFFFF) == _BUTTON_ID:  # WM_COMMAND
            _user32.SetWindowTextW(status_hwnd[0], "Saved")
            return 0
        if message == 0x0002:  # WM_DESTROY
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    wnd_class = _WNDCLASSW()
    wnd_class.lpfnWndProc = wnd_proc
    wnd_class.hInstance = hinstance
    wnd_class.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(32512))
    wnd_class.hbrBackground = ctypes.c_void_p(6)  # COLOR_WINDOW + 1
    wnd_class.lpszClassName = _CLASS_NAME
    if not _user32.RegisterClassW(ctypes.byref(wnd_class)):
        error = ctypes.get_last_error()
        if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS
            raise ctypes.WinError(error)

    hwnd = _user32.CreateWindowExW(
        0, _CLASS_NAME, _WINDOW_TITLE, 0x00CF0000, 100, 100, 420, 180,
        None, None, hinstance, None,
    )
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    # STATIC and BUTTON are native common controls with standard UIA providers.
    status_hwnd[0] = _user32.CreateWindowExW(
        0, "STATIC", "Not saved", 0x50000000,
        20, 20, 340, 28, hwnd, None, hinstance, None,
    )
    button_hwnd = _user32.CreateWindowExW(
        0, "BUTTON", "Save", 0x50000000,
        20, 70, 120, 34, hwnd, ctypes.c_void_p(_BUTTON_ID), hinstance, None,
    )
    if not status_hwnd[0] or not button_hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    _user32.ShowWindow(hwnd, 5)  # SW_SHOW
    _user32.UpdateWindow(hwnd)
    message = wintypes.MSG()
    while True:
        result = _user32.GetMessageW(ctypes.byref(message), None, 0, 0)
        if result <= 0:
            break
        _user32.TranslateMessage(ctypes.byref(message))
        _user32.DispatchMessageW(ctypes.byref(message))
    return 0


class WindowsUIAIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "win32":
            raise unittest.SkipTest("Windows UI Automation integration test only")

        cls._log_file = tempfile.NamedTemporaryFile(
            prefix="mark-uia-test-", suffix=".log", delete=False,
        )
        cls._log_path = Path(cls._log_file.name)
        cls._process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve"],
            stdout=cls._log_file,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 12.0
        cls.hwnd = None
        while time.monotonic() < deadline:
            if cls._process.poll() is not None:
                cls._log_file.flush()
                output = cls._log_path.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(
                    f"Native UIA test window exited early: {output[-2000:]}"
                )
            cls.hwnd = _user32.FindWindowW(_CLASS_NAME, _WINDOW_TITLE)
            if cls.hwnd:
                break
            time.sleep(0.1)
        if not cls.hwnd:
            cls._process.terminate()
            cls._log_file.flush()
            output = cls._log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"Native UIA test window did not appear: {output[-2000:]}"
            )

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "hwnd", None):
            _user32.PostMessageW(cls.hwnd, 0x0010, 0, 0)  # WM_CLOSE
        process = getattr(cls, "_process", None)
        if process is not None:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        log_file = getattr(cls, "_log_file", None)
        if log_file is not None:
            log_file.close()
        log_path = getattr(cls, "_log_path", None)
        if log_path is not None:
            try:
                log_path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_real_native_button_invoke_changes_exact_accessible_text(self):
        snapshot = _snapshot(int(self.hwnd))
        self.assertTrue(
            any(item["name"] == "Not saved" for item in snapshot["records"]),
            snapshot["records"],
        )
        matches = _matching_controls(snapshot["clickables"], "Save")
        self.assertEqual(len(matches), 1, snapshot["clickables"])
        signature = {
            "role": matches[0]["role"],
            "automation_id": matches[0].get("automation_id", ""),
            "runtime_id": matches[0].get("runtime_id", ()),
            "rect": matches[0].get("rect"),
        }

        result = _click_and_verify(
            "Save", "Saved", int(self.hwnd), snapshot["process_id"], signature,
        )
        self.assertTrue(
            result.startswith("status=done action=click_and_verify "),
            result,
        )
        after = _snapshot(int(self.hwnd))
        self.assertTrue(
            any(item["name"] == "Saved" for item in after["records"]),
            after["records"],
        )

    def test_wrong_button_label_does_not_invoke_anything(self):
        snapshot = _snapshot(int(self.hwnd))
        matches = _matching_controls(snapshot["clickables"], "Definitely not present")
        self.assertEqual(matches, [])
        result = _click_and_verify(
            "Definitely not present", "Completed",
            int(self.hwnd), snapshot["process_id"],
            {"role": "Button", "automation_id": "", "runtime_id": (), "rect": None},
        )
        self.assertTrue(result.startswith("status=failed action=click_and_verify "), result)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        raise SystemExit(_serve_native_window())
    unittest.main(verbosity=2)
