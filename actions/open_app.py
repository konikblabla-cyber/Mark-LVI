"""
JARVIS action — open an application, and know whether it actually opened.

WHAT WAS WRONG WITH THIS FILE
    It answered "Opened Spotify." whenever its launcher returned True, and on
    Windows the launcher returned True immediately after

        subprocess.Popen(app_name, shell=True)

    which succeeds because a SHELL started — not because an application did. A
    misspelling, an app that was never installed, an installer that moved:
    every one of them produced "Opened Spotify.", said out loud, while nothing
    appeared on screen.

    The same line was also a command injection. `shutil.which(name.split(".")[0])`
    passed "notepad.exe & whoami" because `which("notepad")` succeeds, and the
    whole string then went to a shell unquoted. Verified before it was changed.

WHAT IT DOES NOW
    * No shell, anywhere. Executables are resolved to a path and started as an
      argument vector; URI schemes go through the OS handler.
    * It watches the process table for a NEW process answering to the name, and
      only says `done` when one appears. When none does, it says `unverified`
      and the assistant tells the user it could not confirm — which is true,
      and is something they can act on.
    * It answers in milliseconds and verifies behind that, so asking to open
      something no longer costs the conversation one to four seconds.

WHY IT IS NOT A LIST OF APPLICATIONS
    Nothing here knows in advance what exists. Verification asks the running
    machine what appeared; matching compares against whatever is running. The
    alias map in core/appidentity.py covers the handful of names that differ
    BETWEEN OPERATING SYSTEMS and restricts nothing — every other name falls
    through untouched, which is what makes software nobody here has heard of
    work exactly as well as the software we have.
"""
import os
import platform
import shutil
import shlex
import subprocess
import threading
import time

from core import appidentity, outcome
from core.lazyimport import LazyModule, installed

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule("pyautogui")

_SYSTEM = platform.system()          # "Windows" | "Darwin" | "Linux"

# How long to keep watching for the application to appear. Chosen from the two
# ends of the range rather than the middle: a text editor is up in a fraction
# of a second, a game launcher can take ten, and a limit that suits the first
# reports the second as broken.
_LAUNCH_TIMEOUT = 12.0

# Nothing here is a keyword list. The Start-Menu / Spotlight fallback types the
# name and presses Enter, which is what a person does, and works in every
# language the desktop is running in.
_SEARCH_SETTLE = 0.8


# ─────────────────────────────────────────────────────────────────────────────
#  Per-OS launching — no shell on any path
# ─────────────────────────────────────────────────────────────────────────────
def _desktop_search(app_name: str) -> bool:
    """Type the name into the desktop's own search and press Enter.

    The last resort, and the only one that reaches Store apps, shortcuts and
    anything else with no executable on PATH. It is also the one that cannot be
    verified from its own return value — which is fine, because nothing here is
    trusted on its own any more; the process table decides.
    """
    if not _PYAUTOGUI:
        return False
    try:
        if _SYSTEM == "Darwin":
            pyautogui.hotkey("command", "space")
        else:
            pyautogui.press("win")
        time.sleep(_SEARCH_SETTLE)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(_SEARCH_SETTLE)
        pyautogui.press("enter")
        return True
    except Exception as exc:
        print(f"[open_app] desktop search failed: {exc}")
        return False


