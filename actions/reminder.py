import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import re
from datetime import datetime
from pathlib import Path

_CNW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if platform.system() == "Windows" else {}
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _get_os() -> str:
    _sys = platform.system()
    if _sys == "Darwin":
        return "mac"
    if _sys == "Linux":
        return "linux"
    return "windows"


def _scripts_dir() -> Path:
    d = Path.home() / ".jarvis" / "reminders"
    d.mkdir(parents=True, exist_ok=True)
    return d


_REMINDER_ID = re.compile(r"^JARVISReminder_\d{8}_\d{6}$")


def _registry_path() -> Path:
    return _scripts_dir() / "reminders.json"


def _read_registry() -> dict:
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("The reminder list is unreadable; refusing to overwrite it.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("The reminder list has an invalid structure.")
    return value


def _write_registry(value: dict) -> None:
    path = _registry_path()
    fd, temp_name = tempfile.mkstemp(prefix=".reminders-", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _list_reminders() -> str:
    try:
        entries = _read_registry()
        changed = False
        visible = []
        scripts = _scripts_dir()
        for reminder_id, record in list(entries.items()):
            if not _REMINDER_ID.fullmatch(str(reminder_id)) or not isinstance(record, dict):
                continue
            script_name = str(record.get("script") or "")
            if script_name != f"{reminder_id}.py":
                continue
            # The notification script deletes itself after firing. Prune its
            # registry row the next time the user asks for the list.
            if not (scripts / script_name).is_file():
                entries.pop(reminder_id, None)
                changed = True
                continue
            visible.append((reminder_id, record))
        if changed:
            _write_registry(entries)
        if not visible:
            return "There are no pending JARVIS reminders."
        rows = []
        for reminder_id, record in sorted(visible, key=lambda item: (
                str(item[1].get("date", "")), str(item[1].get("time", "")))):
            rows.append(
                f"{reminder_id} — {record.get('date', '')} {record.get('time', '')}: "
                f"{str(record.get('message', 'Reminder'))[:120]}"
            )
        return "Pending reminders:\n" + "\n".join(rows[:30])
    except Exception as exc:
        return f"Could not read reminders: {str(exc)[:160]}"


def _cancel_windows_reminder(reminder_id: str) -> str:
    if not _REMINDER_ID.fullmatch(reminder_id):
        return "That reminder ID is invalid. Use the exact ID from the reminder list."
    try:
        entries = _read_registry()
        record = entries.get(reminder_id)
        if not isinstance(record, dict):
            return "That reminder was not found or has already fired."
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", reminder_id, "/F"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10, **_CNW,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return f"Windows could not cancel that reminder: {detail[:140]}"
        script_name = str(record.get("script") or "")
        if script_name == f"{reminder_id}.py":
            try:
                (_scripts_dir() / script_name).unlink(missing_ok=True)
            except OSError:
                pass
        entries.pop(reminder_id, None)
        _write_registry(entries)
        return f"Cancelled reminder {reminder_id}."
    except Exception as exc:
        return f"Could not cancel reminder: {type(exc).__name__}."
def _sanitise(text: str, max_len: int = 200) -> str:
    return (
        text.replace("\\", "")
            .replace('"', "")
            .replace("'", "")
            .replace("\n", " ")
            .replace("\r", "")
            .strip()
    )[:max_len]

def _write_notify_script(task_name: str, message: str, os_name: str) -> Path:
    script_path = _scripts_dir() / f"{task_name}.py"
    msg_literal = json.dumps(message)  

    if os_name == "windows":
        notify_block = f"""
message = {msg_literal}
notified = False

try:
    from plyer import notification
    notification.notify(title="J.A.R.V.I.S Reminder", message=message, timeout=15)
    notified = True
except Exception:
    pass

if not notified:
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast("J.A.R.V.I.S Reminder", message, duration=15, threaded=False)
        notified = True
    except Exception:
        pass

if not notified:
    try:
        import subprocess
        subprocess.run(["msg", "*", "/TIME:30", message], check=False)
    except Exception:
        pass

try:
    import winsound
    for freq in [800, 1000, 1200]:
        winsound.Beep(freq, 180)
        import time; time.sleep(0.08)
except Exception:
    pass
"""

    elif os_name == "mac":
        notify_block = f"""
message = {msg_literal}
notified = False

try:
    from plyer import notification
    notification.notify(title="J.A.R.V.I.S Reminder", message=message, timeout=15)
    notified = True
except Exception:
    pass

if not notified:
    try:
        import subprocess
        script = 'display notification "{{}}" with title "J.A.R.V.I.S Reminder"'.format(
            message.replace('"', '')
        )
        subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass
"""

    else:  # linux
        notify_block = f"""
message = {msg_literal}
notified = False

try:
    from plyer import notification
    notification.notify(title="J.A.R.V.I.S Reminder", message=message, timeout=15)
    notified = True
except Exception:
    pass

if not notified:
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "--urgency=normal", "--expire-time=15000",
             "J.A.R.V.I.S Reminder", message],
            check=False
        )
    except Exception:
        pass
"""

    script_body = f"""# Auto-generated by J.A.R.V.I.S reminder — do not edit
import sys, os, pathlib
{notify_block}
# Self-delete after firing
try:
    pathlib.Path(__file__).unlink(missing_ok=True)
except Exception:
    pass
"""
    script_path.write_text(script_body, encoding="utf-8")
    script_path.chmod(0o600)   # owner read/write only
    return script_path

def _schedule_windows(target_dt: datetime, task_name: str,
                      script_path: Path, message: str) -> str:
    python_exe = Path(sys.executable)
    pythonw = python_exe.parent / "pythonw.exe"
    if pythonw.exists():
        python_exe = pythonw

    xml_path = _scripts_dir() / f"{task_name}.xml"
    xml_content = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo><Description>J.A.R.V.I.S Reminder</Description></RegistrationInfo>\n'
        '  <Triggers><TimeTrigger>\n'
        f'    <StartBoundary>{target_dt.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
        '    <Enabled>true</Enabled>\n'
        '  </TimeTrigger></Triggers>\n'
        '  <Actions><Exec>\n'
        f'    <Command>{python_exe}</Command>\n'
        f'    <Arguments>"{script_path}"</Arguments>\n'
        '  </Exec></Actions>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
        '    <Enabled>true</Enabled>\n'
        '  </Settings>\n'
        '  <Principals><Principal>\n'
        '    <LogonType>InteractiveToken</LogonType>\n'
        '    <RunLevel>LeastPrivilege</RunLevel>\n'
        '  </Principal></Principals>\n'
        '</Task>'
    )

    xml_path.write_text(xml_content, encoding="utf-16")

    result = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **_CNW,
    )

    try:
        xml_path.unlink(missing_ok=True)
    except Exception:
        pass

    if result.returncode != 0:
        script_path.unlink(missing_ok=True)
        err = (result.stderr or result.stdout).strip()
        print(f"[Reminder] ❌ schtasks: {err}")
        return ""  

    return task_name


