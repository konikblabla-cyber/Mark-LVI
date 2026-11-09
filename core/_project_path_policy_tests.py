"""Offline tests for generated project path containment."""
import tempfile
import unittest
from pathlib import Path

from core.project_path_policy import (
    require_new_project_file,
    resolve_project_file,
    validate_project_root,
)


class ProjectPathPolicyTests(unittest.TestCase):
    def test_accepts_nested_relative_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.assertEqual(
                resolve_project_file(root, "src/main.py"),
                root / "src" / "main.py",
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

    def test_rejects_symlink_escape_and_internal_symlink(self):
        with tempfile.TemporaryDirectory() as project_temp, tempfile.TemporaryDirectory() as outside_temp:
            root = Path(project_temp) / "project"
            root.mkdir()
            outside = Path(outside_temp)
            (root / "internal").mkdir()
            try:
                (root / "outside").symlink_to(outside, target_is_directory=True)
                (root / "internal_link").symlink_to(root / "internal", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            for raw in ("outside/generated.py", "internal_link/generated.py"):
                with self.subTest(path=raw):
                    with self.assertRaises(ValueError):
                        resolve_project_file(root, raw)

    def test_rejects_project_root_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projects = root / "projects"
            projects.mkdir()
            outside = root / "outside"
            outside.mkdir()
            link = projects / "project"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            with self.assertRaises(ValueError):
                validate_project_root(projects, link)

    def test_refuses_existing_file_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            existing = root / "main.py"
            existing.write_text("user data", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                require_new_project_file(root, "main.py")
            self.assertEqual(existing.read_text(encoding="utf-8"), "user data")


if __name__ == "__main__":
    unittest.main()
