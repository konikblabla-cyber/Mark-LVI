"""Offline tests for process timeout bounds."""
import unittest

from core.timeout_policy import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    bounded_timeout,
)


class TimeoutPolicyTests(unittest.TestCase):
    def test_uses_safe_default_for_missing_or_invalid_values(self):
        self.assertEqual(bounded_timeout(None), DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(bounded_timeout("not-a-number"), DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(bounded_timeout(True), DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(bounded_timeout(float("inf")), DEFAULT_TIMEOUT_SECONDS)

    def test_clamps_both_ends(self):
        self.assertEqual(bounded_timeout(-100), MIN_TIMEOUT_SECONDS)
        self.assertEqual(bounded_timeout(0), MIN_TIMEOUT_SECONDS)
        self.assertEqual(bounded_timeout(999999), MAX_TIMEOUT_SECONDS)

    def test_preserves_valid_timeout(self):
        self.assertEqual(bounded_timeout("75"), 75)
        self.assertEqual(bounded_timeout(30), 30)

    def test_clamps_invalid_defaults_too(self):
        self.assertEqual(bounded_timeout(None, default=-5), MIN_TIMEOUT_SECONDS)
        self.assertEqual(bounded_timeout(None, default=99999), MAX_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
