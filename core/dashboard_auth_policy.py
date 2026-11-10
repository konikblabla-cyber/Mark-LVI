"""Pure authentication policy helpers for the local dashboard."""
from __future__ import annotations

from urllib.parse import urlsplit

SESSION_COOKIE_NAME = "jarvis_session"
SESSION_COOKIE_TTL_SECONDS = 12 * 60 * 60


def session_cookie_options(https_enabled: bool, ttl_seconds: int = SESSION_COOKIE_TTL_SECONDS) -> dict:
    """HttpOnly, same-site session cookie suitable for same-origin HTTP/WebSockets."""
    try:
        max_age = max(1, min(int(ttl_seconds), SESSION_COOKIE_TTL_SECONDS))
    except (TypeError, ValueError):
        max_age = SESSION_COOKIE_TTL_SECONDS
    return {
        "max_age": max_age,
        "httponly": True,
        "secure": bool(https_enabled),
        "samesite": "strict",
        "path": "/",
    }


def websocket_origin_matches(origin: str, host: str) -> bool:
    """Accept browser WebSocket handshakes only from this exact host origin."""
    origin = str(origin or "").strip()
    host = str(host or "").strip()
    if not origin or not host or origin.lower() == "null":
        return False
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.netloc.casefold() == host.casefold()
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False
