"""
BackgroundMonitor — user-configured topic watching.
Checks DDG news once per day per topic; alerts JARVIS when a new headline appears.
No crypto, no finance, no uninvited tracking.
"""
import hashlib
import re
from datetime import datetime


# ── Blocked categories (never monitor regardless of what user says) ────────────

MAX_MONITORS = 20
MAX_TOPIC_LENGTH = 120


_BLOCKED = {
    # Brand / asset names — spelled the same in every language
    "bitcoin", "ethereum", "dogecoin", "solana", "binance",
    "nft", "blockchain", "defi", "altcoin", "memecoin", "coin", "token",
    # spellings of the "crypto" root across different languages
    "crypto", "kripto", "cripto", "krypto", "крипто", "仮想通貨", "暗号資産",
    "cryptocurrency",
}

def _is_blocked(topic: str) -> bool:
    t = str(topic or "").lower()
    return any(word in t for word in _BLOCKED)


# ── Slug / hash helpers ────────────────────────────────────────────────────────

def _slug(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", topic.lower().strip())[:40].strip("_")
    if slug:
        return slug
    # Keep non-Latin topic names distinct instead of mapping all of them to "".
    return "topic_" + hashlib.sha256(topic.encode("utf-8")).hexdigest()[:12]


def _title_hash(title: str) -> str:
    return hashlib.md5(title.encode("utf-8", errors="ignore")).hexdigest()[:12]


# ── Memory I/O ─────────────────────────────────────────────────────────────────

def _load() -> dict:
    from memory.memory_manager import load_memory
    data = load_memory().get("monitors", {})
    return data if isinstance(data, dict) else {}

def _save(monitors: dict) -> None:
    # All long-term state shares the same atomic writer and corruption guard.
    from memory.memory_manager import load_memory, save_memory
    memory = load_memory()
    memory["monitors"] = monitors
    save_memory(memory)


# ── Public API ─────────────────────────────────────────────────────────────────

def add_monitor(topic: str) -> str:
    topic = str(topic or "").strip()
    if not topic:
        return "Please specify a topic to monitor."
    if len(topic) > MAX_TOPIC_LENGTH:
        return f"Topic is too long (maximum {MAX_TOPIC_LENGTH} characters)."
    if _is_blocked(topic):
        return "I don't monitor crypto or financial topics."
    monitors = _load()
    slug = _slug(topic)
    if slug in monitors:
        existing = monitors[slug]
        return f"Already monitoring: {existing.get('topic', topic)}"
    if len(monitors) >= MAX_MONITORS:
        return f"Monitor limit reached ({MAX_MONITORS} topics). Remove one before adding another."
    monitors[slug] = {
        "topic": topic,
        "added": datetime.now().strftime("%Y-%m-%d"),
        "last_check": "",
        "last_hash": "",
    }
    _save(monitors)
    print(f"[Monitor] ➕ Added: {topic}")
    return f"Now monitoring: {topic}"


def remove_monitor(topic: str) -> str:
    topic = topic.strip().lower()
    monitors = _load()
    # exact slug match first
    slug = _slug(topic)
    if slug in monitors:
        label = monitors.pop(slug)["topic"]
        _save(monitors)
        return f"Stopped monitoring: {label}"
    # partial match fallback
    for key, val in list(monitors.items()):
        if topic in val.get("topic", "").lower():
            label = monitors.pop(key)["topic"]
            _save(monitors)
            return f"Stopped monitoring: {label}"
    return f"Not found in monitored topics: {topic}"


def list_monitors() -> list[str]:
    return [v.get("topic", k) for k, v in _load().items()]


def check_all() -> list[str]:
    """
    Run pending topic checks once per day per topic.
    Report up to three unseen headlines per topic, without repeating old alerts.
    """
    from actions.web_search import _ddg_news

    monitors = _load()
    if not monitors:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    alerts = []
    changed = False

    for slug, data in monitors.items():
        if not isinstance(data, dict):
            continue
        if data.get("last_check") == today:
            continue

        topic = str(data.get("topic") or slug)[:MAX_TOPIC_LENGTH]
        try:
            results = _ddg_news(topic, max_results=5)
            if not results:
                monitors[slug]["last_check"] = today
                changed = True
                continue

            # Migrate the old single-headline format without alerting it again.
            seen = data.get("seen_hashes")
            if not isinstance(seen, list):
                seen = []
            seen = [item for item in seen if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{12}", item)]
            if not seen and isinstance(data.get("last_hash"), str):
                legacy_hash = data["last_hash"]
                if re.fullmatch(r"[0-9a-f]{12}", legacy_hash):
                    seen.append(legacy_hash)

            new_items = []
            new_hashes = []
            for item in results[:5]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()[:300]
                if not title:
                    continue
                digest = _title_hash(title)
                if digest in seen or digest in new_hashes:
                    continue
                new_hashes.append(digest)
                new_items.append({
                    "title": title,
                    "snippet": str(item.get("snippet") or "").strip()[:150],
                    "source": str(item.get("source") or "").strip()[:100],
                })
                if len(new_items) >= 3:
                    break

            monitors[slug]["last_check"] = today
            monitors[slug]["seen_hashes"] = (new_hashes + seen)[:20]
            if new_hashes:
                monitors[slug]["last_hash"] = new_hashes[0]
            changed = True

            if not new_items:
                continue

            parts = [f"[MONITOR_ALERT] {topic}"]
            for index, item in enumerate(new_items, 1):
                parts.append(f"{index}. {item['title']}")
                if item["snippet"]:
                    parts.append(item["snippet"])
                if item["source"]:
                    parts.append(f"Source: {item['source']}")
            alerts.append("\n".join(parts))
            print(f"[Monitor] 🔔 {len(new_items)} new headline(s) for '{topic}'")

        except Exception as e:
            print(f"[Monitor] ⚠️ Check failed for '{topic}': {type(e).__name__}")

    if changed:
        _save(monitors)

    return alerts
