"""Offline tests for dashboard network exposure defaults."""
import unittest

from core.dashboard_policy import (
    dashboard_bind_host, dashboard_display_host, dashboard_https_requested,
)


class DashboardPolicyTests(unittest.TestCase):
    def test_https_is_default(self):
        self.assertTrue(dashboard_https_requested({}))

    def test_explicit_http_opt_out_is_local_only(self):
        self.assertFalse(dashboard_https_requested({"dashboard_https": False}))
        self.assertEqual(dashboard_bind_host(False), "127.0.0.1")
        self.assertEqual(dashboard_display_host(False, "192.168.1.20"), "127.0.0.1")

    def test_tls_dashboard_can_listen_on_lan(self):
        self.assertEqual(dashboard_bind_host(True), "0.0.0.0")
        self.assertEqual(dashboard_display_host(True, "192.168.1.20"), "192.168.1.20")

    def test_malformed_config_does_not_enable_tls(self):
        self.assertFalse(dashboard_https_requested(None))
        self.assertFalse(dashboard_https_requested([]))


if __name__ == "__main__":
    unittest.main()
