"""Windows global media transport controls without a separate music API.

These send standard multimedia keys to the active Windows media session. They
work with players that register Windows media-key handling (for example Spotify,
a browser media tab, and supported desktop players); no Spotify credentials or
provider account are stored by JARVIS.
"""
from __future__ import annotations

import ctypes
import os
from typing import Callable

_MEDIA_KEYS = {
    "play_pause": 0xB3,  # VK_MEDIA_PLAY_PAUSE
    "play": 0xB3,        # Windows exposes a combined play/pause key
    "pause": 0xB3,
    "next": 0xB0,        # VK_MEDIA_NEXT_TRACK
    "previous": 0xB1,    # VK_MEDIA_PREV_TRACK
    "stop": 0xB2,        # VK_MEDIA_STOP
}
_ACTION_LABELS = {
    "play_pause": "play/pause",
    "play": "play/pause",
    "pause": "play/pause",
    "next": "next track",
    "previous": "previous track",
    "stop": "stop",
}


def _windows_media_key(vk: int) -> None:
    """Press and release one global Windows multimedia virtual key."""
    if os.name != "nt":
        raise OSError("Windows media keys are available only on Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = (
        ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_size_t
    )
    user32.keybd_event.restype = None
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 0x0002, 0)  # KEYEVENTF_KEYUP


def perform_media_action(
    action: str,
    *,
    platform_name: str | None = None,
    send_key: Callable[[int], None] | None = None,
) -> str:
    """Perform a tested media-key mapping; send_key is injectable for offline tests."""
    name = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    if name not in _MEDIA_KEYS:
        return "Unsupported media action. Use play_pause, next, previous, or stop."
    platform = platform_name or ("Windows" if os.name == "nt" else os.name)
    if platform.casefold() not in {"windows", "win32", "nt"} and send_key is None:
        return "Media transport controls are currently supported on Windows only."
    sender = send_key or _windows_media_key
    try:
        sender(_MEDIA_KEYS[name])
    except Exception as exc:
        return f"Windows could not send the media command: {type(exc).__name__}."
    return f"Sent {_ACTION_LABELS[name]} to the active media player."


def media_action(parameters: dict, player=None, session_memory=None) -> str:
    result = perform_media_action(parameters.get("action", ""))
    if player is not None:
        try:
            player.write_log("SYS: " + result)
        except Exception:
            pass
    return result


TOOL = {
    "name": "media_control",
    "description": (
        "Control playback in the active Windows media session, such as Spotify "
        "or a browser tab, without a separate music-service API key. Actions: "
        "play_pause, next, previous, stop. Use only when the user asks to "
        "control music or media playback."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["play_pause", "next", "previous", "stop"],
                "description": "Media transport action",
            },
        },
        "required": ["action"],
    },
    "handler": media_action,
}
