"""User-defined JARVIS routines combining verified app launch and safe smart-home actions."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

_MAX_ROUTINES = 30
_MAX_ITEMS = 8
_NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")
_APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,79}$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_ALLOWED_DEVICE_KEYS = {"device", "state", "brightness", "percentage", "temperature"}


def _path() -> Path:
    return Path.home() / ".jarvis" / "routines.json"


def _valid_app_name(value: object) -> bool:
    return isinstance(value, str) and bool(_APP_RE.fullmatch(value.strip()))


def _read() -> dict:
    path = _path()
    if not path.exists():
        return {"version": 1, "routines": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Routine storage is unreadable; refusing to overwrite it.") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("routines"), dict)):
        raise RuntimeError("Routine storage has an invalid structure.")
    if len(data["routines"]) > _MAX_ROUTINES:
        raise RuntimeError("Routine storage exceeds the supported limit.")

    for key, item in data["routines"].items():
        if (not isinstance(key, str) or not _NAME_RE.fullmatch(key)
                or key != key.casefold() or not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or item["name"].casefold() != key):
            raise RuntimeError("Routine storage contains an invalid routine identity.")
        apps = item.get("applications", [])
        devices = item.get("home_actions", [])
        if (not isinstance(apps, list) or len(apps) > _MAX_ITEMS
                or not all(_valid_app_name(app) for app in apps)):
            raise RuntimeError("Routine storage contains an unsafe application entry.")
        if (not isinstance(devices, list) or len(devices) > _MAX_ITEMS
                or not all(_validate_device_action(entry) is not None for entry in devices)):
            raise RuntimeError("Routine storage contains an unsafe smart-home entry.")
    return data


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".routines-", suffix=".tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _validate_device_action(raw: object) -> dict | None:
    if not isinstance(raw, dict) or set(raw) - _ALLOWED_DEVICE_KEYS:
        return None
    alias = str(raw.get("device") or "").strip().lower()
    if not _ALIAS_RE.fullmatch(alias):
        return None
    item = {"device": alias}

    temperature = raw.get("temperature")
    state = raw.get("state")
    brightness = raw.get("brightness")
    percentage = raw.get("percentage")

    if temperature is not None:
        if (isinstance(temperature, bool)
                or not isinstance(temperature, (int, float))
                or not 5 <= float(temperature) <= 35
                or state is not None or brightness is not None or percentage is not None):
            return None
        item["temperature"] = float(temperature)
        return item

    if str(state or "").strip().lower() not in {"on", "off"}:
        return None
    item["state"] = str(state).strip().lower()

    if brightness is not None:
        if (item["state"] != "on" or isinstance(brightness, bool)
                or not isinstance(brightness, int) or not 1 <= brightness <= 255
                or percentage is not None):
            return None
        item["brightness"] = brightness

    if percentage is not None:
        if (item["state"] != "on" or isinstance(percentage, bool)
                or not isinstance(percentage, int) or not 1 <= percentage <= 100
                or brightness is not None):
            return None
        item["percentage"] = percentage
    return item


def run(parameters: dict, player=None, session_memory=None) -> str:
    """List, save, activate or delete a named multi-device routine."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "list").strip().lower()
    try:
        data = _read()
    except Exception as exc:
        return f"Could not read routine storage: {type(exc).__name__}."

    routines = data["routines"]
    if action == "list":
        if not routines:
            return "No JARVIS routines are saved."
        rows = []
        for name, item in sorted(routines.items(), key=lambda row: row[0].casefold())[:_MAX_ROUTINES]:
            apps = item.get("applications", []) if isinstance(item, dict) else []
            devices = item.get("home_actions", []) if isinstance(item, dict) else []
            display_name = str(item.get("name") or name)[:40]
            rows.append(f"{display_name}: {len(apps)} app(s), {len(devices)} smart-home action(s)")
        return "Saved routines:\n" + "\n".join(rows)

    name = str(params.get("routine_name") or "").strip()
    if not _NAME_RE.fullmatch(name):
        return "Use a routine name of 1–40 letters, numbers, spaces, dots, underscores or hyphens."
    key = name.casefold()

    if action == "save":
        apps = params.get("applications") or []
        devices = params.get("home_actions") or []
        if not isinstance(apps, list) or not isinstance(devices, list):
            return "Applications and home_actions must both be lists."
        if len(apps) > _MAX_ITEMS or len(devices) > _MAX_ITEMS:
            return f"A routine can contain at most {_MAX_ITEMS} applications and {_MAX_ITEMS} smart-home actions."
        cleaned_apps = []
        for app in apps:
            value = str(app or "").strip()
            if not isinstance(app, str) or not _APP_RE.fullmatch(value):
                return "Use plain application names only, not shell commands or arguments."
            if value.casefold() not in {entry.casefold() for entry in cleaned_apps}:
                cleaned_apps.append(value)
        cleaned_devices = []
        for entry in devices:
            cleaned = _validate_device_action(entry)
            if cleaned is None:
                return (
                    "A smart-home action is invalid. Use an alias with state on/off, "
                    "brightness 1–255, fan percentage 1–100 or climate temperature 5–35°C."
                )
            cleaned_devices.append(cleaned)
        if not cleaned_apps and not cleaned_devices:
            return "A routine needs at least one application or one smart-home action."
        if key not in routines and len(routines) >= _MAX_ROUTINES:
            return f"Routine limit reached ({_MAX_ROUTINES}). Delete one before adding another."
        routines[key] = {
            "name": name,
            "applications": cleaned_apps,
            "home_actions": cleaned_devices,
        }
        try:
            _write(data)
        except Exception as exc:
            return f"Could not save routine: {type(exc).__name__}."
        return (
            f"Saved routine '{name}' with {len(cleaned_apps)} app(s) and "
            f"{len(cleaned_devices)} smart-home action(s)."
        )

    if action == "delete":
        if key not in routines:
            return f"Routine '{name}' was not found."
        del routines[key]
        try:
            _write(data)
        except Exception as exc:
            return f"Could not delete routine: {type(exc).__name__}."
        return f"Deleted routine '{name}'."

    if action != "activate":
        return "Choose routine action: list, save, activate or delete."

    routine = routines.get(key)
    if not isinstance(routine, dict):
        return f"Routine '{name}' was not found. List saved routines first."

    results = []
    for app in routine.get("applications", [])[:_MAX_ITEMS]:
        try:
            from actions.open_app import open_app
            result = open_app({"app_name": app}, player=player)
        except Exception as exc:
            result = f"launch request failed ({type(exc).__name__})"
        results.append(f"{app}: {result}")

    home_actions = routine.get("home_actions", [])[:_MAX_ITEMS]
    if home_actions:
        try:
            from memory.config_manager import get_plugin_enabled
            from plugins.smart_home import _settings
            if not get_plugin_enabled("smart_home"):
                results.append("Smart-home actions skipped: the Home Assistant plugin is disabled.")
            else:
                _, _, allowed = _settings()
                from plugins.smart_home import run as smart_home_run
                for entry in home_actions:
                    if not isinstance(entry, dict) or entry.get("device") not in allowed:
                        results.append(
                            f"{str(entry.get('device', 'unknown'))[:40]}: alias is not currently allowed in Home Assistant settings."
                        )
                        continue
                    call = {"action": "set", **entry}
                    results.append(f"{entry['device']}: {smart_home_run(call, player=player)}")
        except Exception as exc:
            results.append(f"Smart-home actions skipped: {type(exc).__name__}.")

    if not results:
        return f"Routine '{routine.get('name', name)}' has no actions."
    return (
        f"Routine '{routine.get('name', name)}' executed. App launches and smart-home commands "
        "are reported individually; check each result below:\n" + "\n".join(results)
    )


TOOL = {
    "name": "routine_mode",
    "description": (
        "Create and activate named JARVIS routines combining application launches and "
        "explicitly allowlisted Home Assistant devices. Save with action='save', "
        "routine_name, applications and/or home_actions. List, activate or delete by name. "
        "App launches use the existing verified launcher. Smart-home actions use only the "
        "configured aliases from the enabled smart_home plugin and verify actual state. "
        "Use for routines such as work mode, study mode, gaming or cinema. Never invent "
        "device aliases or claim a launch succeeded without its tool result."
    ),
    "tier": "extra",
    "brief": "Save or activate routines combining verified app launches with allowlisted smart-home actions.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list, save, activate or delete"},
            "routine_name": {"type": "STRING", "description": "Routine name"},
            "applications": {"type": "ARRAY", "items": {"type": "STRING"}},
            "home_actions": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "device": {"type": "STRING"},
                        "state": {"type": "STRING"},
                        "brightness": {"type": "INTEGER"},
                        "percentage": {"type": "INTEGER"},
                        "temperature": {"type": "NUMBER"},
                    },
                    "required": ["device"],
                },
            },
        },
        "required": ["action"],
    },
    "handler": run,
}
