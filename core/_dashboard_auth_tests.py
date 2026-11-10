"""Tests for cookie-backed dashboard sessions and WebSocket origins."""
import unittest

from core.dashboard_auth_policy import (
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_TTL_SECONDS,
    session_cookie_options,
    websocket_origin_matches,
)


class DashboardAuthPolicyTests(unittest.TestCase):
    def test_cookie_is_http_only_secure_on_tls_and_same_site(self):
        options = session_cookie_options(True)
        self.assertEqual(SESSION_COOKIE_NAME, "jarvis_session")
        self.assertTrue(options["httponly"])
        self.assertTrue(options["secure"])
        self.assertEqual(options["samesite"], "strict")
        self.assertEqual(options["path"], "/")
        self.assertEqual(options["max_age"], SESSION_COOKIE_TTL_SECONDS)

    def test_loopback_http_cookie_is_not_secure(self):
        self.assertFalse(session_cookie_options(False)["secure"])

    def test_cookie_lifetime_is_bounded(self):
        self.assertEqual(
            session_cookie_options(True, 10**9)["max_age"],
            SESSION_COOKIE_TTL_SECONDS,
        )
        self.assertEqual(session_cookie_options(True, 0)["max_age"], 1)

    def test_websocket_origin_requires_exact_host_and_port(self):
        self.assertTrue(
            websocket_origin_matches("https://192.168.1.5:8001", "192.168.1.5:8001")
        )
        self.assertTrue(
            websocket_origin_matches("http://127.0.0.1:8000", "127.0.0.1:8000")
        )
        self.assertFalse(
            websocket_origin_matches("https://192.168.1.5", "192.168.1.5:8001")
        )
        self.assertFalse(
            websocket_origin_matches("https://attacker.example", "192.168.1.5:8001")
        )

    def test_null_and_malformed_origins_are_rejected(self):
        values = (
            "", "null", "javascript:alert(1)",
            "https://user:pass@host:8001", "https://host:8001/path",
        )
        for origin in values:
            with self.subTest(origin=origin):
                self.assertFalse(websocket_origin_matches(origin, "host:8001"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
