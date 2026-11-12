"""Model compatibility rules for the Gemini Live session.

Keep this module dependency-free so main.py and the offline tests can use it
without importing the Google SDK or opening a network connection.
"""

PRIMARY_LIVE_MODEL = "models/gemini-3.8-live"

# Keep the older endpoints as real fallbacks for keys/projects that do not yet
# have access to the stable model. Google currently recommends 3.8 Live for new
# low-latency voice agents.
LIVE_MODEL_FALLBACKS = (
    PRIMARY_LIVE_MODEL,
    # Preserve context-aware listening on quota fallback: 2.5 Native Audio
    # supports configurable Proactive Audio; 3.1 Live does not.
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
    "models/gemini-3.1-flash-live-preview",
)


def blocking_tool_declarations(declarations):
    """Make the Live tool protocol match MARK's current receive loop.

    MARK awaits each tool and sends its FunctionResponse before continuing to
    consume the next model event. Gemini 3.8 Live defaults to asynchronous tool
    behavior, so BLOCKING must be explicit until the client implements the
    complete async scheduling/cancellation lifecycle.
    """
    result = []
    for declaration in declarations or ():
        if not isinstance(declaration, dict):
            raise TypeError("Live API tool declarations must be dictionaries")
        item = dict(declaration)
        item["behavior"] = "BLOCKING"
        result.append(item)
    return result


def supports_legacy_thinking_budget(model_name: str) -> bool:
    """Whether MARK's legacy thinking_budget=-1 setting is valid for this model.

    Gemini 3.8's standard Live endpoint omits thinking config entirely. Its
    Extended Thinking endpoint uses thinking_level=low|medium|high and has a
    different asynchronous interaction lifecycle; never pass the legacy
    thinking_budget field to either 3.8 endpoint.
    """
    name = str(model_name or "").strip().lower().rsplit("/", 1)[-1]
    return name not in {
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
    }


def supports_live_thinking_config(model_name: str) -> bool:
    """Backward-compatible name used by main.py for legacy thinking config.

    Keep the entry-point import stable while the policy uses the more explicit
    supports_legacy_thinking_budget name. This intentionally does not enable
    Gemini 3.8 Extended Thinking's separate async thinking-level protocol.
    """
    return supports_legacy_thinking_budget(model_name)



def live_proactive_audio_mode(model_name: str) -> str:
    """Return the Live model's Proactive Audio contract.

    Gemini 3.8 has Proactive Audio permanently enabled; Gemini 2.5 Native
    Audio supports an explicit config flag; Gemini 3.1 Live does not support it.
    Unknown endpoints fail closed rather than being assumed context-aware.
    """
    name = str(model_name or "").strip().lower().rsplit("/", 1)[-1]
    if name in {
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
    }:
        return "always_on"
    if name in {
        "gemini-2.5-flash-native-audio-preview-12-2025",
        "gemini-2.5-flash-native-audio-latest",
    }:
        return "configurable"
    return "unsupported"


def supports_proactive_audio(model_name: str, preference: bool = True,
                             runtime_disabled: bool = False) -> bool:
    """Whether the active Live model will actually filter unaddressed speech."""
    mode = live_proactive_audio_mode(model_name)
    if mode == "always_on":
        return True
    return bool(mode == "configurable" and preference and not runtime_disabled)



def live_model_candidates(models, proactive_audio_enabled: bool = True,
                          runtime_disabled_models=(),
                          local_microphone_gate_enabled: bool = False) -> tuple[str, ...]:
    """Choose fallbacks without silently streaming room audio to an incapable model.

    A model without Proactive Audio is eligible only when the user has selected
    a local microphone gate (wake word or push-to-talk). In the default
    address-aware mode, unsupported endpoints are excluded rather than
    degrading privacy behind the user's back.
    """
    candidates = tuple(str(model) for model in (models or ()) if str(model).strip())
    if local_microphone_gate_enabled:
        return candidates
    disabled = {str(model) for model in (runtime_disabled_models or ())}
    return tuple(
        model for model in candidates
        if supports_proactive_audio(
            model, proactive_audio_enabled, model in disabled
        )
    )
