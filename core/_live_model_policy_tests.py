"""Offline compatibility tests for the Gemini Live model migration."""
import unittest

from core.live_model_policy import (
    LIVE_MODEL_FALLBACKS,
    PRIMARY_LIVE_MODEL,
    blocking_tool_declarations,
    supports_legacy_thinking_budget,
    supports_live_thinking_config,
    live_proactive_audio_mode,
    supports_proactive_audio,
    live_model_candidates,
)
from core import gemini


class LiveModelPolicyTests(unittest.TestCase):
    def test_stable_gemini_live_model_is_primary(self):
        self.assertEqual(PRIMARY_LIVE_MODEL, "models/gemini-3.8-live")
        self.assertEqual(LIVE_MODEL_FALLBACKS[0], PRIMARY_LIVE_MODEL)
        self.assertIn("models/gemini-3.1-flash-live-preview", LIVE_MODEL_FALLBACKS)
        self.assertEqual(gemini.LIVE_MODELS, LIVE_MODEL_FALLBACKS)
        self.assertIn("gemini-3.8-flash", gemini._LADDERS[gemini.SMART])

    def test_live_fallback_order_preserves_proactive_audio_before_3_1(self):
        self.assertEqual(
            LIVE_MODEL_FALLBACKS[1],
            "models/gemini-2.5-flash-native-audio-preview-12-2025",
        )
        self.assertEqual(
            LIVE_MODEL_FALLBACKS[2], "models/gemini-3.1-flash-live-preview"
        )

    def test_proactive_audio_capabilities_are_model_specific(self):
        self.assertEqual(live_proactive_audio_mode("models/gemini-3.8-live"), "always_on")
        self.assertEqual(
            live_proactive_audio_mode("models/gemini-2.5-flash-native-audio-preview-12-2025"),
            "configurable",
        )
        self.assertEqual(
            live_proactive_audio_mode("models/gemini-3.1-flash-live-preview"),
            "unsupported",
        )
        self.assertTrue(supports_proactive_audio("models/gemini-3.8-live", False))
        self.assertTrue(supports_proactive_audio(
            "models/gemini-2.5-flash-native-audio-preview-12-2025", True
        ))
        self.assertFalse(supports_proactive_audio(
            "models/gemini-2.5-flash-native-audio-preview-12-2025", False
        ))
        self.assertFalse(supports_proactive_audio(
            "models/gemini-2.5-flash-native-audio-preview-12-2025", True, True
        ))
        self.assertFalse(supports_proactive_audio(
            "models/gemini-3.1-flash-live-preview", True
        ))

    def test_default_address_aware_mode_excludes_models_without_proactive_audio(self):
        self.assertEqual(
            live_model_candidates(LIVE_MODEL_FALLBACKS),
            (
                "models/gemini-3.8-live",
                "models/gemini-2.5-flash-native-audio-preview-12-2025",
            ),
        )

    def test_address_aware_mode_never_uses_unsupported_fallback_when_audio_preference_is_off(self):
        self.assertEqual(
            live_model_candidates(LIVE_MODEL_FALLBACKS, proactive_audio_enabled=False),
            ("models/gemini-3.8-live",),
        )

    def test_runtime_rejection_removes_only_that_models_proactive_fallback(self):
        self.assertEqual(
            live_model_candidates(
                LIVE_MODEL_FALLBACKS,
                runtime_disabled_models=("models/gemini-2.5-flash-native-audio-preview-12-2025",),
            ),
            ("models/gemini-3.8-live",),
        )

    def test_local_wake_or_push_to_talk_allows_legacy_audio_fallback(self):
        self.assertEqual(
            live_model_candidates(
                LIVE_MODEL_FALLBACKS, local_microphone_gate_enabled=True
            ),
            LIVE_MODEL_FALLBACKS,
        )

    def test_live_model_selects_only_from_safe_candidates(self):
        candidates = (
            "models/gemini-2.5-flash-native-audio-preview-12-2025",
            "models/gemini-3.1-flash-live-preview",
        )
        with unittest.mock.patch.object(
            gemini, "_cooling", side_effect=lambda model: model == candidates[0]
        ):
            self.assertEqual(gemini.live_model(allowed_models=candidates), candidates[1])
        with unittest.mock.patch.object(gemini, "_cooling", return_value=True):
            self.assertEqual(gemini.live_model(allowed_models=candidates), candidates[0])
        with self.assertRaises(RuntimeError):
            gemini.live_model(allowed_models=())

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
