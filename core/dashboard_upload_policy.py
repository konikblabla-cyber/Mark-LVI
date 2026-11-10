"""Pure quota checks for files received by the phone dashboard."""
from __future__ import annotations

from pathlib import Path

MAX_UPLOAD_TOTAL_MB = 2048
MAX_UPLOAD_FILES = 1000
MAX_UPLOAD_TOTAL_BYTES = MAX_UPLOAD_TOTAL_MB * 1024 * 1024


def upload_usage(directory: Path) -> tuple[int, int]:
    """Return (regular-file bytes, file count), refusing unsafe upload roots.

    Symlinks are never followed or counted. Any other scan/stat error is raised
    so callers fail closed instead of assuming that the upload folder is empty.
    """
    root = Path(directory)
    if root.is_symlink():
        raise ValueError("upload directory must not be a symbolic link")
    if not root.exists():
        return 0, 0
    if not root.is_dir():
        raise ValueError("upload path is not a directory")

    total_bytes = 0
    file_count = 0
    for item in root.iterdir():
        if item.is_symlink() or not item.is_file():
            continue
        try:
            size = item.stat().st_size
        except FileNotFoundError:
            continue
        if size < 0:
            raise OSError("upload file reported a negative size")
        total_bytes += size
        file_count += 1
    return total_bytes, file_count
