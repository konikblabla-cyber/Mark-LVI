"""Persistent Mission Control — plans and tracks multi-step work locally.

This plugin stores mission state. The live JARVIS model executes each active
step with its existing tools, verifies the outcome, then records completion.
The plugin never executes arbitrary commands or invents tool results.
"""
from __future__ import annotations

import json
import re
import threading
import tempfile
import os
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
    fd, temp_name = tempfile.mkstemp(prefix=".missions-", suffix=".tmp",
                                     dir=str(_DATA_PATH.parent))
    tmp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        tmp.replace(_DATA_PATH)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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


def _next_open_step(mission: dict):
    """Return the first step not yet verified complete, including an interrupted one."""
    return next((step for step in mission.get("steps", [])
                 if step.get("status") in {"pending", "running"}), None)


def _has_valid_persisted_tool_evidence(step: dict) -> bool:
    evidence = step.get("tool_evidence") if isinstance(step, dict) else None
    if not isinstance(evidence, dict) or evidence.get("status") != "done":
        return False
    if evidence.get("verified") is not True:
        return False
    tool_name = evidence.get("tool_name")
    if not isinstance(tool_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool_name):
        return False
    if evidence.get("action") != tool_name:
        return False
    digest = evidence.get("result_sha256")
    return isinstance(digest, str) and bool(re.fullmatch(r"[0-9a-f]{64}", digest))


def _increment_attempts(step: dict) -> None:
    try:
        step["attempts"] = max(0, int(step.get("attempts", 0))) + 1
    except (TypeError, ValueError):
        step["attempts"] = 1


def _prepare_step_for_execution(mission: dict, session_memory) -> tuple[bool, bool]:
    """Claim a pending step or restore a verified receipt; never replay uncertainty."""
    step = _next_open_step(mission)
    if step is None:
        return True, False
    from core.mission_evidence import activate_step, seed_step_evidence
    mission_id, step_id = str(mission.get("id") or ""), str(step.get("id") or "")
    if step.get("status") == "pending":
        step["status"] = "running"
        _increment_attempts(step)
        step["started_at"] = _now()
        step["updated_at"] = _now()
        step.pop("tool_evidence", None)
        activate_step(session_memory, mission_id, step_id)
        return True, False
    activate_step(session_memory, mission_id, step_id)
    if (_has_valid_persisted_tool_evidence(step)
            and seed_step_evidence(session_memory, mission_id, step_id, step.get("tool_evidence"))):
        return True, True
    step["status"] = "blocked"
    step["blocked_reason"] = "Interrupted execution has no valid verified receipt."
    step["outcome_uncertain"] = True
    step["blocked_at"] = _now()
    step["updated_at"] = _now()
    step.pop("tool_evidence", None)
    mission["status"] = "paused"
    mission["updated_at"] = _now()
    activate_step(session_memory, mission_id, step_id)
    return False, False


def _continue_instruction(mission: dict, recovery: bool = False) -> str:
    blocked = next((s for s in mission.get("steps", [])
                    if s.get("status") == "blocked"), None)
    if blocked:
        return ("\\n[MISSION_RECOVERY_REQUIRED] Step " + str(blocked.get("id") or "?")
                + " may already have changed the real world. Do NOT repeat it. "
                "Inspect only with an allowlisted read-only tool, then call resolve_step.")
    step = _next_open_step(mission)
    if mission.get("status") == "completed" or (
            step is None and all(s.get("status") == "done" for s in mission.get("steps", []))):
        return ("\\n[MISSION_COMPLETE] All mission steps have verified outcomes. "
                "Report the completed result to the user; do not invent extra work.")
    if step is None:
        return "\\n[MISSION_PAUSED] This mission has no runnable step. Do not claim completion."
    if step.get("status") == "running" and recovery:
        return ("\\n[MISSION_RECOVERY] A persisted verified receipt already exists. "
                "Do NOT repeat the action. Call complete_step for this exact mission and step.")
    return ("\\n[MISSION_CONTINUE] The current step is already durably marked running. "
            "Execute exactly this step once using existing tools; do NOT call begin_step again. "
            f"MISSION_ID={mission['id']} STEP_ID={step['id']}. "
            f"Step: {step['title']}. After a matching dispatcher result returns status=done verified=true, "
            "call complete_step. If the result is failed, ambiguous or awaits confirmation, block the "
            "step or stop at the confirmation gate.")



