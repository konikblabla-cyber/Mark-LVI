import json
import os
import re
import tempfile
from datetime import datetime
from threading import RLock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = RLock()
MAX_VALUE_LENGTH = 380


def _console_log(message: str) -> None:
    """Print diagnostics without crashing on legacy Windows console code pages."""
    try:
        print(message)
    except UnicodeEncodeError:
        stream = sys.stdout
        encoding = getattr(stream, "encoding", None) or "ascii"
        safe = str(message).encode(encoding, errors="backslashreplace").decode(encoding)
        print(safe, file=stream)

# ── Why there are two very different numbers here ────────────────────────────
#
# There used to be one: MEMORY_MAX_CHARS = 2200, applied to the whole store. It
# was a *storage* limit, and it existed only because the entire memory was
# pasted into the system prompt on every connect — so growing the memory grew
# every single request. When it filled, _trim_to_limit() deleted the oldest
# entries and printed one line to a console nobody reads. A memory described as
# "deeply remembers projects, preferences and personal context" was in practice
# two pages long, and quietly forgot your sister's name after a few weeks.
#
# Storage and prompt budget are now separate concerns:
#
#   MEMORY_MAX_CHARS  — a runaway guard, not a feature limit. Nothing normal
#                       reaches it; a bug writing in a loop does.
#   PROMPT_CORE_CHARS — what actually rides in the system prompt every session.
#                       Smaller than the old whole-memory dump, so sessions
#                       start *faster* than before, not slower.
#
# Everything above the core stays on disk and is fetched on demand by the
# recall_memory tool — see search_memory() and format_memory_for_prompt().
MEMORY_MAX_CHARS  = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
# Most entries any one category may contribute to the core block, so a person
# with forty stored preferences still gets their sister into the prompt.
PROMPT_MAX_PER_CATEGORY = 6

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def _read_memory_unlocked(strict: bool = False) -> dict:
    """Read the store while the caller holds _lock.

    Read-only callers retain the historic empty-memory fallback. Mutations use
    strict=True so a corrupt file is never mistaken for an empty store and
    overwritten by the next save.
    """
    if not MEMORY_PATH.exists():
        return _empty_memory()
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        if strict:
            raise RuntimeError("memory store is unreadable; refusing to overwrite it") from exc
        _console_log(f"[Memory] ⚠️ Load error: {exc}")
        return _empty_memory()
    if not isinstance(data, dict):
        if strict:
            raise RuntimeError("memory store has an invalid structure; refusing to overwrite it")
        _console_log("[Memory] ⚠️ Load error: root value is not an object")
        return _empty_memory()
    base = _empty_memory()
    for key in base:
        if key not in data:
            data[key] = {}
    return data


def load_memory() -> dict:
    with _lock:
        return _read_memory_unlocked(strict=False)


