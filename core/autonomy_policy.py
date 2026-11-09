"""Least-prompt autonomy policy.

Routine, reversible actions should not interrupt the user. Ask only when an
action is critical or has an external effect that the user's current request
did not clearly authorize. Authorization is attached to the actual user turn,
never to model-supplied tool parameters.
"""
from __future__ import annotations

import re
import threading
import time

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_NEGATED_ACTION = re.compile(
    r"\b(?:do\s+not|don't|never|not)\s+(?:click|press|select|choose|submit|"
    r"send|text|message|publish|post|share|upload|delete|remove)\b|"
    r"\bnie\s+(?:klikaj|kliknij|naciskaj|naciskaj|wybieraj|wybierz|"
    r"wysyłaj|wysylaj|wyślij|wyslij|usuwaj|usun|kasuj|publikuj|opublikuj)\b",
    re.IGNORECASE,
)
_NEGATED_SEND = re.compile(
    r"\b(?:do\s+not|don't|never|not)\s+(?:send|text|message|email|dm)\b|"
    r"\bnie\s+(?:wysyłaj|wysylaj|wysyłać|wysylac|wyślij|wyslij|przesyłaj|przesylaj)\b",
    re.IGNORECASE,
)
_SEND_INTENT = re.compile(
    r"\b(?:send|text|message|email|dm)\b|"
    r"\b(?:wyślij|wyslij|prześlij|przeslij|napisz\s+do)\b",
    re.IGNORECASE,
)
_EXTERNAL_ACTION = re.compile(
    r"\b(?:send|text|message|submit|publish|post|share|upload|subscribe|unsubscribe|"
    r"delete|erase|sign|grant|revoke)\b|"
    r"cancel[\s_-]+subscription|"
    r"\b(?:wyślij|wyslij|wysłać|wyslac|prześlij|przeslij|opublikuj|udostępnij|udostepnij|"
    r"usuń|usun|skasuj|usuńcie|usuniecie|subskrybuj|anuluj)\b|"
    r"anuluj[\s_-]+subskrypcj[ęe]",
    re.IGNORECASE,
)
_CRITICAL_ACTION = re.compile(
    r"\\b(?:pay|payment|purchase|buy|checkout|transfer|wire|withdraw|refund|"
    r"destroy|wipe|format|factory|revoke|authorize|security|authentication|"
    r"credential|password|passcode|otp|2fa)\\b|"
    r"\\b(?:permanent|permanently|irreversible)\\b|"
    r"factory[\\s_-]+reset|reset[\\s_-]+(?:pc|computer|system|password|account|security)|"
    r"sign[\\s_-]+(?:contract|document|agreement)|"
    r"change[\\s_-]+(?:password|security)|"
    r"(?:delete|close|terminate|remove)[\\s_-]+(?:account|profile|membership)|"
    r"(?:account|profile|membership)[\\s_-]+(?:deleted|closed)|"
    r"(?:deleted|closed)[\\s_-]+(?:account|profile|membership)|"
    r"clear[\\s_-]+(?:all[\\s_-]+)?(?:data|files)|"
    r"\\b(?:zapłać|zaplac|płatność|platnosc|kup|zakup|przelew|usuń|usun|"
    r"skasuj|zniszcz|sformatuj|wyczyść|wyczysc|password|hasło|haslo)\\b|"
    r"(?:usuń\\w*|usun\\w*|zamknij)[\\s_-]+(?:konto|profil)|"
    r"resetuj[\\s_-]+(?:komputer|system|hasło|haslo|konto|zabezpieczenia)|"
    r"zmień[\\s_-]+hasło|zmien[\\s_-]+haslo|"
    r"podpisz[\\s_-]+(?:umowę|umowe|dokument)|"
    r"bezpieczeństw\\w*|bezpieczenstw\\w*|uwierzyteln\\w*",
    re.IGNORECASE,
)

_SETTING_VERB = re.compile(
    r"\b(?:enable|disable|turn|switch|allow|deny|check|uncheck|tick|untick|"
    r"accept|agree|consent|subscribe|share|activate|deactivate|"
    r"włącz|wlacz|wyłącz|wylacz|zaznacz|odznacz|akceptuj|zgadzam|"
    r"zezwól|zezwol|zablokuj|aktywuj|dezaktywuj)\b",
    re.IGNORECASE,
)
_PRIVACY_TERMS = {
    "consent", "permission", "allow", "authorize", "authorization",
    "agree", "terms", "policy", "acceptance",
    "subscribe", "subscription", "marketing", "privacy", "location",
    "notifications", "tracking", "personalization", "share", "accept",
    "cookies", "cookie", "analytics", "telemetry", "data", "pliki",
    "ciasteczka", "analityka", "telemetria", "zgoda", "regulamin",
    "prywatność", "prywatnosc", "powiadomienia", "lokalizacja",
    "udostępnianie", "udostepnianie", "udostępnij", "udostepnij",
    "pozwól", "pozwol", "zezwól", "zezwol", "subskrypcja",
    "subskrybuj", "śledzenie", "sledzenie", "dane", "danych",
    "przetwarzanie", "przetwarzania", "trenowanie",
}
_DENY_TERMS = {
    "reject", "decline", "deny", "disable", "off", "stop", "not", "no",
    "unsubscribe", "odsubskrybuj",
    "odrzuć", "odrzuc", "odmów", "odmow", "wyłącz", "wylacz",
    "zablokuj", "wyłączone", "wylaczone", "nie",
}
_SCOPE_LOCK = threading.Lock()
_AUTHORIZATION_TTL_SECONDS = 90.0


