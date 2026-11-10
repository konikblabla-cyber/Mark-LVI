#computer_control.py
import io
import json
import ntpath
import platform
import re
import string
import subprocess
import sys

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}
import time
import random
from pathlib import Path

from core import appidentity, confirm, outcome
from core.lazyimport import LazyModule, installed

# pyautogui and pyperclip are imported on first USE, not at launch — see
# core/lazyimport.py for why and for what stays the same. The flags still mean
# "is it installed?" and still answer without importing anything.
_PYAUTOGUI = installed("pyautogui")
_PYPERCLIP = installed("pyperclip")


def _safe_print(message: object) -> None:
    """Write diagnostics without letting console codepages crash actions."""
    try:
        stream = sys.stdout
        encoding = getattr(stream, "encoding", None) or "utf-8"
        rendered = str(message).encode(encoding, errors="backslashreplace").decode(encoding)
        stream.write(rendered + "\n")
    except Exception:
        # Logging must never break a desktop action.
        pass



def _configure_pyautogui(module) -> None:
    """Settings that used to sit beside the import, run once when it happens."""
    module.FAILSAFE = True
    module.PAUSE    = 0.05


pyautogui = LazyModule("pyautogui", _configure_pyautogui)
pyperclip = LazyModule("pyperclip")

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return fallback

def _require_pyautogui():
    """Refuse early, with the sentence that says what to install.

    The import itself is the module `__getattr__`'s job; this stays exactly
    what it was — the check that turns a missing optional dependency into an
    instruction rather than a traceback.
    """
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _require_pyperclip() -> bool:
    """Is the clipboard usable? Returns a bool rather than raising, because
    every caller already has a hotkey fallback written for the case where it
    is not — turning that into an exception would break the path built to
    survive it."""
    return _PYPERCLIP

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}

def _type(text: str, interval: float = 0.03) -> str:
    _require_pyautogui()
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    _require_pyautogui()
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if len(text) > 20 and _require_pyperclip():
        pyperclip.copy(text)
        time.sleep(0.1)
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"

    pyautogui.typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _screen_point(x, y) -> tuple[int, int] | None:
    """Accept only explicit coordinates inside the current display bounds."""
    if x is None or y is None or isinstance(x, bool) or isinstance(y, bool):
        return None
    try:
        px, py = int(x), int(y)
        if float(x) != px or float(y) != py:
            return None
        width, height = pyautogui.size()
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    except Exception:
        return None
    if width <= 0 or height <= 0 or not (0 <= px < width and 0 <= py < height):
        return None
    return px, py


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    point = _screen_point(x, y)
    if point is None:
        return outcome.failed(
            "computer_control",
            "click requires explicit x/y coordinates inside the current screen; "
            "a click at the unknown current pointer position is refused",
        )
    button = str(button or "left").strip().lower()
    if button not in {"left", "right", "middle"} or clicks not in {1, 2}:
        return outcome.failed("computer_control", "unsupported mouse button or click count")
    px, py = point
    pyautogui.click(px, py, button=button, clicks=clicks)
    return f"{'Double-c' if clicks == 2 else 'C'}licked ({px}, {py}) [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    if (not keys or len(keys) > 4
            or any(not isinstance(k, str) or not k.strip() or len(k) > 24 for k in keys)):
        return outcome.failed("computer_control", "invalid or oversized hotkey")
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} ×{amount}"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    point = _screen_point(x, y)
    if point is None:
        return outcome.failed("computer_control", "mouse target is outside the current screen")
    px, py = point
    pyautogui.moveTo(px, py, duration=duration)
    return f"Mouse → ({px}, {py})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    _require_pyautogui()
    start = _screen_point(x1, y1)
    end = _screen_point(x2, y2)
    if start is None or end is None:
        return outcome.failed("computer_control", "drag endpoints must be inside the current screen")
    pyautogui.moveTo(*start, duration=0.2)
    pyautogui.dragTo(*end, duration=duration, button="left")
    return f"Dragged ({start[0]},{start[1]}) → ({end[0]},{end[1]})"


