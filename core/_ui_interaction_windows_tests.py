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
    _fill_text_and_verify,
)
from actions import ui_sequence, ui_picker_diagnostics, ui_checkbox, ui_radio, ui_tab, ui_list, ui_tree


_CLASS_NAME = "MARK_UIA_Integration_Window"
_WINDOW_TITLE = "MARK UIA Integration Test"
_BUTTON_ID = 101
_NOOP_BUTTON_ID = 102
_RESET_BUTTON_ID = 103
_OPEN_PANEL_BUTTON_ID = 104
_FINISH_WORKFLOW_BUTTON_ID = 105
_SAVE_TITLE_BUTTON_ID = 106
_CHECKBOX_ID = 108
_RADIO_EMAIL_ID = 109
_RADIO_SMS_ID = 110
_TAB_CONTROL_ID = 111
_LISTBOX_ID = 112
_TREE_ID = 113

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
    _comctl32.InitCommonControls.restype = None

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
    _user32.GetDlgItem.restype = wintypes.HWND
    _user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowTextW.restype = wintypes.BOOL
    _user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [
        wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
    ]
    _user32.SendMessageW.restype = ctypes.c_ssize_t
    _user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
    ]
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
    project_title_hwnd = [None]
    checkbox_hwnd = [None]
    radio_hwnds = [None, None]
    tab_hwnd = [None]
    listbox_hwnd = [None]
    tree_hwnd = [None]
    tree_items = {}
    workflow_stage = ["idle"]

    @_WNDPROC
    def wnd_proc(hwnd, message, wparam, lparam):
        command_id = wparam & 0xFFFF
        if message == 0x0111 and command_id == _BUTTON_ID:  # WM_COMMAND
            _user32.SetWindowTextW(status_hwnd[0], "Saved")
            return 0
        if message == 0x0111 and command_id == _RESET_BUTTON_ID:
            workflow_stage[0] = "idle"
            if project_title_hwnd[0]:
                _user32.SetWindowTextW(project_title_hwnd[0], "")
            if checkbox_hwnd[0]:
                _user32.SendMessageW(checkbox_hwnd[0], 0x00F1, 0, 0)  # BM_SETCHECK / BST_UNCHECKED
            if radio_hwnds[0] and radio_hwnds[1]:
                _user32.SendMessageW(radio_hwnds[0], 0x00F1, 1, 0)  # BM_SETCHECK / BST_CHECKED
                _user32.SendMessageW(radio_hwnds[1], 0x00F1, 0, 0)
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
        if message == 0x0111 and command_id == _SAVE_TITLE_BUTTON_ID:
            buffer = ctypes.create_unicode_buffer(512)
            if project_title_hwnd[0]:
                _user32.GetWindowTextW(
                    project_title_hwnd[0], buffer, len(buffer),
                )
            if buffer.value == "Quarterly plan":
                workflow_stage[0] = "title_saved"
                _user32.SetWindowTextW(status_hwnd[0], "Title saved")
            else:
                _user32.SetWindowTextW(status_hwnd[0], "Title missing")
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
        0, _CLASS_NAME, _WINDOW_TITLE, 0x00CF0000, 100, 100, 460, 650,
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
    # The adjacent STATIC is the accessible label for the EDIT control.
    title_label_hwnd = _user32.CreateWindowExW(
        0, "STATIC", "Project title", 0x50000000,
        20, 230, 110, 28, hwnd, None, hinstance, None,
    )
    project_title_hwnd[0] = _user32.CreateWindowExW(
        0x00000200, "EDIT", "", 0x50010080,
        140, 225, 250, 30, hwnd, ctypes.c_void_p(107), hinstance, None,
    )
    save_title_hwnd = _user32.CreateWindowExW(
        0, "BUTTON", "Save title", 0x50000000,
        20, 285, 140, 34, hwnd, ctypes.c_void_p(_SAVE_TITLE_BUTTON_ID), hinstance, None,
    )
    checkbox_hwnd[0] = _user32.CreateWindowExW(
        0, "BUTTON", "Remember this device", 0x50010003,
        190, 288, 220, 28, hwnd, ctypes.c_void_p(_CHECKBOX_ID), hinstance, None,
    )
    radio_hwnds[0] = _user32.CreateWindowExW(
        0, "BUTTON", "Email updates", 0x50030009,
        20, 330, 180, 28, hwnd, ctypes.c_void_p(_RADIO_EMAIL_ID), hinstance, None,
    )
    radio_hwnds[1] = _user32.CreateWindowExW(
        0, "BUTTON", "SMS updates", 0x50010009,
        210, 330, 180, 28, hwnd, ctypes.c_void_p(_RADIO_SMS_ID), hinstance, None,
    )
    if radio_hwnds[0] and radio_hwnds[1]:
        _user32.SendMessageW(radio_hwnds[0], 0x00F1, 1, 0)
        _user32.SendMessageW(radio_hwnds[1], 0x00F1, 0, 0)
    _comctl32.InitCommonControls()
    tab_hwnd[0] = _user32.CreateWindowExW(
        0, "SysTabControl32", "",
        0x50010000, 20, 365, 400, 48, hwnd,
        ctypes.c_void_p(_TAB_CONTROL_ID), hinstance, None,
    )
    if tab_hwnd[0]:
        class _TCITEMW(ctypes.Structure):
            _fields_ = [
                ("mask", wintypes.UINT),
                ("dwState", wintypes.UINT),
                ("dwStateMask", wintypes.UINT),
                ("pszText", wintypes.LPWSTR),
                ("cchText", ctypes.c_int),
                ("iImage", ctypes.c_int),
                ("lParam", ctypes.c_ssize_t),
            ]
        for index, label in enumerate(("Overview", "Details")):
            item = _TCITEMW()
            item.mask = 0x0001  # TCIF_TEXT
            item.pszText = label
            item.cchText = len(label)
            inserted = _user32.SendMessageW(
                tab_hwnd[0], 0x133E, index, ctypes.addressof(item),
            )  # TCM_INSERTITEMW
            if inserted < 0:
                raise RuntimeError("could not add native test tab")
    listbox_hwnd[0] = _user32.CreateWindowExW(
        0, "LISTBOX", "", 0x50010001,
        20, 415, 400, 60, hwnd,
        ctypes.c_void_p(_LISTBOX_ID), hinstance, None,
    )
    if listbox_hwnd[0]:
        for label in ("Projects", "Invoices"):
            # Keep the Unicode buffer alive until SendMessageW has synchronously
            # copied the string into the native listbox.
            text_buffer = ctypes.create_unicode_buffer(label)
            added = _user32.SendMessageW(
                listbox_hwnd[0], 0x0180, 0, ctypes.addressof(text_buffer),
            )  # LB_ADDSTRING
            if added < 0:
                raise RuntimeError("could not add native test list item")
            if added != (0 if label == "Projects" else 1):
                raise RuntimeError("native test listbox inserted an unexpected item index")
        _user32.SendMessageW(listbox_hwnd[0], 0x0186, 0, 0)  # LB_SETCURSEL
    tree_hwnd[0] = _user32.CreateWindowExW(
        0, "SysTreeView32", "",
        0x50010007, 20, 485, 400, 120, hwnd,
        ctypes.c_void_p(_TREE_ID), hinstance, None,
    )
    if tree_hwnd[0]:
        class _TVITEMW(ctypes.Structure):
            _fields_ = [
                ("mask", wintypes.UINT),
                ("hItem", wintypes.HANDLE),
                ("state", wintypes.UINT),
                ("stateMask", wintypes.UINT),
                ("pszText", wintypes.LPWSTR),
                ("cchTextMax", ctypes.c_int),
                ("iImage", ctypes.c_int),
                ("iSelectedImage", ctypes.c_int),
                ("cChildren", ctypes.c_int),
                ("lParam", ctypes.c_ssize_t),
            ]
        class _TVINSERTSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hParent", wintypes.HANDLE),
                ("hInsertAfter", wintypes.HANDLE),
                ("item", _TVITEMW),
            ]
        pointer_mask = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
        tvi_root = ctypes.c_void_p((-0x10000) & pointer_mask)
        tvi_last = ctypes.c_void_p((-0xFFFE) & pointer_mask)

        root_item = _TVINSERTSTRUCTW()
        root_item.hParent = tvi_root
        root_item.hInsertAfter = tvi_last
        root_item.item.mask = 0x0001 | 0x0040  # TVIF_TEXT | TVIF_CHILDREN
        root_item.item.pszText = "Projects"
        root_item.item.cChildren = 1
        root_handle = _user32.SendMessageW(
            tree_hwnd[0], 0x1132, 0, ctypes.addressof(root_item),
        )  # TVM_INSERTITEMW
        if not root_handle:
            raise RuntimeError("could not create native test tree root")

        child_item = _TVINSERTSTRUCTW()
        child_item.hParent = ctypes.c_void_p(int(root_handle))
        child_item.hInsertAfter = tvi_last
        child_item.item.mask = 0x0001 | 0x0040
        child_item.item.pszText = "Reports"
        child_item.item.cChildren = 0
        child_handle = _user32.SendMessageW(
            tree_hwnd[0], 0x1132, 0, ctypes.addressof(child_item),
        )  # TVM_INSERTITEMW
        if not child_handle:
            raise RuntimeError("could not create native test tree child")
        tree_items["Projects"] = int(root_handle)
        tree_items["Reports"] = int(child_handle)
    if not all((status_hwnd[0], button_hwnd, noop_button_hwnd, reset_button_hwnd,
                open_panel_hwnd, finish_workflow_hwnd, title_label_hwnd,
                project_title_hwnd[0], save_title_hwnd, checkbox_hwnd[0],
                radio_hwnds[0], radio_hwnds[1], tab_hwnd[0], listbox_hwnd[0],
                tree_hwnd[0])):
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


