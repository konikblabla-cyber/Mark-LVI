"""Offline tests for MARK's opt-in task router.

Run from the repository root:
    python -m core._model_router_tests

No API keys, network calls, or paid requests are made.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import model_router as router


class RouterClassificationTests(unittest.TestCase):
    def test_explicit_task_type_wins(self):
        self.assertEqual(router.classify_task("plain words", "smart", "coding"), "coding")

    def test_unknown_task_type_is_rejected(self):
        with self.assertRaises(router.RouterError):
            router.classify_task("plain words", task_type="unknown")

    def test_image_is_vision(self):
        image = {"inline_data": {"mime_type": "image/jpeg", "data": "AA=="}}
        self.assertEqual(router.classify_task(["describe this", image]), "vision")

    def test_audio_is_not_sent_to_image_router(self):
        audio = {"inline_data": {"mime_type": "audio/wav", "data": "AA=="}}
        self.assertEqual(router.classify_task(["transcribe", audio]), "")

    def test_coding_detected_in_polish_and_english(self):
        self.assertEqual(router.classify_task("Napisz kod w Pythonie"), "coding")
        self.assertEqual(router.classify_task("Please debug this script"), "coding")

    def test_fast_tier_is_fast(self):
        self.assertEqual(router.classify_task("summarize this", "fast"), "fast")

    def test_smart_tier_is_reasoning(self):
        self.assertEqual(router.classify_task("compare these options", "smart"), "reasoning")

    def test_code_review_is_routed_to_review_role(self):
        self.assertEqual(router.classify_task("Please review this code for security issues"), "review")
        self.assertEqual(router.classify_task("Sprawdź kod pod kątem bezpieczeństwa"), "review")

    def test_complex_reasoning_is_detected_for_second_opinion(self):
        settings = {"second_opinion": True, "review_min_chars": 2500}
        self.assertTrue(router._needs_second_opinion(
            "reasoning", "Review the architecture and security trade-offs", settings))
        self.assertFalse(router._needs_second_opinion(
            "reasoning", "What time is it?", settings))
        self.assertFalse(router._needs_second_opinion(
            "fast", "Review the architecture", settings))


class RouterSafetyTests(unittest.TestCase):
    def test_budget_is_reserved_and_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "usage.json"
            settings = {
                "max_input_chars": 20,
                "daily_calls": 1,
                "daily_input_chars": 20,
            }
            with patch.object(router, "_usage_path", return_value=usage):
                self.assertEqual(router._reserve("12345", settings), 5)
                with self.assertRaises(router.RouterError):
                    router._reserve("x", settings)

    def test_budget_ledger_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "usage.json"
            usage.write_text("not json", encoding="utf-8")
            with patch.object(router, "_usage_path", return_value=usage):
                with self.assertRaises(router.RouterError):
                    router._reserve("hello", {"daily_calls": 5, "daily_input_chars": 100})

    def test_provider_messages_preserve_system_prompt(self):
        messages, temperature = router._provider_messages(
            ["hello"], {"system_instruction": "be concise", "temperature": 0.2})
        self.assertEqual(messages[0], {"role": "system", "content": "be concise"})
        self.assertEqual(messages[1], {"role": "user", "content": "hello"})
        self.assertEqual(temperature, 0.2)

    def test_enabled_router_requires_zero_cost_acknowledgement(self):
        with patch.object(router, "_settings", return_value={
            "enabled": True, "zero_cost_confirmed": False,
        }):
            with self.assertRaises(router.RouterError):
                router.dispatch("summarize this", tier="smart")

    def test_enabled_router_blocks_grounded_search(self):
        with patch.object(router, "_settings", return_value={
            "enabled": True, "zero_cost_confirmed": True,
        }):
            with self.assertRaises(router.RouterError):
                router.dispatch("search the web", tier="search")

    def test_enabled_router_uses_configured_fast_provider(self):
        settings = {
            "enabled": True, "zero_cost_confirmed": True,
            "gemma_vision": True, "daily_calls": 3,
            "daily_input_chars": 100, "max_input_chars": 100,
        }
        expected = router.RouterReply("routed")
        with patch.object(router, "_settings", return_value=settings), \
             patch.object(router, "_reserve", return_value=5), \
             patch.object(router, "_provider_for", return_value="openai_compat@groq"), \
             patch.object(router, "_provider_call", return_value=expected) as call, \
             patch.object(router, "_audit"):
            result = router.dispatch("summarize this", tier="fast")
        self.assertIs(result, expected)
        self.assertEqual(call.call_args.args[0], "openai_compat@groq")
        self.assertEqual(call.call_args.args[4], "fast")

    def test_transient_failure_retries_exactly_once_on_other_provider(self):
        settings = {
            "enabled": True, "zero_cost_confirmed": True,
            "retry_on_transient": True, "second_opinion": False,
            "daily_calls": 5, "daily_input_chars": 500, "max_input_chars": 100,
        }
        good = router.RouterReply("recovered")
        with patch.object(router, "_settings", return_value=settings), \
             patch.object(router, "_reserve", return_value=5) as reserve, \
             patch.object(router, "_provider_for", return_value="primary"), \
             patch.object(router, "_alternate_provider", return_value="backup"), \
             patch.object(router, "_provider_call",
                          side_effect=[TimeoutError("request timed out"), good]) as call, \
             patch.object(router, "_audit"):
            result = router.dispatch("summarize this", tier="fast")
        self.assertIs(result, good)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(reserve.call_count, 2)

    def test_auth_failure_does_not_retry(self):
        settings = {
            "enabled": True, "zero_cost_confirmed": True,
            "retry_on_transient": True, "daily_calls": 5,
            "daily_input_chars": 500, "max_input_chars": 100,
        }
        with patch.object(router, "_settings", return_value=settings), \
             patch.object(router, "_reserve", return_value=5), \
             patch.object(router, "_provider_for", return_value="primary"), \
             patch.object(router, "_alternate_provider", return_value="backup"), \
             patch.object(router, "_provider_call", side_effect=RuntimeError("401 invalid API key")) as call, \
             patch.object(router, "_audit"):
            with self.assertRaises(router.RouterError):
                router.dispatch("summarize this", tier="fast")
        self.assertEqual(call.call_count, 1)

    def test_second_opinion_can_improve_complex_reasoning(self):
        settings = {
            "enabled": True, "zero_cost_confirmed": True,
            "second_opinion": True, "review_min_chars": 2500,
            "daily_calls": 5, "daily_input_chars": 10000, "max_input_chars": 10000,
        }
        primary = router.RouterReply("The first draft.")
        reviewer = router.RouterReply(
            '{"verdict":"improve","issues":["missing limitation"],"answer":"The corrected answer."}')
        with patch.object(router, "_settings", return_value=settings), \
             patch.object(router, "_reserve", return_value=20), \
             patch.object(router, "_provider_for", return_value="coding-provider"), \
             patch.object(router, "_alternate_provider", return_value="review-provider"), \
             patch.object(router, "_provider_call", return_value=primary) as call, \
             patch.object(router, "_gemma_call", return_value=reviewer) as critic, \
             patch.object(router, "_audit"):
            result = router.dispatch(
                "Explain the security architecture trade-offs in detail.",
                tier="smart")
        self.assertEqual(result.text, "The corrected answer.")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(critic.call_count, 1)
        self.assertEqual(critic.call_args.args[0][0].startswith("You are the independent reviewer"), True)

    def test_verified_free_zai_model_is_allowed(self):
        config = {"model": "glm-4.7-flash", "base_url": "https://api.z.ai/api/paas/v4"}
        with patch("memory.config_manager.get_provider_config", return_value=config), \
             patch("providers.registry.catalog_entry", return_value={"id": "zai", "local": False}):
            router._assert_zero_cost_provider("openai_compat@zai")

    def test_metered_groq_model_is_blocked(self):
        config = {"model": "openai/gpt-oss-120b", "base_url": "https://api.groq.com/openai/v1"}
        with patch("memory.config_manager.get_provider_config", return_value=config), \
             patch("providers.registry.catalog_entry", return_value={"id": "groq", "local": False}):
            with self.assertRaises(router.RouterError):
                router._assert_zero_cost_provider("openai_compat@groq")

    def test_only_allowlisted_gemma_is_accepted(self):
        self.assertEqual(router.GEMMA_VISION_MODEL, "gemma-4-26b-a4b-it")

    def test_json_output_is_verified(self):
        with self.assertRaises(router.RouterError):
            router._verify_response("reasoning", router.RouterReply("not JSON"),
                                    {"response_mime_type": "application/json"})
        self.assertEqual(
            router._verify_response("reasoning", router.RouterReply('{"ok":true}'),
                                     {"response_mime_type": "application/json"}),
            '{"ok":true}',
        )

    def test_enabled_router_does_not_fallback_on_missing_provider(self):
        with patch.object(router, "_settings", return_value={
            "enabled": True, "zero_cost_confirmed": True, "gemma_vision": False,
            "daily_calls": 3, "daily_input_chars": 100, "max_input_chars": 100,
        }), patch.object(router, "_reserve", return_value=5), \
             patch.object(router, "_provider_for", return_value=""):
            with self.assertRaises(router.RouterError):
                router.dispatch("please reason about this", tier="smart")


if __name__ == "__main__":
    unittest.main(verbosity=2)
