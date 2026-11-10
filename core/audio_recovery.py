"""Helpers for detecting stopped or stalled audio streams.

PortAudio may leave a stream object alive after a USB endpoint disappears.
The UI can then look connected while no microphone frames or speaker audio
are moving. These helpers keep the health decision deterministic and testable.
"""
from __future__ import annotations

import math


def stream_is_active(stream: object) -> bool:
    """Return False when a stream is missing, stopped, or cannot report state."""
    if stream is None:
        return False
    try:
        return bool(stream.active)
    except Exception:
        return False


def callback_is_stalled(
    last_callback_at: object,
    now: object,
    stall_after: float = 5.0,
) -> bool:
    """Detect a missing input callback heartbeat without trusting bad clocks."""
    try:
        last = float(last_callback_at)
        current = float(now)
        limit = float(stall_after)
    except (TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(value) for value in (last, current, limit)):
        return False
    if limit <= 0 or current < last:
        return False
    return current - last > limit
