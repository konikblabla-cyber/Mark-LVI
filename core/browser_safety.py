"""Pure safety classification helpers shared by browser actions and tests."""
from __future__ import annotations

import re


_SENSITIVE_FIELD = re.compile(
    r"(?:password|passwd|passcode|pin(?: code)?|one[\s_-]*time[\s_-]*(?:password|code|passcode|pin)|"
    r"otp|totp|verification[\s_-]*code|security[\s_-]*code|"
    r"credit[\s_-]*card|card[\s_-]*(?:number|security|verification)|"
    r"cvv|cvc|iban|bank[\s_-]*(?:account|routing)|"
    r"private[\s_-]*key|recovery[\s_-]*phrase|seed[\s_-]*phrase)",
    re.IGNORECASE,
)

_HIGH_IMPACT_TARGET = re.compile(
    r"(?:\bsend\b|\bsubmit\b|\bpay\b|\bpurchase\b|\bbuy\b|"
    r"\bcheckout\b|\bdelete\b|\bremove\b|\bpublish\b|\bpost\b|"
    r"\btransfer\b|\bauthori[sz]e\b|\bsign\b|"
    r"cancel[\s_-]+subscription|close[\s_-]+account)",
    re.IGNORECASE,
)


def is_sensitive_field_descriptor(*values: object) -> bool:
    """True when field metadata suggests a password, code, or payment secret."""
    return bool(_SENSITIVE_FIELD.search(" ".join(str(v or "") for v in values)))


def requires_browser_confirmation(action: str, *target_parts: object) -> bool:
    """Gate explicitly labelled irreversible browser clicks behind HUD approval."""
    action = str(action or "").strip().lower()
    if action not in {"click", "smart_click"}:
        return False
    return bool(_HIGH_IMPACT_TARGET.search(" ".join(str(v or "") for v in target_parts)))


__all__ = ["is_sensitive_field_descriptor", "requires_browser_confirmation"]
