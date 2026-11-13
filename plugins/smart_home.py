"""Constrained Home Assistant control over an authenticated REST API.

Only explicitly mapped entities can be controlled: lights, switches, fans,
climate targets, covers, locks and vacuums. Locks and covers always require
human HUD confirmation. No alarms, cameras, scripts or arbitrary services are
exposed. Every successful command is read back from Home Assistant.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import requests

from core import confirm
from memory.config_manager import get_plugin_config

_ALLOWED_DOMAINS = {"light", "switch", "fan", "climate", "cover", "lock", "vacuum"}
_ENTITY_RE = re.compile(r"^(?:" + "|".join(sorted(_ALLOWED_DOMAINS)) + r")\.[a-z0-9_]+$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MAX_ENTITIES = 40


PLUGIN_SETTINGS = {
    "namespace": "smart_home",
    "title": "Home Assistant — selected devices",
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
            "key": "entities_json",
            "label": "Allowed device aliases (JSON)",
            "type": "text",
            "default": "{}",
            "placeholder": '{"desk":"light.desk","plug":"switch.pc_plug","front_door":"lock.front_door","garage":"cover.garage_door","vacuum":"vacuum.robot"}',
        },
    ],
}


PLUGIN = {
    "name": "smart_home",
    "description": (
        "Controls only Home Assistant entities explicitly listed in Settings → Plugin Settings. "
        "Supported domains: light, switch, fan, climate, cover, lock and vacuum. Use action='list' "
        "to see aliases. Lights/switches/fans use state on/off; optional brightness 1–255 only for "
        "lights and percentage 1–100 only for fans. Climate uses target temperature 5–35°C and does "
        "not change HVAC mode. Covers accept state open/close/stop, locks accept lock/unlock, and "
        "vacuums accept start/pause/return. Every cover and lock operation requires a separate "
        "human confirmation on the HUD before Home Assistant receives the command. Only the exact "
        "allowlisted entity is affected, then its reported state is read back. No alarms, cameras, "
        "scripts, arbitrary services or unconfigured entities can be controlled. Never invent aliases."
    ),
    "tier": "extra",
    "brief": "Control allowlisted Home Assistant lights, climate, covers, locks and vacuums; lock/cover actions need HUD approval.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list or set"},
            "device": {"type": "STRING", "description": "Configured device alias"},
            "state": {"type": "STRING", "description": "on/off, cover open/close/stop, lock/unlock, or vacuum start/pause/return"},
            "brightness": {"type": "INTEGER", "description": "Optional light brightness 1–255 when turning on"},
            "percentage": {"type": "INTEGER", "description": "Optional fan speed 1–100 when turning on"},
            "temperature": {"type": "NUMBER", "description": "Climate target temperature in Celsius, 5–35"},
        },
        "required": ["action"],
    },
}


def _settings() -> tuple[str, str, dict]:
    cfg = get_plugin_config("smart_home")
    base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
    token = str(cfg.get("access_token") or "").strip()
    raw_entities = str(cfg.get("entities_json") or cfg.get("lights_json") or "{}")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Set a valid HTTPS Home Assistant base URL in Plugin Settings.")
    if not token:
        raise ValueError("Add a Home Assistant access token in Plugin Settings.")
    try:
        mapping = json.loads(raw_entities)
    except (TypeError, ValueError) as exc:
        raise ValueError("The allowed device map must be valid JSON.") from exc
    if not isinstance(mapping, dict) or len(mapping) > _MAX_ENTITIES:
        raise ValueError(f"The device map must be a JSON object with at most {_MAX_ENTITIES} entries.")
    clean = {}
    for alias, entity in mapping.items():
        key = str(alias or "").strip().lower()
        value = str(entity or "").strip().lower()
        if not _ALIAS_RE.fullmatch(key) or not _ENTITY_RE.fullmatch(value):
            allowed = ", ".join(sorted(_ALLOWED_DOMAINS))
            raise ValueError(f"Each mapping must use a simple alias and a supported entity ID from these domains: {allowed}.")
        clean[key] = value
    return base_url, token, clean


def _verified_state(base_url: str, headers: dict, entity: str):
    response = requests.get(
        f"{base_url}/api/states/{entity}",
        headers=headers, timeout=(3.0, 5.0), allow_redirects=False,
    )
    if response.status_code != 200:
        return None
    data = response.json()
    if not isinstance(data, dict):
        return None
    return data


def _execute_command(base_url: str, token: str, alias: str, entity: str,
                     domain: str, service: str, payload: dict,
                     requested_state: str, *, brightness=None,
                     percentage=None, temperature=None, player=None) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        f"{base_url}/api/services/{domain}/{service}",
        headers=headers, json=payload, timeout=(3.0, 5.0),
        allow_redirects=False,
    )
    if response.status_code not in {200, 201}:
        return f"Home Assistant rejected the device command (HTTP {response.status_code})."

    data = _verified_state(base_url, headers, entity)
    if data is None:
        return "The command was accepted, but I could not verify the device state."
    actual = str(data.get("state") or "").lower()
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}

    if domain == "climate":
        target = attributes.get("temperature")
        if not isinstance(target, (int, float)) or abs(float(target) - float(temperature)) > 0.5:
            return "The climate command was accepted, but the reported target temperature does not match."
        result = f"{alias} target temperature is now {float(temperature):g}°C."
    elif domain == "light":
        if actual != requested_state:
            return f"The command was accepted, but the reported state is '{actual or 'unknown'}'."
        if brightness is not None:
            actual_brightness = attributes.get("brightness")
            if not isinstance(actual_brightness, (int, float)) or abs(float(actual_brightness) - brightness) > 3:
                return "The light turned on, but its reported brightness did not match."
        result = f"{alias} is now {requested_state}."
        if brightness is not None:
            result += f" Brightness: {brightness}/255."
    elif domain == "fan":
        if actual != requested_state:
            return f"The command was accepted, but the reported state is '{actual or 'unknown'}'."
        if percentage is not None:
            actual_percentage = attributes.get("percentage")
            if not isinstance(actual_percentage, (int, float)) or abs(float(actual_percentage) - percentage) > 2:
                return "The fan turned on, but its reported speed did not match the requested percentage."
        result = f"{alias} is now {requested_state}."
        if percentage is not None:
            result += f" Fan speed: {percentage}%."
    elif domain in {"switch", "lock"}:
        if actual != requested_state:
            return f"The command was accepted, but the reported state is '{actual or 'unknown'}'."
        result = f"{alias} is now {actual}."
    elif domain == "cover":
        if requested_state == "stop":
            if actual in {"opening", "closing"} or actual in {"", "unknown", "unavailable"}:
                return f"The stop command was accepted, but the cover still reports '{actual or 'unknown'}'."
            result = f"{alias} movement stopped; the cover reports '{actual}'."
        else:
            expected = "open" if requested_state == "open" else "closed"
            if actual != expected:
                return f"The cover command was accepted, but its reported state is '{actual or 'unknown'}'."
            result = f"{alias} is now {actual}."
    elif domain == "vacuum":
        accepted = {
            "start": {"cleaning", "running", "on"},
            "pause": {"paused"},
            "return": {"returning", "returning_to_base", "docked", "idle"},
        }[requested_state]
        if actual not in accepted:
            return f"The vacuum command was accepted, but its reported state is '{actual or 'unknown'}'."
        result = f"{alias} is now {actual}."
    else:
        return "That Home Assistant domain is not supported."

    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "list").strip().lower()
    if action not in {"list", "set"}:
        return "Choose smart_home action 'list' or 'set'."
    try:
        base_url, token, entities = _settings()
    except Exception as exc:
        return f"Smart home is not ready: {str(exc)[:180]}"

    if action == "list":
        if not entities:
            return "No smart-home devices are mapped yet. Add aliases and supported entity IDs in Plugin Settings."
        return "Configured devices: " + ", ".join(
            f"{alias} ({entity})" for alias, entity in sorted(entities.items())
        )

    alias = str(parameters.get("device") or "").strip().lower()
    entity = entities.get(alias)
    if not entity:
        return "That device alias is not configured. Use action='list' to see allowed devices."

    domain = entity.split(".", 1)[0]
    state = str(parameters.get("state") or "").strip().lower()
    brightness = parameters.get("brightness")
    percentage = parameters.get("percentage")
    temperature = parameters.get("temperature")
    payload = {"entity_id": entity}
    requires_confirmation = False

    if domain == "climate":
        if state or brightness is not None or percentage is not None:
            return "Climate control accepts only a target temperature; it does not change HVAC mode."
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            return "Provide a climate target temperature from 5 to 35°C."
        if not 5 <= float(temperature) <= 35:
            return "Climate target temperature must be from 5 to 35°C."
        service = "set_temperature"
        payload["temperature"] = float(temperature)
    elif domain == "light":
        if temperature is not None or percentage is not None:
            return "Lights accept only on/off and optional brightness."
        if state not in {"on", "off"}:
            return "Light state must be 'on' or 'off'."
        if brightness is not None:
            if state != "on":
                return "Brightness can only be set while turning a light on."
            if isinstance(brightness, bool) or not isinstance(brightness, int) or not 1 <= brightness <= 255:
                return "Brightness must be a whole number from 1 to 255."
            payload["brightness"] = brightness
        service = "turn_on" if state == "on" else "turn_off"
    elif domain == "switch":
        if temperature is not None or brightness is not None or percentage is not None:
            return "Switches accept only on or off."
        if state not in {"on", "off"}:
            return "Switch state must be 'on' or 'off'."
        service = "turn_on" if state == "on" else "turn_off"
    elif domain == "fan":
        if temperature is not None or brightness is not None:
            return "Fans accept on/off and optional percentage only."
        if state not in {"on", "off"}:
            return "Fan state must be 'on' or 'off'."
        if percentage is not None:
            if state != "on":
                return "Fan percentage can only be set while turning a fan on."
            if isinstance(percentage, bool) or not isinstance(percentage, int) or not 1 <= percentage <= 100:
                return "Fan percentage must be a whole number from 1 to 100."
            service = "set_percentage"
            payload["percentage"] = percentage
        else:
            service = "turn_on" if state == "on" else "turn_off"
    elif domain == "cover":
        if temperature is not None or brightness is not None or percentage is not None:
            return "Covers accept only open, close or stop."
        if state not in {"open", "close", "stop"}:
            return "Cover state must be 'open', 'close' or 'stop'."
        service = {"open": "open_cover", "close": "close_cover", "stop": "stop_cover"}[state]
        requires_confirmation = True
    elif domain == "lock":
        if temperature is not None or brightness is not None or percentage is not None:
            return "Locks accept only lock or unlock."
        if state not in {"lock", "unlock"}:
            return "Lock state must be 'lock' or 'unlock'."
        service = "lock" if state == "lock" else "unlock"
        state = "locked" if state == "lock" else "unlocked"
        requires_confirmation = True
    elif domain == "vacuum":
        if temperature is not None or brightness is not None or percentage is not None:
            return "Vacuums accept only start, pause or return."
        if state not in {"start", "pause", "return"}:
            return "Vacuum state must be 'start', 'pause' or 'return'."
        service = {"start": "start", "pause": "pause", "return": "return_to_base"}[state]
    else:
        return "That Home Assistant domain is not supported."

    def execute():
        return _execute_command(
            base_url, token, alias, entity, domain, service, payload, state,
            brightness=brightness, percentage=percentage, temperature=temperature,
            player=player,
        )

    if requires_confirmation:
        operation = {
            "cover": {"open": "open", "close": "close", "stop": "stop movement of"},
            "lock": {"locked": "lock", "unlocked": "unlock"},
        }[domain][state]
        detail = (
            f"Home Assistant security-sensitive action\n"
            f"Device alias: {alias}\nEntity: {entity}\nOperation: {operation}\n\n"
            "This action affects a physical door, garage or window covering. "
            "Home Assistant will not receive the command until you press CONFIRM."
        )
        return confirm.request(
            key=f"smart_home_{domain}",
            title=f"Confirm {operation} — {alias}?",
            detail=detail,
            run=execute,
        )
    try:
        return execute()
    except requests.RequestException as exc:
        return f"Home Assistant could not be reached: {type(exc).__name__}."
    except (TypeError, ValueError):
        return "Home Assistant returned an invalid state response."
