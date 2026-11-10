"""Constrained Home Assistant control over an authenticated REST API.

Only explicitly mapped lights, switches, fans and climate entities can be
controlled. No locks, covers, alarms, cameras or arbitrary services are exposed.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import requests

from memory.config_manager import get_plugin_config

_ENTITY_RE = re.compile(r"^(?:light|switch|fan|climate)\.[a-z0-9_]+$")
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
            "placeholder": '{"desk":"light.desk","plug":"switch.pc_plug","climate":"climate.office"}',
        },
    ],
}


PLUGIN = {
    "name": "smart_home",
    "description": (
        "Controls only Home Assistant entities explicitly listed in Settings → Plugin Settings. "
        "Supported domains: light, switch, fan and climate. Use action='list' to see aliases. "
        "Use action='set' with state='on'/'off' for lights, switches and fans; optional "
        "brightness 1–255 is allowed only for lights, and percentage 1–100 only for fans. "
        "For climate entities, provide temperature 5–35°C; this changes the target temperature "
        "only and does not change HVAC mode. No locks, covers, doors, alarms, cameras or "
        "arbitrary Home Assistant services can be controlled. Never invent aliases."
    ),
    "tier": "extra",
    "brief": "Control allowlisted Home Assistant lights, switches, fans and climate targets.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list or set"},
            "device": {"type": "STRING", "description": "Configured device alias"},
            "state": {"type": "STRING", "description": "on or off for lights, switches and fans"},
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
    # Read the old lights_json key for backwards compatibility with early setup.
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
            raise ValueError(
                "Each mapping must use a simple alias and a light.*, switch.*, fan.* or climate.* entity ID."
            )
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

    if domain == "climate":
        if state or brightness is not None or percentage is not None:
            return "Climate control accepts only a target temperature; it does not change HVAC mode."
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            return "Provide a climate target temperature from 5 to 35°C."
        if not 5 <= float(temperature) <= 35:
            return "Climate target temperature must be from 5 to 35°C."
        service = "set_temperature"
        payload = {"entity_id": entity, "temperature": float(temperature)}
    else:
        if temperature is not None:
            return "Temperature can only be set on a configured climate entity."
        if state not in {"on", "off"}:
            return "Device state must be 'on' or 'off'."
        payload = {"entity_id": entity}
        if domain == "light":
            if percentage is not None:
                return "Percentage is only supported for fans."
            if brightness is not None:
                if state != "on":
                    return "Brightness can only be set while turning a light on."
                if isinstance(brightness, bool) or not isinstance(brightness, int) or not 1 <= brightness <= 255:
                    return "Brightness must be a whole number from 1 to 255."
                payload["brightness"] = brightness
            service = "turn_on" if state == "on" else "turn_off"
        elif domain == "switch":
            if brightness is not None or percentage is not None:
                return "Brightness and percentage are not supported for switches."
            service = "turn_on" if state == "on" else "turn_off"
        elif domain == "fan":
            if brightness is not None:
                return "Brightness is only supported for lights."
            if percentage is not None:
                if state != "on":
                    return "Fan percentage can only be set while turning a fan on."
                if isinstance(percentage, bool) or not isinstance(percentage, int) or not 1 <= percentage <= 100:
                    return "Fan percentage must be a whole number from 1 to 100."
                service = "set_percentage"
                payload["percentage"] = percentage
            else:
                service = "turn_on" if state == "on" else "turn_off"
        else:
            return "That Home Assistant domain is not supported."

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
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
        else:
            if actual != state:
                return f"The command was accepted, but the reported state is '{actual or 'unknown'}'."
            if domain == "light" and brightness is not None:
                actual_brightness = attributes.get("brightness")
                if not isinstance(actual_brightness, (int, float)) or abs(float(actual_brightness) - brightness) > 3:
                    return "The light turned on, but its reported brightness did not match the requested value."
            if domain == "fan" and percentage is not None:
                actual_percentage = attributes.get("percentage")
                if not isinstance(actual_percentage, (int, float)) or abs(float(actual_percentage) - percentage) > 2:
                    return "The fan turned on, but its reported speed did not match the requested percentage."
    except requests.RequestException as exc:
        return f"Home Assistant could not be reached: {type(exc).__name__}."
    except (TypeError, ValueError):
        return "Home Assistant returned an invalid state response."

    if domain == "climate":
        result = f"{alias} target temperature is now {float(temperature):g}°C."
    else:
        result = f"{alias} is now {state}."
        if brightness is not None:
            result += f" Brightness: {brightness}/255."
        if percentage is not None:
            result += f" Fan speed: {percentage}%."
    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result