def _format(mission: dict) -> str:
    lines = [
        f"MISSION {mission['id']}: {mission['title']}",
        f"Status: {mission['status']}",
        f"Mode: {mission.get('execution_mode', 'plan')}",
        f"Goal: {mission['goal']}",
    ]
    for step in mission.get("steps", []):
        mark = "✓" if step["status"] == "done" else ("!" if step["status"] == "blocked" else ("→" if step["status"] == "running" else "·"))
        lines.append(f"{mark} {step['id']} — {step['title']} [{step['status']}]")
        if step.get("verification"):
            lines.append(f"    Verified outcome: {step['verification']}")
    pending = _next_open_step(mission)
    if pending:
        lines.extend(["", f"NEXT: {pending['id']} — {pending['title']}"])
    return "\n".join(lines)



_UI_PLAN_PARAMETER_TYPES = {
    "set_checkbox_state": {"checkbox_label": str, "checked": bool},
    "select_tab_item": {"tab_label": str},
    "select_radio_option": {"radio_label": str},
    "select_list_item": {"item_label": str},
    "select_dropdown_option": {"combo_label": str, "option_label": str},
    "set_tree_item_expanded": {"item_label": str, "expanded": bool},
    "click_and_verify": {"button_label": str, "expected_result": str},
}


def _fresh_ui_plan(goal: str) -> dict:
    """Take one fresh local UIA snapshot and validate a single read-only plan."""
    try:
        from actions import ui_interaction
        from core.ui_reasoning import plan_ui_task

        snapshot = ui_interaction._snapshot()
        proposed = plan_ui_task(goal, snapshot)
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": f"could not inspect the active application ({type(exc).__name__})",
            "steps": [],
        }

    if not isinstance(proposed, dict):
        return {"status": "blocked", "reason": "planner returned an invalid result", "steps": []}

    status = proposed.get("status")
    if status in {"needs_clarification", "blocked"}:
        return {
            "status": status,
            "reason": str(proposed.get("reason") or "")[:160],
            "question": str(proposed.get("question") or "")[:320],
            "steps": [],
        }
    if status != "planned":
        return {"status": "blocked", "reason": "planner returned an unsupported status", "steps": []}

    proposed_steps = proposed.get("steps")
    if not isinstance(proposed_steps, list) or len(proposed_steps) != 1:
        return {"status": "blocked", "reason": "planner must return exactly one UI action", "steps": []}
    item = proposed_steps[0]
    if not isinstance(item, dict):
        return {"status": "blocked", "reason": "planner returned an invalid UI action", "steps": []}

    tool_name = item.get("tool")
    expected_types = _UI_PLAN_PARAMETER_TYPES.get(tool_name)
    params = item.get("parameters")
    if not expected_types or not isinstance(params, dict) or set(params) != set(expected_types):
        return {"status": "blocked", "reason": "planner returned an unsupported UI action shape", "steps": []}
    for key, expected_type in expected_types.items():
        value = params.get(key)
        if expected_type is bool:
            valid = isinstance(value, bool)
        else:
            valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 160
        if not valid:
            return {"status": "blocked", "reason": "planner returned invalid UI action parameters", "steps": []}

    risk = item.get("risk")
    confirmation = item.get("requires_confirmation")
    if risk not in {"low", "high"} or not isinstance(confirmation, bool):
        return {"status": "blocked", "reason": "planner returned invalid UI action safety metadata", "steps": []}
    if confirmation != (risk == "high"):
        return {"status": "blocked", "reason": "planner confirmation metadata is inconsistent", "steps": []}

    safe_item = {
        "tool": tool_name,
        "parameters": dict(params),
        "risk": risk,
        "requires_confirmation": confirmation,
        "reason": str(item.get("reason") or "")[:240],
        "expected_result": str(item.get("expected_result") or "")[:320],
    }
    return {"status": "planned", "execution": "not_started", "steps": [safe_item]}


