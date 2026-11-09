"""Deterministic, read-only planner for MARK's verified Windows UI actions.

The planner never controls the desktop. It resolves one exact, visible control
from a sanitized UI snapshot and returns at most one existing action to perform.
A fresh snapshot is required after every action.
"""
from __future__ import annotations

import re
from typing import Any

_MAX_GOAL_CHARS = 1200
_MAX_ELEMENTS = 240
_MAX_LABEL_CHARS = 160
_SUPPORTED_ROLES = {
    "Button", "Hyperlink", "MenuItem", "TabItem", "CheckBox",
    "RadioButton", "ListItem", "TreeItem", "ComboBox",
}
_SENSITIVE_PHRASES = (
    "password", "passwd", "passcode", "security code", "one-time code",
    "recovery code", "verification code", "authentication code",
    "credit card", "card number", "cvv", "cvc", "iban", "bank account",
    "routing number", "private key", "api key", "access token", "secret key",
    "security token", "bearer token", "credential",
)
_SENSITIVE_WORDS = {
    "pin", "otp", "cvv", "cvc", "iban", "token", "credential", "secret",
}
_HIGH_IMPACT_WORDS = {
    "privacy", "consent", "permission", "permissions", "security", "subscription",
    "purchase", "payment", "delete", "remove", "send", "submit", "publish",
    "transfer", "share", "grant", "reset", "format", "wipe", "erase",
    "prywatność", "prywatnosc", "zgoda", "uprawnienia", "bezpieczeństwo",
    "bezpieczenstwo", "płatność", "platnosc", "usuń", "usun", "wyślij", "wyslij",
    "opublikuj", "kup", "zapłać", "zaplac", "przelew", "udostępnij", "udostepnij",
}

_NEGATIVE_STATE_WORDS = (
    "turn off", "switch off", "disable", "uncheck", "untick", "deactivate",
    "wyłącz", "wylacz", "odznacz", "wyłącz opcję", "wylacz opcje",
)
_POSITIVE_STATE_WORDS = (
    "turn on", "switch on", "enable", "check", "tick", "activate",
    "włącz", "wlacz", "zaznacz", "włącz opcję", "wlacz opcje",
)
_SELECT_WORDS = (
    "select", "choose", "open", "switch to", "go to", "show", "wybierz",
    "otwórz", "otworz", "przejdź", "przejdz", "wejdź", "wejdz", "pokaż", "pokaz",
)
_CLICK_WORDS = (
    "click", "press", "activate", "tap", "kliknij", "naciśnij", "nacisnij",
    "wciśnij", "wcisnij", "otwórz", "otworz",
)
_EXPAND_WORDS = ("expand", "rozwiń", "rozwin")
_COLLAPSE_WORDS = ("collapse", "zwiń", "zwin", "schowaj")


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


def _is_sensitive(value: str) -> bool:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).casefold()
    words = set(re.findall(r"[a-z0-9]+", text))
    if words.intersection(_SENSITIVE_WORDS):
        return True
    if any(phrase in text for phrase in _SENSITIVE_PHRASES):
        return True
    if re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text, re.I):
        return True
    if re.search(r"(?<!\d)(?:\+?\d[\s().-]*){9,}(?!\d)", text):
        return True
    if re.fullmatch(r"[a-z0-9_-]{32,}", text):
        return True
    return False


def _safe_label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    label = " ".join(value.split())
    if not label or len(label) > _MAX_LABEL_CHARS or _is_sensitive(label):
        return ""
    return label


