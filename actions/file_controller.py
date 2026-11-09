import os
import shutil
import platform
import stat
import tempfile
from pathlib import Path
from datetime import datetime

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

from core.undo import push_undo

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

# Undo keeps a file's previous contents in memory so `write` can be reversed.
# Above this size it does not — a 200 MB log would sit in RAM for the rest of
# the session to protect an edit nobody is going to take back.
_UNDO_CONTENT_LIMIT = 1_000_000


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _lexical_path(path: Path) -> Path:
    """Normalise a path without following a final symlink."""
    return Path(os.path.abspath(os.fspath(path)))


def _file_signature(path: Path):
    try:
        info = path.lstat()
        link = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, link)
    except OSError:
        return None


def _atomic_write_bytes(target: Path, content: bytes) -> None:
    """Replace one file atomically using a temporary sibling file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".mark-write-", suffix=".tmp", dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists() and not target.is_symlink():
            try:
                os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
            except OSError:
                pass
        os.replace(temp, target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _atomic_write_text(target: Path, content: str, append: bool = False) -> None:
    """Stream an append into a sibling temp file, then atomically replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".mark-write-", suffix=".tmp", dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as output:
            if append and target.exists():
                with target.open("rb") as previous:
                    shutil.copyfileobj(previous, output, length=1024 * 1024)
            output.write(content.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        if target.exists() and not target.is_symlink():
            try:
                os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
            except OSError:
                pass
        os.replace(temp, target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _copy_signature(target: Path):
    """Metadata manifest used to avoid deleting a copy changed after creation."""
    if not _path_exists(target):
        return None
    def entry(path: Path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return ("link", os.readlink(path), info.st_dev, info.st_ino)
        if stat.S_ISDIR(info.st_mode):
            return ("dir", info.st_dev, info.st_ino, info.st_mtime_ns)
        return ("file", info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    manifest = {".": entry(target)}
    if target.is_dir() and not target.is_symlink():
        for parent, dirs, names in os.walk(target, followlinks=False):
            current = Path(parent)
            for name in list(dirs) + names:
                child = current / name
                manifest[child.relative_to(target).as_posix()] = entry(child)
                if child.is_symlink() and name in dirs:
                    dirs.remove(name)
    return manifest


def _undo_move(src: Path, dst: Path):
    """Reverse of a move: put it back where it came from."""
    src, dst = _lexical_path(src), _lexical_path(dst)
    def _fn():
        if not _path_exists(dst):
            return f"'{dst.name}' is no longer there — nothing moved back."
        if _path_exists(src):
            return f"Cannot undo: '{src.name}' already exists at the original location; leaving both paths untouched."
        if not _is_safe_path(src) or not _is_safe_path(dst):
            return "Cannot undo: one of the paths is outside the allowed home directory."
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        return f"'{src.name}' is back in {src.parent.name}/."
    return _fn


def _undo_create(target: Path, expected_signature=None):
    """Reverse of a create: remove what we made — and only if we still made it.

    Deliberately refuses to touch a directory that has since been filled: the
    undo for 'create a folder' is not 'delete whatever ended up in it'."""
    def _fn():
        if not _path_exists(target):
            return f"'{target.name}' is already gone."
        if expected_signature is not None and _file_signature(target) != expected_signature:
            return f"'{target.name}' changed after creation — leaving it untouched."
        if target.is_dir():
            if any(target.iterdir()):
                return (f"'{target.name}' is not empty any more — "
                        f"leaving it alone rather than deleting your files.")
            target.rmdir()
        else:
            target.unlink()
        return f"Removed '{target.name}'."
    return _fn


def _undo_write(target: Path, previous: bytes | None, expected_signature=None):
    """Reverse of a write: restore the old contents, or remove a file that did
    not exist before the write created it."""
    def _fn():
        if expected_signature is not None and _file_signature(target) != expected_signature:
            return f"'{target.name}' changed after my write — leaving the newer contents untouched."
        if previous is None:
            if _path_exists(target):
                target.unlink()
                return f"Removed '{target.name}' — it did not exist before."
            return f"'{target.name}' is already gone."
        _atomic_write_bytes(target, previous)
        return f"Restored the previous contents of '{target.name}'."
    return _fn


def _restore_from_trash(original: Path) -> str:
    """Best-effort undelete.

    delete_file uses send2trash, which is the right call: the file lands in the
    Recycle Bin / Trash where the person can also find it themselves. Getting it
    back out again is shell work and only reliable on Windows, where pywin32 is
    already a dependency. Everywhere else this says where the file is instead of
    pretending it failed — the file is not lost either way."""
    if _OS == "Windows":
        try:
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
            bin_folder = shell.NameSpace(10)      # ssfBITBUCKET
            for item in bin_folder.Items():
                if str(bin_folder.GetDetailsOf(item, 1)).strip().lower() == \
                        str(original.parent).strip().lower():
                    if str(item.Name).strip().lower() == original.name.strip().lower():
                        item.InvokeVerb("UNDELETE")
                        return f"'{original.name}' restored from the Recycle Bin."
        except Exception as e:
            print(f"[file] Recycle Bin restore failed: {e}")
    return (f"'{original.name}' is in the Recycle Bin — I could not pull it back "
            f"automatically, but it is there and can be restored by hand.")


_SAFE_ROOTS: list[Path] = [
    Path.home(),
]

def _is_safe_path(target: Path) -> bool:
    """Is the given path inside _SAFE_ROOTS? If not, reject the operation."""
    try:
        resolved = target.resolve()
        return any(
            resolved == root.resolve() or resolved.is_relative_to(root.resolve())
            for root in _SAFE_ROOTS
        )
    except Exception:
        return False

def _get_desktop() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

def _get_downloads() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Downloads"

def _get_documents() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOCUMENTS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Documents"

def _get_pictures() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_PICTURES_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Pictures"

def _get_music() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_MUSIC_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Music"

def _get_videos() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_VIDEOS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Videos"


def _resolve_path(raw: str) -> Path:
    shortcuts: dict[str, Path] = {
        "desktop":   _get_desktop(),
        "downloads": _get_downloads(),
        "documents": _get_documents(),
        "pictures":  _get_pictures(),
        "music":     _get_music(),
        "videos":    _get_videos(),
        "home":      Path.home(),
    }
    raw   = raw.strip().strip('"').strip("'")
    lower = raw.lower()
    if lower in shortcuts:
        return shortcuts[lower]

    # "desktop/notes/a.md" and "desktop\notes\a.md" — a shortcut followed by a
    # sub-path.  Without this branch the whole string falls through to the
    # relative-path return below and is resolved against the process CWD instead
    # of the real Desktop: an "Access denied" when the project lives outside the
    # home directory, or — worse — a silent write into a stray "desktop" folder
    # inside the project when it lives inside it.
    head, sep, rest = raw.replace("\\", "/").partition("/")
    if sep and head.lower() in shortcuts:
        rest = rest.strip("/")
        return shortcuts[head.lower()] / rest if rest else shortcuts[head.lower()]

    return Path(raw).expanduser()

def _format_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _safe_trash(target: Path) -> str:

    if not _SEND2TRASH:
        return (
            "send2trash is not installed. "
            "Run: pip install send2trash — "
            "Permanent deletion is disabled for safety."
        )
    send2trash.send2trash(str(target))
    return f"Moved to Trash: {target.name}"


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    try:
        target = _resolve_path(path)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Path not found: {target}"
        if not target.is_dir():
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return f"Directory is empty: {target.name}/"

        return f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing files: {e}"


def create_file(path: str, name: str = "", content: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target = target.resolve(strict=False)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if _path_exists(target):
            return f"A file named '{target.name}' already exists; I will not overwrite it."
        _atomic_write_bytes(target, str(content).encode("utf-8"))
        push_undo(f"created {target.name}", _undo_create(target, _file_signature(target)))
        return f"File created: {target.name}"
    except Exception as e:
        return f"Could not create file: {e}"


def create_folder(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        already = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        # Only offer to undo a folder we actually made. "mkdir -p" on something
        # that was already there is not a change, and undoing it would delete a
        # directory the user has had for years.
        if not already:
            push_undo(f"created folder {target.name}", _undo_create(target))
        return f"Folder created: {target.name}"
    except Exception as e:
        return f"Could not create folder: {e}"


def delete_file(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if target.is_symlink():
            return "Deleting symbolic links is disabled; choose the in-root target explicitly."
        if not target.exists():
            return f"Not found: {target.name}"

        # Safe-directory check — protect critical user folders
        protected = {
            _get_desktop(), _get_downloads(), _get_documents(),
            _get_pictures(), _get_music(), _get_videos(), Path.home()
        }
        if target.resolve() in {p.resolve() for p in protected}:
            return f"Protected directory, cannot delete: {target.name}"

        original = target.resolve()
        result   = _safe_trash(target)
        if result.startswith("Moved to Trash"):
            push_undo(f"deleted {original.name}",
                      lambda p=original: _restore_from_trash(p))
        return result

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Could not delete: {e}"


def move_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base   = _resolve_path(path)
        src    = (base / name) if name else base
        dst    = _resolve_path(destination) if destination else None

        if not _path_exists(src):
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if src.is_symlink():
            return "Moving symbolic links is disabled; move the link manually so its target cannot change unexpectedly."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"
        if dst.is_dir():
            dst = dst / src.name
        dst = _lexical_path(dst)
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"
        if _path_exists(dst):
            return f"Destination already exists: {dst}; nothing was moved."
        if src.is_dir():
            try:
                if dst.resolve(strict=False).is_relative_to(src.resolve()):
                    return "Cannot move a folder inside itself."
            except Exception:
                return "Could not safely compare source and destination paths."
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"
        origin = _lexical_path(src)
        shutil.move(str(src), str(dst))
        push_undo(f"moved {origin.name} to {dst.parent.name}/", _undo_move(origin, dst))
        return f"Moved: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not move: {e}"


def copy_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base = _resolve_path(path)
        src  = (base / name) if name else base
        dst  = _resolve_path(destination) if destination else None

        if not _path_exists(src):
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if src.is_symlink():
            return "Copying a symbolic link is disabled; choose its in-root target explicitly."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"
        if dst.is_dir():
            dst = dst / src.name
        dst = _lexical_path(dst)
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"
        if _path_exists(dst):
            return f"Destination already exists: {dst}; nothing was copied."
        if src.is_dir():
            try:
                if dst.resolve(strict=False).is_relative_to(src.resolve()):
                    return "Cannot copy a folder into itself."
            except Exception:
                return "Could not safely compare source and destination paths."
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"
        if src.is_dir():
            shutil.copytree(str(src), str(dst), symlinks=True)
            unsafe_links = [p for p in dst.rglob("*") if p.is_symlink() and not _is_safe_path(p)]
            if unsafe_links:
                shutil.rmtree(dst)
                return "Copy refused: the folder contains a symbolic link pointing outside the allowed home directory."
        else:
            shutil.copy2(str(src), str(dst))
        _copy = _lexical_path(dst)
        signature = _copy_signature(_copy)
        def _undo_copy():
            if not _path_exists(_copy):
                return f"The copy '{_copy.name}' is already gone."
            if _copy_signature(_copy) != signature:
                return f"The copy '{_copy.name}' changed after creation — leaving it untouched."
            if _copy.is_dir() and not _copy.is_symlink():
                shutil.rmtree(_copy)
            else:
                _copy.unlink()
            return f"Removed the unchanged copy in {_copy.parent.name}/."
        push_undo(f"copied {src.name} to {dst.parent.name}/", _undo_copy)

        return f"Copied: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not copy: {e}"


def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    try:
        base     = _resolve_path(path)
        target   = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not _path_exists(target):
            return f"Not found: {target.name}"
        if target.is_symlink():
            return "Renaming symbolic links is disabled; choose the link target explicitly."
        if not new_name:
            return "No new name provided."
        if (new_name in {".", ".."} or "/" in new_name or "\\" in new_name
                or Path(new_name).is_absolute() or "\x00" in new_name):
            return "Invalid new name: use one plain file or folder name without a path."
        new_path = _lexical_path(target.parent / new_name)
        if not _is_safe_path(new_path):
            return f"Access denied: {new_path}"
        if _path_exists(new_path):
            return f"A file named '{new_name}' already exists here."

        old_path = _lexical_path(target)
        target.rename(new_path)
        push_undo(f"renamed {old_path.name} to {new_name}", _undo_move(old_path, new_path))
        return f"Renamed: {target.name} → {new_name}"

    except Exception as e:
        return f"Could not rename: {e}"


def read_file(path: str, name: str = "", max_chars: int = 4000) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"File not found: {target.name}"
        if not target.is_file():
            return f"Not a file: {target.name}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} total chars]"
        return content

    except Exception as e:
        return f"Could not read file: {e}"


def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target = target.resolve(strict=False)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if target.exists() and not target.is_file():
            return f"Not a regular file: {target.name}"
        previous: bytes | None = None
        undoable = True
        if target.exists():
            try:
                if target.stat().st_size > _UNDO_CONTENT_LIMIT:
                    undoable = False
                else:
                    previous = target.read_bytes()
            except Exception:
                return "Could not create a safe backup of the existing file; nothing was changed."
        _atomic_write_text(target, str(content), append=bool(append))
        expected = _file_signature(target)
        action = "Appended to" if append else "Written to"
        if undoable:
            push_undo(f"wrote to {target.name}", _undo_write(target, previous, expected))
            return f"{action}: {target.name}"
        return (f"{action}: {target.name}. "
                f"(The previous file exceeded {_UNDO_CONTENT_LIMIT} bytes, so this change is atomic but cannot be undone.)")
    except Exception as e:
        return f"Could not write file: {e}"


def find_files(name: str = "", extension: str = "",
               path: str = "home", max_results: int = 20) -> str:
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Search path not found: {path}"

        results    = []
        dir_count  = 0
        max_dirs   = 500  # performance + safety limit

        for item in search_path.rglob("*"):
            if item.is_dir():
                dir_count += 1
                if dir_count > max_dirs:
                    break
                continue
            if not item.is_file():
                continue
            if extension and item.suffix.lower() != extension.lower():
                continue
            if name and name.lower() not in item.name.lower():
                continue
            size = _format_size(item.stat().st_size)
            results.append(f"📄 {item.name} ({size}) — {item.parent}")
            if len(results) >= max_results:
                break

        if not results:
            query = name or extension or "files"
            return f"No {query} found in {search_path.name}/"

        return f"Found {len(results)} file(s):\n" + "\n".join(results)

    except Exception as e:
        return f"Search error: {e}"


def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    count = min(count, 50)  # maksimum 50
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    try:
        target = _resolve_path(path)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        usage  = shutil.disk_usage(target)
        pct    = usage.used / usage.total * 100
        return (
            f"Disk usage ({target}):\n"
            f"  Total : {_format_size(usage.total)}\n"
            f"  Used  : {_format_size(usage.used)} ({pct:.1f}%)\n"
            f"  Free  : {_format_size(usage.free)}"
        )
    except Exception as e:
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    type_map = {
        "Images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                      ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
        "Videos":    {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
        "Music":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
        "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
        "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
    }

    desktop = _get_desktop()
    moved, skipped = [], []
    journal: list[tuple[Path, Path]] = []   # (where it was, where it went)

    try:
        if not _is_safe_path(desktop) or not desktop.is_dir():
            return f"Access denied: {desktop}"
        for item in desktop.iterdir():
            # Do not relocate symlinks: their relative targets and undo paths
            # can change meaning when the link is moved.
            if item.is_symlink():
                skipped.append(item.name)
                continue
            # Leave folders, hidden files and organize-folders untouched
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name in {k for k in type_map}:
                continue

            ext        = item.suffix.lower()
            target_dir = desktop / "Others"
            for folder, exts in type_map.items():
                if ext in exts:
                    target_dir = desktop / folder
                    break

            if not _is_safe_path(target_dir):
                skipped.append(item.name)
                continue
            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name
            if not _is_safe_path(target_dir) or not _is_safe_path(new_path):
                skipped.append(item.name)
                continue

            if new_path.exists():
                skipped.append(item.name)
                continue

            origin = item.resolve()
            shutil.move(str(item), str(new_path))
            journal.append((origin, new_path.resolve()))
            moved.append(f"{item.name} → {target_dir.name}/")

        # One command, dozens of moves — so one undo that reverses all of them.
        # Without this, "organize my desktop" is the single least reversible
        # thing the assistant can do to a person's files, and it was completely
        # ungated.
        if journal:
            def _undo_organize(entries=tuple(journal)):
                restored = 0
                for origin, moved_to in entries:
                    try:
                        if moved_to.exists() and not _path_exists(origin):
                            if _is_safe_path(moved_to) and _is_safe_path(origin):
                                origin.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(moved_to), str(origin))
                                restored += 1
                    except Exception as e:
                        print(f"[file] undo organize: {moved_to.name}: {e}")
                # Clear away the folders we created, but only while they are
                # empty — anything the user put in since stays.
                for folder in {m.parent for _o, m in entries}:
                    try:
                        if folder.exists() and folder.is_dir() and not any(folder.iterdir()):
                            folder.rmdir()
                    except Exception:
                        pass
                return f"{restored} file(s) put back on the desktop."
            push_undo(f"organized the desktop ({len(journal)} files)", _undo_organize)

        result = f"Desktop organized: {len(moved)} files moved."
        if moved:
            preview = moved[:8]
            result += "\n" + "\n".join(preview)
            if len(moved) > 8:
                result += f"\n... and {len(moved) - 8} more."
        if skipped:
            result += f"\n{len(skipped)} item(s) skipped (unsafe link or name conflict)."
        return result

    except Exception as e:
        return f"Could not organize desktop: {e}"


def get_file_info(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        stat = target.stat()
        info = {
            "Name":      target.name,
            "Type":      "Folder" if target.is_dir() else "File",
            "Size":      _format_size(stat.st_size),
            "Location":  str(target.parent),
            "Created":   datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "—",
        }
        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        return f"Could not get file info: {e}"

def _file_controller_execute(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    path   = params.get("path", "desktop")
    name   = params.get("name", "")

    if player:
        print(f"[File] {action} {name or path}")  # terminal, not the HUD

    try:
        if action == "list":
            return list_files(path)

        elif action == "create_file":
            return create_file(path, name=name, content=params.get("content", ""))

        elif action == "create_folder":
            return create_folder(path, name=name)

        elif action == "delete":
            return delete_file(path, name=name)

        elif action == "move":
            return move_file(path, name=name, destination=params.get("destination", ""))

        elif action == "copy":
            return copy_file(path, name=name, destination=params.get("destination", ""))

        elif action == "rename":
            return rename_file(path, name=name, new_name=params.get("new_name", ""))

        elif action == "read":
            return read_file(path, name=name)

        elif action == "write":
            return write_file(
                path, name=name,
                content=params.get("content", ""),
                append=params.get("append", False)
            )

        elif action == "find":
            return find_files(
                name=name or params.get("name", ""),
                extension=params.get("extension", ""),
                path=path,
                max_results=min(int(params.get("max_results", 20)), 50),
            )

        elif action == "largest":
            return get_largest_files(
                path=path,
                count=int(params.get("count", 10)),
            )

        elif action == "disk_usage":
            return get_disk_usage(path)

        elif action == "organize_desktop":
            return organize_desktop()

        elif action == "info":
            return get_file_info(path, name=name)

        else:
            return f"Unknown action: '{action}'"

    except Exception as e:
        return f"File controller error ({action}): {e}"


def _delete_target_was_explicitly_requested(request: str, path: str, name: str) -> bool:
    """Bind a delete authorization to both the named item and its containing folder."""
    from core.autonomy_policy import explicitly_requested_target_action
    import re

    request = str(request or "")
    name = str(name or "").strip()
    if not name or not explicitly_requested_target_action(request, name):
        return False
    try:
        actual = _resolve_path(path).resolve(strict=False)
    except Exception:
        return False

    locations = {
        "desktop": _get_desktop(), "pulpit": _get_desktop(), "pulpitu": _get_desktop(),
        "downloads": _get_downloads(), "download": _get_downloads(),
        "pobrane": _get_downloads(), "pobranych": _get_downloads(),
        "documents": _get_documents(), "document": _get_documents(),
        "dokumenty": _get_documents(), "dokumentów": _get_documents(),
        "pictures": _get_pictures(), "images": _get_pictures(),
        "obrazy": _get_pictures(), "zdjęcia": _get_pictures(), "zdjecia": _get_pictures(),
        "music": _get_music(), "muzyka": _get_music(),
        "videos": _get_videos(), "video": _get_videos(), "wideo": _get_videos(),
    }
    tokens = {token.casefold() for token in re.findall(r"[^\W_]+", request, re.UNICODE)}
    mentioned = {alias for alias in locations if alias in tokens}
    if mentioned:
        for alias in mentioned:
            root = locations[alias].resolve(strict=False)
            if actual == root or actual.is_relative_to(root):
                return True
        return False

    # With no folder named, a bare filename is scoped to the default Desktop.
    if actual == _get_desktop().resolve(strict=False):
        return True

    # Custom folders are authorized only when their own name was included.
    folder_name = actual.name.casefold()
    return len(folder_name) > 1 and folder_name in tokens


def _file_confirmation(parameters: dict, session_memory=None, player=None):
    """Gate unrequested deletion, bulk desktop moves, and non-undoable overwrites."""
    from core import confirm
    from core.autonomy_policy import requires_ui_confirmation, scope_request
    import re
    params = parameters or {}
    action = str(params.get("action") or "").strip().lower()
    path = str(params.get("path") or "desktop")
    name = str(params.get("name") or "").strip()
    request = scope_request(session_memory)
    if action == "delete":
        target = name or path
        explicit_target = _delete_target_was_explicitly_requested(request, path, name)
        if requires_ui_confirmation("delete " + target, user_request=request) or not explicit_target:
            return confirm.request(
                key="file_delete", title=("Move to Trash: " + target)[:120],
                detail=("File action: delete (move to Trash)\nTarget: " + target +
                        "\nFolder: " + path +
                        "\n\nThis file or folder will be moved to the system Trash. "
                        "Confirm only if this exact target should be removed.")[:1800],
                run=lambda: _file_controller_execute(
                    params, response=None, player=player, session_memory=session_memory),
            )
    elif action == "organize_desktop":
        action_requested = bool(re.search(
            r"\b(?:organize|organise|sort|tidy|uporządkuj|uporzadkuj|"
            r"porządkuj|porzadkuj|posortuj|sortuj|posprzątaj|posprzataj)\b", request, re.IGNORECASE))
        desktop_named = bool(re.search(r"\b(?:desktop|pulpit)\b", request, re.IGNORECASE))
        if not (action_requested and desktop_named):
            return confirm.request(
                key="file_organize_desktop", title="Organize Desktop files",
                detail=("This can move multiple Desktop files into type-based folders. "
                        "It is reversible with Undo, but changes many paths. "
                        "Confirm only if the desktop should be organized now."),
                run=lambda: _file_controller_execute(
                    params, response=None, player=player, session_memory=session_memory),
            )
    elif action == "write":
        target = _resolve_path(path)
        if name:
            target = target / name
        try:
            target = target.resolve(strict=False)
            if (_is_safe_path(target) and target.is_file()
                    and target.stat().st_size > _UNDO_CONTENT_LIMIT):
                size = _format_size(target.stat().st_size)
                return confirm.request(
                    key="file_large_overwrite",
                    title=("Overwrite large file: " + target.name)[:120],
                    detail=(f"File: {target}\nSize: {size}\n\n"
                            "This file exceeds the in-memory Undo backup limit. "
                            "The write is atomic, but Undo cannot reverse it. "
                            "Confirm this overwrite explicitly.")[:1800],
                    run=lambda: _file_controller_execute(
                        params, response=None, player=player, session_memory=session_memory),
                )
        except OSError:
            pass
    return None


def file_controller(parameters: dict = None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    try:
        pending = _file_confirmation(params, session_memory=session_memory, player=player)
        if pending is not None:
            return pending
    except Exception as exc:
        action = str(params.get("action") or "").strip().lower()
        if action in {"delete", "organize_desktop", "write"}:
            return (f"File action blocked: safety confirmation could not be completed "
                    f"({type(exc).__name__}). Nothing was changed.")
        return f"File safety check failed ({type(exc).__name__}). Nothing was changed."
    return _file_controller_execute(
        params, response=response, player=player, session_memory=session_memory)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "file_controller",
    "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"
            },
            "path": {
                "type": "STRING",
                "description": "File/folder path or shortcut: desktop, downloads, documents, home"
            },
            "destination": {
                "type": "STRING",
                "description": "Destination path for move/copy"
            },
            "new_name": {
                "type": "STRING",
                "description": "New name for rename"
            },
            "content": {
                "type": "STRING",
                "description": "Content for create_file/write"
            },
            "name": {
                "type": "STRING",
                "description": "File name to search for"
            },
            "extension": {
                "type": "STRING",
                "description": "File extension to search (e.g. .pdf)"
            },
            "count": {
                "type": "INTEGER",
                "description": "Number of results for largest"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": file_controller,
}
