"""Offline tests for isolated project Python environments."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.project_env import dependency_name, project_venv_python


class ProjectEnvironmentTests(unittest.TestCase):
    def test_accepts_package_names_and_constraints(self):
        self.assertEqual(dependency_name("requests"), "requests")
        self.assertEqual(dependency_name("PyAudio>=0.2.13"), "pyaudio")
        self.assertEqual(dependency_name("torch>=2.1,<3"), "torch")
        self.assertEqual(dependency_name("foo[extra]>=1.0"), "foo")

    def test_rejects_pip_options_urls_and_paths(self):
        for value in ("--index-url=https://example.invalid", "git+https://example.invalid/x.git",
                      "https://example.invalid/pkg.whl", "../local-package", "-r requirements.txt"):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    dependency_name(value)

    def test_accepts_safe_npm_package_specs(self):
        from core.project_env import npm_dependency_name
        self.assertEqual(npm_dependency_name("react@18.2.0"), "react")
        self.assertEqual(npm_dependency_name("@vitejs/plugin-react@^4.0.0"), "@vitejs/plugin-react")

    def test_rejects_npm_urls_flags_and_shell_characters(self):
        from core.project_env import npm_dependency_name
        for value in ("--registry=https://evil.invalid", "git+https://example.invalid/pkg",
                      "file:../local", "react@18&&whoami"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    npm_dependency_name(value)

    def test_builds_safe_cross_platform_npm_invocations(self):
        from core.project_env import npm_install_invocation
        args, shell = npm_install_invocation("npm", ["react@18.2.0"], windows=False)
        self.assertFalse(shell)
        self.assertEqual(args[0], "npm")
        self.assertIn("--ignore-scripts", args)
        command, shell = npm_install_invocation(
            r"C:\Program Files\nodejs\npm.cmd", ["react@^18.2.0"], windows=True
        )
        self.assertTrue(shell)
        self.assertIn("npm.cmd", command)
        self.assertIn("react@^18.2.0", command)

    def test_does_not_create_environment_unless_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.assertIsNone(project_venv_python(root, create=False))
            self.assertFalse((root / ".venv").exists())

    def test_creates_isolated_interpreter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            interpreter = project_venv_python(root, create=True)
            self.assertIsNotNone(interpreter)
            self.assertTrue(interpreter.is_file())
            result = subprocess.run(
                [str(interpreter), "-c", "import sys; print(sys.prefix)"],
                capture_output=True, text=True, timeout=30, check=True
            )
            self.assertEqual(Path(result.stdout.strip()).resolve(), (root / ".venv").resolve())

    def test_refuses_incomplete_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            (root / ".venv").mkdir()
            with self.assertRaises(RuntimeError):
                project_venv_python(root, create=True)

    def test_refuses_symlinked_project(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "real"
            root.mkdir()
            link = base / "linked"
            try:
                link.symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            with self.assertRaises(ValueError):
                project_venv_python(link, create=True)


if __name__ == "__main__":
    unittest.main()
