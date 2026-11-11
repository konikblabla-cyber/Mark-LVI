"""Current weather reports grounded in Gemini's Google Search tool.

Uses the configured Gemini API key only. The assistant returns the forecast in
its conversation; it does not merely open a search page and claim the job is done.
"""
from __future__ import annotations


def weather_action(parameters: dict, player=None, session_memory=None) -> str:
    city = parameters.get("city")
    when = parameters.get("time", "today")

    if not isinstance(city, str) or not city.strip():
        return "I need a city to check the weather."

    city = city.strip()[:120]
    when = str(when or "today").strip()[:80] or "today"
    query = (
        f"Find the current weather and forecast for {city} for {when}. "
        "Use current, location-specific weather sources. Give the local date and "
        "time zone, current temperature in Celsius, feels-like temperature, "
        "weather conditions, wind, precipitation chance, and the relevant "
        "forecast for the requested period. Clearly distinguish observed current "
        "conditions from forecast values. If a value is unavailable, say so. "
        "Do not invent measurements. Keep the report concise and practical."
    )

    try:
        from actions.web_search import _gemini_search
        report = _gemini_search(query).strip()
        if not report:
            raise RuntimeError("Gemini returned an empty weather report.")
        source = "Gemini Google Search grounding"
    except Exception as gemini_error:
        # Keep the feature useful when grounding is rate-limited, but do not
        # fabricate a forecast from snippets or pretend it was verified.
        try:
            from actions.web_search import _ddg_search, _format_ddg
            fallback_query = f"weather forecast {city} {when}"
            results = _ddg_search(fallback_query, max_results=5)
            fallback = _format_ddg(fallback_query, results)
            if not results:
                raise RuntimeError("no search results")
            report = (
                "I could not verify a complete live forecast through Gemini. "
                "Here are the available search results; treat them as snippets, "
                "not as a confirmed weather reading.\n\n" + fallback
            )
            source = "search-result fallback"
        except Exception as fallback_error:
            return (
                "I could not retrieve a reliable weather report right now. "
                f"Gemini search: {type(gemini_error).__name__}; "
                f"fallback: {type(fallback_error).__name__}."
            )

    if player is not None:
        try:
            player.show_content(f"WEATHER — {city} — {when}", report)
        except Exception:
            pass
        try:
            player.write_log(f"SYS: Weather report ready for {city}.")
        except Exception:
            pass

    if session_memory is not None:
        try:
            session_memory.set_last_search(
                query=f"weather {city} {when}", response=report[:12000]
            )
        except Exception:
            pass

    return f"Weather report for {city} ({when}), from {source}:\n{report}"


TOOL = {
    "name": "weather_report",
    "description": (
        "Gets an actual, current weather report and forecast using Gemini "
        "grounded search. Use for current conditions or forecasts; do not just "
        "open a search page. Pass city and an optional date/time period."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {"type": "STRING", "description": "City or precise location"},
            "time": {"type": "STRING", "description": "today, tomorrow, this weekend, or a date; defaults to today"},
        },
        "required": ["city"],
    },
    "handler": weather_action,
}