def new_turn_authorization(user_request: str = "") -> dict:
    """Create a mutable, one-turn authorization scope for trusted app context."""
    return {
        "user_request": str(user_request or "")[:4000],
        "direct_send_used": False,
        "expires_at": time.monotonic() + _AUTHORIZATION_TTL_SECONDS,
    }


def scope_request(scope) -> str:
    """Read the actual user request from app-owned context, never tool args."""
    if not isinstance(scope, dict):
        return ""
    try:
        if time.monotonic() > float(scope.get("expires_at", 0)):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(scope.get("user_request") or "")[:4000]


def _tokens(value: str) -> set[str]:
    return {t.casefold() for t in _TOKEN.findall(str(value or "")) if len(t) > 1}


def _target_mentioned(user_request: str, target: str) -> bool:
    request_tokens = _tokens(user_request)
    target_tokens = _tokens(target)
    if not request_tokens or not target_tokens:
        return False
    # Match exact words or inflected Polish names/verbs by a short shared stem.
    # Exact token overlap only. A fuzzy name match such as Alex/Alexa must
    # never authorize an external action to the wrong person.
    return bool(request_tokens.intersection(target_tokens))


def explicitly_requested_target_action(user_request: str, target: str) -> bool:
    """True only when this turn names the target and explicitly asks to act on it."""
    request = str(user_request or "")
    if not request or _NEGATED_ACTION.search(request):
        return False
    has_action_verb = bool(re.search(
        r"\b(?:click|press|select|choose|submit|send|publish|share|upload|delete|remove|erase|sign|"
        r"wybierz|kliknij|naciśnij|nacisnij|zatwierdź|zatwierdz|usuń|usun|kasuj|"
        r"wyślij|wyslij|opublikuj|udostępnij|udostepnij)\b",
        request, re.IGNORECASE,
    ))
    return has_action_verb and _target_mentioned(request, target)


def requires_ui_confirmation(target: str, user_request: str = "") -> bool:
    """Critical actions always ask; other external effects ask only without scope."""
    target = str(target or "")
    if _CRITICAL_ACTION.search(target):
        return True
    if _EXTERNAL_ACTION.search(target):
        return not explicitly_requested_target_action(user_request, target)
    return False


def requires_checkbox_confirmation(label: str, desired: bool,
                                   session_memory=None) -> bool:
    """Ask only for enabling privacy/consent choices, never for opting out."""
    label = str(label or "")
    if _CRITICAL_ACTION.search(label):
        return True
    if not desired:
        return False
    words = _tokens(label)
    if not words.intersection(_PRIVACY_TERMS):
        return False
    request = scope_request(session_memory)
    request_words = _tokens(request)
    setting_match = any(
        req == term or (len(req) >= 6 and len(term) >= 6 and req[:6] == term[:6])
        for req in request_words for term in words
    )
    if request and _SETTING_VERB.search(request) and setting_match:
        return False
    # A checkbox labelled as a rejection/opt-out is safe to enable.
    if words.intersection(_DENY_TERMS):
        return False
    return True


def consume_explicit_send_authorization(session_memory, receiver: str,
                                        message_text: str) -> bool:
    """Consume one direct, recipient- and exact-content-scoped send request."""

    if not isinstance(session_memory, dict):
        return False
    with _SCOPE_LOCK:
        if session_memory.get("direct_send_used"):
            return False
        request = scope_request(session_memory)
        if not request or _NEGATED_ACTION.search(request) or not _SEND_INTENT.search(request):
            return False
        receiver_tokens = _tokens(receiver)
        request_tokens = _tokens(request)
        if not receiver_tokens or not receiver_tokens.issubset(request_tokens):
            return False
        # Do not let the model invent message content under a recipient-only
        # authorization. The exact text (ignoring case and repeated whitespace)
        # must be present in the user's current request.
        body = " ".join(str(message_text or "").casefold().split())
        request_text = " ".join(request.casefold().split())
        if not body or body not in request_text:
            return False
        session_memory["direct_send_used"] = True
        return True


__all__ = [
    "new_turn_authorization", "scope_request",
    "explicitly_requested_target_action", "requires_ui_confirmation",
    "requires_checkbox_confirmation", "consume_explicit_send_authorization",
]