def _clipboard_get() -> str:
    if _require_pyperclip():
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "(copied — pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if _require_pyperclip():
        pyperclip.copy(text)
        time.sleep(0.1)
        _require_pyautogui()
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Pasted: {text[:60]}{'…' if len(text) > 60 else ''}"
    return "pyperclip not available"


def _screenshot(save_path: str | None = None) -> str:
    _require_pyautogui()
    path = _safe_screenshot_path(save_path)
    img  = pyautogui.screenshot()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    """Focus a uniquely identified running process without interpolating user text into shell code."""
    query = str(title or "").strip()
    if not query:
        return outcome.failed("computer_control", "no window or application name was supplied")
    try:
        matches = appidentity.running(query)
    except Exception as exc:
        return outcome.failed(
            "computer_control", f"could not safely inspect running applications ({type(exc).__name__})"
        )
    if not matches:
        return outcome.failed("computer_control", f"no visible running window matched '{query[:100]}'")

    best_score = matches[0].score
    best = [item for item in matches if item.score == best_score]
    if len(best) != 1:
        return outcome.ask(
            "computer_control",
            f"more than one window matches '{query[:80]}'; name the application or window more precisely",
            candidates=len(best),
        )

    match = best[0]
    try:
        focused = appidentity.focus(match)
    except Exception as exc:
        return outcome.failed("computer_control", f"window focus failed ({type(exc).__name__})")
    if not focused:
        return outcome.failed(
            "computer_control", f"could not verify focus for {match.label}", application=match.label
        )
    return outcome.done("computer_control", focused=match.label, verified=True)

def _close_app(app_name: str, confirmed_index: int | None = None) -> str:
    """Close the application the user named — that one, and never this one.

    THE BUG THIS REPLACES
        `computer_settings.close_app` was two lines:

            if _OS == "Darwin": pyautogui.hotkey("command", "q")
            else:               pyautogui.hotkey("alt", "f4")

        No target. It sent Alt+F4 to whatever window had focus, and while
        somebody is talking to the assistant the window with focus is very
        often the assistant — which is why people reported that asking it to
        close an app closed JARVIS itself. It also returned nothing at all, so
        the model announced a success it had no information about.

        The keystroke is not fixable by aiming it better. "The focused window"
        is the wrong idea: the user named an application, so the application is
        what should be found and asked to close.

    HOW THIS ONE BEHAVES
        * The name is matched against what is actually RUNNING — no list of
          applications, so anything on any machine in any country works.
        * The assistant's own process tree is never a candidate. When the only
          thing matching is the assistant, that is said out loud rather than
          obeyed.
        * Two equally good matches is a question, not a coin toss. Closing the
          wrong window loses somebody's unsaved work.
        * It asks politely — WM_CLOSE on Windows, SIGTERM elsewhere — so an
          application with unsaved changes gets to say so, and it NEVER
          escalates to a kill.
        * It then checks whether the process is gone, and answers `done` only
          if it is.
    """
    app_name = (app_name or "").strip()
    if not app_name:
        return outcome.failed("close_app", "no application name was given")

    if not appidentity._PSUTIL:
        return outcome.failed(
            "close_app",
            "psutil is not installed, so running applications cannot be found "
            "- run: pip install psutil",
            app=app_name,
        )

    matches = appidentity.running(app_name)

    if not matches:
        # Nothing, or nothing that is not us. Telling those two apart matters:
        # "it is not running" and "the only thing by that name is me" need
        # completely different sentences.
        including_self = appidentity.running(app_name, include_self=True)
        if any(m.is_self for m in including_self):
            return outcome.failed(
                "close_app",
                "the only thing matching that name is the assistant itself, "
                "and it will not close itself - if the user really wants the "
                "assistant closed they should close its window themselves",
                app=app_name,
            )
        return outcome.failed(
            "close_app",
            "no running application matches that name - it may already be "
            "closed, or be running under a different name",
            app=app_name,
        )

    # Group by process NAME, because twenty `opera.exe` are one application
    # with nineteen helpers, not twenty applications — and closing "Opera"
    # means all of them. `matches` is already best-first, so insertion order
    # here ranks the groups.
    by_name: dict[str, list] = {}
    for match in matches:
        by_name.setdefault(match.label, []).append(match)

    ranked = list(by_name.items())          # [(name, [matches...]), ...]
    target_name, group = ranked[0]

    # Two DIFFERENT applications that answer equally well is a question, not a
    # coin toss. Only when they tie: "discord" beating "discordcanary" on an
    # exact match is a clear winner and interrupting the user to confirm it
    # would be the assistant refusing to do an unambiguous thing.
    if len(ranked) > 1 and not confirmed_index:
        best_score = group[0].score
        tied = [name for name, ms in ranked if ms[0].score == best_score]
        if len(tied) > 1:
            return outcome.ask(
                "close_app",
                f"more than one application matches '{app_name}' equally well: "
                f"{', '.join(tied[:4])} - which one should be closed?",
                app=app_name, candidates=len(tied),
            )
        _safe_print(f"[ComputerControl] close_app: '{app_name}' -> {target_name} "
              f"(also matched: {', '.join(n for n, _ in ranked[1:5])})")

    closed, still_open, reason = 0, 0, ""
    for match in group:
        gone, detail = appidentity.request_close(match)
        if gone:
            closed += 1
        else:
            still_open += 1
            reason = reason or detail

    if closed and not still_open:
        return outcome.done(
            "close_app", app=app_name, closed=target_name,
            processes=closed, verified=True,
        )

    if closed and still_open:
        # Partly gone. Common and benign for multi-process applications: the
        # window is shut and a background helper is taking its time. Said as
        # `unverified` because that is exactly what it is.
        return outcome.unverified(
            "close_app",
            f"{closed} of {closed + still_open} processes closed; the rest are "
            f"still running - {reason}",
            app=app_name, closed=target_name,
        )

    return outcome.failed(
        "close_app", reason or "the application did not close",
        app=app_name, matched=target_name,
    )


