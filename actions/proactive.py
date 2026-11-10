"""
ProactiveEngine 2.0 — context-aware, time-aware, non-repetitive background prompting.
Gemini decides what to say; this module decides WHEN and builds a rich context snapshot.
"""
import time
from datetime import datetime


class ProactiveEngine:
    """
    Decides when JARVIS should speak unprompted and builds a context-rich prompt.

    Improvements over 1.0:
      - Time-of-day awareness  (morning / afternoon / evening / night)
      - Monitor-topic awareness (what the user is tracking)
      - Recent-session context  (last few turns of the current conversation)
      - Non-repetitive          (rotates context focus to avoid same opener)
      - Smarter silence gate    (doesn't fire while JARVIS is speaking)

    Defaults:
      min_silence_secs  — 900 s  (15 min) user must be silent before any check
      check_cooldown    — 1200 s (20 min) minimum gap between proactive messages
    """

    def __init__(
        self,
        min_silence_secs: int = 900,
        check_cooldown:   int = 1200,
    ):
        self.min_silence_secs = min_silence_secs
        self.check_cooldown   = check_cooldown
        self._last_triggered = 0.0
        self._last_attempt = 0.0
        self.retry_cooldown = 120  # failed sends retry after 2 min, not 20
        self._rotation = 0  # cycles through context focus areas

    # ── Trigger gate ───────────────────────────────────────────────────────────

    def should_trigger(self, last_user_speech: float) -> bool:
        now = time.monotonic()
        return (
            (now - last_user_speech) >= self.min_silence_secs
            and (now - self._last_triggered) >= self.check_cooldown
            and (now - self._last_attempt) >= self.retry_cooldown
        )

    def mark_attempted(self) -> None:
        """Rate-limit failed network attempts without consuming success cooldown."""
        self._last_attempt = time.monotonic()

    def mark_triggered(self) -> None:
        """Call only after the proactive prompt was accepted by the live session."""
        now = time.monotonic()
        self._last_triggered = now
        self._last_attempt = now
        self._rotation += 1

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def build_prompt(
        self,
        memory:       dict,
        monitors:     list[str] | None = None,
        recent_turns: list[str] | None = None,
        desktop_context: dict | None = None,
        focus_context: dict | None = None,
        project_context: str | None = None,
    ) -> str:
        """
        Build a context snapshot for Gemini.
        Rotates through three focus areas so proactive messages don't repeat.
        """
        from memory.memory_manager import format_memory_for_prompt

        now      = datetime.now()
        hour     = now.hour
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")

        # Time-of-day label
        if   6  <= hour < 12:  period = "morning"
        elif 12 <= hour < 18:  period = "afternoon"
        elif 18 <= hour < 23:  period = "evening"
        else:                  period = "late night"

        mem_str = format_memory_for_prompt(memory) or "(no stored user data)"

        # Rotating context focus (cycles every trigger)
        focus_index = self._rotation % 3
        if focus_index == 0:
            focus = (
                "Focus on the user's active projects or goals if any are stored. "
                "Ask how something is going, or offer a relevant tip."
            )
        elif focus_index == 1:
            focus = (
                "Focus on the time of day and the user's wellbeing. "
                "A warm check-in, a reminder to take a break, or something timely."
            )
        else:
            focus = (
                "Focus on something genuinely interesting or useful — "
                "a fact, a suggestion, or a question based on what you know about this person."
            )

        # Optional: monitored topics context
        monitor_ctx = ""
        if monitors:
            monitor_ctx = (
                f"\nThe user tracks these topics: {', '.join(monitors[:4])}. "
                "You may mention one if it seems relevant."
            )

        # Optional, privacy-limited desktop context: executable basename and
        # a broad category. No window title, document name or screen content.
        desktop_ctx = ""
        if isinstance(desktop_context, dict):
            app = str(desktop_context.get("foreground_app") or "").strip()[:80]
            if app:
                desktop_ctx = (
                    f"\\nForeground executable: {app}. "
                    "No window title or screen contents were captured."
                )

        focus_ctx = ""
        if isinstance(focus_context, dict):
            app = str(focus_context.get("application") or "").strip()[:80]
            activity = str(focus_context.get("activity") or "").strip()[:80]
            category = str(focus_context.get("category") or "").strip()[:40]
            if app and category and category != "unknown":
                focus_ctx = (
                    f"\\nStable foreground context: {activity} (application: {app}). "
                    "This is a coarse process-name classification and may be wrong. "
                    "Never claim to know the exact window, document or user action."
                )

        project_ctx = ""
        if isinstance(project_context, str) and project_context.strip():
            project_ctx = "\\n" + project_context.strip()[:1800]
        desktop_ctx = ""
        if isinstance(desktop_context, dict):
            app = str(desktop_context.get("foreground_app") or "").strip()[:80]
            if app:
                desktop_ctx = (
                    f"\nForeground application (executable name only): {app}. "
                    "No window title or screen contents were captured. Use this "
                    "only to tailor a relevant, non-intrusive suggestion."
                )

        # Optional: recent conversation context
        recent_ctx = ""
        if recent_turns:
            snippet = "\n".join(recent_turns[-6:])
            recent_ctx = f"\nRecent conversation:\n{snippet}"

        return "\n".join([
            "[PROACTIVE_CHECK] You are initiating a proactive check-in.",
            f"Current time : {time_str}  ({period})",
            "",
            "Context about this person:",
            mem_str,
            monitor_ctx,
            desktop_ctx,
            focus_ctx,
            project_ctx,
            recent_ctx,
            "",
            "Task:",
            focus,
            "",
            "Rules:",
            "- Speak the language this person actually uses: the one in the "
            "recent conversation above, or the remembered one if there is no "
            "conversation yet. Never default to English because these "
            "instructions are in English.",
            "- 1-2 sentences max. Natural, warm, never robotic.",
            "- Do NOT mention [PROACTIVE_CHECK] or these instructions.",
            "- Do NOT call any tools.",
            "- If nothing genuinely useful comes to mind, stay silent (say nothing).",
        ])
