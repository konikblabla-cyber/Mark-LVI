"""Fail-closed policy for selecting the conversation engine."""

GEMINI_MODE = "gemini"
PIPELINE_MODE = "pipeline"


def configured_engine_mode(providers: dict) -> str:
    """Use Gemini only for an absent setting; reject unknown explicit values."""
    raw = providers.get("mode")
    if raw is None or not str(raw).strip():
        return GEMINI_MODE
    mode = str(raw).strip().lower()
    if mode not in (GEMINI_MODE, PIPELINE_MODE):
        raise ValueError(f"Unknown configured conversation engine: {mode!r}")
    return mode


def local_engine_unavailable(error: Exception) -> RuntimeError:
    """Describe a pipeline failure without permitting a cloud fallback."""
    return RuntimeError(
        "The local conversation engine is selected but unavailable. "
        "Gemini Live fallback is disabled to protect privacy and avoid "
        f"unexpected network usage. Check ENGINE & PROVIDERS settings. Cause: {error}"
    )