def _schedule_mac(target_dt: datetime, task_name: str,
                  script_path: Path) -> str:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    label     = f"com.jarvis.reminder.{task_name}"
    plist_path = agents_dir / f"{label}.plist"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{script_path}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Year</key>   <integer>{target_dt.year}</integer>
    <key>Month</key>  <integer>{target_dt.month}</integer>
    <key>Day</key>    <integer>{target_dt.day}</integer>
    <key>Hour</key>   <integer>{target_dt.hour}</integer>
    <key>Minute</key> <integer>{target_dt.minute}</integer>
  </dict>
  <key>RunAtLoad</key>         <false/>
  <key>StandardOutPath</key>   <string>/dev/null</string>
  <key>StandardErrorPath</key> <string>/dev/null</string>
</dict>
</plist>
"""
    plist_path.write_text(plist_content, encoding="utf-8")
    plist_path.chmod(0o644)

    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )

    if result.returncode != 0:
        plist_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)
        print(f"[Reminder] ❌ launchctl: {result.stderr.strip()}")
        return ""

    return label


def _schedule_linux(target_dt: datetime, task_name: str,
                    script_path: Path) -> str:

    if shutil.which("systemd-run"):
        on_calendar = target_dt.strftime("%Y-%m-%d %H:%M:00")
        result = subprocess.run(
            [
                "systemd-run",
                "--user",
                f"--on-calendar={on_calendar}",
                f"--unit={task_name}",
                "--",
                sys.executable, str(script_path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return task_name
        print(f"[Reminder] ⚠️ systemd-run failed: {result.stderr.strip()}, trying 'at'")

    if shutil.which("at"):
        at_time = target_dt.strftime("%H:%M %Y-%m-%d")
        cmd_str = f"{sys.executable} {script_path}\n"
        result  = subprocess.run(
            ["at", at_time],
            input=cmd_str, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return task_name
        print(f"[Reminder] ❌ at: {result.stderr.strip()}")
        return ""

    print("[Reminder] ❌ Neither systemd-run nor at found on this Linux system.")
    return ""

def reminder(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    action = str(parameters.get("action") or "set").strip().lower()
    if action == "list":
        return _list_reminders()
    if action == "cancel":
        reminder_id = str(parameters.get("reminder_id") or "").strip()
        if _get_os() != "windows":
            return "Reminder cancellation is currently available on Windows only."
        return _cancel_windows_reminder(reminder_id)
    if action != "set":
        return "Choose reminder action 'set', 'list' or 'cancel'."

    date_str = str(parameters.get("date") or "").strip()
    time_str = str(parameters.get("time") or "").strip()
    message = str(parameters.get("message") or "Reminder").strip()
    if not date_str or not time_str:
        return "I need both a date and a time to set a reminder."

    try:
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "I couldn't parse that date or time. Please use YYYY-MM-DD and HH:MM."

    if target_dt <= datetime.now():
        return "That time has already passed — I can't set a reminder in the past."

    os_name = _get_os()
    safe_msg = _sanitise(message)
    task_name = f"JARVISReminder_{target_dt.strftime('%Y%m%d_%H%M%S')}"

    try:
        script_path = _write_notify_script(task_name, safe_msg, os_name)
    except Exception as e:
        return f"Could not prepare the reminder script: {e}"

    try:
        if os_name == "windows":
            job_id = _schedule_windows(target_dt, task_name, script_path, safe_msg)
        elif os_name == "mac":
            job_id = _schedule_mac(target_dt, task_name, script_path)
        else:
            job_id = _schedule_linux(target_dt, task_name, script_path)
    except Exception as e:
        script_path.unlink(missing_ok=True)
        print(f"[Reminder] ❌ Scheduling exception: {e}")
        return "Something went wrong while scheduling the reminder."

    if not job_id:
        return "I couldn't register the reminder with the system scheduler."

    try:
        entries = _read_registry()
        entries[job_id] = {
            "date": date_str,
            "time": time_str,
            "message": safe_msg,
            "script": script_path.name,
        }
        _write_registry(entries)
    except Exception as exc:
        if os_name == "windows":
            # The registry may be exactly what failed. Remove the scheduled
            # task directly; the registry-backed cancel helper cannot find it.
            try:
                cleanup = subprocess.run(
                    ["schtasks", "/Delete", "/TN", job_id, "/F"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=10, **_CNW,
                )
                if cleanup.returncode != 0:
                    print(f"[Reminder] Could not remove orphaned task: {(cleanup.stderr or cleanup.stdout)[:160]}")
            except Exception as cleanup_error:
                print(f"[Reminder] Orphaned task cleanup failed: {type(cleanup_error).__name__}")
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            pass
        return f"Windows scheduled the reminder, but its local record could not be saved: {type(exc).__name__}."

    if player:
        try:
            player.write_log(f"[Reminder] ✅ {date_str} {time_str} — {safe_msg[:40]}")
        except Exception:
            pass

    friendly_time = target_dt.strftime("%B %d at %I:%M %p")
    return f"Reminder set for {friendly_time}. ID: {job_id}."
# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "reminder",
    "description": (
        "Set, list or cancel a timed reminder on this Windows computer. "
        "Use action='set' with a date, time and message; action='list' to show pending reminders; "
        "action='cancel' with the exact reminder_id returned by the list or set action."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "set, list or cancel; defaults to set"},
            "date": {"type": "STRING", "description": "For set: date in YYYY-MM-DD format"},
            "time": {"type": "STRING", "description": "For set: time in HH:MM format (24h)"},
            "message": {"type": "STRING", "description": "For set: reminder message"},
            "reminder_id": {"type": "STRING", "description": "For cancel: exact ID from the reminder list"},
        },
        "required": [],
    },
    "handler": reminder,
}