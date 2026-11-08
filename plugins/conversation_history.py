"""Search the user's opt-in local conversation history."""
PLUGIN = {
    "name": "conversation_history",
    "description": (
        "Search, recall or explicitly clear previous conversations stored locally on this computer. "
        "Use for requests like 'what did we discuss yesterday?', 'find the plan "
        "we made last week', 'show our recent conversation', or 'clear my local history'. "
        "History is saved only when the user enabled it in Plugin Settings; clearing it requires the HUD confirmation button."
    ),
    "tier": "core",
    "brief": "Search recent or past conversation transcripts saved locally (opt-in).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "recent, search, stats or clear"},
            "query": {"type": "STRING", "description": "Search words or phrase"},
            "limit": {"type": "INTEGER", "description": "Number of messages (max 30)"},
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "conversation_history",
    "title": "Local conversation history",
    "fields": [
        {"key": "enabled", "label": "Save text transcripts on this computer", "type": "toggle", "default": False},
        {"key": "retention_days", "label": "Retention period (days)", "type": "choice",
         "options": ["7", "30", "90", "180", "365"], "default": "30"},
    ],
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    from memory import conversation_history as history

    cfg = history.settings()
    if not cfg["enabled"]:
        return "Conversation history is off. Enable local history in Settings → Plugin Settings."
    action = str(parameters.get("action") or "recent").strip().lower()
    try:
        if action == "stats":
            stats = history.history_stats()
            return f"Local history is enabled: {stats['entries']} messages stored, {stats['file_bytes']:,} bytes."
        if action == "clear":
            from core import confirm
            if confirm.pending_title():
                return "A confirmation is already waiting on screen. Resolve it before requesting another."
            return confirm.request(
                "clear_conversation_history",
                "Clear local conversation history",
                "Permanently delete the locally saved text transcript archive. This does not delete long-term memories or session summaries.",
                lambda: ("Conversation history cleared." if history.clear_history()
                         else "The conversation history could not be cleared."),
            )
        if action == "recent":
            rows = history.recent_messages(parameters.get("limit", 10))
            heading = "Recent conversation"
        elif action == "search":
            query = str(parameters.get("query") or "").strip()
            if not query:
                return "Tell me a keyword or phrase to search for."
            rows = history.search_messages(query, parameters.get("limit", 12))
            heading = f"History search: {query}"
        else:
            return "Choose recent, search, stats or clear."

        if not rows:
            return "No matching saved conversation messages were found."
        body = "\n\n".join(
            f"[{row['ts']}] {'You' if row['role'] == 'user' else 'JARVIS'}: {row['text']}"
            for row in rows
        )
        if player:
            try:
                player.show_content(heading, body)
                return f"Found {len(rows)} saved messages. I put the transcript on screen."
            except Exception:
                pass
        return body[:3500]
    except Exception as exc:
        return f"Conversation history failed: {type(exc).__name__}: {str(exc)[:120]}"
