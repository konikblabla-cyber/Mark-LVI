"""Offline tests for generated project path containment."""
import tempfile
import unittest
from pathlib import Path

from core.project_path_policy import resolve_project_file


class ProjectPathPolicyTests(unittest.TestCase):
    def test_accepts_nested_relative_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.assertEqual(
                resolve_project_file(root, "src/main.py"),
                (root / "src" / "main.py").resolve(),
            )

    def test_rejects_absolute_and_traversal_paths(self):
        bad_paths = (
            "/tmp/outside.py",
            "C:\\outside.py",
            "../outside.py",
            "src/../../outside.py",
            "src\\..\\..\\outside.py",
            "\\\\server\\share\\outside.py",
            "file.py:stream",
            "",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            for raw in bad_paths:
                with self.subTest(path=raw):
                    with self.assertRaises(ValueError):
                        resolve_project_file(root, raw)

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as project_temp, tempfile.TemporaryDirectory() as outside_temp:
            root = Path(project_temp) / "project"
            root.mkdir()
            outside = Path(outside_temp)
            link = root / "outside"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            with self.assertRaises(ValueError):
                resolve_project_file(root, "outside/generated.py")


if __name__ == "__main__":
    unittest.main()
