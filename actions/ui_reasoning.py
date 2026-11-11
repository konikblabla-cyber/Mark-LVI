"""Read-only tool that plans one next action from the active UI snapshot."""
from __future__ import annotations

import json

from actions import ui_interaction as ui
from core.ui_reasoning import plan_ui_task as _plan_ui_task, replan_ui_task as _replan_ui_task


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
    previous_result = params.get("previous_result")
    result = (
        _replan_ui_task(goal, snapshot, previous_result)
        if previous_result is not None
        else _plan_ui_task(goal, snapshot)
    )
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


TOOL = {
    "name": "plan_ui_task",
    "tier": "core",
    "brief": "Read the active window's safe accessible labels and plan exactly one next verified UI action; never execute it.",
    "description": (
        "Planning only. Use for an explicitly requested multi-step UI task when you need to map the user's "
        "goal to an exact visible control. This performs one bounded local UI Automation snapshot using "
        "MARK's existing privacy-filtered reader; it does not read edit values, click, type, take a screenshot, "
        "poll in the background, use a network, or call an LLM. If no exact accessibility label exists, "
        "a clearly described visual target with an exact quoted postcondition may produce one computer_control "
        "screen_click_verified action; that separate action sends only the cropped target window to Gemini. "
        "It returns at most ONE next action using an existing MARK tool and exact parameters. Only use that action if it directly matches the user's "
        "request. After each action, call this planner again to use a fresh snapshot. When doing so, pass "
        "previous_result with the actual prior status (done, failed or unverified) and, for a failed action, "
        "its exact tool and parameters. An unverified action stops for inspection; after a definite failure "
        "the planner refuses to propose the same tool/parameters again and may offer a different exact next "
        "action. Duplicate or unknown targets, disabled controls, unspecified checkbox states, and clicks "
        "without an exact new accessible postcondition require clarification. The planner never executes actions."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {"type": "STRING", "description": "The user's exact UI task, including exact control labels and any expected click result."},
            "previous_result": {
                "type": "OBJECT",
                "description": "Optional actual outcome of the immediately previous UI action. Use status=done, failed or unverified. For failed, include step with exact tool and parameters so the planner can block identical retries.",
                "properties": {
                    "status": {"type": "STRING", "enum": ["done", "failed", "unverified", "ask", "confirmation_pending"]},
                    "step": {
                        "type": "OBJECT",
                        "properties": {
                            "tool": {"type": "STRING"},
                            "parameters": {"type": "OBJECT"}
                        },
                        "required": ["tool", "parameters"]
                    }
                },
                "required": ["status"]
            },
        },
        "required": ["goal"],
    },
    "handler": plan_current_ui_task,
}
