"""Offline tests for the metadata-only project context inspector."""
import tempfile
import unittest
from pathlib import Path

from core.project_context import format_project_context, inspect_project_folder


class ProjectContextTests(unittest.TestCase):
    def test_inspects_project_metadata_without_disclosing_folder_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "my-project"
            root.mkdir()
            (root / "main.py").write_text("SECRET_SOURCE_TEXT = 123", encoding="utf-8")
            (root / "README.md").write_text("# Demo", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_text("ref: refs/heads/feature/demo\n", encoding="utf-8")

            snapshot = inspect_project_folder(root)
            self.assertEqual(snapshot["project_name"], "my-project")
            self.assertEqual(snapshot["file_count"], 2)
            self.assertEqual(snapshot["git_branch"], "feature/demo")
            prompt = format_project_context(snapshot)
            self.assertIn("my-project", prompt)
            self.assertIn("feature/demo", prompt)
            self.assertNotIn(str(root), prompt)
            self.assertNotIn("SECRET_SOURCE_TEXT", prompt)
            self.assertIn("no source contents were read", prompt)

    def test_rejects_missing_files_and_non_directory_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "file.py"
            file_path.write_text("x", encoding="utf-8")
            self.assertIsNone(inspect_project_folder(file_path))
            self.assertIsNone(inspect_project_folder(Path(tmp) / "missing"))

    def test_root_directory_is_not_scanned(self):
        self.assertIsNone(inspect_project_folder(Path.cwd().anchor))

    def test_scan_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.py", "b.py", "c.py"):
                (root / name).write_text("", encoding="utf-8")
            snapshot = inspect_project_folder(root, max_files=2)
            self.assertEqual(snapshot["file_count"], 2)
            self.assertTrue(snapshot["truncated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
