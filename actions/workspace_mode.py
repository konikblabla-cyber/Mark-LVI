"""User-defined Windows workspace profiles for JARVIS.

Profiles contain application names only. Launching is delegated to the existing
verified open_app action; this module never runs shell commands or closes apps.
"""
from __future__ import annotations

import json
import os
import platform
import re
import tempfile
from pathlib import Path

_MAX_PROFILES = 30
_MAX_APPS = 8
_NAME = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")
_FORBIDDEN = re.compile(r"[;&|<>\n\r\x00]")


def _store_path() -> Path:
    return Path.home() / ".jarvis" / "workspace_profiles.json"


def _read_profiles() -> dict:
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {}
    for name, apps in data.items():
        if (isinstance(name, str) and _NAME.fullmatch(name)
                and isinstance(apps, list)):
            safe_apps = [
                item.strip() for item in apps
                if isinstance(item, str) and _valid_app_name(item)
            ]
            clean[name.casefold()] = {"name": name, "applications": safe_apps[:_MAX_APPS]}
    return clean


def _valid_app_name(value: str) -> bool:
    value = str(value or "").strip()
    return bool(value and len(value) <= 100 and not _FORBIDDEN.search(value))


def _write_profiles(profiles: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        item["name"]: item["applications"][:_MAX_APPS]
        for item in profiles.values()
    }
    fd, temp_name = tempfile.mkstemp(prefix=".workspace-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def run(parameters: dict, player=None, session_memory=None) -> str:
    """List, save, activate or delete a user-defined application workspace."""
    if platform.system() != "Windows":
        return "Workspace profiles are currently supported on Windows only."

    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "list").strip().casefold()
    profiles = _read_profiles()

    if action == "list":
        if not profiles:
            return "No workspace profiles saved. Save one with a name and up to eight application names."
        return "Saved workspaces: " + "; ".join(
            f"{item['name']} ({', '.join(item['applications'])})"
            for item in sorted(profiles.values(), key=lambda value: value["name"].casefold())
        )

    name = str(params.get("profile_name") or "").strip()
    if not _NAME.fullmatch(name):
        return "Use a profile name of 1–40 letters, numbers, spaces, dots, underscores or hyphens."

    key = name.casefold()
    if action == "save":
        apps = params.get("applications")
        if not isinstance(apps, list) or not apps:
            return "Provide at least one application name to save this workspace."
        if len(apps) > _MAX_APPS:
            return f"A workspace can contain at most {_MAX_APPS} applications."
        cleaned = []
        for app in apps:
            if not isinstance(app, str) or not _valid_app_name(app):
                return "An application name is invalid. Pass plain app names, not commands or arguments."
            value = app.strip()
            if value.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(value)
        if key not in profiles and len(profiles) >= _MAX_PROFILES:
            return f"Workspace limit reached ({_MAX_PROFILES}). Delete a profile first."
        profiles[key] = {"name": name, "applications": cleaned}
        try:
            _write_profiles(profiles)
        except OSError as exc:
            return f"Could not save workspace: {type(exc).__name__}."
        return f"Saved workspace '{name}' with {len(cleaned)} applications."

    if action == "delete":
        if key not in profiles:
            return f"Workspace '{name}' was not found."
        del profiles[key]
        try:
            _write_profiles(profiles)
        except OSError as exc:
            return f"Could not update workspace storage: {type(exc).__name__}."
        return f"Deleted workspace '{name}'."

    if action == "activate":
        profile = profiles.get(key)
        if not profile:
            return f"Workspace '{name}' was not found. List saved workspaces first."
        from actions.open_app import open_app
        results = []
        for app in profile["applications"]:
            try:
                result = open_app({"app_name": app}, player=player)
                results.append(f"{app}: {result}")
            except Exception as exc:
                results.append(f"{app}: launch request failed ({type(exc).__name__})")
        return (
            f"Workspace '{profile['name']}' launch requests submitted. "
            "These are requests, not proof that every window opened: "
            + "; ".join(results)
        )

    return "Choose workspace action: list, save, activate or delete."


TOOL = {
    "name": "workspace_mode",
    "description": (
        "Manage saved Windows workspace profiles. Save up to eight application "
        "names in a named profile, list or delete profiles, and activate a "
        "profile by requesting each app through the verified open_app action. "
        "Use for requests such as 'prepare my work setup', 'start my gaming "
        "workspace' or 'save this as my study setup'. Never claim apps opened "
        "until their separate launch verification arrives. This action does "
        "not close apps, change Windows settings or run shell commands."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["list", "save", "activate", "delete"]},
            "profile_name": {"type": "STRING"},
            "applications": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Plain application names; required for save, up to eight.",
            },
        },
        "required": ["action"],
    },
    "handler": run,
}
