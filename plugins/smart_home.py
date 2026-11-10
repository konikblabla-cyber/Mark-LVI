"""Constrained Home Assistant light control over its authenticated REST API.

Only explicitly mapped light entities can be controlled. No lock, cover,
alarm, camera or arbitrary service endpoint is exposed to the model.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import requests

from memory.config_manager import get_plugin_config

_ENTITY_RE = re.compile(r"^light\.[a-z0-9_]+$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MAX_LIGHTS = 40


PLUGIN_SETTINGS = {
    "namespace": "smart_home",
    "title": "Home Assistant — lights only",
    "fields": [
        {
            "key": "base_url",
            "label": "Home Assistant HTTPS address",
            "type": "text",
            "default": "https://homeassistant.local:8123",
            "placeholder": "https://home.example.com",
        },
        {
            "key": "access_token",
            "label": "Long-lived access token",
            "type": "password",
            "default": "",
            "placeholder": "Stored in the local JARVIS settings file",
        },
        {
            "key": "lights_json",
            "label": "Allowed light aliases (JSON)",
            "type": "text",
            "default": "{}",
            "placeholder": '{"desk":"light.desk","ceiling":"light.living_room"}',
        },
    ],
}


PLUGIN = {
    "name": "smart_home",
    "description": (
        "Controls only the Home Assistant light entities explicitly listed in "
        "Settings → Plugin Settings. Use action='list' to see configured aliases, "
        "or action='set' with an alias and state='on'/'off'. Optional brightness "
        "is 1–255 and is valid only with state='on'. This tool cannot control "
        "locks, doors, alarms, covers, cameras or arbitrary Home Assistant services. "
        "Do not invent device aliases."
    ),
    "tier": "extra",
    "brief": "Control explicitly mapped Home Assistant lights; list aliases or turn one on/off.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list or set"},
            "device": {"type": "STRING", "description": "Configured light alias"},
            "state": {"type": "STRING", "description": "on or off"},
            "brightness": {"type": "INTEGER", "description": "Optional brightness 1–255 when turning on"},
        },
        "required": ["action"],
    },
}


def _settings() -> tuple[str, str, dict]:
    cfg = get_plugin_config("smart_home")
    base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
    token = str(cfg.get("access_token") or "").strip()
    raw_lights = str(cfg.get("lights_json") or "{}")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Set a valid HTTPS Home Assistant base URL in Plugin Settings.")
    if not token:
        raise ValueError("Add a Home Assistant access token in Plugin Settings.")
    try:
        mapping = json.loads(raw_lights)
    except (TypeError, ValueError) as exc:
        raise ValueError("The allowed light map must be valid JSON.") from exc
    if not isinstance(mapping, dict) or len(mapping) > _MAX_LIGHTS:
        raise ValueError(f"The light map must be a JSON object with at most {_MAX_LIGHTS} entries.")
    clean = {}
    for alias, entity in mapping.items():
        key = str(alias or "").strip().lower()
        value = str(entity or "").strip().lower()
        if not _ALIAS_RE.fullmatch(key) or not _ENTITY_RE.fullmatch(value):
            raise ValueError("Each mapping must use a simple alias and a light.* entity ID.")
        clean[key] = value
    return base_url, token, clean


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "list").strip().lower()
    if action not in {"list", "set"}:
        return "Choose smart_home action 'list' or 'set'."
    try:
        base_url, token, lights = _settings()
    except Exception as exc:
        return f"Smart home is not ready: {str(exc)[:180]}"

    if action == "list":
        if not lights:
            return "No lights are mapped yet. Add aliases and light entity IDs in Plugin Settings."
        return "Configured lights: " + ", ".join(
            f"{alias} ({entity})" for alias, entity in sorted(lights.items())
        )

    alias = str(parameters.get("device") or "").strip().lower()
    entity = lights.get(alias)
    if not entity:
        return "That light alias is not configured. Use action='list' to see allowed lights."

    state = str(parameters.get("state") or "").strip().lower()
    if state not in {"on", "off"}:
        return "Light state must be 'on' or 'off'."
    brightness = parameters.get("brightness")
    payload = {"entity_id": entity}
    if brightness is not None:
        if state != "on":
            return "Brightness can only be set while turning a light on."
        if isinstance(brightness, bool) or not isinstance(brightness, int) or not 1 <= brightness <= 255:
            return "Brightness must be a whole number from 1 to 255."
        payload["brightness"] = brightness

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    service = "turn_on" if state == "on" else "turn_off"
    try:
        response = requests.post(
            f"{base_url}/api/services/light/{service}",
            headers=headers, json=payload, timeout=(3.0, 5.0), allow_redirects=False,
        )
        if response.status_code not in {200, 201}:
            return f"Home Assistant rejected the light command (HTTP {response.status_code})."

        # Verify the resulting entity state; a successful service call alone
        # is not proof that the physical light changed state.
        verified = requests.get(
            f"{base_url}/api/states/{entity}",
            headers=headers, timeout=(3.0, 5.0), allow_redirects=False,
        )
        if verified.status_code != 200:
            return "The command was accepted, but I could not verify the light state."
        data = verified.json()
        actual = str(data.get("state") or "").lower() if isinstance(data, dict) else ""
        if actual != state:
            return f"The light command was accepted, but the reported state is '{actual or 'unknown'}'."
    except requests.RequestException as exc:
        return f"Home Assistant could not be reached: {type(exc).__name__}."
    except (TypeError, ValueError):
        return "Home Assistant returned an invalid state response."

    result = f"{alias} is now {state}."
    if brightness is not None:
        result += f" Brightness: {brightness}/255."
    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result
