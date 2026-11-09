"""Offline regression tests for cost-safe provider fallbacks."""
import unittest
from unittest.mock import patch

from core import gemini
from core import model_router as router


class ProviderFallbackGuardTests(unittest.TestCase):
    def test_fallback_requires_explicit_zero_cost_acknowledgement(self):
        with patch.object(router, "_settings", return_value={
            "zero_cost_confirmed": False,
        }), patch.object(router, "_assert_zero_cost_provider") as allowlist, \
             patch.object(router, "_reserve") as reserve:
            with self.assertRaises(router.RouterError):
                gemini._guard_provider_fallback("openai_compat@groq", "hello")
        allowlist.assert_not_called()
        reserve.assert_not_called()

    def test_paid_provider_is_rejected_before_budget_reservation(self):
        settings = {"zero_cost_confirmed": True}
        with patch.object(router, "_settings", return_value=settings), \
             patch.object(router, "_assert_zero_cost_provider",
                          side_effect=router.RouterError("not on $0 allowlist")) as allowlist, \
             patch.object(router, "_reserve") as reserve:
            with self.assertRaises(router.RouterError):
                gemini._guard_provider_fallback("openai_compat@groq", "hello")
        allowlist.assert_called_once_with("openai_compat@groq")
        reserve.assert_not_called()

    def test_allowlisted_provider_reserves_budget(self):
        settings = {
            "zero_cost_confirmed": True, "daily_calls": 2,
            "daily_input_chars": 100, "max_input_chars": 100,
        }
        with patch.object(router, "_settings", return_value=settings), \
             patch.object(router, "_assert_zero_cost_provider") as allowlist, \
             patch.object(router, "_reserve", return_value=5) as reserve:
            gemini._guard_provider_fallback("openai_compat@zai", "hello")
        allowlist.assert_called_once_with("openai_compat@zai")
        reserve.assert_called_once_with("hello", settings)

    def test_exhausted_budget_blocks_fallback(self):
        settings = {"zero_cost_confirmed": True}
        with patch.object(router, "_settings", return_value=settings), \
             patch.object(router, "_assert_zero_cost_provider"), \
             patch.object(router, "_reserve",
                          side_effect=router.RouterError("daily call limit reached")) as reserve:
            with self.assertRaises(router.RouterError):
                gemini._guard_provider_fallback("openai_compat@zai", "hello")
        reserve.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
