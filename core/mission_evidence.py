"""Application-owned evidence binding for durable mission steps.

A model-provided verification sentence is not proof that a tool ran. This
module binds one mission step to the real result returned by the dispatcher.
Only a matching status=done action=<tool name> verified=true result can
authorize complete_step. Evidence lives only for the current user turn and is never
restored after a restart.
"""
from __future__ import annotations

import hashlib
import re

_SCOPE_KEY = "_mission_execution"
_DONE_RESULT = re.compile(
    r"^status=done action=([A-Za-z_][A-Za-z0-9_]*)\b(?=[^\r\n]*\bverified=true(?:\s|$))"
)


def activate_step(session_memory, mission_id: str, step_id: str) -> bool:
    """Bind fresh evidence state to exactly one active mission step."""
    if not isinstance(session_memory, dict):
        return False
    mission = str(mission_id or "").strip()
    step = str(step_id or "").strip()
    if not mission or not step:
        return False
    session_memory[_SCOPE_KEY] = {
        "mission_id": mission,
        "step_id": step,
        "last_result": None,
        "last_recovery_result": None,
    }
    return True


def clear_step(session_memory, mission_id: str = "", step_id: str = "") -> None:
    """Clear only the matching active step; never erase another mission's state."""
    if not isinstance(session_memory, dict):
        return
    current = session_memory.get(_SCOPE_KEY)
    if not isinstance(current, dict):
        return
    if mission_id and current.get("mission_id") != mission_id:
        return
    if step_id and current.get("step_id") != step_id:
        return
    session_memory.pop(_SCOPE_KEY, None)


def record_tool_result(session_memory, tool_name: str, result: str) -> bool:
    """Record only the dispatcher's actual, matching verified-success result.

    Every non-mission tool call replaces the previous result. A failed,
    ambiguous, unverified, pending, or non-structured result therefore cannot
    accidentally reuse an earlier successful tool call.
    """
    if not isinstance(session_memory, dict):
        return False
    current = session_memory.get(_SCOPE_KEY)
    if not isinstance(current, dict):
        return False

    name = str(tool_name or "").strip()
    text = str(result or "")
    match = _DONE_RESULT.match(text)
    action = match.group(1) if match else ""
    if not name or action != name:
        current["last_recovery_result"] = None
        current["last_result"] = {
            "status": "not_verified",
            "tool_name": name[:80],
        }
        return False

    current["last_recovery_result"] = None
    current["last_result"] = {
        "status": "done",
        "tool_name": name[:80],
        "action": action,
        "verified": True,
        "result_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
    }
    return True


def seed_step_evidence(session_memory, mission_id: str, step_id: str,
                       evidence: dict | None) -> bool:
    """Reattach a persisted, app-created receipt to a fresh user-turn scope."""
    if not isinstance(session_memory, dict) or not isinstance(evidence, dict):
        return False
    if (evidence.get("status") != "done"
            or not isinstance(evidence.get("tool_name"), str)
            or evidence.get("action") != evidence.get("tool_name")
            or evidence.get("verified") is not True
            or not isinstance(evidence.get("result_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence["result_sha256"])):
        return False
    current = session_memory.get(_SCOPE_KEY)
    if (not isinstance(current, dict)
            or current.get("mission_id") != str(mission_id or "")
            or current.get("step_id") != str(step_id or "")):
        return False
    current["last_result"] = dict(evidence)
    return True


def verified_evidence(session_memory, mission_id: str, step_id: str) -> dict | None:
    """Return a copy of evidence only for the exact active mission and step."""
    if not isinstance(session_memory, dict):
        return None
    current = session_memory.get(_SCOPE_KEY)
    if not isinstance(current, dict):
        return None
    if (current.get("mission_id") != str(mission_id or "")
            or current.get("step_id") != str(step_id or "")):
        return None
    evidence = current.get("last_result")
    if (not isinstance(evidence, dict) or evidence.get("status") != "done"
            or evidence.get("verified") is not True):
        return None
    tool_name = evidence.get("tool_name")
    if not isinstance(tool_name, str) or evidence.get("action") != tool_name:
        return None
    digest = evidence.get("result_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    return dict(evidence)


_READ_ONLY_FILE_ACTIONS = frozenset({
    "list", "read", "find", "largest", "disk_usage", "info",
})


def record_recovery_tool_result(session_memory, tool_name: str, result: str,
                                parameters: dict | None = None) -> bool:
    """Record an actual allowlisted read-only inspection for this mission step."""
    if not isinstance(session_memory, dict):
        return False
    current = session_memory.get(_SCOPE_KEY)
    if not isinstance(current, dict):
        return False
    current["last_recovery_result"] = None
    name = str(tool_name or "").strip()
    args = parameters if isinstance(parameters, dict) else {}
    if name == "inspect_active_window":
        operation = "inspect_active_window"
    elif name == "file_controller":
        operation = str(args.get("action") or "").strip().lower()
        if operation not in _READ_ONLY_FILE_ACTIONS:
            return False
    else:
        return False
    text = str(result or "").strip()
    if not text or text.startswith((
        "File controller error", "Could not ", "I couldn't ",
        "Local UI inspection failed", "No visible active window",
        "Tool '", "Unknown tool:",
    )):
        return False
    current["last_recovery_result"] = {
        "status": "read_only_inspection",
        "tool_name": name,
        "operation": operation,
        "read_only": True,
        "result_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
    }
    return True


def verified_recovery_evidence(session_memory, mission_id: str,
                               step_id: str) -> dict | None:
    """Return a read-only receipt only for the exact active mission step."""
    if not isinstance(session_memory, dict):
        return None
    current = session_memory.get(_SCOPE_KEY)
    if not isinstance(current, dict):
        return None
    if (current.get("mission_id") != str(mission_id or "")
            or current.get("step_id") != str(step_id or "")):
        return None
    evidence = current.get("last_recovery_result")
    if (not isinstance(evidence, dict)
            or evidence.get("status") != "read_only_inspection"
            or evidence.get("read_only") is not True):
        return None
    name, operation = evidence.get("tool_name"), evidence.get("operation")
    allowed = ((name == "inspect_active_window" and operation == "inspect_active_window")
               or (name == "file_controller" and operation in _READ_ONLY_FILE_ACTIONS))
    digest = evidence.get("result_sha256")
    if not allowed or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    return dict(evidence)


def verification_summary(evidence: dict) -> str:
    """A safe factual receipt; do not persist raw tool output or user data."""
    tool = str(evidence.get("tool_name") or "tool")[:80]
    digest = str(evidence.get("result_sha256") or "")[:16]
    return f"Application verified {tool} returned status=done verified=true (receipt {digest})"


__all__ = [
    "activate_step", "clear_step", "record_tool_result",
    "seed_step_evidence", "verified_evidence", "record_recovery_tool_result",
    "verified_recovery_evidence", "verification_summary",
]
