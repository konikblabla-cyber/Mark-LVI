"""Local Obsidian-compatible Markdown knowledge vault.

All paths are relative to the configured vault. The plugin never follows a path
outside that directory and never executes note content. Note text is returned
as untrusted data so prompt-injection text inside a note stays data.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from memory.config_manager import get_plugin_config
from core.undo import push_undo

_DEFAULT_VAULT = Path.home() / "Documents" / "JarvisVault"
_MAX_NOTE_BYTES = 256_000
_MAX_SEARCH_FILES = 500


PLUGIN_SETTINGS = {
    "namespace": "knowledge_vault",
    "title": "Obsidian-compatible Knowledge Vault",
    "fields": [
        {"key": "vault_path", "label": "Vault folder", "type": "text",
         "default": str(_DEFAULT_VAULT),
         "placeholder": "A local folder for Markdown notes"},
    ],
}


PLUGIN = {
    "name": "knowledge_vault",
    "description": (
        "A local Markdown knowledge base compatible with Obsidian. Use it to save project notes, "
        "research summaries, decisions and reference material, or to search and retrieve existing "
        "notes. Actions: list, search, read, create or append. Use relative note paths only. "
        "Do not copy note contents to the web or another service unless the user explicitly asks."
    ),
    "tier": "core",
    "brief": "Search and manage local Obsidian-compatible Markdown notes in a configured vault.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list, search, read, create or append"},
            "path": {"type": "STRING", "description": "Relative note path, e.g. Projects/Jarvis.md"},
            "query": {"type": "STRING", "description": "Words to search for in note titles and contents"},
            "content": {"type": "STRING", "description": "Markdown content to create or append"},
        },
        "required": ["action"],
    },
}


def _root() -> Path:
    cfg = get_plugin_config("knowledge_vault")
    raw = str(cfg.get("vault_path") or _DEFAULT_VAULT).strip()
    if not raw:
        raw = str(_DEFAULT_VAULT)
    return Path(raw).expanduser().resolve()


def _target(root: Path, relative: str) -> Path:
    raw = str(relative or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("Use a relative path inside the configured vault.")
    parts = [p for p in raw.split("/") if p not in {"", "."}]
    if any(p == ".." or p.startswith(".") for p in parts):
        raise ValueError("Hidden folders and parent paths are not allowed.")
    path = Path(*parts)
    if path.suffix.lower() != ".md":
        path = path.with_suffix(".md")
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("That path leaves the configured vault.") from exc
    return target


def _notes(root: Path):
    if not root.exists():
        return []
    out = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for filename in files:
            if filename.startswith(".") or not filename.lower().endswith(".md"):
                continue
            path = (Path(base) / filename)
            try:
                path.resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            out.append(path)
            if len(out) >= _MAX_SEARCH_FILES:
                return sorted(out)
    return sorted(out)


def _atomic_write(root: Path, target: Path, content: str) -> None:
    """Write inside the vault using an exclusive temporary file and atomic replace."""
    target.parent.resolve().relative_to(root)
    fd, temp_name = tempfile.mkstemp(prefix=".jarvis-", suffix=".tmp",
                                     dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        temp.replace(target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _untrusted(title: str, body: str) -> str:
    return f"<<<UNTRUSTED source=local-note:{title}>>>\n{body}\n<<<END UNTRUSTED>>>"


def _show(player, title: str, body: str) -> bool:
    if player is None:
        return False
    try:
        player.show_content(title, body)
        return True
    except Exception:
        return False


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "list").strip().lower()
    try:
        root = _root()
        if action == "list":
            notes = _notes(root)
            body = "\n".join(p.relative_to(root).as_posix() for p in notes) or "The vault has no Markdown notes yet."
            _show(player, "KNOWLEDGE VAULT", body)
            return body[:3000]
        if action == "search":
            query = str(parameters.get("query") or "").strip().lower()
            terms = [x for x in re.split(r"[^\w]+", query) if len(x) > 1]
            if not terms:
                return "Give me a keyword or phrase to search for."
            found = []
            for path in _notes(root):
                try:
                    if path.stat().st_size > _MAX_NOTE_BYTES:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                title = path.relative_to(root).as_posix()
                hay = (title + "\n" + text).lower()
                score = sum(1 for term in terms if term in hay)
                if score:
                    excerpts = []
                    for line in text.splitlines():
                        if any(term in line.lower() for term in terms):
                            excerpts.append(line.strip()[:240])
                        if len(excerpts) >= 3:
                            break
                    found.append((score, title, "\n".join(excerpts) or text[:240]))
            found.sort(key=lambda x: (-x[0], x[1].lower()))
            body = "\n\n".join(_untrusted(title, excerpt) for _score, title, excerpt in found[:15])
            if not body:
                return f"No notes matched '{query}'."
            _show(player, f"VAULT SEARCH: {query}", body)
            return body[:3500]
        if action == "read":
            target = _target(root, parameters.get("path", ""))
            if not target.is_file():
                return "That note does not exist."
            if target.stat().st_size > _MAX_NOTE_BYTES:
                return "That note is too large to read safely."
            text = target.read_text(encoding="utf-8", errors="replace")
            rel = target.relative_to(root).as_posix()
            _show(player, rel, _untrusted(rel, text[:20_000]))
            return _untrusted(rel, text[:20_000])
        if action in {"create", "append"}:
            content = str(parameters.get("content") or "")
            if not content.strip():
                return "Give me Markdown content to save."
            if len(content) > _MAX_NOTE_BYTES:
                return "The note is too large (limit 256 KB)."
            target = _target(root, parameters.get("path", ""))
            target.parent.mkdir(parents=True, exist_ok=True)
            if action == "create" and target.exists():
                return "That note already exists. Use append to add to it without overwriting."
            previous = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
            updated = (previous.rstrip() + "\n\n" + content.strip() + "\n") if previous else content.strip() + "\n"
            if len(updated.encode("utf-8")) > _MAX_NOTE_BYTES:
                return "The resulting note would exceed 256 KB."
            _atomic_write(root, target, updated)
            def restore():
                target.parent.resolve().relative_to(root)
                if previous is None:
                    if target.exists() or target.is_symlink():
                        target.unlink()
                else:
                    _atomic_write(root, target, previous)
                return f"Restored {target.name} to its previous contents."
            push_undo(f"{'created' if previous is None else 'appended to'} note {target.relative_to(root).as_posix()}", restore)
            rel = target.relative_to(root).as_posix()
            displayed = _show(player, rel, _untrusted(rel, updated[:20_000]))
            result = f"Saved {rel}. You can say undo to reverse this change."
            return result if displayed else result + "\n\n" + updated[:2500]
        return "Choose list, search, read, create or append."
    except Exception as exc:
        return f"Knowledge Vault failed: {type(exc).__name__}: {str(exc)[:160]}"
