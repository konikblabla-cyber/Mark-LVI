"""Personal Spotify Connect, catalog and playlist integration for MARK.

Uses Spotify OAuth 2.0 Authorization Code with PKCE. Client ID is public and
user-owned; no client secret is stored. OAuth tokens live outside the repository.
Spotify catalog metadata is displayed only in MARK's local HUD and is never
returned to the Gemini function-call response.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from core import confirm
from memory.config_manager import get_plugin_config

_REDIRECT_URI = "http://127.0.0.1:8765/callback"
_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_ROOT = "https://api.spotify.com/v1"
_TOKEN_TIMEOUT = (5.0, 20.0)
_API_TIMEOUT = (5.0, 15.0)
_MAX_TRACK_QUERIES = 25
_MAX_SEARCH_TEXT = 180
_MAX_PREVIEW_CHARS = 9000
_URI_RE = re.compile(r"^spotify:(track|album|playlist):([A-Za-z0-9]{10,64})$")
_SCOPES = (
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
)

PLUGIN_SETTINGS = {
    "namespace": "spotify",
    "title": "Spotify Connect",
    "fields": [
        {
            "key": "client_id",
            "label": "Spotify Developer App Client ID",
            "type": "text",
            "default": "",
            "placeholder": "Client ID from developer.spotify.com/dashboard",
        },
    ],
}

PLUGIN = {
    "name": "spotify",
    "description": (
        "Personal Spotify Connect integration using secure OAuth PKCE. First set the Spotify "
        "Developer App Client ID in Settings → Plugin Settings and register redirect URI "
        "http://127.0.0.1:8765/callback in Spotify Developer Dashboard, then use action='connect'. "
        "Actions: status, connect, disconnect, playback_status, devices, search, play, pause, "
        "resume, next, previous, volume, queue, playlist_list, playlist_play, playlist_create, "
        "playlist_add. Search/play accepts a track query or Spotify URI. Playlist actions accept "
        "one track search per line in track_queries; playlist creation/addition always waits for "
        "the exact track matches and target playlist to be approved on the HUD. Playlists are private "
        "by default. Optional device selects an exact Spotify Connect device name. Playback control "
        "requires Spotify Premium. In Development Mode, the app owner must have Premium and the "
        "Spotify developer app must authorize this account. Spotify catalog metadata is shown only "
        "in the local HUD; never send track, artist or playlist metadata back to the language model."
    ),
    "tier": "core",
    "brief": "Control Spotify Connect, search/play tracks and create/add to private playlists with HUD confirmation.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status, connect, disconnect, playback_status, devices, search, play, pause, resume, next, previous, volume, queue, playlist_list, playlist_play, playlist_create, playlist_add"},
            "query": {"type": "STRING", "description": "For search/play/queue: track, artist or playlist search text"},
            "item_type": {"type": "STRING", "description": "For search/play: track (default), playlist or album"},
            "uri": {"type": "STRING", "description": "Optional Spotify URI for play/queue/playlist operations"},
            "device": {"type": "STRING", "description": "Optional exact Spotify Connect device name"},
            "volume": {"type": "INTEGER", "description": "For volume: 0–100"},
            "track_queries": {"type": "STRING", "description": "For playlist_create/playlist_add: one track search per line (maximum 25)"},
            "playlist_name": {"type": "STRING", "description": "For playlist_add/playlist_play: exact playlist name"},
            "playlist_uri": {"type": "STRING", "description": "For playlist_add/playlist_play: exact spotify:playlist URI"},
            "name": {"type": "STRING", "description": "For playlist_create: playlist name"},
            "description": {"type": "STRING", "description": "For playlist_create: optional description"},
            "public": {"type": "BOOLEAN", "description": "For playlist_create: true for public; defaults to false/private"},
        },
        "required": ["action"],
    },
}


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _token_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "MarkLVI" / "token_spotify.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "mark-lvi" / "token_spotify.json"


def _client_id() -> str:
    cfg = get_plugin_config("spotify")
    value = str(cfg.get("client_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{20,64}", value):
        raise ValueError(
            "Set the Spotify Developer App Client ID in Settings → Plugin Settings. "
            "No client secret is needed; MARK uses PKCE."
        )
    return value


def _save_token(data: dict) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(path)


def _read_token() -> dict:
    path = _token_path()
    if not path.is_file():
        raise RuntimeError("Spotify is not connected. Set your Client ID, then say 'connect Spotify'.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("The local Spotify token is unreadable. Disconnect and connect again.") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("The local Spotify token is invalid. Disconnect and connect again.")
    return data


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _oauth_connect() -> str:
    client_id = _client_id()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": " ".join(_SCOPES),
    }
    result: dict = {"code": "", "state": "", "error": ""}
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(parsed.query)
            result["code"] = (query.get("code") or [""])[0]
            result["state"] = (query.get("state") or [""])[0]
            result["error"] = (query.get("error") or [""])[0]
            body = (
                b"Spotify authorization finished. You can close this tab and return to MARK."
                if result["code"] else
                b"Spotify authorization was not completed. Return to MARK and try again."
            )
            self.send_response(200 if result["code"] else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    try:
        server = HTTPServer(("127.0.0.1", 8765), CallbackHandler)
    except OSError as exc:
        raise RuntimeError(
            "Cannot open Spotify's local OAuth callback on 127.0.0.1:8765. "
            "Close the program using that port and try again."
        ) from exc
    server.timeout = 1.0
    auth_url = _AUTH_URL + "?" + urlencode(params)
    try:
        opened = webbrowser.open(auth_url, new=1, autoraise=True)
        if not opened:
            print("[Spotify] Open this URL in your browser to connect:", auth_url)
        deadline = time.monotonic() + 180.0
        while not result["code"] and not result["error"] and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if result["state"] != state:
        raise RuntimeError("Spotify OAuth state verification failed; no token was saved.")
    if result["error"]:
        raise RuntimeError("Spotify authorization was denied or failed: " + result["error"][:100])
    if not result["code"]:
        raise RuntimeError("Spotify sign-in timed out after 3 minutes. Try connect again.")

    response = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": _REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TOKEN_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError("Spotify could not exchange the authorization code. Check the Client ID and exact redirect URI.")
    token = response.json()
    if not token.get("access_token") or not token.get("refresh_token"):
        raise RuntimeError("Spotify did not return the required tokens. Recheck the PKCE app configuration.")
    _save_token({
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "expires_at": time.time() + max(60, int(token.get("expires_in", 3600)) - 60),
        "scope": token.get("scope", ""),
        "token_type": "Bearer",
    })
    return "Spotify connected. The token is stored locally outside the MARK repository."


def _refresh_token(data: dict) -> dict:
    response = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
            "client_id": _client_id(),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TOKEN_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError("Spotify could not refresh the session. Say 'disconnect Spotify', then connect again.")
    updated = response.json()
    data["access_token"] = updated["access_token"]
    if updated.get("refresh_token"):
        data["refresh_token"] = updated["refresh_token"]
    data["expires_at"] = time.time() + max(60, int(updated.get("expires_in", 3600)) - 60)
    if updated.get("scope"):
        data["scope"] = updated["scope"]
    _save_token(data)
    return data


def _access_token() -> str:
    data = _read_token()
    if float(data.get("expires_at", 0)) <= time.time():
        data = _refresh_token(data)
    return str(data["access_token"])


def _api(method: str, path: str, *, params: dict | None = None,
         body: dict | None = None, retry_auth: bool = True):
    if not path.startswith("/") or ".." in path:
        raise ValueError("Invalid Spotify API path.")
    token = _access_token()
    response = requests.request(
        method.upper(), _API_ROOT + path,
        params=params,
        json=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        timeout=_API_TIMEOUT,
        allow_redirects=False,
    )
    if response.status_code == 401 and retry_auth:
        data = _refresh_token(_read_token())
        response = requests.request(
            method.upper(), _API_ROOT + path,
            params=params,
            json=body,
            headers={"Authorization": "Bearer " + data["access_token"], "Content-Type": "application/json"},
            timeout=_API_TIMEOUT,
            allow_redirects=False,
        )
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "a short time")
        raise RuntimeError(f"Spotify rate limit reached. Retry after {retry} seconds.")
    if response.status_code == 403:
        raise RuntimeError(
            "Spotify denied this action (403). Playback requires Spotify Premium. "
            "For Development Mode, the app owner needs Premium and your account must be on the app's allowed-user list; "
            "also check the granted scopes."
        )
    if response.status_code == 404:
        raise RuntimeError("Spotify could not find that item or active device. Open Spotify on a Connect device and retry.")
    if response.status_code not in {200, 201, 202, 204}:
        detail = ""
        try:
            detail = str((response.json().get("error") or {}).get("message") or "")
        except Exception:
            pass
        raise RuntimeError(f"Spotify API returned HTTP {response.status_code}" + (f": {detail[:180]}" if detail else "."))
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def _show(player, title: str, text: str) -> bool:
    if player is None:
        return False
    try:
        player.show_content(title, text[:_MAX_PREVIEW_CHARS])
        return True
    except Exception:
        return False


def _track_label(track: dict) -> str:
    name = str(track.get("name") or "Unknown track")[:160]
    artists = ", ".join(str(a.get("name") or "") for a in (track.get("artists") or []) if isinstance(a, dict))
    album = str((track.get("album") or {}).get("name") or "")
    return f"{name} — {artists[:240]}" + (f"\nAlbum: {album[:180]}" if album else "")


def _playlist_uri(value: object) -> str:
    raw = str(value or "").strip()
    if raw.startswith("https://open.spotify.com/playlist/"):
        raw = raw.split("/playlist/", 1)[1].split("?", 1)[0]
        raw = "spotify:playlist:" + raw
    if not _URI_RE.fullmatch(raw) or not raw.startswith("spotify:playlist:"):
        raise ValueError("Provide a valid Spotify playlist URI or URL.")
    return raw


def _resolve_device(name: object) -> str | None:
    requested = str(name or "").strip()
    if not requested:
        return None
    devices = _api("GET", "/me/player/devices").get("devices") or []
    exact = [d for d in devices if str(d.get("name") or "").casefold() == requested.casefold()]
    if len(exact) == 1:
        return str(exact[0].get("id") or "") or None
    if len(exact) > 1:
        raise ValueError("More than one Spotify device has that exact name. Rename one in Spotify and try again.")
    available = ", ".join(str(d.get("name") or "") for d in devices[:10])
    raise ValueError("No Connect device matches that exact name." + (f" Available: {available}" if available else " Open Spotify on a device first."))


def _search(query: object, item_type: object = "track") -> list[dict]:
    text = str(query or "").strip()
    kind = str(item_type or "track").strip().lower()
    if not text or len(text) > _MAX_SEARCH_TEXT or "\n" in text or "\r" in text:
        raise ValueError("Give one search phrase (up to 180 characters, no line breaks).")
    if kind not in {"track", "playlist", "album"}:
        raise ValueError("Search type must be track, playlist or album.")
    response = _api("GET", "/search", params={"q": text, "type": kind, "limit": 5})
    block = response.get(kind + "s") or {}
    items = block.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def _search_track_uris(raw_queries: object) -> tuple[list[str], list[str]]:
    raw = str(raw_queries or "").strip()
    queries = [line.strip() for line in raw.splitlines() if line.strip()]
    if not queries:
        raise ValueError("Give at least one track search, one per line.")
    if len(queries) > _MAX_TRACK_QUERIES:
        raise ValueError(f"A single playlist operation is limited to {_MAX_TRACK_QUERIES} tracks.")
    if any(len(q) > _MAX_SEARCH_TEXT for q in queries):
        raise ValueError("Each track search must be no longer than 180 characters.")
    uris, labels = [], []
    missing = []
    for query in queries:
        results = _search(query, "track")
        if not results or not results[0].get("uri"):
            missing.append(query)
            continue
        track = results[0]
        uris.append(str(track["uri"]))
        labels.append(_track_label(track))
    if missing:
        raise ValueError("No Spotify match for: " + "; ".join(missing[:5]))
    return uris, labels


def _find_playlist(name: object) -> tuple[str, str]:
    wanted = str(name or "").strip()
    if not wanted or len(wanted) > 100:
        raise ValueError("Give an exact playlist name.")
    matches = []
    for offset in (0, 50, 100):
        response = _api("GET", "/me/playlists", params={"limit": 50, "offset": offset})
        items = response.get("items") or []
        matches.extend(x for x in items if str(x.get("name") or "").casefold() == wanted.casefold())
        if len(items) < 50:
            break
    if not matches:
        raise ValueError("No playlist with that exact name was found in your Spotify library.")
    unique = {str(x.get("id")): x for x in matches if x.get("id")}
    if len(unique) != 1:
        raise ValueError("Several playlists have that exact name. List playlists in the HUD and use the exact playlist URI.")
    item = next(iter(unique.values()))
    return str(item["id"]), str(item.get("name") or wanted)


def _resolve_playlist(parameters: dict) -> tuple[str, str]:
    uri = str(parameters.get("playlist_uri") or "").strip()
    if uri:
        clean = _playlist_uri(uri)
        return clean.split(":")[-1], clean
    playlist_id, name = _find_playlist(parameters.get("playlist_name"))
    return playlist_id, name


def _playlist_list(player) -> str:
    response = _api("GET", "/me/playlists", params={"limit": 50, "offset": 0})
    rows = []
    for item in response.get("items") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        owner = str((item.get("owner") or {}).get("display_name") or "")
        rows.append(
            f"{item.get('name', '(untitled)')}\n"
            f"Owner: {owner or 'unknown'} | Tracks: {((item.get('items') or {}).get('total')) or 'n/a'}\n"
            f"URI: spotify:playlist:{item['id']}"
        )
    _show(player, "SPOTIFY PLAYLISTS", "\n\n".join(rows) or "No playlists returned by Spotify.")
    # Spotify catalog metadata must not be passed back to the language model.
    return "Your Spotify playlists are displayed in the MARK HUD."


def _devices(player) -> str:
    response = _api("GET", "/me/player/devices")
    rows = []
    for d in response.get("devices") or []:
        rows.append(
            f"{d.get('name', 'Unknown device')} — {d.get('type', 'unknown')} "
            f"| {'ACTIVE' if d.get('is_active') else 'available'} "
            f"| volume {d.get('volume_percent') if d.get('volume_percent') is not None else 'unknown'}%"
        )
    _show(player, "SPOTIFY CONNECT DEVICES", "\n".join(rows) or "No Spotify Connect devices are currently available.")
    return "Spotify Connect devices are displayed in the MARK HUD."


def _playback_status(player) -> str:
    state = _api("GET", "/me/player")
    if not state:
        _show(player, "SPOTIFY PLAYBACK", "No active Spotify playback was returned.")
        return "Spotify playback status is displayed in the MARK HUD."
    item = state.get("item") or {}
    device = state.get("device") or {}
    content = (
        _track_label(item) + "\n"
        f"State: {'playing' if state.get('is_playing') else 'paused'}\n"
        f"Device: {device.get('name') or 'unknown'}\n"
        f"Progress: {state.get('progress_ms') or 0} ms"
    )
    _show(player, "SPOTIFY PLAYBACK", content)
    return "Spotify playback details are displayed in the MARK HUD."


def _play_uri(uri: str, device_name: object = "") -> str:
    match = _URI_RE.fullmatch(uri)
    if not match:
        raise ValueError("Use a Spotify track, album or playlist URI.")
    kind = match.group(1)
    device_id = _resolve_device(device_name)
    params = {"device_id": device_id} if device_id else None
    body = {"uris": [uri]} if kind == "track" else {"context_uri": uri}
    _api("PUT", "/me/player/play", params=params, body=body)
    return "Spotify playback started."


def _choose_track_or_context(parameters: dict, player) -> str:
    uri = str(parameters.get("uri") or "").strip()
    query = str(parameters.get("query") or "").strip()
    if uri:
        uri = _playlist_uri(uri) if uri.startswith("spotify:playlist:") or "open.spotify.com/playlist/" in uri else uri
        _play_uri(uri, parameters.get("device"))
        return "Spotify playback started."
    if not query:
        raise ValueError("Give a track/playlist URI or a search phrase.")
    kind = str(parameters.get("item_type") or "track").strip().lower()
    items = _search(query, kind)
    if not items:
        raise ValueError("Spotify found no matching item.")
    selected = items[0]
    uri = str(selected.get("uri") or "")
    if not uri:
        raise RuntimeError("Spotify did not return a playable URI for the selected result.")
    _show(player, "SPOTIFY SEARCH MATCH", _track_label(selected) if kind == "track" else f"{selected.get('name', 'Selected item')}\n{selected.get('uri', '')}")
    _play_uri(uri, parameters.get("device"))
    # Do not pass Spotify catalog metadata to Gemini.
    return "Started the top Spotify match for your request."


def _queue_track(parameters: dict, player) -> str:
    uri = str(parameters.get("uri") or "").strip()
    if not uri:
        query = str(parameters.get("query") or "").strip()
        results = _search(query, "track")
        if not results:
            raise ValueError("Spotify found no matching track.")
        selected = results[0]
        uri = str(selected.get("uri") or "")
        _show(player, "SPOTIFY QUEUE MATCH", _track_label(selected))
    if not re.fullmatch(r"spotify:track:[A-Za-z0-9]{10,64}", uri):
        raise ValueError("Queue accepts a Spotify track URI.")
    _api("POST", "/me/player/queue", params={"uri": uri})
    return "Added the selected Spotify track to the playback queue."


def _prepare_playlist(parameters: dict, player, *, adding: bool):
    uris, labels = _search_track_uris(parameters.get("track_queries"))
    if adding:
        playlist_id, playlist_name = _resolve_playlist(parameters)
        return playlist_id, playlist_name, uris, labels
    name = str(parameters.get("name") or "").strip()
    description = str(parameters.get("description") or "").strip()
    public = parameters.get("public", False)
    if not name or len(name) > 100 or "\n" in name or "\r" in name:
        raise ValueError("Playlist name is required and must be one line, up to 100 characters.")
    if len(description) > 300:
        raise ValueError("Playlist description must be no longer than 300 characters.")
    if not isinstance(public, bool):
        raise ValueError("Playlist public must be true or false.")
    return name, description, uris, labels, public


def _create_playlist(name: str, description: str, uris: list[str], public: bool) -> str:
    created = _api("POST", "/me/playlists", body={
        "name": name,
        "description": description,
        "public": public,
        "collaborative": False,
    })
    playlist_id = str(created.get("id") or "")
    if not playlist_id:
        return "Spotify created no playlist ID; check your Spotify library."
    try:
        _api("POST", f"/playlists/{playlist_id}/items", body={"uris": uris})
    except Exception as exc:
        return (
            f"Spotify created the playlist, but could not add its tracks. "
            f"The playlist may be empty; reason: {str(exc)[:180]}"
        )
    return "Created the Spotify playlist and added the confirmed tracks."


def _add_to_playlist(playlist_id: str, uris: list[str]) -> str:
    _api("POST", f"/playlists/{playlist_id}/items", body={"uris": uris})
    return "Added the confirmed tracks to your Spotify playlist."


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "status").strip().lower()
    if action == "status":
        if _token_path().is_file():
            return "A Spotify token is stored locally. Use playback_status or devices to verify the connection."
        return "Spotify is not connected. Set your Spotify Client ID in Plugin Settings and say 'connect Spotify'."
    if action == "disconnect":
        try:
            existed = _token_path().exists()
            _token_path().unlink(missing_ok=True)
            return "Removed the local Spotify token." if existed else "No local Spotify token was found."
        except OSError as exc:
            return f"Could not remove the local Spotify token: {type(exc).__name__}."
    if action == "connect":
        try:
            return _oauth_connect()
        except Exception as exc:
            return f"Spotify connection failed: {str(exc)[:280]}"

    try:
        if action == "playback_status":
            return _playback_status(player)
        if action == "devices":
            return _devices(player)
        if action == "search":
            query = str(parameters.get("query") or "").strip()
            kind = str(parameters.get("item_type") or "track").strip().lower()
            items = _search(query, kind)
            labels = []
            for item in items:
                if kind == "track":
                    labels.append(_track_label(item))
                else:
                    labels.append(f"{item.get('name', 'Unknown')} — {item.get('uri', '')}")
            _show(player, f"SPOTIFY {kind.upper()} SEARCH", "\n\n".join(labels) or "No Spotify matches.")
            return "Spotify search results are displayed in the MARK HUD."
        if action == "play":
            return _choose_track_or_context(parameters, player)
        if action == "pause":
            _api("PUT", "/me/player/pause", params={"device_id": _resolve_device(parameters.get("device"))} if parameters.get("device") else None)
            return "Spotify playback paused."
        if action == "resume":
            _api("PUT", "/me/player/play", params={"device_id": _resolve_device(parameters.get("device"))} if parameters.get("device") else None)
            return "Spotify playback resumed."
        if action == "next":
            _api("POST", "/me/player/next", params={"device_id": _resolve_device(parameters.get("device"))} if parameters.get("device") else None)
            return "Skipped to the next Spotify track."
        if action == "previous":
            _api("POST", "/me/player/previous", params={"device_id": _resolve_device(parameters.get("device"))} if parameters.get("device") else None)
            return "Skipped to the previous Spotify track."
        if action == "volume":
            value = parameters.get("volume")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise ValueError("Spotify volume must be a whole number from 0 to 100.")
            params = {"volume_percent": value}
            if parameters.get("device"):
                params["device_id"] = _resolve_device(parameters.get("device"))
            _api("PUT", "/me/player/volume", params=params)
            return "Spotify device volume updated."
        if action == "queue":
            return _queue_track(parameters, player)
        if action == "playlist_list":
            return _playlist_list(player)
        if action == "playlist_play":
            if parameters.get("playlist_uri"):
                uri = _playlist_uri(parameters["playlist_uri"])
            else:
                playlist_id, _name = _find_playlist(parameters.get("playlist_name"))
                uri = "spotify:playlist:" + playlist_id
            return _play_uri(uri, parameters.get("device"))
        if action == "playlist_create":
            name, description, uris, labels, public = _prepare_playlist(parameters, player, adding=False)
            visibility = "public" if public else "private"
            detail = (
                f"Create {visibility} Spotify playlist: {name}\n"
                f"Description: {description or '(none)'}\n\n"
                "Exact matched tracks (these will be added):\n"
                + "\n".join(f"{i+1}. {label}" for i, label in enumerate(labels))
            )
            _show(player, "SPOTIFY PLAYLIST PREVIEW", detail)
            return confirm.request(
                key="spotify_playlist_create",
                title=f"Create Spotify playlist '{name}'?",
                detail=detail,
                run=lambda: _create_playlist(name, description, uris, public),
            )
        if action == "playlist_add":
            playlist_id, playlist_name, uris, labels = _prepare_playlist(parameters, player, adding=True)
            detail = (
                f"Add {len(uris)} matched tracks to Spotify playlist: {playlist_name}\n\n"
                + "\n".join(f"{i+1}. {label}" for i, label in enumerate(labels))
            )
            _show(player, "SPOTIFY PLAYLIST ADD PREVIEW", detail)
            return confirm.request(
                key="spotify_playlist_add",
                title=f"Add tracks to '{playlist_name}'?",
                detail=detail,
                run=lambda: _add_to_playlist(playlist_id, uris),
            )
    except Exception as exc:
        return f"Spotify {action} failed: {str(exc)[:300]}"
    return (
        "Choose Spotify action: status, connect, disconnect, playback_status, devices, search, "
        "play, pause, resume, next, previous, volume, queue, playlist_list, playlist_play, "
        "playlist_create or playlist_add."
    )
