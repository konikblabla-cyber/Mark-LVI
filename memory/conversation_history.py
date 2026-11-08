"""Opt-in, local-only transcript history for MARK.

Only text transcripts are stored. Audio, screenshots, tool arguments and API
keys are never written here. The feature is disabled until the user enables
it in Plugin Settings.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from memory.config_manager import get_plugin_config

_HISTORY_PATH = Path(__file__).resolve().parent / "conversation_history.jsonl"
_LOCK = threading.RLock()
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_KEEP_LINES = 12000
_MAX_TEXT_CHARS = 6000

_REDACTIONS = (
    re.compile(r"(?i)\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"(?i)\bsk-[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(api[_ -]?key|password|token)\s*[:=]\s*[^\s,;]{8,}"),
)


def settings() -> dict:
    cfg = get_plugin_config("conversation_history")
    raw_days = cfg.get("retention_days", "30")
    try:
        days = max(1, min(int(raw_days), 365))
    except (TypeError, ValueError):
        days = 30
    return {"enabled": bool(cfg.get("enabled", False)), "retention_days": days}


def _clean_text(value: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    for pattern in _REDACTIONS:
        text = pattern.sub("[REDACTED]", text)
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS].rstrip() + " …[truncated]"
    return text


def _rotate_if_needed() -> None:
    try:
        if not _HISTORY_PATH.exists() or _HISTORY_PATH.stat().st_size <= _MAX_FILE_BYTES:
            return
        lines = _HISTORY_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        kept = lines[-_MAX_KEEP_LINES:]
        tmp = _HISTORY_PATH.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp.replace(_HISTORY_PATH)
    except OSError:
        # Keep the existing file; never destroy history because rotation failed.
        return


def append_message(role: str, text: str, at: datetime | None = None) -> bool:
    """Append one transcript message; returns False when history is disabled."""
    cfg = settings()
    if not cfg["enabled"]:
        return False
    role = str(role or "").strip().lower()
    if role not in {"user", "assistant"}:
        return False
    body = _clean_text(text)
    if not body:
        return False
    stamp = (at or datetime.now().astimezone()).isoformat(timespec="seconds")
    record = {"ts": stamp, "role": role, "text": body}
    try:
        with _LOCK:
            _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed()
            with _HISTORY_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        print(f"[History] could not save transcript: {exc}")
        return False


def _read_entries(limit_scan: int = 12000) -> list[dict]:
    """Read retained transcripts and physically purge expired/corrupt rows."""
    cfg = settings()
    if not cfg["enabled"] or not _HISTORY_PATH.exists():
        return []
    cutoff = datetime.now().astimezone() - timedelta(days=cfg["retention_days"])
    rows = []
    retained_lines: list[str] = []
    needs_rewrite = False
    try:
        with _LOCK:
            lines = _HISTORY_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            scan_lines = lines[-limit_scan:]
            if len(scan_lines) != len(lines):
                needs_rewrite = True
            for line in scan_lines:
                try:
                    item = json.loads(line)
                    ts = datetime.fromisoformat(item.get("ts", ""))
                    if ts.tzinfo is None:
                        ts = ts.astimezone()
                    role = item.get("role")
                    text = item.get("text")
                    if ts < cutoff or role not in {"user", "assistant"} or not isinstance(text, str):
                        needs_rewrite = True
                        continue
                    row = {"ts": item["ts"], "role": role, "text": text}
                    rows.append(row)
                    retained_lines.append(json.dumps(row, ensure_ascii=False))
                except (ValueError, TypeError, json.JSONDecodeError, AttributeError):
                    needs_rewrite = True
            if needs_rewrite:
                tmp = _HISTORY_PATH.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(retained_lines) + ("\n" if retained_lines else ""),
                               encoding="utf-8")
                tmp.replace(_HISTORY_PATH)
    except OSError:
        return []
    return rows


def recent_messages(limit: int = 10) -> list[dict]:
    limit = max(1, min(int(limit), 50))
    return _read_entries()[-limit:]


def search_messages(query: str, limit: int = 12) -> list[dict]:
    terms = [t for t in re.split(r"[^\w]+", str(query or "").lower()) if len(t) > 1]
    if not terms:
        return []
    matches = []
    for item in _read_entries():
        text = item["text"].lower()
        score = sum(1 for term in terms if term in text)
        if score:
            matches.append((score, item))
    matches.sort(key=lambda pair: (pair[0], pair[1]["ts"]), reverse=True)
    return [item for _score, item in matches[:max(1, min(int(limit), 30))]]


def clear_history() -> bool:
    """Delete the local transcript archive after the caller has confirmed."""
    with _LOCK:
        try:
            if _HISTORY_PATH.exists():
                _HISTORY_PATH.unlink()
            tmp = _HISTORY_PATH.with_suffix(".jsonl.tmp")
            if tmp.exists():
                tmp.unlink()
            return True
        except OSError as exc:
            print(f"[History] clear failed: {exc}")
            return False


def history_stats() -> dict:
    rows = _read_entries()
    return {
        "enabled": settings()["enabled"],
        "entries": len(rows),
        "file_bytes": _HISTORY_PATH.stat().st_size if _HISTORY_PATH.exists() else 0,
    }
