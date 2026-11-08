"""Cost-bounded, task-specific routing for MARK's one-shot model calls.

This module deliberately does NOT replace the live voice session.  It is only
used by core.gemini.call() for background work such as image analysis,
classification, reasoning and coding.  Provider selection reuses MARK's
existing ENGINE & PROVIDERS settings.

No automatic cross-provider fallback is performed here.  A missing route,
missing key, exhausted local budget, provider error or rate limit fails closed.
"""
from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

NOT_ROUTED = object()
GEMMA_VISION_MODEL = "gemma-4-26b-a4b-it"

class RouterError(RuntimeError):
    """A routed task could not safely be sent to its configured model."""

class RouterReply:
    """Small response-compatible object; callers only rely on .text."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

_lock = threading.RLock()
_CODING_RE = re.compile(
    r"\b(code|coding|program|programming|python|javascript|typescript|script|"
    r"repository|repo|debug|bug|refactor|implement|compile|kod|programowanie|"
    r"skrypt|repozytorium|błąd|blad|napraw kod|napisz kod)\b",
    re.IGNORECASE,
)
_IMAGE_MIMES = ("image/",)
_NON_IMAGE_MIMES = ("audio/", "video/")


_settings_cache: tuple[int, dict] = (-1, {})
_settings_lock = threading.Lock()


def _settings() -> dict:
    """Cache settings by config mtime; the disabled path stays nearly free."""
    global _settings_cache
    try:
        from memory.config_manager import CONFIG_FILE, get_model_router_config
        mtime = CONFIG_FILE.stat().st_mtime_ns if CONFIG_FILE.exists() else 0
        with _settings_lock:
            if _settings_cache[0] == mtime:
                return dict(_settings_cache[1])
            values = get_model_router_config()
            _settings_cache = (mtime, dict(values))
            return values
    except Exception as exc:
        raise RouterError("model-router settings could not be read") from exc


def is_enabled() -> bool:
    return bool(_settings().get("enabled", False))


def _items(contents) -> list:
    return list(contents) if isinstance(contents, (list, tuple)) else [contents]


def _mime_of(item) -> str:
    if isinstance(item, dict):
        blob = item.get("inline_data") or item.get("inlineData")
        if isinstance(blob, dict):
            return str(blob.get("mime_type") or blob.get("mimeType") or "").lower()
        image_url = item.get("image_url")
        if item.get("type") == "image_url" or isinstance(image_url, dict):
            return "image/encoded"
    blob = getattr(item, "inline_data", None)
    if blob is not None:
        return str(getattr(blob, "mime_type", "") or "").lower()
    return ""


def _is_pil_image(item) -> bool:
    return (type(item).__module__.startswith("PIL.")
            and hasattr(item, "save") and hasattr(item, "size"))


def _contains_image(contents) -> bool:
    for item in _items(contents):
        if _is_pil_image(item):
            return True
        if _mime_of(item).startswith(_IMAGE_MIMES):
            return True
    return False


def _contains_recording(contents) -> bool:
    return any(_mime_of(item).startswith(_NON_IMAGE_MIMES) for item in _items(contents))


def _text_of(contents) -> str:
    chunks = []
    for item in _items(contents):
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            chunks.append(item["text"])
        else:
            value = getattr(item, "text", None)
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(chunks)


def classify_task(contents, tier: str = "smart", task_type: str | None = None) -> str:
    """Deterministic, inexpensive routing.  Explicit task_type always wins."""
    allowed = {"vision", "fast", "reasoning", "coding"}
    explicit = str(task_type or "").strip().lower()
    if explicit:
        if explicit not in allowed:
            raise RouterError(f"unknown task type: {explicit}")
        return explicit

    if _contains_recording(contents):
        return ""
    if _contains_image(contents):
        return "vision"

    text = _text_of(contents)
    if _CODING_RE.search(text) or "```" in text:
        return "coding"
    if tier == "fast":
        return "fast"
    if tier == "smart":
        return "reasoning"
    # A caller that explicitly passed a model ID keeps that choice.
    return ""


def _provider_for(task: str, provider_override: str = "") -> str:
    from memory.config_manager import get_coding_provider, get_sidecar_provider

    sidecar = get_sidecar_provider()
    coding = get_coding_provider()
    if task == "fast":
        return sidecar
    if task in ("reasoning", "coding"):
        return coding or sidecar
    if task == "vision":
        return sidecar
    return ""


def _input_chars(contents) -> int:
    # Images are already compressed by MARK's capture/file-processing paths.
    # Count actual text here; the request-count budget also bounds image calls.
    return len(_text_of(contents))


def _usage_path() -> Path:
    from memory.config_manager import CONFIG_DIR
    return CONFIG_DIR / "model_router_usage.json"


def _audit_path() -> Path:
    from memory.config_manager import CONFIG_DIR
    return CONFIG_DIR / "model_router_audit.jsonl"


def _limit_int(settings: dict, key: str, default: int, maximum: int) -> int:
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(0, min(value, maximum))


def _reserve(contents, settings: dict) -> int:
    """Reserve budget before the network call. Failed attempts still count."""
    chars = _input_chars(contents)
    max_call = _limit_int(settings, "max_input_chars", 50_000, 200_000)
    max_daily_calls = _limit_int(settings, "daily_calls", 40, 500)
    max_daily_chars = _limit_int(settings, "daily_input_chars", 200_000, 5_000_000)

    if chars > max_call:
        raise RouterError(f"input is {chars:,} characters; per-call limit is {max_call:,}")

    path = _usage_path()
    with _lock:
        try:
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(state, dict):
                raise ValueError("usage ledger is not an object")
        except Exception as exc:
            raise RouterError("usage ledger is unreadable; routing stopped for safety") from exc

        today = date.today().isoformat()
        if state.get("date") != today:
            state = {"date": today, "calls": 0, "input_chars": 0}

        calls = int(state.get("calls", 0))
        used = int(state.get("input_chars", 0))
        if calls >= max_daily_calls:
            raise RouterError(f"daily call limit reached ({max_daily_calls})")
        if used + chars > max_daily_chars:
            raise RouterError(f"daily input limit reached ({max_daily_chars:,} characters)")

        state["calls"] = calls + 1
        state["input_chars"] = used + chars
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            temp.replace(path)
        except Exception as exc:
            raise RouterError("could not save usage budget; routing stopped for safety") from exc
    return chars


def _audit(task: str, target: str, status: str, chars: int,
           elapsed_ms: int, error_type: str = "") -> None:
    # Never log prompts, API keys, headers, or provider error bodies.
    record = {
        "date": date.today().isoformat(),
        "task": task,
        "target": target,
        "status": status,
        "input_chars": chars,
        "elapsed_ms": elapsed_ms,
        "error_type": error_type,
    }
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # The budget ledger is the hard stop; audit write failure must not
        # strand an otherwise completed answer.
        pass


def _gemma_call(contents, config, timeout_ms: int, model: str):
    from core import gemini

    key = gemini.api_key()
    if not key:
        raise RouterError("Gemma route needs the existing Gemini API key")
    client = gemini.client(timeout_ms=timeout_ms, key=key)
    kwargs = {"model": model, "contents": contents}
    if config is not None:
        kwargs["config"] = config
    response = client.models.generate_content(**kwargs)
    answer = (getattr(response, "text", None) or "").strip()
    if not answer:
        raise RouterError("Gemma returned an empty answer")
    return response


def _provider_messages(contents, config) -> tuple[list[dict], float | None]:
    """Convert MARK parts/PIL images into the OpenAI-compatible message shape."""
    texts: list[str] = []
    multimodal: list[dict] = []

    for item in _items(contents):
        if isinstance(item, str):
            texts.append(item)
            multimodal.append({"type": "text", "text": item})
            continue
        if isinstance(item, dict):
            if isinstance(item.get("text"), str):
                texts.append(item["text"])
                multimodal.append({"type": "text", "text": item["text"]})
                continue
            inline = item.get("inline_data") or item.get("inlineData")
            if isinstance(inline, dict):
                data = inline.get("data")
                mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
                if isinstance(data, str):
                    url = f"data:{mime};base64,{data}"
                elif isinstance(data, bytes):
                    url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                else:
                    continue
                multimodal.append({"type": "image_url", "image_url": {"url": url}})
                continue
            if item.get("type") == "image_url" and isinstance(item.get("image_url"), dict):
                multimodal.append(item)
                continue

        blob = getattr(item, "inline_data", None)
        if blob is not None:
            data = getattr(blob, "data", None)
            mime = getattr(blob, "mime_type", None) or "image/png"
            if isinstance(data, bytes):
                data = base64.b64encode(data).decode("ascii")
            if isinstance(data, str):
                multimodal.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                })
            continue

        if _is_pil_image(item):
            buffer = io.BytesIO()
            image = item.convert("RGB") if getattr(item, "mode", "") in ("RGBA", "P") else item
            image.save(buffer, format="JPEG", quality=82)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            multimodal.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            })
            continue

        value = getattr(item, "text", None)
        if isinstance(value, str):
            texts.append(value)
            multimodal.append({"type": "text", "text": value})

    system = ""
    temperature = None
    if config is not None:
        if isinstance(config, dict):
            system = config.get("system_instruction") or ""
            temperature = config.get("temperature")
        else:
            system = getattr(config, "system_instruction", None) or ""
            temperature = getattr(config, "temperature", None)

    user_content: Any = "\n".join(texts) if len(multimodal) == len(texts) else multimodal
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": str(system)})
    messages.append({"role": "user", "content": user_content})
    return messages, float(temperature) if temperature is not None else None


def _provider_call(provider_id: str, contents, config, timeout_ms: int,
                   task: str):
    from memory.config_manager import get_provider_config
    from providers import registry

    cfg = get_provider_config(provider_id)
    missing = registry.missing_fields(provider_id, cfg)
    if missing:
        raise RouterError("provider is not configured: " + ", ".join(missing))

    llm = registry.build(provider_id, cfg)
    if not hasattr(llm, "complete"):
        raise RouterError("selected provider does not support one-shot text requests")
    if task == "vision" and not (getattr(llm, "caps", {}) or {}).get("vision"):
        raise RouterError("selected background model has not passed MARK's vision check")

    messages, temperature = _provider_messages(contents, config)
    text_out, _tool_calls = llm.complete(
        messages,
        tools=None,
        timeout=max(10.0, timeout_ms / 1000.0),
        temperature=temperature,
    )
    if not (text_out or "").strip():
        raise RouterError("configured provider returned an empty answer")
    return RouterReply(text_out.strip())


def dispatch(contents, tier: str = "smart", config=None,
             timeout_ms: int = 10_000, task_type: str | None = None,\n             provider_override: str = ""):
    """Return NOT_ROUTED when disabled/unmatched; otherwise a routed response.

    Once enabled, a classified task never drops back to the legacy Gemini
    ladder. Missing routes and errors raise RouterError instead.
    """
    settings = _settings()
    if not settings.get("enabled"):
        return NOT_ROUTED
    if not settings.get("zero_cost_confirmed"):
        raise RouterError("enable only after verifying every selected API is on a $0 plan with no payment method")
    if tier == "search":
        raise RouterError("grounded web-search requests are not enabled in the task router")

    task = classify_task(contents, tier, task_type)
    if not task:
        raise RouterError("this request has no safe route; specify a supported task type")

    chars = _reserve(contents, settings)
    started = time.monotonic()
    target = ""
    try:
        if task == "vision" and settings.get("gemma_vision", True):
            target = GEMMA_VISION_MODEL
            response = _gemma_call(contents, config, timeout_ms)
        else:
            provider_id = _provider_for(task, provider_override)
            if not provider_id:
                raise RouterError(f"no provider assigned to the {task} route")
            target = provider_id
            response = _provider_call(provider_id, contents, config, timeout_ms, task)
        _audit(task, target, "ok", chars, int((time.monotonic() - started) * 1000))
        return response
    except RouterError as exc:
        _audit(task, target or "unassigned", "blocked", chars,
               int((time.monotonic() - started) * 1000), type(exc).__name__)
        raise
    except Exception as exc:
        _audit(task, target or "unassigned", "error", chars,
               int((time.monotonic() - started) * 1000), type(exc).__name__)
        # Do not print provider response bodies; they can contain sensitive data.
        raise RouterError(f"{task} route failed ({type(exc).__name__}); no fallback was attempted") from exc
