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


def _continue_instruction(mission: dict) -> str:
    """Durable hand-off with crash-safe handling of a possibly interrupted action."""
    step = _next_open_step(mission)
    if mission.get("status") == "completed" or step is None:
        return ("\n[MISSION_COMPLETE] All mission steps have verified outcomes. "
                "Report the completed result to the user; do not invent extra work.")
    if step.get("status") == "running":
        if step.get("ui_action"):
            return ("\n[MISSION_RECOVERY] A UI action plan was already issued for this step before "
                    "the last session ended. The action may or may not have happened. Do NOT issue "
                    "or repeat it. First use read-only tools to inspect the real state. If success "
                    "is verified, call complete_step; if uncertain, call block_step and explain why.")
        return ("\n[MISSION_RECOVERY] This step was started before the last session ended. "
                "Its action may already have happened. Do NOT repeat side effects. First use "
                "read-only tools to inspect the real state. If success is verified, call "
                "complete_step; if uncertain, call block_step and explain why.")
    return ("\n[MISSION_CONTINUE] Continue this autonomous mission now. "
            f"MISSION_ID={mission['id']} STEP_ID={step['id']}. "
            "First call begin_step and wait for its result. Only then execute this step once "
            f"using existing tools: {step['title']}. "
            "After a real tool result verifies success, call complete_step. On a blocker, "
            "call block_step and stop. Never invent tool results.")


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
                    return ("[MISSION_RECOVERY_REQUIRED] This mission has an unresolved step. "
                            "Inspect the real state with read-only tools, then use resolve_step: "
                            "resolution='completed' if the result is already present, or "
                            "resolution='retry_safe' only if inspection proves no side effect occurred.")
                mission["execution_mode"] = "autonomous"
                mission["status"] = "active"
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission) + _continue_instruction(mission)
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
                _write(data)
                result = _format(mission) + "\n[MISSION_STEP_STARTED] Step is durably marked running. Execute it once, verify the real outcome, then call complete_step."
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
                    return "Call begin_step before executing this step; completion is not accepted yet."
                verification = str(parameters.get("verification") or "").strip()
                if not verification:
                    return ("Give me the verified result from the tool that completed this step. "
                            "I will not mark a step done without evidence.")
                step["status"] = "done"
                step["verification"] = verification[:500]
                step["blocked_reason"] = ""
                step["updated_at"] = _now()
                mission["updated_at"] = _now()
                if all(s["status"] == "done" for s in mission["steps"]):
                    mission["status"] = "completed"
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
                    return f"Only the next pending step ({expected['id']}) can be blocked."
                step["status"] = "blocked"
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
                verification = str(parameters.get("verification") or "").strip()
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
                if not verification:
                    return ("Recovery requires evidence from a read-only inspection. "
                            "Do not guess whether the earlier action took effect.")
                step["recovery_verification"] = verification[:500]
                step["recovery_resolution"] = resolution
                step["recovery_resolved_at"] = _now()
                step["blocked_reason"] = ""
                step["outcome_uncertain"] = False
                step["updated_at"] = _now()
                if resolution == "completed":
                    step["status"] = "done"
                    step["verification"] = verification[:500]
                else:
                    # The previous attempt is retained for audit, but must not be
                    # replayed. A fresh plan is required after read-only proof that
                    # no externally visible effect occurred.
                    if step.get("ui_action"):
                        step["last_ui_action"] = step.pop("ui_action")
                    if step.get("ui_action_planned_at"):
                        step["last_ui_action_planned_at"] = step.pop("ui_action_planned_at")
                    step["status"] = "pending"
                    step["verification"] = ""
                    step.pop("started_at", None)
                mission["status"] = (
                    "completed" if all(s.get("status") == "done" for s in mission["steps"])
                    else "active"
                )
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
                if mission.get("execution_mode") == "autonomous":
                    result += _continue_instruction(mission)
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
                if action == "resume":
                    blocked = next((s for s in mission.get("steps", [])
                                    if s.get("status") == "blocked"), None)
                    if blocked:
                        return (
                            f"[MISSION_RECOVERY_REQUIRED] Step {blocked['id']} may already have "
                            "changed the real world. Do not reset or repeat it. Inspect the outcome "
                            "with read-only tools, then call resolve_step with resolution='completed' "
                            "if success is visible, or resolution='retry_safe' only if evidence "
                            "proves that no side effect occurred."
                        )
                mission["status"] = status
                mission["updated_at"] = _now()
                _write(data)
                result = _format(mission)
                if action == "resume" and mission.get("execution_mode") == "autonomous":
                    result += _continue_instruction(mission)
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
        "For a running desktop-UI step, call plan_ui_step after begin_step. It takes a fresh "
        "privacy-filtered local UI snapshot and returns at most one exact existing verified action; "
        "it never executes that action. Execute only that returned tool and parameters once, then "
        "complete_step only with real verification evidence. A UI plan is persisted before hand-off; "
        "after interruption never reissue it—inspect read-only and complete or block. If the planner "
        "asks for clarification or blocks, do not guess. High-impact actions still require the real "
        "HUD confirmation. Never mark a step done without tool evidence. Stop at "
        "[CONFIRMATION_PENDING] and ask for the real HUD confirmation. The live assistant executes "
        "A blocked step is never reset by resume: first inspect with read-only tools, then "
        "call resolve_step with resolution='completed' if the outcome is already visible, "
        "or resolution='retry_safe' only when evidence proves the previous attempt had no "
        "side effect. Preserve old UI plans for audit and create a fresh plan before retrying. "
        "Never blindly repeat an uncertain action. The live assistant executes steps through "
        "existing verified tools."
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
            "verification": {"type": "STRING", "description": "Short result or evidence from the tool that completed the step"},
            "reason": {"type": "STRING", "description": "Why the current step cannot proceed, for block_step"},
            "resolution": {"type": "STRING", "description": "For resolve_step: completed or retry_safe, chosen only after read-only verification."},
        },
        "required": ["action"],
    },
}
