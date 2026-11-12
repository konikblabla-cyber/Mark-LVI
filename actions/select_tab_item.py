"""Select one exact visible Windows TabItem and verify SelectionItemPattern."""
from __future__ import annotations

from core import outcome
from actions import ui_interaction as ui


def select_tab_item(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters if isinstance(parameters, dict) else {}
    label = " ".join(str(params.get("tab_label") or "").split())
    if not label or len(label) > 120:
        return outcome.failed("select_tab_item", "tab_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed(
            "select_tab_item",
            "credential, verification-code and payment controls are not supported",
            tab_label=label,
        )
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed(
            "select_tab_item",
            f"could not identify the active application ({type(exc).__name__})",
        )

    def run():
        completed, result, error = ui._run_uia(
            lambda cancelled: ui._select_item_on_window(
                handle, process_id, label, "TabItem", cancelled,
            ),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified(
                "select_tab_item",
                "the selection may have occurred; inspect the original application before retrying",
                tab_label=label,
            )
        if not isinstance(result, tuple) or len(result) != 2:
            return outcome.failed("select_tab_item", "UI Automation returned an invalid result")
        status, reason = result
        if status in {"selected", "already_selected"}:
            return outcome.done(
                "select_tab_item",
                tab_label=label,
                verified=True,
                changed=status == "selected",
            )
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("select_tab_item", reason, tab_label=label)
        if status in {"ambiguous_item", "busy", "cancelled"}:
            return outcome.ask("select_tab_item", reason, tab_label=label)
        return outcome.failed("select_tab_item", reason or status, tab_label=label)

    if ui._requires_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask(
                "select_tab_item",
                "another confirmation is already waiting on the HUD",
                tab_label=label,
            )
        return confirm.request(
            key="select_tab_item",
            title="Confirm tab: " + label,
            detail=(
                "MARK will select the exact visible TabItem '" + label +
                "'. It will not submit, save or send the form."
            ),
            run=run,
        )
    return run()


TOOL = {
    "name": "select_tab_item",
    "tier": "core",
    "brief": "Select one exact visible Windows TabItem and verify its selection state.",
    "description": (
        "Use only when the user explicitly asks MARK to open or select one specific visible tab. "
        "The action requires one unique visible, enabled, non-sensitive control in the active application, "
        "revalidates the original window handle and process, uses SelectionItemPattern only once, and "
        "verifies CurrentIsSelected locally. Already-selected items are left unchanged. Ambiguous labels, "
        "unsupported patterns and unverified outcomes are never guessed or blindly retried. "
        "Potentially high-impact labels require HUD confirmation. This action does not submit or save a form, "
        "read field values, use a screenshot, or send extra model/API requests."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "tab_label": {
                "type": "STRING",
                "description": "Exact accessible label of one visible, enabled, non-sensitive Windows TabItem.",
            }
        },
        "required": ["tab_label"],
    },
    "handler": select_tab_item,
}
