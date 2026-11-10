"""On-demand, privacy-limited Windows desktop context."""
from __future__ import annotations

from core.desktop_context import get_desktop_context, desktop_is_idle
from core.focus_context import describe_focus_context


def describe_current_desktop() -> dict:
    """Return the foreground executable/category and safe Windows activity signals."""
    context = get_desktop_context(include_app=True)
    if not isinstance(context, dict) or not context.get("available"):
        return {"available": False, "reason": "Windows desktop sensing is unavailable."}

    app = str(context.get("foreground_app") or "").strip()
    focus = describe_focus_context(app) if app else {
        "application": "", "category": "unknown", "activity": "unknown activity",
    }
    idle = context.get("idle_seconds")
    try:
        idle_seconds = round(max(0.0, float(idle)), 1)
    except (TypeError, ValueError, OverflowError):
        idle_seconds = None

    return {
        "available": True,
        "application": focus["application"],
        "category": focus["category"],
        "activity": focus["activity"],
        "idle_seconds": idle_seconds,
        "fullscreen": bool(context.get("fullscreen")),
        "fullscreen_known": bool(context.get("fullscreen_known")),
        "desktop_idle": desktop_is_idle(context),
        "privacy": "Only process name and activity metadata were read; no title, screenshot, text or clipboard.",
    }


def format_desktop_context(snapshot: object) -> str:
    if not isinstance(snapshot, dict) or not snapshot.get("available"):
        reason = snapshot.get("reason") if isinstance(snapshot, dict) else ""
        return str(reason or "Desktop context is unavailable.")
    application = str(snapshot.get("application") or "Unknown application")
    activity = str(snapshot.get("activity") or "Unknown activity")
    idle = snapshot.get("idle_seconds")
    idle_text = f"{idle} seconds" if isinstance(idle, (int, float)) else "unknown"
    fullscreen = "yes" if snapshot.get("fullscreen") else "no"
    return (
        f"Foreground application: {application}. Activity category: {activity}. "
        f"Keyboard/mouse idle: {idle_text}. Fullscreen: {fullscreen}. "
        f"{snapshot.get('privacy', '')}"
    )


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        return format_desktop_context(describe_current_desktop())
    except Exception as exc:
        return f"Could not inspect desktop context: {type(exc).__name__}."


TOOL = {
    "name": "desktop_awareness",
    "description": (
        "On explicit request, report which application is in the Windows foreground "
        "and its broad activity category (programming, browser, communication, gaming, "
        "creative, office, media, files or system). Reads only executable name and local "
        "idle/fullscreen metadata. Never captures window titles, documents, screenshots, "
        "keystrokes or clipboard. Use when asked what the user currently has open or is doing."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}
