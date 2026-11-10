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


def get_running_communication_apps() -> set[str]:
    """Return known communication app processes currently running, locally only.

    Process enumeration is intentionally conservative: an open Discord/Teams
    client counts as a potential call even if its window is not foreground.
    This avoids reopening the mic just because the user switches to a game.
    """
    try:
        import psutil
        names = set()
        for process in psutil.process_iter(["name"]):
            try:
                name = process.info.get("name")
                normalized = normalize_process_name(name)
                if normalized in COMMUNICATION_APP_NAMES:
                    names.add(normalized)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return names
    except Exception:
        return set()


def should_forward_microphone(
    communication_app_active: bool,
    addressed_turn_open: bool,
    push_to_talk_held: bool = False,
) -> bool:
    """In a communication app, forward audio only for an addressed/held turn."""
    if not communication_app_active:
        return True
    return bool(addressed_turn_open or push_to_talk_held)
