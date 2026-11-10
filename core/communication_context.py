"""Local, conservative detection of foreground communication applications.

This is a process-name hint, not proof that a call is active. It is used only
for local microphone/privacy gating and is never sent to a model.
"""
from __future__ import annotations

import ntpath
import os
import sys


# Windows executable basenames commonly used by desktop chat and calling apps.
# Browser-based calls are intentionally not inferred from chrome.exe/msedge.exe:
# process identity alone cannot reliably tell a Meet/Teams tab from other browsing.


def initial_communication_gate(platform_name: object = None) -> bool:
    """Keep the Windows microphone local until the first WASAPI probe completes."""
    platform = sys.platform if platform_name is None else str(platform_name)
    return platform.casefold() == "win32"

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


def communication_apps_from_sessions(sessions) -> set[str]:
    """Extract known communication clients from active WASAPI session objects."""
    names = set()
    for session in sessions or ():
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


def _capture_sessions_from_devices(devices, session_type, session_interface):
    """Enumerate WASAPI sessions attached to active microphone endpoints."""
    sessions = []
    for device in devices or ():
        manager = device.AudioSessionManager
        enumerator = manager.GetSessionEnumerator()
        for index in range(enumerator.GetCount()):
            control = enumerator.GetSession(index)
            if control is None:
                continue
            control2 = control.QueryInterface(session_interface)
            if control2 is not None:
                sessions.append(session_type(control2))
    return sessions


def get_active_communication_apps() -> set[str] | None:
    """Return communication apps with active Windows audio sessions.

    An open Discord window is not proof of a call. Check both render sessions
    and sessions attached to active capture endpoints, so a call that is
    sending microphone audio is still seen even when remote playback is silent.
    Returns None when the Windows audio-session API is unavailable.
    """
    try:
        import comtypes
        from pycaw.api.audiopolicy import IAudioSessionControl2
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioUtilities
        from pycaw.utils import AudioSession
    except Exception:
        return None

    initialized = False
    try:
        # asyncio.to_thread uses worker threads; initialize COM in that thread
        # before asking WASAPI for its session enumerator.
        comtypes.CoInitialize()
        initialized = True
        # AudioUtilities.GetAllSessions() only walks the default render
        # endpoint. Enumerate capture endpoints separately: WASAPI exposes
        # microphone sessions through each endpoint's AudioSessionManager.
        capture_devices = AudioUtilities.GetAllDevices(
            data_flow=EDataFlow.eCapture.value,
            device_state=DEVICE_STATE.ACTIVE.value,
        )
        sessions = list(AudioUtilities.GetAllSessions() or ())
        sessions.extend(_capture_sessions_from_devices(
            capture_devices, AudioSession, IAudioSessionControl2
        ))
        return communication_apps_from_sessions(sessions)
    except Exception:
        return None
    finally:
        if initialized:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def is_communication_context(
    foreground_app: object,
    active_audio_apps: set[str] | None,
) -> bool:
    """Treat an active call session as private; fall back to focus if API fails."""
    if active_audio_apps is None:
        # On Windows the app-session API is required to detect a call behind
        # a game. If it is unavailable, fail closed instead of forwarding
        # microphone audio merely because a non-communication window is focused.
        if os.name == "nt":
            return True
        # Other platforms do not use this Windows-only WASAPI sensor.
        return is_communication_app(foreground_app)
    return any(is_communication_app(name) for name in active_audio_apps)


def should_auto_wake(
    idle_seconds: object,
    communication_active: bool,
    max_idle_seconds: float = 15.0,
) -> bool:
    """Wake an armed assistant on fresh local input, never during a call."""
    if communication_active:
        return False
    try:
        idle = float(idle_seconds)
    except (TypeError, ValueError):
        return False
    return 0.0 <= idle <= max(0.0, float(max_idle_seconds))


def should_forward_microphone(
    communication_app_active: bool,
    addressed_turn_open: bool,
    push_to_talk_held: bool = False,
) -> bool:
    """In a communication app, forward audio only for an addressed/held turn."""
    if not communication_app_active:
        return True
    return bool(addressed_turn_open or push_to_talk_held)
