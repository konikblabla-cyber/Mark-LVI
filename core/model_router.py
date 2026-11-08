"""Cost-bounded, task-specific routing for MARK's one-shot model calls.

This module deliberately does NOT replace the live voice session.  It is only
used by core.gemini.call() for background work such as image analysis,
classification, reasoning and coding.  Provider selection reuses MARK's
existing ENGINE & PROVIDERS settings.

Provider retry is optional and bounded to one alternate configured endpoint on
temporary outages.  A complex-task critic is also opt-in and never fans out on
ordinary requests. Missing routes, missing keys or exhausted local budgets
fail closed.
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
_REVIEW_RE = re.compile(
    r"\b(review|code review|security audit|audit|audyt|przegląd|przeglad|"
    r"sprawdź kod|sprawdz kod|bezpieczeństwo kodu|bezpieczenstwo kodu)\b",
    re.IGNORECASE,
)
_CODING_RE = re.compile(
    r"\b(code|coding|program|programming|python|javascript|typescript|script|"
    r"repository|repo|debug|bug|refactor|implement|compile|kod|programowanie|"
    r"skrypt|repozytorium|błąd|blad|napraw kod|napisz kod)\b",
    re.IGNORECASE,
)
_COMPLEX_RE = re.compile(
    r"\b(architecture|architectural|security|threat|risk|trade.?off|"
    r"root cause|scalability|migration|performance|strategy|deep analysis|"
    r"architektura|bezpieczeństwo|bezpieczenstwo|zagrożenia|zagrozenia|"
    r"ryzyko|porównaj|porownaj|strategia|przyczyna|skalowanie|migracja|audyt)\b",
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
    allowed = {"vision", "fast", "reasoning", "coding", "review"}
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
    if _REVIEW_RE.search(text):
        return "review"
    if _CODING_RE.search(text) or (chr(96) * 3) in text:
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
    coding = provider_override or get_coding_provider()
    if task in ("fast", "review", "vision"):
        return sidecar
    if task in ("reasoning", "coding"):
        return coding or sidecar
    return ""


def _is_zero_cost_provider(provider_id: str) -> bool:
    try:
        _assert_zero_cost_provider(provider_id)
        return True
    except RouterError:
        return False


def has_independent_reviewer() -> bool:
    """True when at least one configured free/local text provider can be reviewed by Gemma."""
    from memory.config_manager import get_coding_provider, get_sidecar_provider
    return any(_is_zero_cost_provider(pid) for pid in
               (get_coding_provider(), get_sidecar_provider()) if pid)


def _alternate_provider(primary: str, provider_override: str = "") -> str:
    """The one other configured $0 endpoint, if it is genuinely different."""
    from memory.config_manager import get_coding_provider, get_sidecar_provider

    candidates = (get_sidecar_provider(), provider_override or get_coding_provider())
    return next((candidate for candidate in candidates
                 if candidate and candidate != primary and _is_zero_cost_provider(candidate)), "")


def _is_transient_error(exc: Exception) -> bool:
    """Retry only transport/outage/quota failures, never auth or bad requests."""
    message = str(exc).lower()
    if any(code in message for code in ("401", "403", "404", "400", "invalid api key",
                                        "permission denied", "model not found")):
        return False
    return any(token in message for token in (
        "429", "500", "502", "503", "504", "timeout", "timed out",
        "connecterror", "connection error", "connection refused",
        "service unavailable", "temporarily unavailable", "overloaded",
        "network is unreachable",
    ))


def _json_object(text: str) -> dict | None:
    """Parse the first JSON object from a model reply without trusting its prose."""
    body = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "")
    fence = chr(96) * 3
    candidates = re.findall(r"(?is)" + re.escape(fence) + r"(?:json)?\s*(.*?)" + re.escape(fence), body) + [body]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _end = decoder.raw_decode(candidate, index)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def _verify_response(task: str, response, config=None) -> str:
    """Small deterministic checks before a result is accepted."""
    answer = (getattr(response, "text", None) or "").strip()
    if not answer:
        raise RouterError("model returned an empty response")

    mime = ""
    if isinstance(config, dict):
        mime = str(config.get("response_mime_type") or "")
    elif config is not None:
        mime = str(getattr(config, "response_mime_type", "") or "")
    if mime.lower() == "application/json":
        fence = chr(96) * 3
        candidate = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*|\s*" + re.escape(fence) + r"$", "", answer, flags=re.I)
        try:
            json.loads(candidate)
        except ValueError as exc:
            raise RouterError("response failed JSON validation") from exc
    if task in ("coding", "review") and answer.count(chr(96) * 3) % 2:
        raise RouterError("response contains an unclosed code fence")
    return answer


def _needs_second_opinion(task: str, contents, settings: dict) -> bool:
    """Only complex reasoning gets a critic; ordinary prompts stay single-model."""
    if task != "reasoning" or not settings.get("second_opinion", False):
        return False
    text = _text_of(contents)
    try:
        threshold = max(500, min(int(settings.get("review_min_chars", 2500)), 20000))
    except (TypeError, ValueError):
        threshold = 2500
    return len(text) >= threshold or bool(_COMPLEX_RE.search(text))


def _second_opinion(contents, draft: str, reviewer_id: str, settings: dict,
                    timeout_ms: int) -> str | None:
    """Ask one independent model to challenge a complex answer and return a fix."""
    original = _text_of(contents)
    prompt = (
        "You are the independent reviewer for a personal AI assistant. "
        "Review the proposed answer against the original task. Look for factual "
        "errors, missing constraints, contradictions, unsafe assumptions and "
        "unsupported claims. Do not invent sources. If the draft is sound, accept it. "
        "If it has a material problem, provide a corrected complete answer. "
        "Return ONLY JSON with this schema: "
        "{\"verdict\":\"accept\",\"issues\":[],\"answer\":\"\"}. "
        "The verdict must be either accept or improve.\n\n"
        "ORIGINAL TASK:\n" + original[:12000] +
        "\n\nDRAFT ANSWER:\n" + draft[:16000]
    )
    chars = len(prompt)
    started = time.monotonic()
    try:
        _reserve(prompt, settings)
        review_response = _gemma_call(
            [prompt], {"temperature": 0.1}, timeout_ms,
            str(settings.get("gemma_model") or GEMMA_VISION_MODEL),
        )
        review_text = _verify_response("review", review_response)
        obj = _json_object(review_text)
        if not obj:
            _audit("review", reviewer_id, "invalid_review", chars,
                   int((time.monotonic() - started) * 1000), "InvalidJSON")
            return None
        verdict = str(obj.get("verdict") or "accept").strip().lower()
        if verdict == "improve":
            improved = str(obj.get("answer") or "").strip()
            if improved:
                _audit("review", reviewer_id, "improved", chars,
                       int((time.monotonic() - started) * 1000))
                return improved
        _audit("review", reviewer_id, "accepted", chars,
               int((time.monotonic() - started) * 1000))
        return None
    except Exception as exc:
        _audit("review", reviewer_id, "skipped", chars,
               int((time.monotonic() - started) * 1000), type(exc).__name__)
        return None


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

        try:
            calls = int(state.get("calls", 0))
            used = int(state.get("input_chars", 0))
            if calls < 0 or used < 0:
                raise ValueError("negative usage counters")
        except (TypeError, ValueError) as exc:
            raise RouterError("usage ledger counters are invalid; routing stopped for safety") from exc
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

    if model != GEMMA_VISION_MODEL:
        raise RouterError("only the verified free Gemma 4 26B A4B model is allowed")
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


def _assert_zero_cost_provider(provider_id: str) -> None:
    """Allow only documented $0 hosted model IDs or verified local endpoints.

    Groq GPT-OSS, OpenAI, paid Z.AI models and arbitrary custom URLs are
    deliberately rejected even after the UI acknowledgement. The model ID,
    not the vendor's marketing label, determines the allowlist entry.
    """
    from memory.config_manager import get_provider_config
    from providers import registry
    from providers.llm_openai import _is_own_hardware

    cfg = get_provider_config(provider_id)
    entry = registry.catalog_entry(provider_id) or {}
    model = str(cfg.get("model") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip().rstrip("/").lower()

    if entry.get("local") is True and _is_own_hardware(base_url):
        return
    if (entry.get("id") == "zai" and model == "glm-4.7-flash"
            and base_url == "https://api.z.ai/api/paas/v4"):
        return
    raise RouterError(
        f"{provider_id} / {model or 'no model'} is not on MARK's verified $0 allowlist; "
        "use Z.AI glm-4.7-flash or a private local endpoint"
    )


def _provider_call(provider_id: str, contents, config, timeout_ms: int,
                   task: str):
    from memory.config_manager import get_provider_config
    from providers import registry

    cfg = get_provider_config(provider_id)
    _assert_zero_cost_provider(provider_id)
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
             timeout_ms: int = 10_000, task_type: str | None = None,
             provider_override: str = ""):
    """Route one task, optionally retry once or obtain a bounded second opinion.

    The live voice session is not routed here. No model fan-out occurs for
    ordinary requests: critics run only for enabled, complex reasoning tasks.
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
            target = str(settings.get("gemma_model") or GEMMA_VISION_MODEL)
            response = _gemma_call(contents, config, timeout_ms, target)
        elif task == "review":
            target = str(settings.get("gemma_model") or GEMMA_VISION_MODEL)
            response = _gemma_call(contents, config, timeout_ms, target)
        else:
            primary_id = _provider_for(task, provider_override)
            if not primary_id:
                raise RouterError(f"no provider assigned to the {task} route")
            target = primary_id
            try:
                response = _provider_call(primary_id, contents, config, timeout_ms, task)
            except Exception as primary_error:
                alternate_id = _alternate_provider(primary_id, provider_override)
                can_retry = bool(settings.get("retry_on_transient", False))
                if not (can_retry and _is_transient_error(primary_error)):
                    raise
                retry_chars = _reserve(contents, settings)
                try:
                    if alternate_id:
                        response = _provider_call(alternate_id, contents, config, timeout_ms, task)
                        retry_target = alternate_id
                    else:
                        # If there is no second configured $0 provider, Gemma is
                        # the single permitted independent retry for text tasks.
                        if task in ("vision", "review"):
                            raise RouterError("no independent free retry route is available")
                        retry_target = str(settings.get("gemma_model") or GEMMA_VISION_MODEL)
                        response = _gemma_call(contents, config, timeout_ms, retry_target)
                    target = f"{primary_id} -> {retry_target}"
                    _audit(task, target, "retry_success", retry_chars,
                           int((time.monotonic() - started) * 1000))
                except Exception as retry_error:
                    _audit(task, f"{primary_id} -> {alternate_id or 'gemma'}", "retry_failed",
                           retry_chars, int((time.monotonic() - started) * 1000),
                           type(retry_error).__name__)
                    raise RouterError(
                        "primary model was temporarily unavailable and the one permitted retry failed"
                    ) from retry_error

            answer = _verify_response(task, response, config)
            if _needs_second_opinion(task, contents, settings):
                reviewer_id = str(settings.get("gemma_model") or GEMMA_VISION_MODEL)
                improved = _second_opinion(
                    contents, answer, reviewer_id, settings, timeout_ms)
                if improved:
                    answer = improved
                    response = RouterReply(answer)
            if isinstance(response, RouterReply):
                response.text = answer
            else:
                try:
                    response.text = answer
                except Exception:
                    response = RouterReply(answer)

        answer = _verify_response(task, response, config)
        if isinstance(response, RouterReply):
            response.text = answer
        _audit(task, target or "unassigned", "ok",
               chars, int((time.monotonic() - started) * 1000))
        return response
    except RouterError as exc:
        _audit(task, target or "unassigned", "blocked", chars,
               int((time.monotonic() - started) * 1000), type(exc).__name__)
        raise
    except Exception as exc:
        _audit(task, target or "unassigned", "error", chars,
               int((time.monotonic() - started) * 1000), type(exc).__name__)
        raise RouterError(f"{task} route failed ({type(exc).__name__}); no further provider was tried") from exc
