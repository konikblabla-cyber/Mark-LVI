"""Validation and resource limits for model-generated project plans."""
from __future__ import annotations

import re
import shlex
from typing import Any

from core.project_env import dependency_name, npm_dependency_name
from core.project_path_policy import validate_relative_project_path

MAX_PROJECT_FILES = 40
MAX_PROJECT_DEPENDENCIES = 25
MAX_PROJECT_IMPORTS_PER_FILE = 30
MAX_RUN_COMMAND_CHARS = 512
MAX_GENERATED_FILE_BYTES = 2_000_000

_ALLOWED_RUNNERS = {
    "python", "python3", "py", "node", "npm", "npx",
    "go", "cargo", "dotnet", "java", "ruby", "php", "bash", "sh",
}


def _supported_runner(value: str) -> bool:
    runner = value.lower()
    if runner.endswith(".exe"):
        runner = runner[:-4]
    return runner in _ALLOWED_RUNNERS or bool(re.fullmatch(r"python3\.\d+", runner))



def validate_generated_code_size(code: str, path: str = "generated file") -> int:
    """Reject model output that would create an unexpectedly large source file."""
    if not isinstance(code, str):
        raise ValueError(f"Generated content for {path} must be text.")
    size = len(code.encode("utf-8"))
    if size > MAX_GENERATED_FILE_BYTES:
        raise ValueError(
            f"Generated file {path} exceeds the {MAX_GENERATED_FILE_BYTES}-byte safety limit."
        )
    return size


def validate_project_plan(plan: Any, language: str = "python") -> dict:
    """Reject malformed, oversized, unsafe, or internally inconsistent plans."""
    if not isinstance(plan, dict):
        raise ValueError("Project planner must return a JSON object.")
    lang = str(language or "python").strip().lower()
    is_python = lang in {"python", "py"}
    is_javascript = lang in {"javascript", "typescript", "js", "ts", "node"}

    name = plan.get("project_name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        raise ValueError("Project name is missing or too long.")
    if not re.fullmatch(r"[\w-]+", name.strip(), flags=re.UNICODE):
        raise ValueError("Project name may contain only letters, numbers, underscores and hyphens.")

    files = plan.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_PROJECT_FILES:
        raise ValueError(f"Project plan must contain between 1 and {MAX_PROJECT_FILES} files.")

    normalized_paths: dict[str, str] = {}
    validated_files = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Every project file entry must be an object.")
        path = item.get("path")
        parts = validate_relative_project_path(path)
        canonical = "/".join(parts).casefold()
        if canonical in normalized_paths:
            raise ValueError(f"Project plan contains duplicate file paths: {path}")
        normalized_paths[canonical] = path

        description = item.get("description", "")
        imports = item.get("imports", [])
        if not isinstance(description, str) or len(description) > 800:
            raise ValueError(f"File description is invalid or too long: {path}")
        if not isinstance(imports, list) or len(imports) > MAX_PROJECT_IMPORTS_PER_FILE:
            raise ValueError(f"Import list is invalid or too large: {path}")
        if is_python:
            if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value)
                   for value in imports):
                raise ValueError(f"Project imports must be dotted module names: {path}")
        elif any(not isinstance(value, str) or not value.strip() or len(value) > 200
                 or any(ord(ch) < 32 for ch in value)
                 or value.strip().startswith("../") or value.strip().startswith("..\\")
                 for value in imports):
            raise ValueError(f"Project imports are invalid: {path}")
        validated_files.append({
            "path": "/".join(parts),
            "description": description,
            "imports": list(dict.fromkeys(imports)),
        })

    entry = plan.get("entry_point")
    entry_parts = validate_relative_project_path(entry)
    entry = "/".join(entry_parts)
    if entry.casefold() not in normalized_paths:
        raise ValueError("Project entry point must be one of the generated files.")

    if is_python:
        planned_paths = set(normalized_paths)
        for item in validated_files:
            for module in item["imports"]:
                module_base = module.replace(".", "/")
                module_paths = {module_base + ".py", module_base + "/__init__.py"}
                if not any(candidate.casefold() in planned_paths for candidate in module_paths):
                    raise ValueError(f"Project import is not present in the file plan: {module}")

    dependencies = plan.get("dependencies", [])
    if not isinstance(dependencies, list) or len(dependencies) > MAX_PROJECT_DEPENDENCIES:
        raise ValueError(f"Project plan may declare at most {MAX_PROJECT_DEPENDENCIES} dependencies.")
    if is_javascript:
        names = [npm_dependency_name(value) for value in dependencies]
    elif is_python:
        names = [dependency_name(value) for value in dependencies]
    else:
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200
               or any(ord(ch) < 32 for ch in value) for value in dependencies):
            raise ValueError("Language-native dependencies must be short, plain text entries.")
        names = [value.strip().casefold() for value in dependencies]
    if len(set(names)) != len(names):
        raise ValueError("Project plan contains duplicate dependencies.")

    command = plan.get("run_command", f"python {entry}")
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_RUN_COMMAND_CHARS:
        raise ValueError("Project run command is missing or too long.")
    if any(ord(ch) < 32 for ch in command):
        raise ValueError("Project run command contains control characters.")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("Project run command has invalid quoting.") from exc
    if not argv or len(argv) > 40:
        raise ValueError("Project run command has an invalid number of arguments.")
    if "/" in argv[0] or "\\" in argv[0] or not _supported_runner(argv[0]):
        raise ValueError(f"Unsupported project runner: {argv[0] if argv else ''}")
    if any(len(arg) > 256 for arg in argv[1:]):
        raise ValueError("Project run command contains an argument that is too long.")

    return {
        "project_name": name.strip(),
        "entry_point": entry,
        "files": validated_files,
        "run_command": command.strip(),
        "dependencies": dependencies,
    }
