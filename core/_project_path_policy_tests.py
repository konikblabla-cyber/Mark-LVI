"""Offline tests for generated project path containment."""
import tempfile
import unittest
from pathlib import Path

from core.project_path_policy import (
    atomic_write_project_text,
    require_new_project_file,
    resolve_project_file,
    resolve_project_path_without_symlinks,
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
            with self.assertRaises(ValueError):
                resolve_project_file(link, "main.py")

    def test_refuses_symlink_alias_to_protected_internal_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            secret = root / ".env"
            secret.write_text("API_KEY=secret", encoding="utf-8")
            alias = root / "ordinary.py"
            try:
                alias.symlink_to(secret)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            with self.assertRaises(ValueError):
                resolve_project_path_without_symlinks(root, "ordinary.py")
            with self.assertRaises(ValueError):
                atomic_write_project_text(root, "ordinary.py", "overwritten", expected_text="API_KEY=secret")
            self.assertEqual(secret.read_text(encoding="utf-8"), "API_KEY=secret")

    def test_atomic_writer_refuses_symlinked_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            real = base / "real"
            real.mkdir()
            link = base / "linked"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            with self.assertRaises(ValueError):
                atomic_write_project_text(link, "main.py", "unsafe", expect_missing=True)
            self.assertFalse((real / "main.py").exists())

    def test_refuses_symlinked_parent_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("keep", encoding="utf-8")
            try:
                (root / "linked").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            with self.assertRaises(ValueError):
                resolve_project_path_without_symlinks(root, "linked/secret.txt")
            with self.assertRaises(ValueError):
                atomic_write_project_text(root, "linked/secret.txt", "bad", expected_text="keep")
            self.assertEqual((outside / "secret.txt").read_text(encoding="utf-8"), "keep")

    def test_atomic_write_rejects_concurrent_user_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            target = root / "main.py"
            target.write_text("original", encoding="utf-8")
            target.write_text("user's newer edit", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                atomic_write_project_text(root, "main.py", "model output", expected_text="original")
            self.assertEqual(target.read_text(encoding="utf-8"), "user's newer edit")

    def test_atomic_create_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            target = root / "main.py"
            target.write_text("user file", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                atomic_write_project_text(root, "main.py", "generated", expect_missing=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "user file")

    def test_refuses_case_variants_of_dependency_and_metadata_dirs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            blocked = (
                ".GIT/config",
                ".Git/hooks/pre-commit",
                "NODE_MODULES/pkg/index.js",
                ".VENV/pyvenv.cfg",
                "Target/debug/app",
            )
            for rel in blocked:
                with self.subTest(path=rel):
                    with self.assertRaises(ValueError):
                        resolve_project_path_without_symlinks(root, rel)
                    with self.assertRaises(ValueError):
                        atomic_write_project_text(root, rel, "bad", expect_missing=True)

    def test_backup_directory_remains_available_for_agent_undo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            backup = atomic_write_project_text(
                root, ".jarvis_backup/restore/main.py", "original", expect_missing=True
            )
            self.assertEqual(backup.read_text(encoding="utf-8"), "original")

    def test_atomic_write_creates_nested_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            target = atomic_write_project_text(root, "src/main.py", "print('ok')", expect_missing=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "print('ok')")

    def test_refuses_windows_device_names_on_every_platform(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            for name in ("CON", "NUL.txt", "COM1.log", "LPT9.data", "bad?.py", "trailing."):
                with self.subTest(path=name):
                    with self.assertRaises(ValueError):
                        resolve_project_path_without_symlinks(root, name)

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
