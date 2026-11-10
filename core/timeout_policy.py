"""Bounds for untrusted execution timeout parameters."""
from __future__ import annotations

import math

DEFAULT_TIMEOUT_SECONDS = 30
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300


def bounded_timeout(value, default: int = DEFAULT_TIMEOUT_SECONDS) -> int:
    """Convert a user/model value to a safe integer timeout in 1..300 seconds."""
    fallback = min(MAX_TIMEOUT_SECONDS, max(MIN_TIMEOUT_SECONDS, int(default)))
    if isinstance(value, bool):
        return fallback
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return fallback
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return min(MAX_TIMEOUT_SECONDS, max(MIN_TIMEOUT_SECONDS, parsed))