def _contains_phrase(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def _extract_elements(snapshot: Any) -> tuple[list[dict] | None, str]:
    if isinstance(snapshot, list):
        raw_elements = snapshot
        raw_clickables = []
    elif isinstance(snapshot, dict):
        raw_elements = snapshot.get("records", snapshot.get("elements"))
        raw_clickables = snapshot.get("clickables", [])
    else:
        return None, "ui_snapshot must be a list or an object containing records"

    if not isinstance(raw_elements, list):
        return None, "ui_snapshot must contain a records array"
    if len(raw_elements) > _MAX_ELEMENTS:
        return None, "ui_snapshot exceeds the 240-element planning limit"
    clickable_keys = set()
    if isinstance(raw_clickables, list):
        for item in raw_clickables[:_MAX_ELEMENTS]:
            if isinstance(item, dict):
                role = item.get("role")
                name = _safe_label(item.get("name"))
                if isinstance(role, str) and name:
                    clickable_keys.add((role, _normalise(name)))

    elements = []
    for item in raw_elements:
        if not isinstance(item, dict):
            continue
        role = item.get("role", item.get("control_type"))
        name = _safe_label(item.get("name", item.get("accessible_name")))
        if not isinstance(role, str) or not name:
            continue
        visible = item.get("visible", not bool(item.get("offscreen", False)))
        enabled = item.get("enabled")
        if enabled is None and (role, _normalise(name)) in clickable_keys:
            enabled = True
        parent = item.get("parent_name", item.get("parent", ""))
        if isinstance(parent, dict):
            parent = parent.get("name", "")
        parent_name = _safe_label(parent) if isinstance(parent, str) else ""
        raw_path = item.get("parent_path", item.get("path", []))
        parent_path = []
        if isinstance(raw_path, (list, tuple)):
            for part in raw_path[:8]:
                safe_part = _safe_label(part)
                if safe_part:
                    parent_path.append(safe_part)
        elements.append({
            "role": role,
            "name": name,
            "visible": visible is True,
            "enabled": enabled is True,
            "enabled_known": isinstance(enabled, bool),
            "parent_name": parent_name,
            "parent_path": parent_path,
        })
    return elements, ""


def _goal_has_any(goal: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_phrase(goal, _normalise(phrase)) for phrase in phrases)


def _expected_result(goal: str, target_position: int, elements: list[dict]) -> str:
    # A button click is only plannable when the user explicitly names the exact
    # new accessible label that should appear. The expected text must not already
    # be visible in the current snapshot.
    patterns = (
        r"(?:verify|expect|wait for|until|shows?|appears?)\s+[\"'“]([^\"'”]{1,160})[\"'”]",
        r"(?:sprawdź|sprawdz|aż pojawi się|az pojawi sie|oczekuj)\s+[\"'“]([^\"'”]{1,160})[\"'”]",
    )
    tail = goal[target_position:]
    for pattern in patterns:
        match = re.search(pattern, tail, re.I)
        if match:
            candidate = _safe_label(match.group(1))
            if candidate and not any(_normalise(item["name"]) == _normalise(candidate)
                                     for item in elements):
                return candidate
    return ""


def _risk_for(*parts: str) -> str:
    words = set()
    for part in parts:
        words.update(re.findall(r"[\w-]+", _normalise(part)))
    return "high" if words.intersection(_HIGH_IMPACT_WORDS) else "low"


def _clarify(question: str, reason: str = "ambiguous") -> dict:
    return {"status": "needs_clarification", "reason": reason, "question": question, "steps": []}


def _blocked(reason: str) -> dict:
    return {"status": "blocked", "reason": reason, "steps": []}


def _resolve_target(goal: str, elements: list[dict]) -> tuple[dict | None, str, int]:
    mentions = []
    actionable = [element for element in elements if element["role"] in _SUPPORTED_ROLES]
    for element in actionable:
        name = _normalise(element["name"])
        position = goal.find(name)
        if position >= 0:
            mentions.append((position, element))
    if not mentions:
        return None, "no_exact_label", -1
    mentions.sort(key=lambda pair: pair[0])
    first_position = mentions[0][0]
    earliest = [item for position, item in mentions if position == first_position]
    # Different controls can share a label. An explicit parent context in the
    # user's goal may disambiguate only if it identifies exactly one candidate.
    label = earliest[0]["name"]
    duplicates = [item for item in actionable if _normalise(item["name"]) == _normalise(label)]
    if len(duplicates) > 1:
        contextual = [item for item in duplicates
                      if any(_contains_phrase(goal, _normalise(context))
                             for context in ([item["parent_name"]] + item["parent_path"]) if context)]
        if len(contextual) == 1:
            return contextual[0], "", first_position
        return None, "duplicate_label", first_position
    if len(earliest) != 1:
        return None, "duplicate_label", first_position
    return earliest[0], "", first_position


def plan_ui_task(goal: str, ui_snapshot: Any) -> dict:
    """Return one safe, exact next action; never execute it.

    Snapshots may be the current actions.ui_interaction._snapshot() result or
    a list of sanitized element dictionaries. Optional parent_name/parent_path
    fields allow duplicate labels to be disambiguated when a richer snapshot is
    available. Editable field values are never read or copied.
    """
    if not isinstance(goal, str) or not goal.strip() or len(goal) > _MAX_GOAL_CHARS:
        return _blocked("goal must contain 1 to 1200 characters")
    clean_goal = " ".join(goal.split())
    goal_norm = _normalise(clean_goal)
    if _is_sensitive(clean_goal):
        return _blocked("goals involving credentials, verification codes or payment data are unsupported")

    elements, error = _extract_elements(ui_snapshot)
    if elements is None:
        return _blocked(error)
    elements = [item for item in elements if item["visible"]]
    target, match_error, position = _resolve_target(goal_norm, elements)
    if target is None:
        if match_error == "duplicate_label":
            return _clarify("Which occurrence do you mean? Name its parent panel or section.", match_error)
        return _clarify("Name the exact visible control label you want MARK to use.", match_error)
    if not target["enabled_known"]:
        return _clarify("The selected control's enabled state is unknown. Refresh the UI snapshot first.", "enabled_state_unknown")
    if not target["enabled"]:
        return _blocked("the exact target control is disabled")

    role = target["role"]
    label = target["name"]
    tool_name = ""
    parameters: dict[str, Any] = {}
    reason = "Matched an exact visible, enabled accessible label."
    expected = "The existing MARK action must verify its own postcondition before the next plan is requested."

    if role == "CheckBox":
        negative = _goal_has_any(goal_norm, _NEGATIVE_STATE_WORDS)
        positive = _goal_has_any(goal_norm, _POSITIVE_STATE_WORDS)
        if negative == positive:
            return _clarify("Should this checkbox be checked or unchecked?", "checkbox_state_unspecified")
        tool_name = "set_checkbox_state"
        parameters = {"checkbox_label": label, "checked": positive}
        expected = "The checkbox action verifies the requested checked state locally."
        reason = "The goal explicitly specifies the checkbox's desired state."
    elif role == "TabItem":
        if not _goal_has_any(goal_norm, _SELECT_WORDS):
            return _clarify("Do you want MARK to open this tab?", "tab_intent_unspecified")
        tool_name = "select_tab_item"
        parameters = {"tab_label": label}
        expected = "The tab action verifies that this exact tab is selected."
        reason = "The goal explicitly asks to open or select this visible tab."
    elif role == "RadioButton":
        if not _goal_has_any(goal_norm, _SELECT_WORDS):
            return _clarify("Do you want MARK to select this radio option?", "selection_intent_unspecified")
        tool_name = "select_radio_option"
        parameters = {"radio_label": label}
        expected = "The radio action verifies the selected state locally."
        reason = "The goal explicitly asks to select this exact radio option."
    elif role == "ListItem":
        if not _goal_has_any(goal_norm, _SELECT_WORDS):
            return _clarify("Do you want MARK to select this list item?", "selection_intent_unspecified")
        combo_parent = target["parent_name"]
        combo_matches = [item for item in elements
                         if item["role"] == "ComboBox" and combo_parent
                         and _normalise(item["name"]) == _normalise(combo_parent)
                         and item["enabled"]]
        if combo_matches and _contains_phrase(goal_norm, _normalise(combo_parent)):
            if len(combo_matches) != 1:
                return _clarify("Which dropdown contains this option? Name its parent section.", "ambiguous_dropdown")
            tool_name = "select_dropdown_option"
            parameters = {"combo_label": combo_parent, "option_label": label}
            expected = "The dropdown action verifies selection of this exact already-visible option."
            reason = "The exact option is a visible child of the named ComboBox."
        else:
            tool_name = "select_list_item"
            parameters = {"item_label": label}
            expected = "The list action verifies selection in a single-selection list."
            reason = "The goal explicitly asks to select this exact visible list item."
    elif role == "TreeItem":
        expand = _goal_has_any(goal_norm, _EXPAND_WORDS)
        collapse = _goal_has_any(goal_norm, _COLLAPSE_WORDS)
        if expand == collapse:
            return _clarify("Should MARK expand or collapse this tree item?", "tree_state_unspecified")
        tool_name = "set_tree_item_expanded"
        parameters = {"item_label": label, "expanded": expand}
        expected = "The tree action verifies the requested expanded or collapsed state locally."
        reason = "The goal explicitly specifies the tree item's desired expansion state."
    elif role in {"Button", "Hyperlink", "MenuItem"}:
        if not _goal_has_any(goal_norm, _CLICK_WORDS):
            return _clarify("Do you want MARK to click this control?", "click_intent_unspecified")
        expected_label = _expected_result(clean_goal, position + len(_normalise(label)), elements)
        if not expected_label:
            return _clarify(
                "What exact accessible label should appear after this click? MARK will not invent a postcondition.",
                "click_postcondition_missing",
            )
        tool_name = "click_and_verify"
        parameters = {"button_label": label, "expected_result": expected_label}
        expected = f"The exact accessible label '{expected_label}' must appear after the click."
        reason = "The goal supplies an exact postcondition for the existing verified click action."
    elif role == "ComboBox":
        return _clarify("Open the dropdown first, then specify the exact visible option to select.", "dropdown_option_not_available")
    else:
        return _clarify("This control type has no supported verified action yet.", "unsupported_control_type")

    risk = _risk_for(label, expected)
    step = {
        "tool": tool_name,
        "parameters": parameters,
        "target": {"role": role, "name": label,
                    "parent_name": target["parent_name"],
                    "parent_path": target["parent_path"]},
        "reason": reason,
        "expected_result": expected,
        "risk": risk,
        "requires_confirmation": risk == "high",
    }
    return {"status": "planned", "goal": clean_goal, "steps": [step], "execution": "not_started"}


def replan_ui_task(goal: str, new_ui_snapshot: Any, previous_result: Any) -> dict:
    """Plan from a fresh snapshot after one previous action result.

    An unverified or confirmation-pending outcome is never retried. For a
    definite failure, the same tool/parameters pair is not proposed again.
    """
    if not isinstance(previous_result, dict):
        return _blocked("previous_result must be a structured local action result")
    status = previous_result.get("status")
    if status in {"unverified", "uncertain", "ask", "confirmation_pending"}:
        return _clarify(
            "The previous action was not conclusively verified. Inspect the application and resolve its state before continuing.",
            "previous_action_unverified",
        )
    if status not in {"done", "failed"}:
        return _blocked("previous_result must have status done or failed")

    next_plan = plan_ui_task(goal, new_ui_snapshot)
    previous_step = previous_result.get("step")
    if status == "failed" and isinstance(previous_step, dict) and next_plan.get("status") == "planned":
        proposed = next_plan["steps"][0]
        if (proposed.get("tool") == previous_step.get("tool")
                and proposed.get("parameters") == previous_step.get("parameters")):
            return _clarify(
                "The same action failed once. Check the application or choose a different exact control; automatic retry is disabled.",
                "same_action_retry_blocked",
            )
    return next_plan
