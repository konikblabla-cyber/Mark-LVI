"""Local, conservative detection of foreground communication applications.

This is a process-name hint, not proof that a call is active. It is used only
for local microphone/privacy gating and is never sent to a model.
"""
from __future__ import annotations

import ntpath


# Windows executable basenames commonly used by desktop chat and calling apps.
# Browser-based calls are intentionally not inferred from chrome.exe/msedge.exe:
# process identity alone cannot reliably tell a Meet/Teams tab from other browsing.
COMMUNICATION_APP_NAMES = frozenset({
    "discord", "discordptb", "discordcanary",
    "teams", "ms-teams", "skype", "skypeapp",
    "zoom", "webex", "ciscocollabhost",
    "whatsapp", "telegram", "phoneexperiencehost", "yourphone",
    "slack", "signal", "viber", "line",
})


def normalize_process_name(value: object) -> str:
    """Return a safe, case-insensitive executable basename without .exe."""
    name = ntpath.basename(str(value or "").replace("/", "\\")).strip().casefold()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def is_communication_app(process_name: object) -> bool:
    """True for a known chat/calling app; unknown apps fail open for usability."""
    return normalize_process_name(process_name) in COMMUNICATION_APP_NAMES


def get_active_communication_apps() -> set[str] | None:
    """Return communication apps with active Windows audio sessions.

    An open Discord window is not proof of a call. WASAPI session state is a
    better signal: active playback/call sessions remain visible while the app
    is behind a game. Returns None when the Windows audio-session API is
    unavailable, allowing the caller to fall back to foreground-app detection.
    """
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return None

    try:
        names = set()
        for session in AudioUtilities.GetAllSessions():
            try:
                if int(session.State) != 1:  # AudioSessionStateActive
                    continue
                process = session.Process
                if process is None:
                    continue
                name = normalize_process_name(process.name())
                if name in COMMUNICATION_APP_NAMES:
                    names.add(name)
            except Exception:
                # An exited/protected process must not break the audio loop.
                continue
        return names
    except Exception:
        return None


def is_communication_context(
    foreground_app: object,
    active_audio_apps: set[str] | None,
) -> bool:
    """Treat a focused chat app or an active call audio session as private."""
    if is_communication_app(foreground_app):
        return True
    return bool(active_audio_apps and any(
        is_communication_app(name) for name in active_audio_apps
    ))


def should_forward_microphone(
    communication_app_active: bool,
    addressed_turn_open: bool,
    push_to_talk_held: bool = False,
) -> bool:
    """In a communication app, forward audio only for an addressed/held turn."""
    if not communication_app_active:
        return True
    return bool(addressed_turn_open or push_to_talk_held)
