"""Safe, metadata-only snapshot of a user-selected Windows project folder.

This module never reads source contents or follows directory symlinks. It is
intended for proactive context and project orientation, not code execution.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "dist", "build", "target", "coverage",
}
_EXTENSIONS = {
    ".py": "Python", ".pyw": "Python", ".js": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".cs": "C#", ".cpp": "C++", ".c": "C", ".h": "C/C++",
    ".java": "Java", ".kt": "Kotlin", ".rs": "Rust", ".go": "Go",
    ".html": "Web", ".css": "Web", ".scss": "Web", ".vue": "Web",
    ".json": "Configuration", ".yaml": "Configuration", ".yml": "Configuration",
    ".md": "Documentation", ".sql": "SQL",
}
_ENTRY_FILES = (
    "main.py", "app.py", "__main__.py", "manage.py", "package.json",
    "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml", "Program.cs",
    "index.html", "index.js", "index.ts",
)


def _git_branch(root: Path) -> str:
    """Read only the local .git HEAD pointer; never invoke Git or inspect diffs."""
    git = root / ".git"
    try:
        if git.is_dir():
            head = git / "HEAD"
        elif git.is_file():
            # Worktrees store a gitdir pointer. Do not follow arbitrary paths.
            raw = git.read_text(encoding="utf-8", errors="replace").strip()
            prefix = "gitdir:"
            if not raw.lower().startswith(prefix):
                return ""
            relative = raw[len(prefix):].strip()
            if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                return ""
            head = root / relative / "HEAD"
        else:
            return ""
        value = head.read_text(encoding="utf-8", errors="replace").strip()
        if value.startswith("ref: refs/heads/"):
            return value[len("ref: refs/heads/"):][:80]
        return "detached"
    except OSError:
        return ""


def inspect_project_folder(path: object, max_files: int = 1500) -> dict | None:
    """Summarise structure and language counts for an explicitly selected folder."""
    try:
        raw = str(path or "").strip()
        if not raw:
            return None
        root = Path(raw).expanduser().resolve(strict=True)
        if not root.is_dir() or root == Path(root.anchor):
            return None
        limit = max(1, min(int(max_files), 5000))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

    counts: dict[str, int] = {}
    entries: list[str] = []
    total = 0
    truncated = False
    stack = [root]

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                children = list(iterator)
        except OSError:
            continue
        for item in children:
            try:
                if item.is_dir(follow_symlinks=False):
                    if item.name not in _SKIP_DIRS and not item.name.startswith("."):
                        stack.append(Path(item.path))
                    continue
                if not item.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue

            total += 1
            if total > limit:
                truncated = True
                stack.clear()
                break
            suffix = Path(item.name).suffix.lower()
            language = _EXTENSIONS.get(suffix)
            if language:
                counts[language] = counts.get(language, 0) + 1
            if item.name.lower() in {name.lower() for name in _ENTRY_FILES}:
                relative = Path(item.path).relative_to(root).as_posix()
                if len(entries) < 12:
                    entries.append(relative)

    languages = sorted(counts.items(), key=lambda row: (-row[1], row[0]))
    return {
        "project_name": root.name[:100],
        "file_count": min(total, limit),
        "truncated": truncated,
        "languages": languages[:6],
        "entry_points": sorted(entries)[:12],
        "git_branch": _git_branch(root),
    }


def remember_project_snapshot(snapshot: object) -> bool:
    """Persist a metadata-only note for a folder the user explicitly selected."""
    if not isinstance(snapshot, dict):
        return False
    name = str(snapshot.get("project_name") or "").strip()
    if not name:
        return False
    safe_key = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:36]
    if not safe_key:
        return False

    try:
        languages = snapshot.get("languages") or []
        language_text = ", ".join(
            str(label)[:30] for label, count in languages[:5]
            if isinstance(label, str) and isinstance(count, int) and count > 0
        )
        branch = str(snapshot.get("git_branch") or "")[:60]
        files = int(snapshot.get("file_count") or 0)
        value = f"User-selected project folder: {name[:80]}; files: {files}"
        if language_text:
            value += f"; technologies: {language_text}"
        if branch:
            value += f"; Git branch: {branch}"
        value += "; source code and absolute path were not stored."
        from memory.memory_manager import update_memory
        update_memory({"projects": {f"local_project_{safe_key}": value[:360]}})
        return True
    except Exception:
        return False


def format_project_context(snapshot: object) -> str:
    """Produce a short prompt fragment without disclosing the selected path."""
    if not isinstance(snapshot, dict):
        return ""
    name = str(snapshot.get("project_name") or "").strip()[:100]
    if not name:
        return ""
    languages = snapshot.get("languages") or []
    language_text = ", ".join(
        f"{str(label)[:40]} ({int(count)})"
        for label, count in languages[:5]
        if isinstance(label, str) and isinstance(count, int) and count >= 0
    )
    entries = snapshot.get("entry_points") or []
    entry_text = ", ".join(str(item)[:100] for item in entries[:6])
    branch = str(snapshot.get("git_branch") or "")[:80]
    parts = [f"Selected project folder: {name}."]
    if language_text:
        parts.append(f"Detected file types: {language_text}.")
    parts.append(f"Files inspected: {int(snapshot.get('file_count') or 0)}.")
    if branch:
        parts.append(f"Git branch: {branch}.")
    if entry_text:
        parts.append(f"Entry/config files: {entry_text}.")
    parts.append("Only file metadata was inspected; no source contents were read.")
    return "[LOCAL PROJECT CONTEXT]\n" + " ".join(parts)
