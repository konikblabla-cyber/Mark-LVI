"""Persistent Mission Control — plans and tracks multi-step work locally.

This plugin stores plans only. It does not run shell commands, code or external
actions by itself. JARVIS executes a step using its existing tools, verifies the
outcome, then marks that step complete here.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "memory" / "missions.json"
_LOCK = threading.RLock()
_MAX_MISSIONS = 100
_MAX_STEPS = 20


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read() -> dict:
    if not _DATA_PATH.exists():
        return {"version": 1, "missions": []}
    try:
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("mission store is unreadable; refusing to overwrite it") from exc
    if not isinstance(data, dict) or not isinstance(data.get("missions"), list):
        raise RuntimeError("mission store has an invalid structure; refusing to overwrite it")
    return data


def _write(data: dict) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DATA_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_DATA_PATH)


def _normalise_steps(raw) -> list[str]:
    if isinstance(raw, str):
        values = raw.splitlines()
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    out = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("title") or value.get("step") or ""
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(value or "")).strip()
        if text:
            out.append(text[:220])
        if len(out) >= _MAX_STEPS:
            break
    return out


def _generate_steps(goal: str) -> list[str]:
    from core import gemini
    prompt = (
        "Turn the user's goal into a practical, ordered execution plan. "
        "Return ONLY JSON: {\"steps\":[\"short actionable step\"]}. "
        "Use 3-10 steps. Each step must be independently verifiable. "
        "Do not claim a step has already been performed.\n\nGoal: " + goal[:4000]
    )
    value = gemini.as_json(
        prompt, tier=gemini.SMART, timeout_ms=45_000, default=None,
        task_type="reasoning",
    )
    if isinstance(value, dict):
        return _normalise_steps(value.get("steps"))
    if isinstance(value, list):
        return _normalise_steps(value)
    return []


def _format(mission: dict) -> str:
    lines = [
        f"MISSION {mission['id']}: {mission['title']}",
        f"Status: {mission['status']}",
        f"Goal: {mission['goal']}",
    ]
    for step in mission.get("steps", []):
        mark = "✓" if step["status"] == "done" else ("!" if step["status"] == "blocked" else "·")
        lines.append(f"{mark} {step['id']} — {step['title']} [{step['status']}]")
    pending = next((s for s in mission.get("steps", []) if s["status"] == "pending"), None)
    if pending:
        lines.extend(["", f"NEXT: {pending['id']} — {pending['title']}"])
    return "\n".join(lines)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "list").strip().lower()
    mission_id = str(parameters.get("mission_id") or "").strip()
    try:
        with _LOCK:
            data = _read()
            missions = data["missions"]

            if action == "plan":
                title = str(parameters.get("title") or "").strip()[:120]
                goal = str(parameters.get("goal") or title).strip()[:4000]
                if not title or not goal:
                    return "Give the mission a title and a goal."
                steps = _normalise_steps(parameters.get("steps"))
                if not steps:
                    try:
                        steps = _generate_steps(goal)
                    except Exception:
                        steps = []
                if not steps:
                    return ("I could not generate a verified step list. Pass the steps as a numbered "
                            "list and I will save it without guessing.")
                active = sum(1 for m in missions if m.get("status") in {"active", "paused"})
                if active >= 20 or len(missions) >= _MAX_MISSIONS:
                    return "Mission storage is full. Archive completed missions before adding another."
                mid = "M-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
                mission = {
                    "id": mid, "title": title, "goal": goal, "status": "active",
                    "created_at": _now(), "updated_at": _now(),
                    "steps": [{"id": f"S{i+1:02d}", "title": step, "status": "pending",
                               "updated_at": ""} for i, step in enumerate(steps)],
                }
                missions.append(mission)
                _write(data)
                result = _format(mission)
            elif action in {"list", "show", "next"}:
                selected = None
                if mission_id:
                    selected = next((m for m in missions if m.get("id") == mission_id), None)
                    if selected is None:
                        return f"No mission named {mission_id} exists."
                if action == "list":
                    visible = [m for m in missions if m.get("status") != "archived"][-20:]
                    result = "\n\n".join(_format(m) for m in reversed(visible)) if visible else "No missions saved."
                elif action == "next":
                    candidates = [selected] if selected else [m for m in missions if m.get("status") == "active"]
                    item = next(( (m, s) for m in candidates if m for s in m["steps"] if s["status"] == "pending"), None)
                    if not item:
                        result = "There are no pending mission steps."
                    else:
                        m, step = item
                        result = f"{m['title']} ({m['id']}): {step['id']} — {step['title']}"
                else:
                    result = _format(selected) if selected else "Give me a mission_id to show its details."
            elif action == "complete_step":
                sid = str(parameters.get("step_id") or "").strip()
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                if mission is None:
                    return "Give me a valid mission_id."
                step = next((s for s in mission["steps"] if s["id"] == sid), None)
                if step is None:
                    return "Give me a valid step_id for that mission."
                step["status"] = "done"
                step["updated_at"] = _now()
                mission["updated_at"] = _now()
                if all(s["status"] == "done" for s in mission["steps"]):
                    mission["status"] = "completed"
                _write(data)
                result = _format(mission)
            elif action == "add_step":
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                title = str(parameters.get("title") or "").strip()[:220]
                if mission is None or not title:
                    return "Give me a valid mission_id and a step title."
                if len(mission["steps"]) >= _MAX_STEPS:
                    return "This mission already has the maximum number of steps."
                sid = f"S{len(mission['steps'])+1:02d}"
                mission["steps"].append({"id": sid, "title": title, "status": "pending", "updated_at": ""})
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
            elif action in {"pause", "resume", "archive", "complete"}:
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                if mission is None:
                    return "Give me a valid mission_id."
                status = {"pause": "paused", "resume": "active", "archive": "archived",
                          "complete": "completed"}[action]
                if action == "complete" and any(s["status"] != "done" for s in mission["steps"]):
                    return "This mission still has unfinished steps. Complete or explicitly archive those first."
                mission["status"] = status
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
            else:
                return "Choose plan, list, show, next, complete_step, add_step, pause, resume, complete or archive."

        if player:
            try:
                player.show_content("MISSION CONTROL", result)
            except Exception:
                pass
        return result if len(result) < 900 else "Mission updated. The full plan is displayed on screen."
    except Exception as exc:
        return f"Mission Control failed: {type(exc).__name__}: {str(exc)[:160]}"


PLUGIN = {
    "name": "mission_control",
    "description": (
        "Persistent Mission Control for multi-step goals and projects. Use it when a request "
        "requires several ordered steps, spans more than one turn, or the user asks to plan, "
        "track, resume or review ongoing work. Create a mission first, then execute its steps "
        "with the existing computer/browser/file tools; mark a step complete only after the "
        "tool outcome is verified. This plugin stores plans and progress but does NOT itself "
        "execute commands or modify files."
    ),
    "tier": "core",
    "brief": "Create and track durable multi-step missions, see next steps and resume paused work.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "plan, list, show, next, complete_step, add_step, pause, resume, complete or archive"},
            "title": {"type": "STRING", "description": "Mission title"},
            "goal": {"type": "STRING", "description": "What must be achieved"},
            "steps": {"type": "STRING", "description": "Optional ordered step list, one step per line"},
            "mission_id": {"type": "STRING", "description": "ID of an existing mission"},
            "step_id": {"type": "STRING", "description": "Step ID such as S01"},
        },
        "required": ["action"],
    },
}
