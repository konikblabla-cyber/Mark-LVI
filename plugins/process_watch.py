"""Persistent one-shot Windows process start/exit watchers.

Only executable basenames are inspected. Command lines, file paths, window
contents and process memory are never read. A pending event stays on disk until
the live assistant successfully delivers and acknowledges it.
"""
from __future__ import annotations

import json
import ntpath
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

import psutil

_PATH = Path(__file__).resolve().parent.parent / "memory" / "process_watchers.json"
_LOCK = threading.RLock()
_MAX_WATCHERS = 20
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


def _normalise_process_name(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or "/" in raw or "\\" in raw:
        return ""
    name = ntpath.basename(raw).casefold()
    if name.endswith(".exe"):
        name = name[:-4]
    return name if _NAME_RE.fullmatch(name) else ""


def _read() -> dict:
    if not _PATH.exists():
        return {"version": 1, "watchers": []}
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Process watcher store is unreadable; refusing to overwrite it.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("watchers"), list):
        raise RuntimeError("Process watcher store has an invalid structure.")
    return data


def _write(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".process-watchers-", suffix=".tmp", dir=str(_PATH.parent)
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, _PATH)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _running_process_names() -> set[str]:
    found = set()
    for process in psutil.process_iter(["name"]):
        try:
            name = _normalise_process_name(process.info.get("name"))
            if name:
                found.add(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    return found


def add_watcher(process_name: object, event: object = "exit") -> str:
    name = _normalise_process_name(process_name)
    mode = str(event or "exit").strip().lower()
    if not name:
        return "Give an executable name only, such as python.exe or chrome.exe."
    if mode not in {"start", "exit"}:
        return "Choose event 'start' or 'exit'."

    try:
        active = name in _running_process_names()
        if mode == "exit" and not active:
            return f"{name}.exe is not running. Start it first, or watch event='start'."
        if mode == "start" and active:
            return f"{name}.exe is already running. Use event='exit' to watch its completion."

        with _LOCK:
            data = _read()
            watchers = data["watchers"]
            existing = next(
                (item for item in watchers
                 if item.get("process") == name and item.get("event") == mode
                 and not item.get("pending")),
                None,
            )
            if existing:
                return f"Already watching {name}.exe for {mode} (ID: {existing['id']})."
            if len(watchers) >= _MAX_WATCHERS:
                return f"Process watcher limit reached ({_MAX_WATCHERS}). Stop one before adding another."
            watcher_id = uuid.uuid4().hex[:10]
            watchers.append({
                "id": watcher_id,
                "process": name,
                "event": mode,
                "seen_running": bool(active),
                "pending": False,
                "created_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            _write(data)
        verb = "start" if mode == "start" else "finish"
        return f"Watching {name}.exe. I will notify you when it {verb}. ID: {watcher_id}."
    except Exception as exc:
        return f"Could not start process watch: {type(exc).__name__}."


def list_watchers() -> str:
    try:
        with _LOCK:
            watchers = list(_read()["watchers"])
        if not watchers:
            return "No process watches are active."
        rows = []
        for item in watchers[:_MAX_WATCHERS]:
            state = "notification pending" if item.get("pending") else "watching"
            rows.append(
                f"{item.get('id', '')} — {item.get('process', '')}.exe "
                f"({item.get('event', '')}), {state}"
            )
        return "Process watches:\n" + "\n".join(rows)
    except Exception as exc:
        return f"Could not read process watches: {type(exc).__name__}."


def stop_watcher(watcher_id: object = "", process_name: object = "") -> str:
    target_id = str(watcher_id or "").strip()
    target_name = _normalise_process_name(process_name) if process_name else ""
    if not target_id and not target_name:
        return "Give the watcher ID or executable name to stop."
    try:
        with _LOCK:
            data = _read()
            before = len(data["watchers"])
            data["watchers"] = [
                item for item in data["watchers"]
                if not (
                    (target_id and item.get("id") == target_id)
                    or (target_name and item.get("process") == target_name)
                )
            ]
            removed = before - len(data["watchers"])
            if removed:
                _write(data)
        return f"Stopped {removed} process watch(es)." if removed else "No matching process watch was found."
    except Exception as exc:
        return f"Could not stop process watch: {type(exc).__name__}."


def check_all() -> list[dict]:
    """Update all watchers and return persisted one-shot alerts not yet acknowledged."""
    active = _running_process_names()
    changed = False
    alerts = []
    with _LOCK:
        data = _read()
        for item in data["watchers"]:
            if item.get("pending"):
                pass
            else:
                name = str(item.get("process") or "")
                is_running = name in active
                mode = item.get("event")
                if mode == "start" and is_running and not item.get("seen_running"):
                    item["pending"] = True
                    item["alert"] = f"{name}.exe has started."
                    changed = True
                elif mode == "exit" and item.get("seen_running") and not is_running:
                    item["pending"] = True
                    item["alert"] = f"{name}.exe has finished or closed."
                    changed = True
                elif bool(item.get("seen_running")) != bool(is_running):
                    item["seen_running"] = bool(is_running)
                    changed = True

            if item.get("pending"):
                alerts.append({
                    "id": str(item.get("id") or ""),
                    "message": str(item.get("alert") or "")[:180],
                    "process": str(item.get("process") or ""),
                })
        if changed:
            _write(data)
    return alerts


def acknowledge_alerts(watcher_ids: list[str]) -> int:
    ids = {str(value) for value in watcher_ids if value}
    if not ids:
        return 0
    with _LOCK:
        data = _read()
        before = len(data["watchers"])
        data["watchers"] = [
            item for item in data["watchers"]
            if not (item.get("id") in ids and item.get("pending"))
        ]
        removed = before - len(data["watchers"])
        if removed:
            _write(data)
        return removed


PLUGIN = {
    "name": "process_watch",
    "description": (
        "Monitor a Windows process by executable name and notify the user when it starts "
        "or finishes. Actions: start (event='start' or 'exit'), list, stop. Use for "
        "compilations, downloads and other programs whose completion matters. Only "
        "executable basenames are inspected; command lines, paths, screen contents "
        "and user files are never read. Notifications are saved until safely delivered."
    ),
    "tier": "extra",
    "brief": "Watch a Windows process start or exit and notify when the event occurs.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "start, list or stop"},
            "process_name": {"type": "STRING", "description": "Executable basename, e.g. python.exe"},
            "event": {"type": "STRING", "description": "For start: start or exit; defaults to exit"},
            "watcher_id": {"type": "STRING", "description": "Exact ID returned by start/list when stopping"},
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "list").strip().lower()
    if action == "start":
        return add_watcher(parameters.get("process_name"), parameters.get("event", "exit"))
    if action == "list":
        return list_watchers()
    if action == "stop":
        return stop_watcher(
            watcher_id=parameters.get("watcher_id"),
            process_name=parameters.get("process_name"),
        )
    return "Choose process_watch action 'start', 'list' or 'stop'."
