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

    def test_enabled_router_does_not_fallback_on_missing_provider(self):
        with patch.object(router, "_settings", return_value={
            "enabled": True, "gemma_vision": False,
            "daily_calls": 3, "daily_input_chars": 100, "max_input_chars": 100,
        }), patch.object(router, "_reserve", return_value=5), \
             patch.object(router, "_provider_for", return_value=""):
            with self.assertRaises(router.RouterError):
                router.dispatch("please reason about this", tier="smart")


if __name__ == "__main__":
    unittest.main(verbosity=2)
