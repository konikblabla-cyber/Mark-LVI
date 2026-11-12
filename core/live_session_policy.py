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

def should_use_local_wake_mode(local_wake_preference: bool,
                               proactive_audio_enabled: bool) -> bool:
    """Use local wake gating only when explicitly selected instead of semantic addressing."""
    return bool(local_wake_preference and not proactive_audio_enabled)


def should_halt_tool_batch(result) -> bool:
    """Stop precomputed sibling tool calls after a gate or uncertain outcome.

    Gemini can return several function calls in one response. Once one call
    asks for human approval, hits a blocker/recovery boundary, or cannot verify
    its effect, later calls from that same already-generated batch must not run.
    They are returned to the model as not executed so it can reconsider them
    after the user resolves the first result.
    """
    text = str(result or "").strip()
    if not text:
        return False
    if any(marker in text for marker in (
        "[CONFIRMATION_PENDING]",
        "[CONFIRMATION_BUSY]",
        "[MISSION_BLOCKED]",
        "[MISSION_RECOVERY]",
        "[MISSION_UI_RECOVERY]",
    )):
        return True
    status = text.split(None, 1)[0]
    return status in {"status=failed", "status=unverified", "status=ask"}
