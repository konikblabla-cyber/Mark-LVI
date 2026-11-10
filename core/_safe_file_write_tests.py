"""Offline tests for atomic code-file writes and undo."""
import tempfile
import unittest
from pathlib import Path

from core import undo
from core.safe_file_write import safe_write_text


class SafeFileWriteTests(unittest.TestCase):
    def setUp(self):
        undo.clear()

    def tearDown(self):
        undo.clear()

    def test_new_file_can_be_undone(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "new.py"
            result = safe_write_text(target, "print('hello')", undo_label="new code")
            self.assertIn("undo available", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "print('hello')")
            self.assertIn("Removed 'new.py'", undo.undo_last())
            self.assertFalse(target.exists())

    def test_existing_file_is_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "old.py"
            target.write_text("original", encoding="utf-8")
            safe_write_text(target, "updated", undo_label="edit code")
            self.assertEqual(target.read_text(encoding="utf-8"), "updated")
            self.assertIn("Restored the previous contents", undo.undo_last())
            self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_undo_does_not_overwrite_later_user_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "main.py"
            target.write_text("before", encoding="utf-8")
            safe_write_text(target, "generated")
            target.write_text("user changed this later", encoding="utf-8")
            result = undo.undo_last()
            self.assertIn("left untouched", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "user changed this later")

    def test_refuses_final_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside.py"
            outside.write_text("keep", encoding="utf-8")
            link = root / "link.py"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            with self.assertRaises(ValueError):
                safe_write_text(link, "must not replace symlink")
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            self.assertTrue(link.is_symlink())

    def test_large_existing_file_is_atomic_but_not_undoable(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "large.py"
            target.write_text("x" * 20, encoding="utf-8")
            result = safe_write_text(target, "new", undo_limit=5)
            self.assertIn("cannot be undone", result)
            self.assertFalse(undo.can_undo())
            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_refuses_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "folder"
            target.mkdir()
            with self.assertRaises(ValueError):
                safe_write_text(target, "not a file")


if __name__ == "__main__":
    unittest.main()
