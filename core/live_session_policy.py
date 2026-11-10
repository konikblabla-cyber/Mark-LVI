"""Small, SDK-independent helpers for long-lived Gemini Live sessions."""


def go_away_notice(response):
    """Return the server GoAway notice from an SDK response or mapping."""
    if isinstance(response, dict):
        return response.get("go_away", response.get("goAway"))
    return getattr(response, "go_away", None)


def go_away_time_left(response):
    """Return the optional time-left value without assuming an SDK scalar type."""
    notice = go_away_notice(response)
    return getattr(notice, "time_left", None) if notice is not None else None