def _windows_executable_path(app_name: str):
    """Resolve a real Windows executable without shell parsing or guessed args."""
    launcher = str(appidentity.launcher_name(app_name) or app_name).strip()
    if not launcher or launcher.casefold().startswith(("ms-settings:", "shell:")):
        return None

    names = [launcher]
    if not launcher.casefold().endswith(".exe"):
        names.append(launcher + ".exe")
    for name in names:
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return found

    # Windows App Paths is the standard per-application executable registry.
    try:
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
        views = (
            getattr(winreg, "KEY_WOW64_64KEY", 0),
            getattr(winreg, "KEY_WOW64_32KEY", 0),
        )
        for name in names:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in views:
                    try:
                        with winreg.OpenKey(
                            hive, base + "\\" + name, 0,
                            getattr(winreg, "KEY_READ", 0) | view,
                        ) as key:
                            value, _ = winreg.QueryValueEx(key, "")
                        candidate = str(value).strip().strip('"')
                        if os.path.isfile(candidate) and candidate.casefold().endswith(".exe"):
                            return candidate
                    except (OSError, ValueError, TypeError):
                        continue
    except ImportError:
        pass

    # Browser vendors install per-user binaries outside PATH and App Paths.
    key = appidentity.fold(launcher)
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = []
    if key in {"opera", "operabrowser"}:
        candidates.extend([
            os.path.join(local, "Programs", "Opera", "launcher.exe"),
            os.path.join(local, "Programs", "Opera", "opera.exe"),
            os.path.join(roaming, "Opera Software", "Opera Stable", "launcher.exe"),
            os.path.join(roaming, "Opera Software", "Opera Stable", "opera.exe"),
            os.path.join(pf, "Opera", "launcher.exe"),
            os.path.join(pf, "Opera", "opera.exe"),
            os.path.join(pf86, "Opera", "launcher.exe"),
            os.path.join(pf86, "Opera", "opera.exe"),
        ])
    elif key == "operagx":
        candidates.extend([
            os.path.join(local, "Programs", "Opera GX", "launcher.exe"),
            os.path.join(local, "Programs", "Opera GX", "opera.exe"),
            os.path.join(roaming, "Opera Software", "Opera GX Stable", "launcher.exe"),
            os.path.join(roaming, "Opera Software", "Opera GX Stable", "opera.exe"),
            os.path.join(pf, "Opera GX", "launcher.exe"),
            os.path.join(pf, "Opera GX", "opera.exe"),
            os.path.join(pf86, "Opera GX", "launcher.exe"),
            os.path.join(pf86, "Opera GX", "opera.exe"),
        ])
    elif key in {"chrome", "googlechrome"}:
        candidates.extend([
            os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        ])
    elif key in {"msedge", "edge"}:
        candidates.extend([
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        ])
    elif key == "firefox":
        candidates.extend([
            os.path.join(pf, "Mozilla Firefox", "firefox.exe"),
            os.path.join(pf86, "Mozilla Firefox", "firefox.exe"),
        ])
    return next((path for path in candidates if os.path.isfile(path)), None)


def _windows_start_menu_shortcut(app_name: str):
    """Find a strong Start Menu shortcut match; refuse equally plausible apps."""
    launcher = str(appidentity.launcher_name(app_name) or app_name)
    queries = {appidentity.fold(app_name), appidentity.fold(launcher)}
    queries.discard("")
    roots = []
    for env_name in ("PROGRAMDATA", "APPDATA"):
        base = os.environ.get(env_name, "")
        if base:
            roots.append(os.path.join(
                base, "Microsoft", "Windows", "Start Menu", "Programs"
            ))

    ranked = []
    scanned = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            for current, _dirs, files in os.walk(root):
                for filename in files:
                    if not filename.casefold().endswith(".lnk"):
                        continue
                    scanned += 1
                    if scanned > 3000:
                        break
                    stem = appidentity.fold(os.path.splitext(filename)[0])
                    if not stem:
                        continue
                    score = 0
                    for query in queries:
                        if stem == query:
                            score = max(score, 100)
                        elif min(len(stem), len(query)) >= 4 and (
                            stem.startswith(query) or query.startswith(stem)
                        ):
                            score = max(score, 70)
                        elif min(len(stem), len(query)) >= 4 and (
                            query in stem or stem in query
                        ):
                            score = max(score, 50)
                    if score:
                        ranked.append((score, os.path.join(current, filename), stem))
                if scanned > 3000:
                    break
        except (OSError, PermissionError):
            continue

    if not ranked:
        return None, False
    best_score = max(item[0] for item in ranked)
    best = [item for item in ranked if item[0] == best_score]
    if len({item[2] for item in best}) > 1:
        return None, True
    # Same shortcut label in both menus is a duplicate, not a conflict.
    return best[0][1], False


