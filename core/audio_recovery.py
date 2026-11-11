"""Helpers for detecting stopped or stalled audio streams.

PortAudio may leave a stream object alive after a USB endpoint disappears.
The UI can then look connected while no microphone frames or speaker audio
are moving. These helpers keep the health decision deterministic and testable.
"""
from __future__ import annotations

import asyncio
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


# Live audio is time-sensitive: a 16-frame backlog is at most about half a
# second when the PC microphone uses 32 ms blocks. Older audio is less useful
# than the current microphone state once a sender falls behind.
LIVE_AUDIO_QUEUE_SIZE = 16


def offer_latest_audio(queue: asyncio.Queue, item) -> bool:
    """Put an audio frame without allowing stale backlog to build up.

    If the bounded queue is full, discard the oldest frame and keep the newest.
    This deliberately favours conversation latency over replaying seconds-old
    audio after a network or scheduler stall. Must be called on the queue's
    owning asyncio loop.
    """
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        pass

    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        pass

    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        return False
