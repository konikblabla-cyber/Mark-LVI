"""Offline tests for aggregate dashboard upload quotas."""
import tempfile
import unittest
from pathlib import Path

from core.dashboard_upload_policy import (
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
    upload_usage,
)


class DashboardUploadPolicyTests(unittest.TestCase):
    def test_missing_directory_has_zero_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(upload_usage(Path(temp) / "not-created"), (0, 0))

    def test_counts_regular_files_and_ignores_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.bin").write_bytes(b"1234")
            (root / "two.bin").write_bytes(b"567")
            (root / "folder").mkdir()
            self.assertEqual(upload_usage(root), (7, 2))

    def test_does_not_follow_or_count_symlink_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "uploads"
            root.mkdir()
            outside = Path(temp) / "outside.bin"
            outside.write_bytes(b"private bytes")
            try:
                (root / "linked.bin").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic link creation is not permitted")
            self.assertEqual(upload_usage(root), (0, 0))

    def test_refuses_symlinked_upload_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            actual = base / "actual"
            actual.mkdir()
            link = base / "uploads"
            try:
                link.symlink_to(actual, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic link creation is not permitted")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                upload_usage(link)

    def test_usage_scan_stops_at_file_count_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(MAX_UPLOAD_FILES + 20):
                (root / f"{index}.bin").touch()
            total, count = upload_usage(root)
            self.assertEqual(total, 0)
            self.assertEqual(count, MAX_UPLOAD_FILES)

    def test_limits_are_finite(self):
        self.assertGreater(MAX_UPLOAD_TOTAL_BYTES, 0)
        self.assertGreater(MAX_UPLOAD_FILES, 0)
        self.assertLessEqual(MAX_UPLOAD_TOTAL_BYTES, 4 * 1024 * 1024 * 1024)
        self.assertLessEqual(MAX_UPLOAD_FILES, 10_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
