"""Offline process-name alias tests for cross-platform app identity."""
import unittest
from unittest.mock import patch

from core import appidentity


class ProcessNameAliasTests(unittest.TestCase):
    def test_linux_libreoffice_launcher_matches_real_runtime_processes(self):
        with patch.object(appidentity, "_OS", "Linux"):
            variants = appidentity.spelling_variants("libreoffice")
        self.assertEqual(variants, ["libreoffice", "soffice", "sofficebin", "oosplash"])

    def test_linux_office_app_alias_expands_to_libreoffice_launcher(self):
        with patch.object(appidentity, "_OS", "Linux"):
            self.assertEqual(appidentity.launcher_name("word"), "libreoffice --writer")

    def test_process_aliases_do_not_leak_into_unrelated_names(self):
        with patch.object(appidentity, "_OS", "Linux"):
            variants = appidentity.spelling_variants("msedgewebview2")
        self.assertNotIn("edge", variants)


if __name__ == "__main__":
    unittest.main(verbosity=2)
