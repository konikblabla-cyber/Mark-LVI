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
    "models/gemini-3.1-flash-live-preview",
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
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
