"""Compact, local daily briefing across JARVIS' existing Windows subsystems."""
from __future__ import annotations

import platform
from datetime import datetime


def build_status_briefing() -> str:
    """Read pending work, reminders, monitored topics and machine health."""
    if platform.system() != "Windows":
        return "The integrated status briefing is currently supported on Windows only."

    sections = [f"JARVIS status briefing — {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    try:
        from plugins import mission_control
        missions = mission_control.run({"action": "list"})
        if missions and missions != "No missions saved.":
            sections.append("ACTIVE MISSIONS:\n" + missions[:2200])
        else:
            sections.append("ACTIVE MISSIONS: none saved.")
    except Exception as exc:
        sections.append(f"ACTIVE MISSIONS: unavailable ({type(exc).__name__}).")

    try:
        from actions.reminder import _list_reminders
        sections.append("REMINDERS:\n" + _list_reminders()[:1600])
    except Exception as exc:
        sections.append(f"REMINDERS: unavailable ({type(exc).__name__}).")

    try:
        from actions.background_monitor import list_monitors
        topics = list_monitors()
        sections.append(
            "MONITORED TOPICS: " + (", ".join(topics[:12]) if topics else "none configured.")
        )
    except Exception as exc:
        sections.append(f"MONITORED TOPICS: unavailable ({type(exc).__name__}).")

    try:
        from actions.workspace_mode import _read_profiles
        profiles = _read_profiles()
        labels = [item["name"] for item in profiles.values()]
        sections.append(
            "SAVED WORKSPACES: " + (", ".join(labels[:12]) if labels else "none saved.")
        )
    except Exception as exc:
        sections.append(f"SAVED WORKSPACES: unavailable ({type(exc).__name__}).")

    try:
        from actions.system_monitor import get_system_status
        status = get_system_status()
        disk = status.get("disk") or {}
        disk_free = disk.get("free_gb")
        warnings = []
        if status.get("cpu_percent", 0) >= 90:
            warnings.append(f"CPU high ({status['cpu_percent']:.0f}%)")
        if status.get("ram_percent", 0) >= 90:
            warnings.append(f"RAM high ({status['ram_percent']:.0f}%)")
        if disk.get("free_percent") is not None and disk["free_percent"] <= 10:
            warnings.append(f"system drive low ({disk_free} GB free)")
        temp = status.get("cpu_temp_c")
        if temp is not None and temp >= 85:
            warnings.append(f"CPU temperature high ({temp:.0f}°C)")
        sections.append(
            "PC HEALTH: "
            f"CPU {status['cpu_percent']:.0f}%, RAM {status['ram_percent']:.0f}%, "
            f"disk free {disk_free if disk_free is not None else 'unknown'} GB, "
            f"uptime {status.get('uptime', 'unknown')}. "
            + ("Warnings: " + ", ".join(warnings) if warnings else "No critical resource warning.")
        )
    except Exception as exc:
        sections.append(f"PC HEALTH: unavailable ({type(exc).__name__}).")

    sections.append(
        "This is a local read-only snapshot. Do not claim that reminders, missions "
        "or monitored topics were changed."
    )
    return "\n\n".join(sections)


def run(parameters: dict, player=None, session_memory=None) -> str:
    return build_status_briefing()


TOOL = {
    "name": "status_briefing",
    "description": (
        "Build a compact local Windows briefing with active Mission Control "
        "tasks, pending reminders, monitored topics, saved workspace profiles "
        "and PC health. Use when the user asks what is pending, what is on today, "
        "or asks for a JARVIS status briefing. It reads local state only and "
        "does not modify tasks, reminders, profiles or Windows settings."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": run,
}
