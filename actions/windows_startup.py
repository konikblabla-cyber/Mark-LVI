"""Opt-in Windows logon startup registration for JARVIS."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

_TASK_NAME = "JARVIS Assistant (Mark LVI)"
_CREATE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _launch_command() -> str:
    """Build a fixed, quoted launch command from this installed copy."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        return f'"{executable}"'
    root = Path(__file__).resolve().parent.parent
    entry = root / "main.py"
    if not entry.is_file():
        raise FileNotFoundError("main.py was not found beside the installed JARVIS files.")
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.is_file() else python
    return f'"{interpreter}" "{entry.resolve()}"'


def _run_schtasks(args: list[str]):
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=_CREATE_WINDOW,
        check=False,
    )


def run(parameters: dict, player=None, session_memory=None) -> str:
    if platform.system() != "Windows":
        return "Windows startup management is available on Windows only."

    action = str((parameters or {}).get("action") or "status").strip().lower()
    if action == "status":
        result = _run_schtasks(["/Query", "/TN", _TASK_NAME, "/FO", "LIST"])
        if result.returncode == 0:
            return "JARVIS is registered to start when this Windows user signs in."
        detail = (result.stderr or result.stdout).strip().lower()
        if "cannot find" in detail or "does not exist" in detail or "cannot find the file" in detail:
            return "JARVIS is not registered to start with Windows."
        return f"Could not check Windows startup task: {(result.stderr or result.stdout).strip()[:160]}"

    if action == "enable":
        try:
            command = _launch_command()
        except Exception as exc:
            return f"Could not locate the JARVIS launch target: {type(exc).__name__}."
        result = _run_schtasks([
            "/Create", "/SC", "ONLOGON",
            "/TN", _TASK_NAME,
            "/TR", command,
            "/RL", "LIMITED",
            "/F",
        ])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return f"Windows could not register JARVIS startup: {detail[:180]}"
        verify = _run_schtasks(["/Query", "/TN", _TASK_NAME, "/FO", "LIST"])
        if verify.returncode != 0:
            return "Windows accepted the startup task, but I could not verify it."
        return "JARVIS will start when this Windows user signs in."

    if action == "disable":
        result = _run_schtasks(["/Delete", "/TN", _TASK_NAME, "/F"])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().lower()
            if "cannot find" in detail or "does not exist" in detail:
                return "JARVIS was already not registered to start with Windows."
            return f"Windows could not remove the startup task: {(result.stderr or result.stdout).strip()[:180]}"
        return "JARVIS will no longer start automatically when Windows signs in."

    return "Choose windows_startup action 'status', 'enable' or 'disable'."


TOOL = {
    "name": "windows_startup",
    "description": (
        "Check, enable or disable starting JARVIS automatically when this Windows "
        "user signs in. Use action='status', 'enable' or 'disable'. Enabling creates "
        "one current-user Task Scheduler entry with limited privileges; it does not "
        "require administrator rights. Never enable this unless the user explicitly asks."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status, enable or disable; defaults to status"},
        },
        "required": [],
    },
}
