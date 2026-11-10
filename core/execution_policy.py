"""Confirmation policy for actions that execute generated or supplied code."""


def requires_dev_agent_confirmation(parameters: dict) -> bool:
    """The coding agent installs dependencies and executes project code."""
    mode = str((parameters or {}).get("mode") or "build").strip().lower()
    return mode != "status"


def requires_code_helper_confirmation(action: str) -> bool:
    """Only code_helper's run/build intents execute code; writing is reversible."""
    return str(action or "").strip().lower() in {"run", "build"}
