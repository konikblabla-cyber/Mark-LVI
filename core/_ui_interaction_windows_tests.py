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
from unittest.mock import patch

from actions.ui_interaction import (
    _click_and_verify,
    _matching_controls,
    _snapshot,
)
from actions import ui_sequence, ui_form


_CLASS_NAME = "MARK_UIA_Integration_Window"
_WINDOW_TITLE = "MARK UIA Integration Test"
_BUTTON_ID = 101
_NOOP_BUTTON_ID = 102
_RESET_BUTTON_ID = 103
_OPEN_PANEL_BUTTON_ID = 104
_FINISH_WORKFLOW_BUTTON_ID = 105
_FORM_EDIT_ID = 106

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
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
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
    workflow_stage = ["idle"]

    @_WNDPROC
    def wnd_proc(hwnd, message, wparam, lparam):
        command_id = wparam & 0xFFFF
        if message == 0x0111 and command_id == _BUTTON_ID:  # WM_COMMAND
            _user32.SetWindowTextW(status_hwnd[0], "Saved")
            return 0
        if message == 0x0111 and command_id == _RESET_BUTTON_ID:
            workflow_stage[0] = "idle"
            _user32.SetWindowTextW(status_hwnd[0], "Not saved")
            return 0
        if message == 0x0111 and command_id == _OPEN_PANEL_BUTTON_ID:
            workflow_stage[0] = "panel"
            _user32.SetWindowTextW(status_hwnd[0], "Panel ready")
            return 0
        if message == 0x0111 and command_id == _FINISH_WORKFLOW_BUTTON_ID:
            if workflow_stage[0] == "panel":
                workflow_stage[0] = "complete"
                _user32.SetWindowTextW(status_hwnd[0], "Workflow complete")
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
        0, _CLASS_NAME, _WINDOW_TITLE, 0x00CF0000, 100, 100, 460, 300,
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
    noop_button_hwnd = _user32.CreateWindowExW(
        0, "BUTTON", "No change", 0x50000000,
        170, 70, 140, 34, hwnd, ctypes.c_void_p(_NOOP_BUTTON_ID), hinstance, None,
    )
    reset_button_hwnd = _user32.CreateWindowExW(
        0, "BUTTON", "Restore baseline", 0x50000000,
        20, 120, 160, 34, hwnd, ctypes.c_void_p(_RESET_BUTTON_ID), hinstance, None,
    )
    open_panel_hwnd = _user32.CreateWindowExW(
        0, "BUTTON", "Open panel", 0x50000000,
        20, 170, 140, 34, hwnd, ctypes.c_void_p(_OPEN_PANEL_BUTTON_ID), hinstance, None,
    )
    finish_workflow_hwnd = _user32.CreateWindowExW(
        0, "BUTTON", "Finish workflow", 0x50000000,
        190, 170, 160, 34, hwnd, ctypes.c_void_p(_FINISH_WORKFLOW_BUTTON_ID), hinstance, None,
    )
    form_edit_hwnd = _user32.CreateWindowExW(
        0, "EDIT", "Initial value", 0x50010080,
        20, 230, 250, 28, hwnd, ctypes.c_void_p(_FORM_EDIT_ID), hinstance, None,
    )
    if not all((status_hwnd[0], button_hwnd, noop_button_hwnd, reset_button_hwnd,
                open_panel_hwnd, finish_workflow_hwnd, form_edit_hwnd)):
        raise ctypes.WinError(ctypes.get_last_error())

    _user32.ShowWindow(hwnd, 5)  # SW_SHOW
    _user32.UpdateWindow(hwnd)
    _user32.SetForegroundWindow(hwnd)
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
        repo_root = Path(__file__).resolve().parents[1]
        cls._process = subprocess.Popen(
            [sys.executable, "-m", "core._ui_interaction_windows_tests", "--serve"],
            cwd=str(repo_root),
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

    def setUp(self):
        # Tests share one native window, so restore a known state each time.
        snapshot = _snapshot(int(self.hwnd))
        if any(item["name"] == "Not saved" for item in snapshot["records"]):
            return
        matches = _matching_controls(snapshot["clickables"], "Restore baseline")
        self.assertEqual(len(matches), 1, snapshot["clickables"])
        item = matches[0]
        signature = {
            "role": item["role"],
            "automation_id": item.get("automation_id", ""),
            "runtime_id": item.get("runtime_id", ()),
            "rect": item.get("rect"),
        }
        result = _click_and_verify(
            "Restore baseline", "Not saved", int(self.hwnd),
            snapshot["process_id"], signature,
        )
        self.assertTrue(
            result.startswith("status=done action=click_and_verify "),
            result,
        )

    def test_real_native_button_without_expected_change_is_not_reported_done(self):
        snapshot = _snapshot(int(self.hwnd))
        matches = _matching_controls(snapshot["clickables"], "No change")
        self.assertEqual(len(matches), 1, snapshot["clickables"])
        signature = {
            "role": matches[0]["role"],
            "automation_id": matches[0].get("automation_id", ""),
            "runtime_id": matches[0].get("runtime_id", ()),
            "rect": matches[0].get("rect"),
        }
        result = _click_and_verify(
            "No change", "Completed", int(self.hwnd),
            snapshot["process_id"], signature,
        )
        self.assertTrue(
            result.startswith("status=unverified action=click_and_verify "),
            result,
        )
        after = _snapshot(int(self.hwnd))
        self.assertTrue(
            any(item["name"] == "Not saved" for item in after["records"]),
            after["records"],
        )

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

    def test_reused_window_handle_from_another_process_is_rejected(self):
        snapshot = _snapshot(int(self.hwnd))
        matches = _matching_controls(snapshot["clickables"], "Save")
        self.assertEqual(len(matches), 1, snapshot["clickables"])
        item = matches[0]
        signature = {
            "role": item["role"],
            "automation_id": item.get("automation_id", ""),
            "runtime_id": item.get("runtime_id", ()),
            "rect": item.get("rect"),
        }
        wrong_process_id = snapshot["process_id"] + 9999
        result = _click_and_verify(
            "Save", "Saved", int(self.hwnd), wrong_process_id, signature,
        )
        self.assertTrue(
            result.startswith("status=failed action=click_and_verify "),
            result,
        )
        after = _snapshot(int(self.hwnd))
        self.assertTrue(
            any(item["name"] == "Not saved" for item in after["records"]),
            after["records"],
        )


    def test_native_edit_valuepattern_fills_and_verifies_without_returning_value(self):
        snapshot = ui_form._form_snapshot(int(self.hwnd))
        candidates = [
            item for item in snapshot["fields"]
            if item.get("automation_id") == str(_FORM_EDIT_ID)
        ]
        self.assertEqual(len(candidates), 1, snapshot["fields"])
        result = ui_form._run_fill_with_timeout(
            snapshot["handle"], snapshot["process_id"],
            [{"field_label": f"AutomationId:{_FORM_EDIT_ID}", "text": "MARK verified entry"}],
        )
        self.assertTrue(
            result.startswith("status=done action=fill_ui_fields "),
            result,
        )
        self.assertNotIn("MARK verified entry", result)

        def read_value():
            from pywinauto import Desktop
            window = Desktop(backend="uia").window(
                handle=int(self.hwnd),
            ).wrapper_object()
            for control in window.descendants():
                info = control.element_info
                if str(getattr(info, "automation_id", "") or "") == str(_FORM_EDIT_ID):
                    return str(control.iface_value.CurrentValue)
            return None

        completed, value, error = ui_form.ui._run_uia(
            read_value, 4.0, exclusive_lock=ui_form.ui._SNAPSHOT_LOCK,
        )
        self.assertTrue(completed)
        self.assertIsNone(error)
        self.assertEqual(value, "MARK verified entry")

    def test_two_step_ui_sequence_verifies_each_real_native_transition(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        active = _snapshot()
        self.assertEqual(active["handle"], int(self.hwnd), active)
        self.assertTrue(
            any(item["name"] == "Open panel" for item in active["clickables"]),
            active["clickables"],
        )
        with patch.object(ui_sequence, "_capture_active_window", return_value=active):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    {"button_label": "Open panel", "expected_result": "Panel ready"},
                    {"button_label": "Finish workflow", "expected_result": "Workflow complete"},
                ],
            })
        self.assertTrue(
            result.startswith("status=done action=run_ui_sequence "),
            result,
        )
        self.assertIn("steps_completed=2", result)
        after = _snapshot(int(self.hwnd))
        self.assertTrue(
            any(item["name"] == "Workflow complete" for item in after["records"]),
            after["records"],
        )

    def test_ui_sequence_stops_after_real_noop_and_does_not_click_next_button(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        active = _snapshot()
        self.assertEqual(active["handle"], int(self.hwnd), active)
        self.assertTrue(
            any(item["name"] == "No change" for item in active["clickables"]),
            active["clickables"],
        )
        with patch.object(ui_sequence, "_capture_active_window", return_value=active):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    {"button_label": "No change", "expected_result": "Should not appear"},
                    {"button_label": "Finish workflow", "expected_result": "Workflow complete"},
                ],
            })
        self.assertTrue(
            result.startswith("status=unverified action=run_ui_sequence "),
            result,
        )
        self.assertIn("failed_step=1", result)
        after = _snapshot(int(self.hwnd))
        self.assertTrue(
            any(item["name"] == "Not saved" for item in after["records"]),
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
