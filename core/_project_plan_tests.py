"""Offline tests for generated project plan limits and validation."""
import unittest

from core.project_plan import validate_project_plan


def valid_plan():
    return {
        "project_name": "safe_app",
        "entry_point": "main.py",
        "files": [
            {"path": "lib/helpers.py", "description": "Helpers", "imports": []},
            {"path": "main.py", "description": "Entry point", "imports": ["lib.helpers"]},
        ],
        "run_command": "python main.py",
        "dependencies": ["requests>=2.30"],
    }


class ProjectPlanTests(unittest.TestCase):
    def test_accepts_valid_python_and_versioned_runners(self):
        plan = validate_project_plan(valid_plan())
        self.assertEqual(plan["entry_point"], "main.py")
        self.assertEqual(len(plan["files"]), 2)
        js = valid_plan()
        js["run_command"] = "node main.js"
        js["files"] = [{"path": "main.js", "description": "", "imports": []}]
        js["entry_point"] = "main.js"
        self.assertEqual(validate_project_plan(js)["run_command"], "node main.js")

    def test_accepts_python_package_imports(self):
        plan = {
            "project_name": "pkg_app",
            "entry_point": "main.py",
            "files": [
                {"path": "lib/helpers/__init__.py", "description": "Package", "imports": []},
                {"path": "main.py", "description": "Entry", "imports": ["lib.helpers"]},
            ],
            "run_command": "python main.py",
            "dependencies": [],
        }
        self.assertEqual(validate_project_plan(plan)["entry_point"], "main.py")

    def test_accepts_javascript_relative_imports(self):
        js = {
            "project_name": "web_app",
            "entry_point": "src/main.js",
            "files": [
                {"path": "src/utils.js", "description": "Helpers", "imports": []},
                {"path": "src/main.js", "description": "Entry", "imports": ["./utils.js"]},
            ],
            "run_command": "node src/main.js",
            "dependencies": ["react@18.2.0"],
        }
        result = validate_project_plan(js, language="javascript")
        self.assertEqual(result["files"][1]["imports"], ["./utils.js"])
        py = valid_plan()
        py["run_command"] = "python3.11 main.py"
        self.assertEqual(validate_project_plan(py)["run_command"], "python3.11 main.py")

    def test_rejects_paths_and_duplicate_case_insensitive_names(self):
        for change in (
            lambda p: p["files"][0].update(path="../outside.py"),
            lambda p: p["files"].append({"path": "MAIN.py", "description": "", "imports": []}),
            lambda p: p["files"][0].update(path="CON.py"),
            lambda p: p["files"][0].update(path="bad?.py"),
        ):
            plan = valid_plan()
            change(plan)
            with self.subTest(plan=plan):
                with self.assertRaises(ValueError):
                    validate_project_plan(plan)

    def test_rejects_missing_entry_point_and_unknown_import(self):
        plan = valid_plan()
        plan["entry_point"] = "missing.py"
        with self.assertRaises(ValueError):
            validate_project_plan(plan)
        plan = valid_plan()
        plan["files"][1]["imports"] = ["missing.module"]
        with self.assertRaises(ValueError):
            validate_project_plan(plan)

    def test_rejects_oversized_file_and_dependency_lists(self):
        plan = valid_plan()
        plan["files"] = [
            {"path": f"file_{i}.py", "description": "", "imports": []}
            for i in range(41)
        ]
        with self.assertRaises(ValueError):
            validate_project_plan(plan)
        plan = valid_plan()
        plan["dependencies"] = [f"pkg{i}" for i in range(26)]
        with self.assertRaises(ValueError):
            validate_project_plan(plan)

    def test_rejects_pip_options_and_duplicate_dependencies(self):
        for deps in (["--index-url=https://example.invalid"], ["requests", "Requests"]):
            plan = valid_plan()
            plan["dependencies"] = deps
            with self.subTest(deps=deps):
                with self.assertRaises(ValueError):
                    validate_project_plan(plan)

    def test_rejects_shells_and_unknown_run_commands(self):
        for command in ("cmd.exe /c whoami", "powershell -Command whoami",
                        "/bin/sh main.py", "python main.py\nwhoami"):
            plan = valid_plan()
            plan["run_command"] = command
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    validate_project_plan(plan)

    def test_validates_language_specific_dependencies(self):
        js = valid_plan()
        js["dependencies"] = ["react@18.2.0", "@vitejs/plugin-react@^4.0.0"]
        self.assertEqual(
            validate_project_plan(js, language="javascript")["dependencies"],
            js["dependencies"],
        )
        js["dependencies"] = ["--registry=https://evil.invalid"]
        with self.assertRaises(ValueError):
            validate_project_plan(js, language="javascript")

    def test_generated_file_size_is_bounded_in_utf8_bytes(self):
        from core.project_plan import MAX_GENERATED_FILE_BYTES, validate_generated_code_size
        self.assertEqual(validate_generated_code_size("x" * 100), 100)
        self.assertEqual(
            validate_generated_code_size("ą", "unicode.py"),
            len("ą".encode("utf-8")),
        )
        with self.assertRaises(ValueError):
            validate_generated_code_size("x" * (MAX_GENERATED_FILE_BYTES + 1), "large.py")

    def test_rejects_invalid_project_names(self):
        for name in ("../outside", "bad/name", "", "x" * 61):
            plan = valid_plan()
            plan["project_name"] = name
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_project_plan(plan)


if __name__ == "__main__":
    unittest.main()
