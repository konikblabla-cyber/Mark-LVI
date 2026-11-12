"""Presentation helpers for user-facing tool results."""
from __future__ import annotations

from core import outcome


def web_search_panel_payload(tool_name: str, result, arguments=None):
    """Return a clean HUD payload for a successful web search, otherwise None.

    Keep dispatcher output as the single source of truth. In particular, do not
    read a side variable left over from another tool-dispatch branch.
    """
    if str(tool_name or "") != "web_search":
        return None

    content = str(result or "").strip()
    if not content or content.startswith(("No results", "No news found", "Search failed")):
        return None
    if outcome.is_problem(content):
        return None

    args = arguments if isinstance(arguments, dict) else {}
    mode = str(args.get("mode") or "search").strip().upper() or "SEARCH"
    query = args.get("query")
    if not isinstance(query, str):
        query = ""
    query = query.strip()
    if not query:
        items = args.get("items")
        if isinstance(items, (list, tuple)):
            query = ", ".join(str(item).strip() for item in items if str(item).strip())
    label = f"{mode} — {query[:38]}" if query else mode
    return label, outcome.unwrap(content)
