"""Offline tests for fail-closed conversation engine selection."""
import unittest

from core.engine_policy import (
    GEMINI_MODE, PIPELINE_MODE, configured_engine_mode, local_engine_unavailable,
)


class EnginePolicyTests(unittest.TestCase):
    def test_missing_mode_keeps_legacy_gemini_default(self):
        self.assertEqual(configured_engine_mode({}), GEMINI_MODE)
        self.assertEqual(configured_engine_mode({"mode": "  "}), GEMINI_MODE)

    def test_valid_modes_are_preserved(self):
        self.assertEqual(configured_engine_mode({"mode": "pipeline"}), PIPELINE_MODE)
        self.assertEqual(configured_engine_mode({"mode": " GEMINI "}), GEMINI_MODE)

    def test_unknown_explicit_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            configured_engine_mode({"mode": "local-ish"})

    def test_pipeline_failure_message_disables_cloud_fallback(self):
        error = local_engine_unavailable(RuntimeError("missing local model"))
        self.assertIn("fallback is disabled", str(error))
        self.assertIn("missing local model", str(error))


if __name__ == "__main__":
    unittest.main()
