"""Read-only tool that plans one next action from the active UI snapshot."""
from __future__ import annotations

import json

from actions import ui_interaction as ui
from core.ui_reasoning import plan_ui_task as _plan_ui_task


def plan_current_ui_task(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters if isinstance(parameters, dict) else {}
    goal = params.get("goal")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 1200:
        result = {"status": "blocked", "reason": "goal must contain 1 to 1200 characters", "steps": []}
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    try:
        snapshot = ui._snapshot()
    except Exception as exc:
        result = {"status": "blocked", "reason": f"could not inspect the active application ({type(exc).__name__})", "steps": []}
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    result = _plan_ui_task(goal, snapshot)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


TOOL = {
    "name": "plan_ui_task",
    "tier": "core",
    "brief": "Read the active window's safe accessible labels and plan exactly one next verified UI action; never execute it.",
    "description": (
        "Planning only. Use for an explicitly requested multi-step UI task when you need to map the user's "
        "goal to an exact visible control. This performs one bounded local UI Automation snapshot using "
        "MARK's existing privacy-filtered reader; it does not read edit values, click, type, screenshot, "
        "poll in the background, use a network, or call an LLM. It returns at most ONE next action using "
        "an existing MARK tool and exact parameters. Only use that action if it directly matches the user's "
        "request. After each action, call this planner again to use a fresh snapshot. Never retry an action "
        "whose result is unverified. Duplicate or unknown targets, disabled controls, unspecified checkbox "
        "states, and clicks without an exact new accessible postcondition require clarification. The planner "
        "does not execute any returned action."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {"type": "STRING", "description": "The user's exact UI task, including exact control labels and any expected click result."},
        },
        "required": ["goal"],
    },
    "handler": plan_current_ui_task,
}
