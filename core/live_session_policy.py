"""Small, SDK-independent helpers for long-lived Gemini Live sessions."""


def go_away_notice(response):
    """Return the server GoAway notice from an SDK response or mapping."""
    if isinstance(response, dict):
        return response.get("go_away", response.get("goAway"))
    return getattr(response, "go_away", None)


def live_turn_is_active(input_pending=False, output_pending=False,
                      response_payload=False, audio_pending=False,
                      assistant_speaking=False, tool_call_pending=False) -> bool:
    """True when reconnecting now could cut off a live request or response."""
    return any((
        input_pending, output_pending, response_payload, audio_pending,
        assistant_speaking, tool_call_pending,
    ))

def go_away_time_left(response):
    """Return the optional time-left value without assuming an SDK scalar type."""
    notice = go_away_notice(response)
    return getattr(notice, "time_left", None) if notice is not None else None


def should_sleep_after_addressed_response(wake_word_enabled: bool,
                                          addressed_turn_pending: bool,
                                          turn_complete: bool,
                                          tool_call_pending: bool = False) -> bool:
    """Close the cloud mic gate after a final addressed turn, even without text transcription."""
    return bool(
        wake_word_enabled and addressed_turn_pending
        and turn_complete and not tool_call_pending
    )