def record_tool_result(session_memory, tool_name: str, result: str,
                       parameters: dict | None = None) -> bool:
    """Persist dispatcher success and read-only recovery evidence separately."""
    from core.mission_evidence import (
        record_tool_result as record_turn_result,
        record_recovery_tool_result,
        verified_evidence,
    )
    accepted = record_turn_result(session_memory, tool_name, result)
    recovery_recorded = record_recovery_tool_result(session_memory, tool_name, result, parameters)
    current = (session_memory.get("_mission_execution")
               if isinstance(session_memory, dict) else None)
    if not isinstance(current, dict):
        return accepted or recovery_recorded
    mission_id = str(current.get("mission_id") or "")
    step_id = str(current.get("step_id") or "")
    if not mission_id or not step_id:
        return accepted or recovery_recorded
    evidence = verified_evidence(session_memory, mission_id, step_id) if accepted else None
    try:
        with _LOCK:
            data = _read()
            mission = next((m for m in data["missions"] if m.get("id") == mission_id), None)
            if mission is None:
                return accepted or recovery_recorded
            step = next((s for s in mission.get("steps", []) if s.get("id") == step_id), None)
            if step is None or step.get("status") not in {"running", "blocked"}:
                return accepted or recovery_recorded
            if evidence is None:
                step.pop("tool_evidence", None)
            else:
                step["tool_evidence"] = evidence
            step["updated_at"] = _now()
            mission["updated_at"] = _now()
            _write(data)
        return evidence is not None or recovery_recorded
    except Exception:
        return False


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
                    "execution_mode": "plan",
                    "created_at": _now(), "updated_at": _now(),
                    "steps": [{"id": f"S{i+1:02d}", "title": step, "status": "pending",
                               "verification": "", "blocked_reason": "", "updated_at": ""}
                              for i, step in enumerate(steps)],
                }
                missions.append(mission)
                _write(data)
                result = _format(mission) + (
                    "\n\nPlan saved only. Start execution with action='start' "
                    "when the user has asked you to carry out the goal."
                )
            elif action == "start":
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                if mission is None:
                    return "Give me a valid mission_id."
                if mission.get("status") in {"completed", "archived"}:
                    return "This mission is completed or archived and cannot be started."
                blocked = next((step for step in mission.get("steps", [])
                                if step.get("status") == "blocked"), None)
                if blocked:
                    from core.mission_evidence import activate_step
                    activate_step(session_memory, mission_id, blocked["id"])
                    return ("[MISSION_RECOVERY_REQUIRED] This mission has an unresolved step. "
                            "Do NOT repeat it. Inspect with inspect_active_window or a read-only "
                            "file_controller action, then call resolve_step. Model-written "
                            "verification text alone is not evidence.")
                mission["execution_mode"] = "autonomous"
                mission["status"] = "active"
                ready, recovery = _prepare_step_for_execution(mission, session_memory)
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission) + _continue_instruction(mission, recovery=recovery)
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
                    item = next(((m, step) for m in candidates if m
                                 for step in [_next_open_step(m)] if step is not None), None)
                    if not item:
                        result = "There are no pending mission steps."
                    else:
                        m, step = item
                        state = "RECOVERY REQUIRED" if step.get("status") == "running" else "NEXT"
                        result = f"{state}: {m['title']} ({m['id']}): {step['id']} — {step['title']}"
                else:
                    result = _format(selected) if selected else "Give me a mission_id to show its details."
            elif action == "plan_ui_step":
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                sid = str(parameters.get("step_id") or "").strip()
                if mission is None:
                    return "Give me a valid mission_id."
                if mission.get("status") != "active" or mission.get("execution_mode") != "autonomous":
                    return "Start this mission in autonomous mode before planning a UI step."
                step = next((s for s in mission.get("steps", []) if s.get("id") == sid), None)
                if step is None:
                    return "Give me a valid step_id for that mission."
                expected = _next_open_step(mission)
                if expected is None or expected.get("id") != sid or step.get("status") != "running":
                    return "The current step must already be running before planning its UI action."
                if step.get("ui_action"):
                    result = (
                        "[MISSION_UI_RECOVERY] A UI action plan was already issued for this step. "
                        "Do NOT request or execute it again. Inspect the current application using "
                        "read-only tools. If the action's success is verified, call complete_step "
                        "with real evidence; if uncertain, call block_step and stop."
                    )
                else:
                    ui_plan = _fresh_ui_plan(step["title"])
                    if ui_plan.get("status") == "planned":
                        step["ui_action"] = ui_plan["steps"][0]
                        step["ui_action_planned_at"] = _now()
                        step["updated_at"] = _now()
                        mission["updated_at"] = _now()
                        _write(data)
                    result = "[MISSION_UI_PLAN] " + json.dumps({
                        "mission_id": mission_id,
                        "step_id": sid,
                        **ui_plan,
                    }, ensure_ascii=False, separators=(",", ":"))
            elif action == "begin_step":
                sid = str(parameters.get("step_id") or "").strip()
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                if mission is None:
                    return "Give me a valid mission_id."
                if mission.get("status") != "active" or mission.get("execution_mode") != "autonomous":
                    return "Start or resume this mission in autonomous mode before beginning a step."
                step = next((s for s in mission.get("steps", []) if s.get("id") == sid), None)
                if step is None:
                    return "Give me a valid step_id for that mission."
                if step.get("status") == "running":
                    return "[MISSION_RECOVERY] This step was already started. Do not repeat side effects; inspect with read-only tools, then complete_step if verified or block_step if uncertain."
                expected = _next_open_step(mission)
                if expected is None or expected.get("id") != sid or step.get("status") != "pending":
                    return "Only the next pending step can be started."
                step["status"] = "running"
                try:
                    step["attempts"] = max(0, int(step.get("attempts", 0))) + 1
                except (TypeError, ValueError):
                    step["attempts"] = 1
                step["started_at"] = _now()
                step["updated_at"] = _now()
                mission["updated_at"] = _now()
                from core.mission_evidence import activate_step
                step.pop("tool_evidence", None)
                activate_step(session_memory, mission_id, sid)
                _write(data)
                result = _format(mission) + "\n[MISSION_STEP_STARTED] Step is durably marked running. Execute it once, then complete_step only after a matching dispatcher result returns status=done verified=true."
            elif action == "complete_step":
                sid = str(parameters.get("step_id") or "").strip()
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                if mission is None:
                    return "Give me a valid mission_id."
                if mission.get("status") != "active":
                    return "This mission is paused or inactive. Resume it before completing any step."
                step = next((s for s in mission["steps"] if s["id"] == sid), None)
                if step is None:
                    return "Give me a valid step_id for that mission."
                if step.get("status") == "done":
                    return "That step is already marked done; do not execute it again."
                expected = _next_open_step(mission)
                if expected is not None and expected.get("id") != sid:
                    return (f"Out-of-order completion refused. The next pending step is "
                            f"{expected['id']}: {expected['title']}.")
                if step.get("status") != "running":
                    return "Only the currently running step can be completed."
                from core.mission_evidence import clear_step, verification_summary, verified_evidence
                evidence = verified_evidence(session_memory, mission_id, sid)
                if evidence is None:
                    return ("No matching verified tool result is recorded for this mission step. "
                            "The step remains running. Do not invent a verification sentence. "
                            "Run one appropriate existing tool and complete the step only after "
                            "it returns status=done verified=true for that same tool.")
                step["status"] = "done"
                step["verification"] = verification_summary(evidence)
                step["blocked_reason"] = ""
                step["updated_at"] = _now()
                step.pop("tool_evidence", None)
                mission["updated_at"] = _now()
                clear_step(session_memory, mission_id, sid)
                if all(s.get("status") == "done" for s in mission["steps"]):
                    mission["status"] = "completed"
                    ready, recovery = True, False
                else:
                    ready, recovery = _prepare_step_for_execution(mission, session_memory)
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
                if mission.get("execution_mode") == "autonomous":
                    result += _continue_instruction(mission, recovery=recovery)
            elif action == "block_step":
                sid = str(parameters.get("step_id") or "").strip()
                reason = str(parameters.get("reason") or "").strip()
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                if mission is None:
                    return "Give me a valid mission_id."
                if mission.get("status") != "active":
                    return "This mission is paused or inactive. Resume it before blocking a step."
                step = next((s for s in mission.get("steps", []) if s.get("id") == sid), None)
                if step is None or not reason:
                    return "Give me a valid step_id and a concise blocker reason."
                expected = _next_open_step(mission)
                if expected is not None and expected.get("id") != sid:
                    return f"Only the next pending step ({expected['id']}) can be blocked."
                step["status"] = "blocked"
                step.pop("tool_evidence", None)
                from core.mission_evidence import clear_step
                clear_step(session_memory, mission_id, sid)
                step["blocked_reason"] = reason[:500]
                step["outcome_uncertain"] = True
                step["blocked_at"] = _now()
                step["updated_at"] = _now()
                mission["status"] = "paused"
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission) + (
                    "\n[MISSION_BLOCKED] Stop this mission here. Explain the exact "
                    "blocker to the user and do not attempt later steps."
                )
            elif action == "resolve_step":
                sid = str(parameters.get("step_id") or "").strip()
                resolution = str(parameters.get("resolution") or "").strip().lower()
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                if mission is None:
                    return "Give me a valid mission_id."
                if mission.get("status") != "paused":
                    return "Resolve a step only after Mission Control has paused on a blocker."
                step = next((s for s in mission.get("steps", []) if s.get("id") == sid), None)
                if step is None or step.get("status") != "blocked":
                    return "Give me the blocked step_id that needs recovery."
                if resolution not in {"completed", "retry_safe"}:
                    return "Choose resolution='completed' or resolution='retry_safe'."
                from core.mission_evidence import verified_recovery_evidence
                inspection = verified_recovery_evidence(session_memory, mission_id, sid)
                if inspection is None:
                    return ("Recovery requires an actual read-only inspection in this turn. "
                            "Use inspect_active_window or file_controller with action=list, read, "
                            "find, info, largest or disk_usage. A verification sentence alone "
                            "cannot unlock this step.")
                digest = inspection["result_sha256"][:16]
                audit = (f"Read-only inspection: {inspection['tool_name']}/"
                         f"{inspection['operation']} (receipt {digest})")
                step["recovery_verification"] = audit
                step["recovery_resolution"] = resolution
                step["recovery_resolved_at"] = _now()
                step["blocked_reason"] = ""
                step["outcome_uncertain"] = False
                step.pop("tool_evidence", None)
                step["updated_at"] = _now()
                if resolution == "completed":
                    step["status"] = "done"
                    step["verification"] = audit
                else:
                    if step.get("ui_action"):
                        step["last_ui_action"] = step.pop("ui_action")
                    if step.get("ui_action_planned_at"):
                        step["last_ui_action_planned_at"] = step.pop("ui_action_planned_at")
                    step["status"] = "pending"
                    step["verification"] = ""
                    step.pop("started_at", None)
                if all(s.get("status") == "done" for s in mission["steps"]):
                    mission["status"] = "completed"
                    ready, recovery = True, False
                else:
                    mission["status"] = "active"
                    ready, recovery = _prepare_step_for_execution(mission, session_memory)
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
                if mission.get("execution_mode") == "autonomous":
                    result += _continue_instruction(mission, recovery=recovery)
            elif action == "add_step":
                mission = next((m for m in missions if m.get("id") == mission_id), None)
                title = str(parameters.get("title") or "").strip()[:220]
                if mission is None or not title:
                    return "Give me a valid mission_id and a step title."
                if len(mission["steps"]) >= _MAX_STEPS:
                    return "This mission already has the maximum number of steps."
                sid = f"S{len(mission['steps'])+1:02d}"
                mission["steps"].append({"id": sid, "title": title, "status": "pending",
                                         "verification": "", "updated_at": ""})
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
                recovery = False
                if action == "resume":
                    blocked = next((s for s in mission.get("steps", [])
                                    if s.get("status") == "blocked"), None)
                    if blocked:
                        from core.mission_evidence import activate_step
                        activate_step(session_memory, mission_id, blocked["id"])
                        return (f"[MISSION_RECOVERY_REQUIRED] Step {blocked['id']} may already have "
                                "changed the real world. Do NOT reset or repeat it. Perform an actual "
                                "read-only inspection and then call resolve_step; model-written "
                                "verification text alone is never accepted.")
                    mission["status"] = "active"
                    ready, recovery = _prepare_step_for_execution(mission, session_memory)
                    if not ready:
                        _write(data)
                        return _format(mission) + _continue_instruction(mission, recovery=True)
                else:
                    mission["status"] = status
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
                if action == "resume" and mission.get("execution_mode") == "autonomous":
                    result += _continue_instruction(mission, recovery=recovery)
            else:
                return ("Choose plan, start, list, show, next, plan_ui_step, begin_step, complete_step, block_step, "
                        "resolve_step, add_step, pause, resume, complete or archive.")

        if player:
            try:
                player.show_content("MISSION CONTROL", result)
            except Exception:
                pass
        if len(result) < 900:
            return result
        # Never drop the machine-readable continuation/block/completion hand-off
        # when a long mission plan is shown in the HUD instead of spoken.
        marker = next((tag for tag in (
            "[MISSION_CONTINUE]", "[MISSION_BLOCKED]", "[MISSION_COMPLETE]",
            "[MISSION_RECOVERY]", "[MISSION_RECOVERY_REQUIRED]", "[MISSION_PAUSED]", "[MISSION_UI_PLAN]", "[MISSION_UI_RECOVERY]"
        ) if tag in result), "")
        tail = result[result.index(marker):] if marker else ""
        return "Mission updated. The full plan is displayed on screen. " + tail
    except Exception as exc:
        return f"Mission Control failed: {type(exc).__name__}: {str(exc)[:160]}"