def _screen_find(description: str) -> tuple[int, int] | None:
    api_key = _get_api_key()
    if not api_key:
        _safe_print("[ComputerControl] ⚠️ No API key for screen_find")
        return None

    try:
        from google import genai
        from google.genai import types as gtypes

        _require_pyautogui()
        w, h  = pyautogui.size()
        img   = pyautogui.screenshot()
        buf   = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        prompt = (
            f"This is a screenshot of a {w}×{h} pixel screen. "
            f"Locate the UI element described as: '{description}'. "
            f"Reply with ONLY the center coordinates as: x,y "
            f"If the element is not visible, reply: NOT_FOUND"
        )

        from core import gemini
        response = gemini.call(
            [gtypes.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
            tier=gemini.FAST, timeout_ms=20_000,
        )
        if response is None:
            return None

        text = (response.text or "").strip()
        if "NOT_FOUND" in text.upper():
            return None

        match = re.search(r"(\d+)\s*,\s*(\d+)", text)
        if match:
            return int(match.group(1)), int(match.group(2))

    except Exception as e:
        _safe_print(f"[ComputerControl] ⚠️ screen_find failed: {e}")

    return None

_INPUT_ACTIONS = {
    "type", "smart_type", "click", "left_click", "double_click",
    "right_click", "move", "drag", "hotkey", "press", "scroll",
    "copy", "paste", "screen_click", "clear_field",
}


def _app_process_token(value: object) -> str:
    """Compare executable basenames, accepting an optional .exe suffix."""
    name = ntpath.basename(str(value or "").strip()).casefold()
    stem, suffix = ntpath.splitext(name)
    return stem if suffix in {".exe", ".app"} else name


def _input_window_guard(target_app: str | None = None) -> str:
    """Allow input only when the explicitly named target is foreground on Windows."""
    if _get_os() != "windows":
        return ""
    expected = _app_process_token(target_app)
    if not expected:
        return outcome.failed(
            "computer_control",
            "target_app is required for desktop input; identify the intended application "
            "and pass its executable name (for example notepad.exe); no input was sent",
        )
    try:
        from core.desktop_context import get_desktop_context
        context = get_desktop_context(include_app=True)
        foreground = str(context.get("foreground_app") or "").strip()
        if not context.get("available") or not foreground:
            return outcome.failed(
                "computer_control",
                "foreground application could not be identified safely; no input was sent",
            )
        foreground_token = _app_process_token(foreground)
        own_exes = {
            _app_process_token(exe)
            for exe in appidentity.own_exes()
            if exe
        }
        own_exes.add(_app_process_token(sys.executable))
        if foreground_token in own_exes:
            return outcome.failed(
                "computer_control",
                "JARVIS is the active window; refusing synthetic input into its own interface",
                foreground_app=foreground,
            )
        if foreground_token != expected:
            return outcome.failed(
                "computer_control",
                "active application does not match the requested target; no input was sent",
                foreground_app=foreground,
                expected_app=str(target_app)[:100],
            )
        return ""
    except Exception as exc:
        return outcome.failed(
            "computer_control",
            f"foreground safety check failed ({type(exc).__name__}); no input was sent",
        )


def _run_confirmed_input(action, target_app: str | None = None):
    """Recheck the exact target application when delayed HUD approval is accepted."""
    blocked = _input_window_guard(target_app)
    return blocked if blocked else action()



def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates
      button        : 'left' | 'right' (default: left)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window
      target_app    : exact foreground process name for all synthetic input
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          — type text at cursor
      smart_type    — clear field + type (clipboard-backed)
      click         — left click
      double_click  — double left click
      right_click   — right click
      move          — move mouse
      drag          — click-drag between two points
      hotkey        — key combination
      press         — single key
      scroll        — scroll the wheel
      copy          — read clipboard
      paste         — write + paste clipboard
      screenshot    — capture screen (safe path only)
      wait          — sleep N seconds
      clear_field   — select-all + delete
      focus_window  — bring window to foreground
      screen_find   — AI element finder (returns x,y)
      screen_click  — AI element finder + click
      random_data   — generate fake form data
      user_data     — pull real data from memory
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified for computer_control."

    target_app = params.get("target_app")
    if action in _INPUT_ACTIONS:
        blocked = _input_window_guard(target_app)
        if blocked:
            return blocked

    if player:
        # Terminal, not the HUD: the activity log is for the conversation and
        # for problems, not a receipt for every keystroke. See
        # core/outcome.is_problem().
        _safe_print(f"[ComputerControl] {action}")

    _safe_print(f"[ComputerControl] action={action}")

    try:

        if action == "type":
            return _type(params.get("text", ""))

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action in ("click", "left_click"):
            return _click(params.get("x"), params.get("y"), "left", 1)

        if action == "double_click":
            return _click(params.get("x"), params.get("y"), "left", 2)

        if action == "right_click":
            return _click(params.get("x"), params.get("y"), "right", 1)

        if action == "move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "hotkey":
            raw = params.get("keys", "")
            keys = [k.strip().lower() for k in raw.split("+")] if isinstance(raw, str) else raw
            dangerous = {
                frozenset(("alt", "f4")), frozenset(("ctrl", "w")),
                frozenset(("ctrl", "q")), frozenset(("win", "l")),
                frozenset(("ctrl", "shift", "delete")),
                frozenset(("ctrl", "alt", "delete")),
                frozenset(("shift", "delete")),
                frozenset(("win", "r")),
            }
            if isinstance(keys, list) and frozenset(keys) in dangerous:
                return confirm.request(
                    key="computer_control.dangerous_hotkey",
                    title="Confirm potentially disruptive keyboard shortcut",
                    detail=f"Shortcut: {'+'.join(keys)}\\nIt may close the active app, lock Windows, or clear browser data. Confirm only if the active window and effect are intended.",
                    run=lambda: _run_confirmed_input(lambda: _hotkey(*keys), target_app),
                )
            return _hotkey(*keys) if isinstance(keys, list) else outcome.failed(
                "computer_control", "hotkey must be supplied as a plus-separated string"
            )

        if action == "press":
            key = str(params.get("key", "enter") or "enter").strip().lower()
            if key == "delete":
                return confirm.request(
                    key="computer_control.delete_key",
                    title="Confirm Delete key",
                    detail="The Delete key may remove the currently selected item. Confirm only after checking the active window and selection.",
                    run=lambda: _run_confirmed_input(lambda: _press(key), target_app),
                )
            return _press(key)

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            return _clipboard_get()

        if action == "paste":
            return _clipboard_paste(params.get("text", ""))

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "screen_click":
            desc   = params.get("description", "")
            coords = _screen_find(desc)
            if coords:
                time.sleep(0.2)
                # Screenshot analysis may take seconds. Recheck the exact foreground
                # process immediately before the click to avoid targeting a newly
                # focused window with coordinates from the previous screenshot.
                blocked = _input_window_guard(target_app)
                if blocked:
                    return blocked
                _click(x=coords[0], y=coords[1])
                return f"Clicked '{desc}' at {coords}"
            return f"Element not found on screen: '{desc}'"

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action in ("close_app", "close_application", "quit_app"):
            return _close_app(
                params.get("app_name") or params.get("text") or "",
                confirmed_index=params.get("confirmed"),
            )

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            _safe_print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                _safe_print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        _safe_print(f"[ComputerControl] ❌ {action}: {e}")
        return f"computer_control '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_control",
    "description": (
        "Direct computer control: type, click, hotkeys, scroll, move mouse. "
        "Every input action must include target_app, the exact foreground process "
        "name (for example notepad.exe); the action is refused if another app is active. "
        "screenshots, find elements on screen, and CLOSE an application. "
        "Use action='close_app' with 'app_name' whenever the user asks to "
        "close, quit or exit a program - 'close Spotify', 'quit Chrome'. "
        "Always name the application in 'app_name'; there is no action that "
        "closes 'whatever is in front', because that is usually the assistant. "
        "To OPEN an application use the open_app tool instead. "
        "Read the status the tool returns: 'done' means it was confirmed, "
        "'unverified' means it was requested and could NOT be confirmed - say "
        "so rather than claiming success - and 'ask' means nothing was done "
        "and the question must go to the user first."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Controls the computer: type, click, hotkeys, scroll, screenshots, and close_app to close a named application (put its name in 'app_name'). Use open_app to open one."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | close_app | screen_find | screen_click | random_data | user_data"
            },
            "app_name": {
                "type": "STRING",
                "description": (
                    "For close_app: the application to close, named as the "
                    "user said it. Any application running on the machine "
                    "works - there is no fixed list. Never leave this empty."
                )
            },
            "confirmed": {
                "type": "BOOLEAN",
                "description": (
                    "For close_app: set true only after the tool asked which "
                    "application was meant AND the user answered. Pass their "
                    "answer in 'app_name'."
                )
            },
            "target_app": {
                "type": "STRING",
                "description": "Required for every mouse/keyboard/clipboard action: exact target application's foreground process name, e.g. notepad.exe or chrome.exe. Do not guess; inspect the foreground app or focus the named app first."
            },
            "text": {
                "type": "STRING",
                "description": "Text to type or paste"
            },
            "x": {
                "type": "INTEGER",
                "description": "X coordinate"
            },
            "y": {
                "type": "INTEGER",
                "description": "Y coordinate"
            },
            "keys": {
                "type": "STRING",
                "description": "Key combination e.g. 'ctrl+c'"
            },
            "key": {
                "type": "STRING",
                "description": "Single key e.g. 'enter'"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down | left | right"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount (default: 3)"
            },
            "seconds": {
                "type": "NUMBER",
                "description": "Seconds to wait"
            },
            "title": {
                "type": "STRING",
                "description": "Window title for focus_window"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for screen_find/screen_click"
            },
            "type": {
                "type": "STRING",
                "description": "Data type for random_data"
            },
            "field": {
                "type": "STRING",
                "description": "Field for user_data: name|email|city"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": computer_control,
}
