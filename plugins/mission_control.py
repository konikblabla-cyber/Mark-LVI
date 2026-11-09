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


def _continue_instruction(mission: dict, recovery: bool = False) -> str:
    """Hand off the already-claimed step, or recover without replaying it."""
    step = _next_open_step(mission)
    if mission.get("status") == "completed" or step is None:
        return ("\n[MISSION_COMPLETE] All mission steps have verified outcomes. "
                "Report the completed result to the user; do not invent extra work.")
    if step.get("status") == "running" and recovery:
        if step.get("tool_evidence"):
            return ("\n[MISSION_RECOVERY] The previous session has a persisted receipt from "
                    "a tool that returned status=done. Do NOT repeat the action. Call "
                    "complete_step for this exact step; the application will accept it only "
                    "if the persisted receipt still matches the running mission step.")
        if step.get("ui_action"):
            return ("\n[MISSION_RECOVERY] A UI action plan was persisted before the last "
                    "session ended. The action may or may not have happened. Do NOT issue "
                    "or repeat it. First inspect the real state using read-only tools. "
                    "Only complete the step after a fresh tool result verifies the outcome; "
                    "otherwise block_step and stop.")
        return ("\n[MISSION_RECOVERY] This step was already running before the current "
                "execution context. Its action may already have happened. Do NOT repeat "
                "side effects. Inspect with read-only tools and obtain a fresh verifiable "
                "result; otherwise block_step and stop.")
    return ("\n[MISSION_CONTINUE] The current step is already durably marked running. "
            "Execute exactly this step once using the existing tools; do NOT call begin_step "
            f"again. MISSION_ID={mission['id']} STEP_ID={step['id']}. "
            f"Step: {step['title']}. After a real tool returns matching status=done, "
            "immediately call complete_step for this mission and step. If the tool fails, "
            "is ambiguous, unverified or waits for confirmation, do not claim success; "
            "block the step or stop at the confirmation gate.")


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


def record_tool_result(session_memory, tool_name: str, result: str) -> bool:
    """Persist the actual dispatcher receipt for the currently running step.

    This is an internal application hook, not a model-callable plugin action.
    Raw tool output is reduced to a status/action pair and a SHA-256 fingerprint.
    """
    from core.mission_evidence import (
        record_tool_result as record_turn_result,
        verified_evidence,
    )

    accepted = record_turn_result(session_memory, tool_name, result)
    current = (session_memory.get("_mission_execution")
               if isinstance(session_memory, dict) else None)
    if not isinstance(current, dict):
        return False
    mission_id = str(current.get("mission_id") or "")
    step_id = str(current.get("step_id") or "")
    if not mission_id or not step_id:
        return False

    evidence = verified_evidence(session_memory, mission_id, step_id) if accepted else None
    try:
        with _LOCK:
            data = _read()
            mission = next((m for m in data["missions"] if m.get("id") == mission_id), None)
            if mission is None:
                return False
            step = next((s for s in mission.get("steps", []) if s.get("id") == step_id), None)
            if step is None or step.get("status") != "running":
                return False
            if evidence is None:
                step.pop("tool_evidence", None)
            else:
                step["tool_evidence"] = evidence
            step["updated_at"] = _now()
            mission["updated_at"] = _now()
            _write(data)
        return evidence is not None
    except Exception:
        # Failure to persist evidence must never authorize a completion.
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
                if any(step.get("status") == "blocked" for step in mission.get("steps", [])):
                    return "This mission has a blocked step. Resolve the blocker, then use resume."
                mission["execution_mode"] = "autonomous"
                mission["status"] = "active"
                mission["updated_at"] = _now()
                next_step = _next_open_step(mission)
                was_running = bool(next_step and next_step.get("status") == "running")
                if next_step and next_step.get("status") == "pending":
                    next_step["status"] = "running"
                    next_step["started_at"] = _now()
                    next_step["updated_at"] = _now()
                    from core.mission_evidence import activate_step
                    next_step.pop("tool_evidence", None)
                    activate_step(session_memory, mission_id, next_step["id"])
                elif next_step and next_step.get("status") == "running":
                    from core.mission_evidence import seed_step_evidence
                    activate_step(session_memory, mission_id, next_step["id"])
                    seed_step_evidence(
                        session_memory, mission_id, next_step["id"],
                        next_step.get("tool_evidence"),
                    )
                _write(data)
                result = _format(mission) + _continue_instruction(
                    mission, recovery=was_running,
                )
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
                    return "Call begin_step for the current mission step before planning its UI action."
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
                    return "[MISSION_RECOVERY] This step was already started. Do not repeat side effects; inspect with read-only tools and obtain fresh evidence, or block_step if uncertain."
                expected = _next_open_step(mission)
                if expected is None or expected.get("id") != sid or step.get("status") != "pending":
                    return "Only the next pending step can be started."
                step["status"] = "running"
                step.pop("tool_evidence", None)
                step["started_at"] = _now()
                step["updated_at"] = _now()
                mission["updated_at"] = _now()
                from core.mission_evidence import activate_step
                activate_step(session_memory, mission_id, sid)
                _write(data)
                result = _format(mission) + (
                    "\n[MISSION_STEP_STARTED] Step is durably marked running. "
                    "Execute it once, then complete_step only after a matching tool result "
                    "returns status=done."
                )
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
                from core.mission_evidence import (
                    clear_step, verification_summary, verified_evidence,
                )
                evidence = verified_evidence(session_memory, mission_id, sid)
                if evidence is None:
                    return (
                        "No matching verified tool result is recorded for this mission step. "
                        "The step remains running. Do not invent a verification sentence. "
                        "Run one appropriate existing tool and complete the step only after "
                        "it returns status=done for that same tool."
                    )
                step["status"] = "done"
                step["verification"] = verification_summary(evidence)
                step["blocked_reason"] = ""
                step["updated_at"] = _now()
                mission["updated_at"] = _now()
                clear_step(session_memory, mission_id, sid)
                next_step = _next_open_step(mission)
                step.pop("tool_evidence", None)
                if next_step is None:
                    mission["status"] = "completed"
                elif next_step.get("status") == "pending":
                    # Claim the next safe step now; the model can act without
                    # another tool round-trip just to call begin_step.
                    next_step["status"] = "running"
                    next_step["started_at"] = _now()
                    next_step["updated_at"] = _now()
                    from core.mission_evidence import activate_step
                    next_step.pop("tool_evidence", None)
                    activate_step(session_memory, mission_id, next_step["id"])
                _write(data)
                result = _format(mission)
                if mission.get("execution_mode") == "autonomous":
                    result += _continue_instruction(mission)
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
                    return f"Only the next active step ({expected['id']}) can be blocked."
                step["status"] = "blocked"
                step.pop("tool_evidence", None)
                step["blocked_reason"] = reason[:500]
                step["updated_at"] = _now()
                mission["status"] = "paused"
                mission["updated_at"] = _now()
                from core.mission_evidence import clear_step
                clear_step(session_memory, mission_id, sid)
                _write(data)
                result = _format(mission) + (
                    "\n[MISSION_BLOCKED] Stop this mission here. Explain the exact "
                    "blocker to the user and do not attempt later steps."
                )
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
                    # A previously running step is recovery: never replay it.
                    for step in mission.get("steps", []):
                        if step.get("status") == "blocked":
                            step["status"] = "pending"
                            step["blocked_reason"] = ""
                            step["updated_at"] = _now()
                    mission["status"] = "active"
                    next_step = _next_open_step(mission)
                    recovery = bool(next_step and next_step.get("status") == "running")
                    if next_step and next_step.get("status") == "pending":
                        next_step["status"] = "running"
                        next_step["started_at"] = _now()
                        next_step["updated_at"] = _now()
                        from core.mission_evidence import activate_step
                        next_step.pop("tool_evidence", None)
                        activate_step(session_memory, mission_id, next_step["id"])
                    elif next_step and next_step.get("status") == "running":
                        from core.mission_evidence import seed_step_evidence
                        activate_step(session_memory, mission_id, next_step["id"])
                        seed_step_evidence(
                            session_memory, mission_id, next_step["id"],
                            next_step.get("tool_evidence"),
                        )
                else:
                    mission["status"] = status
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
                if action == "resume" and mission.get("execution_mode") == "autonomous":
                    result += _continue_instruction(mission, recovery=recovery)
            else:
                return ("Choose plan, start, list, show, next, plan_ui_step, begin_step, complete_step, block_step, "
                        "add_step, pause, resume, complete or archive.")

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
            "[MISSION_RECOVERY]", "[MISSION_UI_PLAN]", "[MISSION_UI_RECOVERY]"
        ) if tag in result), "")
        tail = result[result.index(marker):] if marker else ""
        return "Mission updated. The full plan is displayed on screen. " + tail
    except Exception as exc:
        return f"Mission Control failed: {type(exc).__name__}: {str(exc)[:160]}"


