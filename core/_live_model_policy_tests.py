"""Offline compatibility tests for the Gemini Live model migration."""
import unittest

from core.live_model_policy import (
    LIVE_MODEL_FALLBACKS,
    PRIMARY_LIVE_MODEL,
    blocking_tool_declarations,
    supports_legacy_thinking_budget,
    supports_live_thinking_config,
)
from core import gemini


class LiveModelPolicyTests(unittest.TestCase):
    def test_stable_gemini_live_model_is_primary(self):
        self.assertEqual(PRIMARY_LIVE_MODEL, "models/gemini-3.8-live")
        self.assertEqual(LIVE_MODEL_FALLBACKS[0], PRIMARY_LIVE_MODEL)
        self.assertIn("models/gemini-3.1-flash-live-preview", LIVE_MODEL_FALLBACKS)
        self.assertEqual(gemini.LIVE_MODELS, LIVE_MODEL_FALLBACKS)
        self.assertIn("gemini-3.8-flash", gemini._LADDERS[gemini.SMART])

    def test_live_tools_are_explicitly_blocking(self):
        original = [
            {"name": "open_app", "parameters": {"type": "OBJECT"}},
            {"name": "slow_task", "behavior": "NON_BLOCKING"},
        ]
        prepared = blocking_tool_declarations(original)
        self.assertEqual([item["behavior"] for item in prepared], ["BLOCKING", "BLOCKING"])
        self.assertNotIn("behavior", original[0])
        self.assertEqual(original[1]["behavior"], "NON_BLOCKING")
        self.assertEqual(prepared[0]["parameters"], original[0]["parameters"])

    def test_non_mapping_tool_declaration_is_rejected(self):
        with self.assertRaises(TypeError):
            blocking_tool_declarations([object()])

    def test_legacy_thinking_budget_is_not_sent_to_any_gemini_38_live_model(self):
        self.assertFalse(supports_legacy_thinking_budget("models/gemini-3.8-live"))
        self.assertFalse(supports_legacy_thinking_budget(
            "models/gemini-3.8-live-extended-thinking"
        ))
        self.assertTrue(supports_legacy_thinking_budget(
            "models/gemini-3.1-flash-live-preview"
        ))

    def test_main_entry_point_thinking_policy_alias_matches_legacy_policy(self):
        for model in LIVE_MODEL_FALLBACKS:
            self.assertEqual(
                supports_live_thinking_config(model),
                supports_legacy_thinking_budget(model),
            )