def _launch_windows(app_name: str) -> bool:
    """Start a resolved app or a uniquely matched shortcut, never a shell command."""
    # URI schemes such as ms-settings: go through the Windows shell handler.
    scheme, _, rest = app_name.partition(":")
    if rest and scheme and scheme.isalnum() and len(scheme) > 1:
        try:
            os.startfile(app_name)               # noqa: S606 - ShellExecute, no command shell
            return True
        except Exception as exc:
            print(f"[open_app] startfile failed for {app_name!r}: {type(exc).__name__}")

    resolved = _windows_executable_path(app_name)
    if resolved:
        try:
            subprocess.Popen(
                [resolved],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
            return True
        except Exception as exc:
            print(f"[open_app] Popen failed for {resolved!r}: {type(exc).__name__}")

    shortcut, ambiguous = _windows_start_menu_shortcut(app_name)
    if ambiguous:
        print(f"[open_app] multiple Start Menu shortcuts match {app_name!r}; refusing to guess")
        return False
    if shortcut:
        try:
            os.startfile(shortcut)               # noqa: S606 - trusted .lnk from Start Menu
            return True
        except Exception as exc:
            print(f"[open_app] Start Menu shortcut failed: {type(exc).__name__}")

    # Last resort: native Start search, preserving Store/UWP app compatibility.
    return _desktop_search(app_name)


def _launch_macos(app_name: str) -> bool:
    for candidate in (app_name, f"{app_name}.app"):
        try:
            result = subprocess.run(
                ["open", "-a", candidate], capture_output=True, timeout=8
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            pass

    return _desktop_search(app_name)


_LINUX_TERMINALS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]


def _linux_launcher_command(app_name: str) -> list[str] | None:
    """Resolve a launcher command (including alias arguments) without a shell."""
    try:
        parts = shlex.split(str(app_name or ""))
    except ValueError:
        return None
    if not parts:
        return None
    executable = shutil.which(parts[0])
    if not executable:
        return None
    return [executable, *parts[1:]]

def _launch_linux(app_name: str) -> bool:
    # "terminal" means whichever one this distribution actually ships.
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINALS:
            path = shutil.which(term)
            if not path:
                continue
            try:
                subprocess.Popen(
                    [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                return True
            except Exception:
                continue

    # Cross-platform aliases may include arguments, e.g. "libreoffice --writer".
    # Resolve the executable only and keep each argument separate; never use a shell.
    command = _linux_launcher_command(app_name)
    if command:
        try:
            subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            pass

    binary = (
        shutil.which(app_name)
        or shutil.which(app_name.lower())
        or shutil.which(app_name.lower().replace(" ", "-"))
        or shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            pass

    for name in (
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ):
        try:
            result = subprocess.run(
                ["gtk-launch", name], capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            break
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["xdg-open", app_name], capture_output=True, timeout=5
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    return _desktop_search(app_name)


_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin": _launch_macos,
    "Linux": _launch_linux,
}


def _verification_name(spoken: str, launcher_name: str) -> str:
    """Use the resolved Linux executable name for process evidence of command aliases."""
    if _SYSTEM == "Linux":
        try:
            parts = shlex.split(str(launcher_name or ""))
        except ValueError:
            parts = []
        if len(parts) > 1 and shutil.which(parts[0]):
            return parts[0]
    return spoken

# ─────────────────────────────────────────────────────────────────────────────
#  HUD helpers — no-ops without a player, exactly as the whatsapp plugins do
# ─────────────────────────────────────────────────────────────────────────────
def _log(player, message: str) -> None:
    if player:
        try:
            player.write_log(message)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Launch + verify, off the conversation's thread
# ─────────────────────────────────────────────────────────────────────────────
def _focus_launched_application(match, attempts: int = 8) -> bool:
    """Wait briefly for a newly created process to expose its real window."""
    try:
        pid = int(match.pid)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False
    for attempt in range(max(1, min(int(attempts), 12))):
        try:
            if not appidentity.is_running(pid):
                return False
            if appidentity.focus(match):
                return bool(appidentity.is_running(pid))
        except Exception:
            return False
        if attempt + 1 < attempts:
            time.sleep(0.25)
    return False


def _launch_and_verify(player, spoken: str, launcher_name: str,
                       before: set[int], verification_name: str | None = None) -> None:
    """The part that takes seconds. Speaks ONLY when something needs saying.

    Silence means it worked: the window is on the user's screen and a second
    announcement over the top of it is noise — the same reasoning
    whatsapp_call.py applies to a phone that is already ringing.
    """
    try:
        launcher = _LAUNCHERS[_SYSTEM]
        requested = launcher(launcher_name)
        if not requested and launcher_name.lower() != spoken.lower():
            requested = launcher(spoken)

        appeared = appidentity.wait_for_launch(
            verification_name or spoken, before, timeout=_LAUNCH_TIMEOUT
        )
        if appeared is not None:
            # A process can exist before its window is ready. Wait briefly for
            # the exact new app window, but never claim success from PID alone.
            try:
                focused = _focus_launched_application(appeared)
            except Exception as verify_error:
                focused = False
                print(f"[open_app] foreground verification failed: {type(verify_error).__name__}")
            if focused:
                _log(player, f"JARVIS: {appeared.label} is open and in the foreground.")
                return
            trouble = (
                f"{spoken} started as a process, but MARK could not verify its "
                "window in the foreground. It may still be loading, hidden, or "
                "waiting behind another window; MARK will not claim it is open."
            )
            _log(player, f"JARVIS: {trouble}")
            _say(player, "Tell the user, in one short sentence in their own "
                         f"language: {trouble}")
            return

        if not requested:
            trouble = (f"{spoken} could not be started - nothing on this system "
                       f"answers to that name, so it may not be installed")
        else:
            trouble = (f"{spoken} was asked to start but no new application "
                       f"appeared within {int(_LAUNCH_TIMEOUT)} seconds, so it "
                       f"is NOT confirmed open - it may be slow to load, or not "
                       f"installed under that name")

        _log(player, f"JARVIS: {trouble}")
        _say(player, "Tell the user, in one short sentence in their own "
                     f"language: {trouble}")
    except Exception as exc:
        print(f"[open_app] {type(exc).__name__}: {exc}")
        _say(player, "Tell the user, in one short sentence in their own "
                     f"language: opening {spoken} failed - {exc}")


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────
def open_app(parameters=None, response=None, player=None,
             session_memory=None) -> str:
    spoken = ((parameters or {}).get("app_name") or "").strip()
    if not spoken:
        return outcome.failed("open_app", "no application name was given")

    if _SYSTEM not in _LAUNCHERS:
        return outcome.failed(
            "open_app", f"this operating system ({_SYSTEM}) is not supported",
            app=spoken,
        )

    # Already open? Then there is nothing to launch and nothing to wait for,
    # and saying so is more useful than a second copy. This is also the one
    # branch that can answer `done` immediately, because the evidence is
    # already in hand rather than somewhere in the next ten seconds.
    #
    # include_self, unlike the close path, and for the opposite reason. There
    # the assistant's own processes must never be a TARGET; here the question
    # is only "is this already running", and it is already running whether the
    # process belongs to us or not. Without this, asking to open the very
    # application the assistant lives inside reports `started` and goes off to
    # launch a second copy of it — seen exactly that way, against a packaged
    # build whose siblings the close guard had just learnt to protect.
    launcher_name = appidentity.launcher_name(spoken)
    verification_name = _verification_name(spoken, launcher_name)
    existing = appidentity.running(spoken, include_self=True)
    # On Linux, Office aliases launch a shared LibreOffice process (soffice).
    # Check the executable identity too, or we may start a second instance and
    # still fail to recognise the already-running Writer/Calc/Impress window.
    if not existing and verification_name != spoken:
        existing = appidentity.running(verification_name, include_self=True)
    if existing:
        best = existing[0]
        # ALREADY RUNNING IS NOT ALREADY DONE.
        #
        # This branch used to report "it was already open" and stop. That is
        # true and useless: an application running behind three other windows
        # is not on the user's screen, nothing changed when they asked, and
        # from where they are sitting the assistant did not open anything.
        # Reported as a bug in exactly those words — "it does not even open
        # applications" — with WhatsApp and Edge both running in the tray.
        #
        # Clicking a taskbar icon for a running app raises its window. So does
        # this. When there is no window to raise — a background service, or an
        # app that has gone to the tray — focus() says so and the launcher runs
        # below, which is what brings a tray app back.
        # Try every candidate, not just the best-scoring one.
        #
        # A modern application is many processes and only one of them owns the
        # window. Ranking puts the exact name match first, and on Windows that
        # is routinely a background service: measured here, `WhatsApp` and
        # `msedge` both scored 100 on processes with no window at all, so
        # focusing "the best match" focused nothing and the branch gave up.
        #
        # Store apps are the harder half of the same thing — their window
        # belongs to ApplicationFrameHost, not to the app's own process — which
        # is why failing all of these falls through to the launcher rather than
        # reporting failure. Typing the name into the Start Menu raises a
        # running Store app, and that is the path that ends up serving them.
        for candidate in existing[:8]:
            if appidentity.focus(candidate):
                _log(player, f"JARVIS: {candidate.label} brought to the front.")
                return outcome.done(
                    "open_app", app=spoken, matched=candidate.label,
                    already_running=True, focused=True, verified=True,
                )
        print(f"[open_app] {best.label} is running but no window could be "
              f"raised — launching it instead, which is what brings back an "
              f"app that is only in the tray.")

    before = appidentity.snapshot()
    _log(player, f"JARVIS: Opening {spoken}…")

    threading.Thread(
        target=_launch_and_verify,
        args=(player, spoken, launcher_name, before, verification_name),
        daemon=True, name=f"open-app-{spoken[:20]}",
    ).start()

    # Facts, not a sentence. Handing the model a finished English sentence is
    # an invitation to repeat it verbatim — whatsapp_call.py has the receipt:
    # a request made in Turkish came back in English, padded with an offer
    # nobody had made. The model writes the sentence, in the user's language.
    return outcome.started("open_app", app=spoken)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_app",
    "description": (
        "Opens an application on this computer. Use whenever the user asks to "
        "open, launch or start any app or program. Put the application's name "
        "in 'app_name' exactly as the user said it. "
        "On Windows, it resolves installed executables through App Paths, known browser locations, "
        "and unique Start Menu shortcuts before using desktop search. After launch it verifies both "
        "a matching process and that the application window is in the foreground. "
        "This tool answers immediately and confirms the launch in the "
        "background: on status=started say only that it is opening, never that "
        "it is open. If it does not appear, you will be told separately - "
        "relay that plainly rather than claiming success. Never say you opened "
        "something without calling this tool. "
        "To CLOSE an application use computer_control with action='close_app', "
        "not this tool. "
        "DO NOT use this tool to reach a WEB PAGE. 'open Chrome and go to "
        "YouTube', 'open YouTube', 'search the web' are all browser_control "
        "alone - it opens the browser itself. Calling open_app for the browser "
        "first and browser_control after it launches the browser TWICE, the "
        "second copy on a different profile, which is what puts a 'Who's using "
        "Chrome?' picker on the screen. Use this tool only when the user wants "
        "the application and nothing in it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": (
                    "The application's REAL name, not the user's shorthand. "
                    "Expand abbreviations and nicknames before passing them: "
                    "'wp' or 'whats' is 'WhatsApp', 'ig' is 'Instagram', 'vs "
                    "code' is 'Visual Studio Code'. A two-letter fragment "
                    "matches the wrong program - 'wp' finds WPS Office. Any "
                    "application on the machine works; there is no fixed list."
                ),
            }
        },
        "required": ["app_name"],
    },
    "handler": open_app,
}
