"""On-demand metadata-only status for the explicitly selected project folder."""
from __future__ import annotations

from pathlib import Path

from core.project_context import (
    inspect_project_folder, format_project_context, remember_project_snapshot,
)


def inspect_selected_project(player=None) -> dict | None:
    """Inspect the current user-selected/uploaded project directory, if any."""
    path = getattr(player, "current_file", None) if player is not None else None
    if not path:
        return None
    try:
        if not Path(str(path)).is_dir():
            return None
    except (OSError, ValueError, TypeError):
        return None
    return inspect_project_folder(path)


def run(parameters: dict, player=None, session_memory=None) -> str:
    snapshot = inspect_selected_project(player)
    if snapshot is None:
        return "No project folder is selected. Open or upload a project folder first."
    remember_project_snapshot(snapshot)
    result = format_project_context(snapshot)
    if player:
        try:
            player.show_content("PROJECT OVERVIEW", result)
        except Exception:
            pass
    return result


TOOL = {
    "name": "project_status",
    "description": (
        "Inspect the structure of the project folder currently selected in JARVIS. "
        "Use when asked what project is open, what technologies it uses, or for a "
        "quick project overview. Reads filenames, extensions, entry-point names and "
        "the Git branch only; never reads source code or sends the absolute folder path. "
        "Remembers a short metadata summary in local project memory."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}