PLUGIN = {
    "name": "mission_control",
    "description": (
        "Persistent Mission Control for ordered goals. Start claims the next safe "
        "step automatically; complete_step accepts only a real dispatcher result "
        "with status=done verified=true and claims the next step. Use plan_ui_step "
        "for a single verified UI action in the already-running step. Never repeat "
        "an interrupted or blocked step. Recovery requires a real read-only inspection "
        "through inspect_active_window or file_controller list/read/find/info/largest/"
        "disk_usage, followed by resolve_step. A model-written verification sentence "
        "alone cannot authorize recovery. Critical actions retain their HUD confirmation."
    ),
    "tier": "core",
    "brief": "Create and track durable multi-step missions, see next steps and resume paused work.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "plan, start, list, show, next, plan_ui_step, begin_step, complete_step, block_step, resolve_step, add_step, pause, resume, complete or archive"},
            "title": {"type": "STRING", "description": "Mission title"},
            "goal": {"type": "STRING", "description": "What must be achieved"},
            "steps": {"type": "STRING", "description": "Optional ordered step list, one step per line"},
            "mission_id": {"type": "STRING", "description": "ID of an existing mission"},
            "step_id": {"type": "STRING", "description": "Step ID such as S01"},
            "verification": {"type": "STRING", "description": "Short user-safe summary; the app separately requires an actual read-only dispatcher receipt for resolve_step."},
            "reason": {"type": "STRING", "description": "Why the current step cannot proceed, for block_step"},
            "resolution": {"type": "STRING", "description": "For resolve_step: completed or retry_safe, chosen only after read-only verification."},
        },
        "required": ["action"],
    },
}
