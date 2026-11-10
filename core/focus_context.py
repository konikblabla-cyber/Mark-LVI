"""Privacy-limited foreground application context for JARVIS.

Only executable basenames are classified. Window titles, document names,
keystrokes, screenshots and clipboard contents are deliberately excluded.
"""
from __future__ import annotations

import ntpath
from dataclasses import dataclass
from typing import Optional


_GROUPS = {
    "development": {
        "code", "devenv", "pycharm64", "idea64", "cursor", "sublime_text",
        "notepad++", "windsurf", "rider64", "webstorm64",
    },
    "browser": {
        "chrome", "msedge", "firefox", "opera", "brave", "vivaldi",
        "arc", "browser",
    },
    "communication": {
        "discord", "discordcanary", "teams", "ms-teams", "zoom",
        "skype", "slack", "whatsapp", "telegram", "signal", "webex",
    },
    "gaming": {
        "steam", "steamwebhelper", "epicgameslauncher", "riotclientservices",
        "valorant", "cs2", "overwatch", "minecraft", "robloxplayerbeta",
    },
    "creative": {
        "blender", "photoshop", "illustrator", "premiere", "afterfx",
        "obs64", "capcut", "davinciresolve",
    },
    "office": {
        "winword", "excel", "powerpnt", "onenote", "soffice", "acrobat",
    },
    "media": {"spotify", "vlc", "wmplayer", "music"},
    "files": {"explorer", "fileexplorer"},
    "system": {"taskmgr", "mmc", "control", "systemsettings"},
}

_LABELS = {
    "development": "programming",
    "browser": "web browsing",
    "communication": "communication",
    "gaming": "gaming",
    "creative": "creative work",
    "office": "office work",
    "media": "media playback",
    "files": "file management",
    "system": "Windows settings or system tools",
    "other": "other activity",
}


def normalise_executable(value: object) -> str:
    """Reduce a process path/name to a safe lowercase executable basename."""
    name = ntpath.basename(str(value or "").replace("/", "\\")).strip().casefold()
    return name[:-4] if name.endswith(".exe") else name


def classify_application(value: object) -> str:
    """Classify a known foreground executable without looking inside its window."""
    name = normalise_executable(value)
    if not name:
        return "unknown"
    for group, names in _GROUPS.items():
        if name in names:
            return group
    # Some Windows app versions append a release/channel suffix.
    if name.startswith("code-") or name.startswith("pycharm"):
        return "development"
    if name.startswith("discord"):
        return "communication"
    return "other"


def describe_focus_context(value: object) -> dict:
    """Return model-safe context: executable basename and broad activity only."""
    name = normalise_executable(value)
    category = classify_application(name)
    return {
        "application": name[:80],
        "category": category,
        "activity": _LABELS.get(category, "unknown activity"),
    }


@dataclass
class FocusContextTracker:
    """Debounce foreground changes so fast Alt-Tab sequences are not treated as intent."""

    stable_seconds: float = 8.0
    current: Optional[dict] = None
    _candidate: str = ""
    _candidate_since: float = 0.0

    def observe(self, process_name: object, now: float) -> Optional[dict]:
        name = normalise_executable(process_name)
        if not name:
            return None
        try:
            timestamp = float(now)
        except (TypeError, ValueError, OverflowError):
            return None
        if name != self._candidate:
            self._candidate = name
            self._candidate_since = timestamp
            return None
        if timestamp < self._candidate_since:
            self._candidate_since = timestamp
            return None
        if timestamp - self._candidate_since < max(0.0, float(self.stable_seconds)):
            return None
        snapshot = describe_focus_context(name)
        if self.current == snapshot:
            return None
        self.current = snapshot
        return dict(snapshot)