def _native_tree_item_handle(label: str) -> int:
    # The fixture server records the native HTREEITEM values in its own process.
    # The single known root is the first inserted child of TVI_ROOT.
    tree = _user32.GetDlgItem(_user32.FindWindowW(_CLASS_NAME, _WINDOW_TITLE), _TREE_ID)
    class _TVITEMW(ctypes.Structure):
        _fields_ = [
            ("mask", wintypes.UINT), ("hItem", wintypes.HANDLE),
            ("state", wintypes.UINT), ("stateMask", wintypes.UINT),
            ("pszText", wintypes.LPWSTR), ("cchTextMax", ctypes.c_int),
            ("iImage", ctypes.c_int), ("iSelectedImage", ctypes.c_int),
            ("cChildren", ctypes.c_int), ("lParam", ctypes.c_ssize_t),
        ]
    # TVM_GETNEXTITEM / TVGN_ROOT retrieves the root item handle.
    root = _user32.SendMessageW(tree, 0x110A, 5, 0)  # TVGN_ROOT
    if not root:
        raise AssertionError("native tree fixture has no root item")
    return int(root)


def _native_tree_item_expanded(tree_hwnd, item_handle) -> bool:
    class _TVITEMW(ctypes.Structure):
        _fields_ = [
            ("mask", wintypes.UINT), ("hItem", wintypes.HANDLE),
            ("state", wintypes.UINT), ("stateMask", wintypes.UINT),
            ("pszText", wintypes.LPWSTR), ("cchTextMax", ctypes.c_int),
            ("iImage", ctypes.c_int), ("iSelectedImage", ctypes.c_int),
            ("cChildren", ctypes.c_int), ("lParam", ctypes.c_ssize_t),
        ]
    item = _TVITEMW()
    item.mask = 0x0008  # TVIF_STATE
    item.hItem = ctypes.c_void_p(int(item_handle))
    item.stateMask = 0x0020  # TVIS_EXPANDED
    result = _user32.SendMessageW(
        tree_hwnd, 0x113E, 0, ctypes.addressof(item),
    )  # TVM_GETITEMW
    if not result:
        raise AssertionError("could not read native TreeView item state")
    return bool(item.state & 0x0020)


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
        # Reset the isolated fixture directly so tests never inherit typed data.
        _user32.SendMessageW(
            self.hwnd, 0x0111, _RESET_BUTTON_ID, 0,
        )
        tree = _user32.GetDlgItem(self.hwnd, _TREE_ID)
        if tree:
            root = _native_tree_item_handle("Projects")
            _user32.SendMessageW(tree, 0x1102, 1, root)  # TVM_EXPAND / TVE_COLLAPSE
        time.sleep(0.05)

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


    def test_value_pattern_fills_and_verifies_native_edit_without_returning_value(self):
        snapshot = _snapshot(int(self.hwnd))
        result = _fill_text_and_verify(
            "Project title", "Quarterly plan",
            int(self.hwnd), snapshot["process_id"],
        )
        self.assertTrue(
            result.startswith("status=done action=fill_text_and_verify "),
            result,
        )
        # The action compares ValuePattern.CurrentValue internally; it must
        # never include the entered value in its result sent back to the model.
        self.assertNotIn("Quarterly plan", result)

    def test_fill_text_does_not_touch_password_or_secret_fields(self):
        snapshot = _snapshot(int(self.hwnd))
        result = _fill_text_and_verify(
            "Password", "do-not-enter",
            int(self.hwnd), snapshot["process_id"],
        )
        self.assertTrue(result.startswith("status=failed action=fill_text_and_verify "), result)
        self.assertNotIn("do-not-enter", result)

    def test_form_sequence_fills_edit_then_clicks_save_button(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        active = _snapshot()
        self.assertEqual(active["handle"], int(self.hwnd), active)
        with patch.object(ui_sequence, "_capture_active_window", return_value=active):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    {
                        "action": "fill_text",
                        "field_label": "Project title",
                        "text": "Quarterly plan",
                    },
                    {
                        "action": "click",
                        "button_label": "Save title",
                        "expected_result": "Title saved",
                    },
                ],
            })
        self.assertTrue(result.startswith("status=done action=run_ui_sequence "), result)
        self.assertIn("steps_completed=2", result)
        after = _snapshot(int(self.hwnd))
        self.assertTrue(
            any(item["name"] == "Title saved" for item in after["records"]),
            after["records"],
        )

    def test_picker_diagnostics_are_bounded_and_do_not_return_edit_contents(self):
        snapshot = _snapshot(int(self.hwnd))
        sentinel = "PRIVATE_FIELD_VALUE_NOT_FOR_MODEL_48291"
        result = _fill_text_and_verify(
            "Project title", sentinel, int(self.hwnd), snapshot["process_id"],
        )
        self.assertTrue(result.startswith("status=done action=fill_text_and_verify "), result)
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        report = ui_picker_diagnostics.inspect_picker_controls()
        self.assertIn("Project title", report)
        self.assertNotIn(sentinel, report)
        # Inspection is read-only: a subsequent local snapshot still has one
        # Edit control, and the diagnostic response contains no field value.
        after = _snapshot(int(self.hwnd))
        self.assertTrue(any(item["name"] == "Project title" for item in after["records"]), after["records"])
        self.assertNotIn(sentinel, report)


    def test_native_checkbox_toggle_is_verified_and_idempotent(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        checkbox = _user32.GetDlgItem(self.hwnd, _CHECKBOX_ID)
        self.assertTrue(checkbox)
        self.assertEqual(_user32.SendMessageW(checkbox, 0x00F0, 0, 0), 0)  # BM_GETCHECK
        first = ui_checkbox.set_checkbox_state({
            "checkbox_label": "Remember this device", "checked": True,
        })
        native_state = _user32.SendMessageW(checkbox, 0x00F0, 0, 0)
        self.assertIn("status=done", first, f"{first}; native BM_GETCHECK={native_state}")
        self.assertIn("checked=true", first)
        self.assertEqual(_user32.SendMessageW(checkbox, 0x00F0, 0, 0), 1)
        second = ui_checkbox.set_checkbox_state({
            "checkbox_label": "Remember this device", "checked": True,
        })
        self.assertIn("status=done", second)
        self.assertIn("changed=false", second)
        self.assertEqual(_user32.SendMessageW(checkbox, 0x00F0, 0, 0), 1)


    def test_native_radio_selection_is_verified_and_idempotent(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        email = _user32.GetDlgItem(self.hwnd, _RADIO_EMAIL_ID)
        sms = _user32.GetDlgItem(self.hwnd, _RADIO_SMS_ID)
        self.assertTrue(email)
        self.assertTrue(sms)
        self.assertEqual(_user32.SendMessageW(email, 0x00F0, 0, 0), 1)
        self.assertEqual(_user32.SendMessageW(sms, 0x00F0, 0, 0), 0)
        first = ui_radio.select_radio_option({"radio_label": "SMS updates"})
        self.assertIn("status=done", first, first)
        self.assertIn("selected=true", first)
        self.assertEqual(_user32.SendMessageW(email, 0x00F0, 0, 0), 0)
        self.assertEqual(_user32.SendMessageW(sms, 0x00F0, 0, 0), 1)
        second = ui_radio.select_radio_option({"radio_label": "SMS updates"})
        self.assertIn("status=done", second, second)
        self.assertIn("changed=false", second)
        self.assertEqual(_user32.SendMessageW(sms, 0x00F0, 0, 0), 1)


    def test_native_tab_item_selection_is_verified_and_idempotent(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        tab = _user32.GetDlgItem(self.hwnd, _TAB_CONTROL_ID)
        self.assertTrue(tab)
        self.assertEqual(_user32.SendMessageW(tab, 0x130B, 0, 0), 0)  # TCM_GETCURSEL
        first = ui_tab.select_tab_item({"tab_label": "Details"})
        self.assertIn("status=done", first, first)
        self.assertIn("selected=true", first)
        self.assertEqual(_user32.SendMessageW(tab, 0x130B, 0, 0), 1)
        second = ui_tab.select_tab_item({"tab_label": "Details"})
        self.assertIn("status=done", second, second)
        self.assertIn("changed=false", second)
        self.assertEqual(_user32.SendMessageW(tab, 0x130B, 0, 0), 1)


    def test_native_single_selection_list_item_is_verified_and_idempotent(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        listbox = _user32.GetDlgItem(self.hwnd, _LISTBOX_ID)
        self.assertTrue(listbox)
        self.assertEqual(_user32.SendMessageW(listbox, 0x018B, 0, 0), 2)  # LB_GETCOUNT
        self.assertEqual(_user32.SendMessageW(listbox, 0x0188, 0, 0), 0)  # LB_GETCURSEL
        visible = _snapshot(int(self.hwnd))
        self.assertTrue(any(item.get("name") == "Invoices" for item in visible["records"]),
                        visible["records"])
        first = ui_list.select_list_item({"item_label": "Invoices"})
        self.assertIn("status=done", first, f"{first}; controls={_snapshot(int(self.hwnd))['records']}")
        self.assertIn("selected=true", first)
        self.assertEqual(_user32.SendMessageW(listbox, 0x0188, 0, 0), 1)
        second = ui_list.select_list_item({"item_label": "Invoices"})
        self.assertIn("status=done", second, second)
        self.assertIn("changed=false", second)
        self.assertEqual(_user32.SendMessageW(listbox, 0x0188, 0, 0), 1)

    def test_native_tree_item_expand_and_collapse_are_verified_and_idempotent(self):
        self.assertTrue(_user32.SetForegroundWindow(self.hwnd))
        time.sleep(0.1)
        tree = _user32.GetDlgItem(self.hwnd, _TREE_ID)
        self.assertTrue(tree)
        root = _native_tree_item_handle("Projects")
        self.assertFalse(_native_tree_item_expanded(tree, root))

        snapshot = _snapshot(int(self.hwnd))
        self.assertTrue(any(
            item.get("name") == "Projects" and item.get("role") == "TreeItem"
            for item in snapshot["records"]
        ), snapshot["records"])

        first = ui_tree.set_tree_item_expanded({
            "item_label": "Projects", "expanded": True,
        })
        self.assertIn("status=done", first, first)
        self.assertIn("expanded=true", first)
        self.assertTrue(_native_tree_item_expanded(tree, root))

        second = ui_tree.set_tree_item_expanded({
            "item_label": "Projects", "expanded": True,
        })
        self.assertIn("status=done", second, second)
        self.assertIn("changed=false", second)
        self.assertTrue(_native_tree_item_expanded(tree, root))

        collapsed = ui_tree.set_tree_item_expanded({
            "item_label": "Projects", "expanded": False,
        })
        self.assertIn("status=done", collapsed, collapsed)
        self.assertIn("expanded=false", collapsed)
        self.assertFalse(_native_tree_item_expanded(tree, root))

        again = ui_tree.set_tree_item_expanded({
            "item_label": "Projects", "expanded": False,
        })
        self.assertIn("status=done", again, again)
        self.assertIn("changed=false", again)
        self.assertFalse(_native_tree_item_expanded(tree, root))

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
