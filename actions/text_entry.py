"""Standalone safe text entry, sharing the verified UIA implementation."""
from __future__ import annotations

from actions import ui_interaction as ui
from core import outcome

_MAX_FIELD_LABEL_CHARS = 120
_MAX_TEXT_CHARS = 4000


def _validate(parameters: dict) -> tuple[str | None, str | None, str]:
    params = parameters if isinstance(parameters, dict) else {}
    label = " ".join(str(params.get("field_label") or "").split())
    value = params.get("text")
    if not label or len(label) > _MAX_FIELD_LABEL_CHARS:
        return None, None, "field_label is required and must be at most 120 characters"
    if ui._is_sensitive_input_label(label):
        return None, None, "credential, verification-code and payment fields are not supported"
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT_CHARS or "\x00" in value:
        return None, None, "text must contain 1 to 4000 characters and no NUL bytes"
    if ui._looks_like_secret_payload(value):
        return None, None, "the text resembles a credential, key or opaque access token; it was not entered"
    return label, value, ""


def fill_text_field(parameters: dict, player=None, session_memory=None) -> str:
    label, value, error = _validate(parameters)
    if error:
        return outcome.failed("fill_text_field", error)
    try:
        snapshot = ui._snapshot()
    except Exception as exc:
        return outcome.failed(
            "fill_text_field",
            f"could not identify the active application window ({type(exc).__name__})",
            field=label,
        )
    # Share the same pinned-window ValuePattern implementation used by
    # run_ui_sequence. This keeps sensitive-value screening, timeouts, identity
    # checks and local-only verification in one place.
    return ui._fill_text_and_verify(
        label, value, snapshot["handle"], snapshot["process_id"],
    )


TOOL = {
    "name": "fill_text_field",
    "tier": "core",
    "brief": "Fill one exact non-sensitive field locally and verify without returning its value.",
    "description": (
        "Use only when the user explicitly asks MARK to enter supplied text into "
        "one currently visible, exactly labelled field in the active Windows "
        "application. Require the exact accessible field label and exact text. "
        "This uses the same local UI Automation ValuePattern implementation as "
        "verified multi-step form workflows. Passwords, PINs, one-time/recovery "
        "codes, payment and bank details, API keys, tokens, private keys and "
        "credential-like values are rejected. The existing field value is read "
        "only inside the local COM worker for exact verification and secret-like "
        "value screening; neither existing nor entered text is returned to the "
        "conversation or logged. It does not press Enter, submit forms, click "
        "buttons, use the clipboard, inject keystrokes, take screenshots, poll "
        "in the background, or make extra model/API calls. Some applications "
        "may autosave while text is entered. Uncertain results are reported as "
        "unverified and must not be retried automatically."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "field_label": {
                "type": "STRING",
                "description": "Exact accessible label of one visible, non-sensitive editable field.",
            },
            "text": {
                "type": "STRING",
                "description": "Exact text explicitly supplied by the user to enter; never infer or invent it.",
            },
        },
        "required": ["field_label", "text"],
    },
    "handler": fill_text_field,
}