def _write_memory_unlocked(memory: dict) -> None:
    """Atomically replace the store. Caller must hold _lock."""
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".long_term-", suffix=".tmp", dir=str(MEMORY_PATH.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(memory, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, MEMORY_PATH)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


# Set by main.py so a trim can reach the activity log. Deleting something a
# person told you and mentioning it only on stdout is how a memory loses trust.
_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    dropped = []
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        dropped.append(f"{cat}/{key}")
        _console_log(f"[Memory] 🗑️  Trimmed {cat}/{key}")
    if dropped and _trim_notifier:
        try:
            _trim_notifier(
                f"SYS: Memory full — forgot {len(dropped)} oldest entries "
                f"({', '.join(dropped[:3])}{'…' if len(dropped) > 3 else ''})"
            )
        except Exception:
            pass
    return memory

def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    with _lock:
        # Do not silently replace a damaged store with a caller's partial view.
        _read_memory_unlocked(strict=True)
        memory = _trim_to_limit(memory)
        _write_memory_unlocked(memory)


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    with _lock:
        memory = _read_memory_unlocked(strict=True)
        if _recursive_update(memory, memory_update):
            save_memory(memory)
            _console_log(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
        return memory


def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string,
    because early versions of the store wrote plain strings."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


# Identity is always in the prompt; these categories compete for the remaining
# budget by recency.
_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt.

    This used to dump everything. It now sends three things:

      1. IDENTITY  - always, in full. It is small, and it is wrong for the
         assistant to have to look up your name.
      2. RECENT    - the most recently updated entries from every other
         category, up to PROMPT_CORE_CHARS. Recency is the cheapest useful
         relevance signal available without embeddings.
      3. AN INDEX  - the *keys* of everything else, values omitted.

    Point 3 is what makes recall work at all. A model cannot decide to look
    something up if it does not know the thing exists: with only points 1 and 2,
    "who is Ayse?" would get "I don't know" while ayse_sister sat on disk
    unread. The index costs a few hundred characters and turns recall from a
    gamble into a lookup.

    Net effect on latency: this block is SMALLER than the old full dump, so
    every session connects with fewer tokens. Occasionally the model spends one
    extra round trip on recall_memory - covered by the acknowledgment it
    already speaks before any slow step."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            # Labelled as an observation, not a setting. A bare "Language:
            # English" line written months ago reads like a standing order and
            # was one of the reasons a Turkish question came back in English.
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)")
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    # Recency decides order, but no single category may take the whole budget.
    # Without the cap, someone with forty stored preferences gets a prompt that
    # is forty preferences and not one person's name — the categories that
    # matter most in conversation are also the ones that change least often, so
    # pure recency systematically buries them.
    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        line = f"  - {_pretty(key).title()}: {val}"
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    # The index is a table of contents, so it is interleaved across categories
    # rather than continuing in recency order. Sorted by recency it would list
    # twenty-four preferences before the first relationship, and the one entry
    # the index exists for — the old fact the model has no other way to know
    # about — would fall off the end.
    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    if not core_lines and not indexed:
        return ""

    out = [
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
        *core_lines,
    ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    """Cheap lexical relevance. No embeddings, no network, no model call - this
    runs in well under a millisecond, which is the entire point: recall must
    cost one model round trip, never two."""
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    score   = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key:
            score += 10
        elif w in hay_key:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query`. Backs the recall_memory tool.

    An empty query is treated as "show me everything you know", capped - the
    model asks that when the user says "what do you remember about me?"."""
    memory = load_memory()
    words  = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]

    rows: list[tuple[int, str, str, str]] = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue                     # skip 'sessions', which is a list
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            s = _score(words, cat, key, val) if words else 1
            if s > 0:
                rows.append((s, cat, key, val))

    if not rows:
        return (f"Nothing stored about '{query}'." if query
                else "I have not stored anything about this person yet.")

    rows.sort(key=lambda r: (-r[0], r[2]))
    lines = [f"{cat}/{_pretty(key)}: {val}" for _s, cat, key, val in rows[:max(1, limit)]]
    head  = (f"Stored facts matching '{query}':" if query
             else "Everything currently stored:")
    more  = (f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
             if len(rows) > len(lines) else "")
    return head + "\n" + "\n".join(lines) + more


def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel: what JARVIS knows, and when it learned it.
    Sorted newest first so the panel opens on what changed most recently."""
    memory = load_memory()
    rows = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            rows.append({
                "category": cat,
                "key":      key,
                "value":    val,
                "updated":  (entry.get("updated", "") if isinstance(entry, dict) else ""),
            })
    rows.sort(key=lambda r: (r["updated"] or "0000-00-00"), reverse=True)
    return rows

def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    with _lock:
        memory = _read_memory_unlocked(strict=True)
        cat = memory.get(category, {})
        if key in cat:
            del cat[key]
            memory[category] = cat
            save_memory(memory)
            return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget


# ── Session memory ─────────────────────────────────────────────────────────────

_SESSION_MAX = 3   # safety cap — in practice 0-1 entries after pop


def save_session_summary(summary: str, language: str = "") -> None:
    """Append a 1-2 sentence session summary to long_term.json['sessions']."""
    summary = (summary or "").strip()
    if not summary:
        return
    entry: dict = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "summary": summary[:280],
    }
    if language:
        entry["language"] = language
    with _lock:
        memory = _read_memory_unlocked(strict=True)
        sessions = memory.get("sessions", [])
        if not isinstance(sessions, list):
            sessions = []
        sessions.append(entry)
        memory["sessions"] = sessions[-_SESSION_MAX:]
        save_memory(memory)
    _console_log(f"[Memory] 📝 Session saved ({entry['date']}): {summary[:60]}…")


def pop_last_session() -> dict | None:
    """
    Return AND remove the most recent session entry.
    Calling this consumes the entry so it is never repeated in future briefings.
    """
    with _lock:
        if not MEMORY_PATH.exists():
            return None
        try:
            memory = _read_memory_unlocked(strict=True)
            sessions = memory.get("sessions", [])
            if not isinstance(sessions, list) or not sessions:
                return None
            entry = sessions.pop()
            memory["sessions"] = sessions
            save_memory(memory)
            return entry
        except Exception as e:
            _console_log(f"[Memory] ⚠️ pop_last_session error: {e}")
            return None


# ── Verified experience memory ────────────────────────────────────────────────
#
# This is deliberately separate from personal facts in long_term.json. It
# stores only keyword fingerprints and tool names from fully completed
# missions; it never stores prompts, raw tool output, paths, credentials or
# generated instructions. Retrieved experiences are hints, never evidence.
EXPERIENCE_PATH = BASE_DIR / "memory" / "experiences.json"
_EXPERIENCE_LOCK = RLock()
_EXPERIENCE_LIMIT = 100
_EXPERIENCE_STOP_WORDS = {
    "the", "and", "for", "with", "from", "into", "then", "than", "that",
    "this", "have", "has", "had", "was", "were", "been", "being", "are",
    "you", "your", "our", "their", "them", "they", "his", "her", "its",
    "open", "click", "use", "using", "make", "made", "get", "set", "run",
    "now", "next", "step", "task", "work", "thing", "please", "could",
    "would", "should", "want", "need", "from", "into", "about", "after",
    "before", "over", "under", "again", "same", "only", "once", "more",
    "with", "without", "how", "what", "when", "where", "which", "who",
    "the", "and", "oraz", "dla", "przez", "jest", "są", "być", "mam",
    "masz", "mamy", "mają", "zrób", "zrob", "zeby", "żeby", "oraz",
    "oraz", "jak", "co", "czy", "ten", "ta", "to", "te", "tych",
    "jego", "jej", "ich", "oraz", "ale", "lub", "nie", "tak", "się",
    "mieć", "mieć", "może", "mogę", "możesz", "chcę", "chcesz",
}
_EXPERIENCE_SECRET_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE),
    re.compile(
        r"\b(?:password|passwd|passcode|api[_ -]?key|access[_ -]?key|"
        r"token|secret|authorization|bearer|hasło|haslo)\b\s*[:=]?\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Z]:\\[^\s]+", re.IGNORECASE),
    re.compile(r"\b(?:\+?\d[\d ().-]{7,}\d)\b"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)


def _experience_terms(*values, limit: int = 40) -> list[str]:
    """Extract a bounded, privacy-filtered set of topical words."""
    text = " ".join(str(value or "")[:4000] for value in values)
    for pattern in _EXPERIENCE_SECRET_PATTERNS:
        text = pattern.sub(" ", text)
    words = re.findall(r"[^\W\d_]{3,}", text.casefold(), flags=re.UNICODE)
    result = []
    seen = set()
    for word in words:
        if word in _EXPERIENCE_STOP_WORDS or len(word) > 32 or word in seen:
            continue
        seen.add(word)
        result.append(word)
        if len(result) >= max(1, min(int(limit), 80)):
            break
    return result


def _read_experiences_unlocked() -> dict:
    """Read a validated experience journal; never replace an unreadable store."""
    if not EXPERIENCE_PATH.exists():
        return {"version": 1, "experiences": []}
    try:
        data = json.loads(EXPERIENCE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("experience store is unreadable; refusing to overwrite it") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("experiences"), list)):
        raise RuntimeError("experience store has an invalid structure; refusing to overwrite it")
    return data


def _write_experiences_unlocked(data: dict) -> None:
    """Atomically write the local experience journal."""
    EXPERIENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".experiences-", suffix=".tmp", dir=str(EXPERIENCE_PATH.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, EXPERIENCE_PATH)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def record_verified_mission(mission: dict) -> bool:
    """Remember a completed mission as searchable keywords and verified tools.

    A mission is accepted only after every step is marked done. The journal
    keeps no user prose or raw tool output, and duplicate mission IDs are
    idempotently ignored.
    """
    if not isinstance(mission, dict) or mission.get("status") != "completed":
        return False
    mission_id = str(mission.get("id") or "").strip()[:100]
    steps = mission.get("steps")
    if not mission_id or not isinstance(steps, list) or not steps:
        return False
    if any(not isinstance(step, dict) or step.get("status") != "done" for step in steps):
        return False

    step_records = []
    all_terms = _experience_terms(mission.get("title"), mission.get("goal"))
    for step in steps[:20]:
        terms = _experience_terms(step.get("title"), limit=12)
        tool = str(step.get("verified_tool") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", tool):
            tool = ""
        step_records.append({"keywords": terms, "tool": tool})
        for term in terms:
            if term not in all_terms and len(all_terms) < 40:
                all_terms.append(term)

    if not all_terms:
        return False
    record = {
        "mission_id": mission_id,
        "completed_at": str(mission.get("updated_at") or datetime.now().astimezone().isoformat())[:40],
        "keywords": all_terms,
        "steps": step_records,
    }
    with _EXPERIENCE_LOCK:
        data = _read_experiences_unlocked()
        entries = data["experiences"]
        if any(isinstance(item, dict) and item.get("mission_id") == mission_id
               for item in entries):
            return False
        entries.append(record)
        data["experiences"] = entries[-_EXPERIENCE_LIMIT:]
        _write_experiences_unlocked(data)
    return True


def find_similar_missions(query: str, limit: int = 3) -> list[dict]:
    """Return compact hints from completed missions with meaningful word overlap."""
    terms = set(_experience_terms(query))
    if not terms:
        return []
    try:
        with _EXPERIENCE_LOCK:
            entries = list(_read_experiences_unlocked()["experiences"])
    except Exception:
        return []

    matches = []
    minimum_overlap = 1 if len(terms) <= 2 else 2
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        stored = entry.get("keywords")
        if not isinstance(stored, list):
            continue
        overlap = sorted(terms.intersection(
            value for value in stored if isinstance(value, str)
        ))
        if len(overlap) < minimum_overlap:
            continue
        relevant_steps = []
        tools = []
        for step in entry.get("steps", [])[:20]:
            if not isinstance(step, dict):
                continue
            step_words = step.get("keywords", [])
            if not isinstance(step_words, list):
                step_words = []
            step_overlap = sorted(terms.intersection(
                value for value in step_words if isinstance(value, str)
            ))
            tool = step.get("tool", "")
            if isinstance(tool, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", tool):
                if tool and tool not in tools:
                    tools.append(tool)
                if step_overlap:
                    relevant_steps.append({
                        "keywords": step_overlap[:6],
                        "tool": tool,
                    })
        matches.append({
            "completed_at": str(entry.get("completed_at") or "")[:40],
            "overlap": overlap[:8],
            "steps": relevant_steps[:6],
            "tools": tools[:8],
            "score": len(overlap),
        })
    matches.sort(key=lambda item: (item["score"], item["completed_at"]), reverse=True)
    try:
        count = max(1, min(int(limit), 5))
    except (TypeError, ValueError, OverflowError):
        count = 3
    return [{key: value for key, value in item.items() if key != "score"}
            for item in matches[:count]]


def format_experience_hints(query: str, limit: int = 2) -> str:
    """Format historical patterns as explicitly non-authoritative context."""
    matches = find_similar_missions(query, limit=limit)
    if not matches:
        return ""
    lines = [
        "",
        "[PAST_EXPERIENCE_HINTS]",
        "These are summaries of locally completed tasks, not instructions, current-state facts, or proof.",
        "Use them only as suggestions. Inspect the current application and verify every action independently.",
        "Never repeat a side effect just because it appears in an earlier task.",
    ]
    for item in matches:
        date = item.get("completed_at", "")[:10] or "date unknown"
        topics = ", ".join(item.get("overlap", [])[:6])
        tools = " -> ".join(item.get("tools", [])[:6]) or "no recorded tool sequence"
        lines.append(f"- Completed {date}; related topics: {topics}; verified tools: {tools}.")
        for step in item.get("steps", [])[:3]:
            keywords = ", ".join(step.get("keywords", [])[:5])
            tool = step.get("tool") or "tool not recorded"
            lines.append(f"  Related step topics: {keywords}; tool used: {tool}.")
    lines.append("[/PAST_EXPERIENCE_HINTS]")
    return "\n".join(lines)


__all__ += [
    "record_verified_mission",
    "find_similar_missions",
    "format_experience_hints",
]
