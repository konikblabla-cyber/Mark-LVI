"""Local Google Calendar connector for MARK.

Uses Google's desktop-app OAuth flow and Python's standard library only.
No polling, background requests, third-party package or paid fallback.
Every Calendar API request is explicit and subject to a conservative local
per-day request cap. Event creation is gated by the existing HUD confirmation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from memory.config_manager import get_plugin_config, save_plugin_config

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_URL = "https://www.googleapis.com/calendar/v3"
_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
_MAX_DAILY_REQUESTS = 50
_TIMEOUT_SECONDS = 15
_CONNECT_TIMEOUT_SECONDS = 180
_USAGE_PATH = Path(__file__).resolve().parent.parent / "memory" / "google_calendar_usage.json"
_USAGE_LOCK = threading.RLock()
_CONNECT_LOCK = threading.Lock()


def _config() -> dict:
    try:
        return get_plugin_config("google_calendar")
    except Exception:
        return {}


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = __import__("tempfile").mkstemp(
        prefix=".google-calendar-", suffix=".tmp", dir=str(path.parent)
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temp.replace(path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _today_key() -> str:
    return datetime.now().astimezone().date().isoformat()


def _reserve_daily_request() -> int:
    """Reserve one explicit Calendar API request; fail closed on corrupt ledger."""
    with _USAGE_LOCK:
        if _USAGE_PATH.exists():
            try:
                data = json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("date"), str):
                    raise ValueError("invalid request ledger")
                count = int(data.get("count", -1))
                if count < 0:
                    raise ValueError("invalid request count")
            except Exception as exc:
                raise RuntimeError(
                    "The local Google Calendar request limit file is unreadable; "
                    "requests are paused rather than resetting the safety limit."
                ) from exc
            if data["date"] != _today_key():
                data = {"date": _today_key(), "count": 0}
        else:
            data = {"date": _today_key(), "count": 0}
        if data["count"] >= _MAX_DAILY_REQUESTS:
            raise RuntimeError(
                f"Daily Google Calendar safety limit reached "
                f"({_MAX_DAILY_REQUESTS} requests). Try again tomorrow."
            )
        data["count"] += 1
        _write_json_atomic(_USAGE_PATH, data)
        return data["count"]


def _google_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        error = payload.get("error", {})
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("status") or "")
        else:
            message = str(error)
    except Exception:
        message = ""
    message = " ".join(message.split())[:180]
    return f"Google returned HTTP {exc.code}" + (f": {message}" if message else ".")


def _post_form(url: str, form: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_google_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Google: {getattr(exc, 'reason', exc)}") from exc
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(f"Google request failed: {type(exc).__name__}") from exc


def _api_request(path: str, access_token: str, params: dict | None = None,
                 payload: dict | None = None) -> dict:
    _reserve_daily_request()
    url = _CALENDAR_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_google_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Google Calendar: {getattr(exc, 'reason', exc)}") from exc
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(f"Google Calendar request failed: {type(exc).__name__}") from exc


def _access_token() -> str:
    cfg = _config()
    client_id = str(cfg.get("client_id") or "").strip()
    refresh_token = str(cfg.get("refresh_token") or "").strip()
    if not client_id or not refresh_token:
        raise RuntimeError(
            "Google Calendar is not connected. Open Settings → Plugin Settings "
            "and enter a Desktop OAuth client ID, then press Connect."
        )
    form = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    client_secret = str(cfg.get("client_secret") or "").strip()
    if client_secret:
        form["client_secret"] = client_secret
    result = _post_form(_TOKEN_URL, form)
    token = str(result.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Google did not return an access token. Reconnect the account.")
    return token


class _OAuthCallback(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        expected = getattr(self.server, "expected_state", "")
        supplied = (query.get("state") or [""])[0]
        if parsed.path != "/oauth2callback" or not secrets.compare_digest(supplied, expected):
            result = {"error": "The OAuth callback state did not match."}
            status = 400
        elif query.get("error"):
            result = {"error": "Google authorization was cancelled or denied."}
            status = 400
        elif query.get("code"):
            result = {"code": query["code"][0]}
            status = 200
        else:
            result = {"error": "Google returned no authorization code."}
            status = 400
        self.server.oauth_result = result
        page = (
            "<!doctype html><meta charset=utf-8><title>MARK Google Calendar</title>"
            "<h2>Authorization received</h2><p>You can close this tab and return to MARK.</p>"
            if status == 200 else
            "<!doctype html><meta charset=utf-8><title>MARK Google Calendar</title>"
            "<h2>Authorization was not completed</h2><p>Return to MARK and try again.</p>"
        )
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def _oauth_connect(values: dict) -> tuple[bool, str]:
    """Run a one-time browser consent flow from the Plugin Settings worker thread."""
    if not _CONNECT_LOCK.acquire(blocking=False):
        return False, "A Google Calendar connection attempt is already running."
    try:
        client_id = str(values.get("client_id") or "").strip()
        client_secret = str(values.get("client_secret") or "").strip()
        if not client_id:
            return False, "Enter the OAuth Client ID first."
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")

        with HTTPServer(("127.0.0.1", 0), _OAuthCallback) as server:
            server.expected_state = state
            server.oauth_result = None
            server.timeout = 1
            port = server.server_address[1]
            redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
            query = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(_SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
            }
            auth_url = _AUTH_URL + "?" + urllib.parse.urlencode(query)
            if not webbrowser.open(auth_url, new=1):
                return False, "Could not open the browser. Set a default browser and try again."
            deadline = time.monotonic() + _CONNECT_TIMEOUT_SECONDS
            while server.oauth_result is None and time.monotonic() < deadline:
                server.handle_request()
            result = server.oauth_result
            if not result:
                return False, "Google authorization timed out. Press Connect to try again."
            if result.get("error"):
                return False, result["error"]
            code = result["code"]

        form = {
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if client_secret:
            form["client_secret"] = client_secret
        tokens = _post_form(_TOKEN_URL, form)
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not refresh_token:
            refresh_token = str(_config().get("refresh_token") or "").strip()
        if not refresh_token:
            return False, (
                "Google did not issue a refresh token. In Google Cloud, use a Desktop app "
                "OAuth client and retry with consent enabled."
            )
        save_plugin_config("google_calendar", {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        })
        return True, "Google Calendar connected. MARK will only access it when you ask."
    except Exception as exc:
        return False, f"Google Calendar connection failed: {type(exc).__name__}: {str(exc)[:140]}"
    finally:
        _CONNECT_LOCK.release()


def _parse_datetime(value: str, timezone_name: str) -> tuple[datetime, dict]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Provide a start and end time in ISO format.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Use ISO date-time, for example 2026-11-09T16:00:00.") from exc
    body = {"dateTime": parsed.isoformat(timespec="seconds")}
    if parsed.tzinfo is None:
        if not timezone_name:
            raise ValueError("Set a timezone in Google Calendar Plugin Settings.")
        body["timeZone"] = timezone_name
    return parsed, body


def _event_payload(parameters: dict) -> dict:
    cfg = _config()
    timezone_name = str(cfg.get("timezone") or "Europe/Warsaw").strip()
    title = str(parameters.get("title") or "").strip()
    if not title:
        raise ValueError("Give the event a title.")
    start, start_body = _parse_datetime(parameters.get("start", ""), timezone_name)
    end, end_body = _parse_datetime(parameters.get("end", ""), timezone_name)
    if (start.tzinfo is None) != (end.tzinfo is None):
        raise ValueError("Start and end must both include a timezone offset, or neither.")
    if end <= start:
        raise ValueError("The event end must be later than its start.")
    event = {"summary": title[:180], "start": start_body, "end": end_body}
    details = str(parameters.get("details") or "").strip()
    location = str(parameters.get("location") or "").strip()
    if details:
        event["description"] = details[:4000]
    if location:
        event["location"] = location[:500]
    return event


def _create_event(parameters: dict) -> str:
    from core import confirm

    if confirm.pending_title():
        return "A confirmation is already waiting on the HUD. Resolve it first."
    try:
        event = _event_payload(parameters)
    except ValueError as exc:
        return str(exc)

    def create_after_confirmation():
        token = _access_token()
        result = _api_request("/calendars/primary/events", token, payload=event)
        html_link = str(result.get("htmlLink") or "")
        when = result.get("start", {}).get("dateTime") or result.get("start", {}).get("date") or event["start"]["dateTime"]
        message = f"Created calendar event: {result.get('summary') or event['summary']} at {when}."
        if html_link:
            message += f" Open it here: {html_link}"
        return message

    return confirm.request(
        "google_calendar_create_event",
        "Create Google Calendar event",
        f"Add this event to your primary Google Calendar?\n\n"
        f"Title: {event['summary']}\n"
        f"Start: {event['start']['dateTime']}\n"
        f"End: {event['end']['dateTime']}\n"
        "Nothing is sent until you confirm on the HUD.",
        create_after_confirmation,
    )


def _list_events(parameters: dict) -> str:
    token = _access_token()
    now = datetime.now(timezone.utc)
    time_min = str(parameters.get("time_min") or now.isoformat(timespec="seconds").replace("+00:00", "Z"))
    time_max = str(parameters.get("time_max") or
                   (now + timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z"))
    try:
        start = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
        end = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
    except ValueError:
        return "Use ISO date-time for time_min and time_max."
    if (start.tzinfo is None) != (end.tzinfo is None) or end <= start:
        return "The end of the requested time window must be after its start, with matching timezone formats."
    try:
        count = max(1, min(int(parameters.get("max_results", 15)), 25))
    except (TypeError, ValueError):
        count = 15
    result = _api_request(
        "/calendars/primary/events", token,
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(count),
        },
    )
    items = result.get("items") if isinstance(result.get("items"), list) else []
    if not items:
        return "No Google Calendar events were found in that time window."
    lines = []
    for item in items[:count]:
        start = item.get("start") or {}
        when = start.get("dateTime") or start.get("date") or "time unknown"
        lines.append(
            f"- {str(item.get('summary') or '(untitled)')[:180]} — {when}"
            + (f" — {str(item.get('location'))[:120]}" if item.get("location") else "")
        )
    return f"Google Calendar: {len(items)} event(s).\n" + "\n".join(lines)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "status").strip().lower()
    cfg = _config()
    if action == "status":
        connected = bool(cfg.get("client_id") and cfg.get("refresh_token"))
        count = "connected" if connected else "not connected"
        return (
            f"Google Calendar is {count}. Requests are only made after an explicit command; "
            f"the local daily limit is {_MAX_DAILY_REQUESTS}."
        )
    if action == "connect":
        return (
            "Open Settings → Plugin Settings, enter a Google OAuth Desktop Client ID, "
            "then press CONNECT GOOGLE ACCOUNT. MARK will open Google's consent page."
        )
    if action == "disconnect":
        if not cfg.get("refresh_token"):
            return "Google Calendar is already disconnected."
        save_plugin_config("google_calendar", {"refresh_token": ""})
        return (
            "The refresh token was removed from this computer. To revoke the app's "
            "Google permission too, remove MARK from your Google Account security settings."
        )
    try:
        if action == "list_events":
            return _list_events(parameters)
        if action == "create_event":
            return _create_event(parameters)
        return "Choose status, connect, disconnect, list_events or create_event."
    except Exception as exc:
        return f"Google Calendar failed: {type(exc).__name__}: {str(exc)[:160]}"


def _settings_connect(values: dict):
    return _oauth_connect(values)


PLUGIN_SETTINGS = {
    "namespace": "google_calendar",
    "title": "Google Calendar — local OAuth",
    "fields": [
        {"key": "client_id", "label": "Google OAuth Desktop Client ID", "type": "text",
         "placeholder": "…apps.googleusercontent.com"},
        {"key": "client_secret", "label": "OAuth client secret (optional)", "type": "password",
         "placeholder": "Optional for a Desktop app client"},
        {"key": "timezone", "label": "Calendar timezone", "type": "text",
         "default": "Europe/Warsaw"},
    ],
    "action": {"label": "CONNECT GOOGLE ACCOUNT", "run": _settings_connect},
}


PLUGIN = {
    "name": "google_calendar",
    "description": (
        "Access the user's Google Calendar only when explicitly asked. Use list_events to "
        "show upcoming events in a requested time window, or create_event to prepare an event. "
        "Creating an event always waits for the user's separate HUD confirmation. The connect "
        "flow is started in Settings → Plugin Settings. No background polling, auto-scheduling "
        "or deletion is performed."
    ),
    "tier": "core",
    "brief": "Read upcoming Google Calendar events or prepare an event with HUD confirmation.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status, connect, disconnect, list_events or create_event"},
            "title": {"type": "STRING", "description": "Event title"},
            "start": {"type": "STRING", "description": "ISO start date-time, with timezone offset or configured calendar timezone"},
            "end": {"type": "STRING", "description": "ISO end date-time, with timezone offset or configured calendar timezone"},
            "details": {"type": "STRING", "description": "Optional event description"},
            "location": {"type": "STRING", "description": "Optional event location"},
            "time_min": {"type": "STRING", "description": "Optional ISO beginning of event search window"},
            "time_max": {"type": "STRING", "description": "Optional ISO end of event search window"},
            "max_results": {"type": "INTEGER", "description": "Maximum events to return (1-25)"},
        },
        "required": ["action"],
    },
}
