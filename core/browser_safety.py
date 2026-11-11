"""Pure safety classification helpers shared by browser actions and tests."""
from __future__ import annotations

import re
from urllib.parse import quote_plus


_SENSITIVE_FIELD = re.compile(
    r"(?:password|passwd|passcode|pin(?: code)?|one[\s_-]*time[\s_-]*(?:password|code|passcode|pin)|"
    r"otp|totp|verification[\s_-]*code|security[\s_-]*code|"
    r"credit[\s_-]*card|card[\s_-]*(?:number|security|verification)|"
    r"cvv|cvc|iban|bank[\s_-]*(?:account|routing)|"
    r"private[\s_-]*key|recovery[\s_-]*phrase|seed[\s_-]*phrase|has[lł]o|kod[\s_-]*jednorazowy|kod[\s_-]*weryfikacyjny|karta[\s_-]*(?:płatnicza|platnicza|kredytowa)|numer[\s_-]*karty|rachunek[\s_-]*bankowy|klucz[\s_-]*prywatny|fraza[\s_-]*(?:odzyskiwania|seed))",
    re.IGNORECASE,
)

_HIGH_IMPACT_TARGET = re.compile(
    r"(?:\bsend\b|\bsubmit\b|\bpay\b|\bpurchase\b|\bbuy\b|"
    r"\bcheckout\b|\bplace[\s_-]+order\b|\border[\s_-]+now\b|\bcomplete[\s_-]+order\b|\bdelete\b|\bremove\b|\bpublish\b|\bpost\b|"
    r"\btransfer\b|\bshare\b|\bsubscribe\b|\bstart[\s_-]+trial\b|\bcreate[\s_-]+account\b|\badd[\s_-]+to[\s_-]+(?:cart|basket)\b|\bauthori[sz]e\b|\bsign\b|"
    r"cancel[\s_-]+subscription|close[\s_-]+account|utwórz\s+konto|utworz\s+konto|załóż\s+konto|zaloz\s+konto|dodaj\s+do\s+koszyka|subskrybuj|rozpocznij\s+okres\s+próbny|złóż\s+zamówienie|zloz\s+zamowienie|wyślij|wyslij|prześlij|przeslij|zatwierdź|zatwierdz|zapłać|zaplac|kup(?:\s+teraz)?|zamów|zamow|usuń|usun|skasuj|opublikuj|przenieś|przenies|autoryzuj|podpisz|zakończ\s+zakup|anuluj\s+subskrypcj[ęe]|zamknij\s+konto)",
    re.IGNORECASE,
)


def build_search_url(query: object, engine: object, engines: dict[str, str]) -> str:
    """Encode a search query as one parameter, defaulting unknown engines to Google."""
    name = str(engine or "google").strip().lower()
    base = engines.get(name) or engines.get("google")
    if not isinstance(base, str) or not base:
        raise ValueError("A Google search URL must be configured as the fallback.")
    return base + quote_plus(str(query or ""), safe="")


def is_sensitive_field_descriptor(*values: object) -> bool:
    """True when field metadata suggests a password, code, or payment secret."""
    return bool(_SENSITIVE_FIELD.search(" ".join(str(v or "") for v in values)))


def requires_browser_confirmation(action: str, *target_parts: object) -> bool:
    """Gate explicitly labelled irreversible browser clicks behind HUD approval."""
    action = str(action or "").strip().lower()
    if action not in {"click", "smart_click"}:
        return False
    return bool(_HIGH_IMPACT_TARGET.search(" ".join(str(v or "") for v in target_parts)))


__all__ = ["build_search_url", "is_sensitive_field_descriptor", "requires_browser_confirmation"]
