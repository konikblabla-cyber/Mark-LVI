"""Atomic text writes with bounded, conflict-aware undo."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from core.undo import push_undo

UNDO_CONTENT_LIMIT = 1_000_000
MAX_SAFE_WRITE_BYTES = 2_000_000


def _signature(path: Path):
    """Identify the exact file written, including its content."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        digest = hashlib.sha256(path.read_bytes()).digest()
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, digest)
    except OSError:
        return None


def _atomic_replace(target: Path, content: bytes, mode: int | None = None) -> None:
    """Write beside the destination and atomically replace it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".mark-safe-write-", suffix=".tmp", dir=str(target.parent)
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def safe_write_text(path: Path, content: str, *,
                    undo_label: str = "wrote code",
                    undo_limit: int = UNDO_CONTENT_LIMIT,
                    expected_text: str | None = None,
                    verify_expected: bool = False) -> str:
    """Atomically write a file and register undo when the old file is small.

    Final-component symlinks and non-regular files are refused. A parent
    directory symlink is resolved once so the write destination is explicit.
    When verify_expected is true, the existing file must exactly match expected_text.
    Undo restores/removes only if the file still matches this exact write.
    """
    if not isinstance(content, str):
        raise ValueError("File content must be text.")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_SAFE_WRITE_BYTES:
        raise ValueError(
            f"Refusing to write {len(encoded)} bytes; the limit is {MAX_SAFE_WRITE_BYTES} bytes."
        )

    requested = Path(path).expanduser()
    if not requested.name or requested.name in {".", ".."}:
        raise ValueError("A file path is required.")

    requested.parent.mkdir(parents=True, exist_ok=True)
    target = requested.parent.resolve() / requested.name
    if target.is_symlink():
        raise ValueError("Refusing to write through a symbolic link.")
    if target.exists() and not target.is_file():
        raise ValueError("Refusing to replace a non-regular file.")

    previous = None
    previous_mode = None
    existed = target.exists()
    if verify_expected and not existed:
        raise FileNotFoundError("The file disappeared before the edit could be saved.")
    if existed:
        try:
            info = target.stat()
            previous_mode = stat.S_IMODE(info.st_mode)
            current_bytes = target.read_bytes() if (info.st_size <= undo_limit or verify_expected) else None
            if verify_expected and current_bytes != expected_text.encode("utf-8"):
                raise RuntimeError("The file changed while the model was working; newer contents were left untouched.")
            if info.st_size <= undo_limit:
                previous = current_bytes
        except OSError as exc:
            raise OSError("Could not safely back up the existing file; nothing was changed.") from exc
    elif verify_expected:
        raise FileNotFoundError("The file disappeared before the edit could be saved.")

    _atomic_replace(target, encoded, previous_mode)
    written_signature = _signature(target)

    if not existed or previous is not None:
        def undo_write() -> str:
            if target.is_symlink() or _signature(target) != written_signature:
                return f"'{target.name}' changed after the write; newer contents were left untouched."
            if previous is None:
                target.unlink()
                return f"Removed '{target.name}' — it did not exist before."
            _atomic_replace(target, previous, previous_mode)
            return f"Restored the previous contents of '{target.name}'."

        push_undo(undo_label, undo_write)
        return f"Saved to: {target} (undo available)"

    return (
        f"Saved to: {target}. The previous file exceeded {undo_limit} bytes, "
        "so the replacement was atomic but cannot be undone."
    )