PLUGIN = {
    "name": "mission_control",
    "description": (
        "Persistent Mission Control for multi-step goals and projects. Use it when a request "
        "requires several ordered steps, spans more than one turn, or the user asks to plan, "
        "track, resume or review ongoing work. If the user only asks for a plan, save it with "
        "action='plan' and stop. If the user asks you to DO the goal, call action='plan' then "
        "action='start' with its mission_id. The [MISSION_CONTINUE] result is an instruction "
        "to execute the next step immediately using the existing tools, record verified "
        "success with complete_step, and continue until [MISSION_COMPLETE] or [MISSION_BLOCKED]. "
        "Starting or resuming an autonomous mission claims the current step automatically; do not call begin_step again. "
                "For a running desktop-UI step, call plan_ui_step. It takes a fresh "
        "privacy-filtered local UI snapshot and returns at most one exact existing verified action; "
        "it never executes that action. Execute only that returned tool and parameters once, then "
        "complete_step only with real verification evidence. A UI plan is persisted before hand-off; "
        "after interruption never reissue it—inspect read-only and complete or block. If the planner "
        "asks for clarification or blocks, do not guess. High-impact actions still require the real "
        "HUD confirmation. Never mark a step done without tool evidence. Stop at "
        "[CONFIRMATION_PENDING] and ask for the real HUD confirmation. The live assistant executes "
        "steps through existing verified tools."
    ),
    "tier": "core",
    "brief": "Create and track durable multi-step missions, see next steps and resume paused work.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "plan, start, list, show, next, plan_ui_step, begin_step, complete_step, block_step, add_step, pause, resume, complete or archive"},
            "title": {"type": "STRING", "description": "Mission title"},
            "goal": {"type": "STRING", "description": "What must be achieved"},
            "steps": {"type": "STRING", "description": "Optional ordered step list, one step per line"},
            "mission_id": {"type": "STRING", "description": "ID of an existing mission"},
            "step_id": {"type": "STRING", "description": "Step ID such as S01"},
            "verification": {"type": "STRING", "description": "Short result or evidence from the tool that completed the step"},
            "reason": {"type": "STRING", "description": "Why the current step cannot proceed, for block_step"},
        },
        "required": ["action"],
    },
}
