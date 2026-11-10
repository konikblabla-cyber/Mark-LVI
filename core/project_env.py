"""Per-project Python environments and safe dependency specifications."""
import os
import re
import subprocess
import sys
from pathlib import Path


_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9._,-]+\])?"
    r"(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9][A-Za-z0-9.*+!_-]*"
    r"(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9][A-Za-z0-9.*+!_-]*)*)?$"
)


def dependency_name(spec: str) -> str:
    """Return the package name or reject pip options, URLs and local paths."""
    if not isinstance(spec, str):
        raise TypeError("Dependency entries must be text.")
    value = spec.strip()
    match = _REQUIREMENT_RE.fullmatch(value)
    if not match:
        raise ValueError(
            f"Unsupported dependency specification: {value!r}. "
            "Use a package name with optional version constraints."
        )
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


_NPM_REQUIREMENT_RE = re.compile(
    r"^(?P<name>(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*)"
    r"(?:@(?P<version>[A-Za-z0-9.*+!~_-]+))?$",
    re.IGNORECASE,
)


def npm_dependency_name(spec: str) -> str:
    """Accept registry package names and simple versions; reject URLs and flags."""
    if not isinstance(spec, str):
        raise TypeError("Node dependency entries must be text.")
    value = spec.strip()
    match = _NPM_REQUIREMENT_RE.fullmatch(value)
    if not match or any(ch in value for ch in "&|<>%^$!"):
        raise ValueError(f"Unsupported npm dependency specification: {value!r}")
    name = match.group("name").lower()
    if name.startswith("@") and "/" not in name:
        raise ValueError("Scoped npm packages must include a package name.")
    return name


def npm_install_invocation(npm_path: str, dependencies: list[str],
                           windows: bool | None = None):
    """Build a safe npm invocation, including npm.cmd on Windows."""
    for value in dependencies:
        npm_dependency_name(value)
    args = [
        str(npm_path), "install", "--ignore-scripts", "--no-audit", "--no-fund", "--save"
    ] + dependencies
    on_windows = os.name == "nt" if windows is None else bool(windows)
    if on_windows and str(npm_path).lower().endswith((".cmd", ".bat")):
        # All arguments are validated registry package specs, so they cannot
        # inject shell operators or additional commands into cmd.exe.
        return subprocess.list2cmdline(args), True
    return args, False


def project_venv_python(project_dir: Path, create: bool = False,
                        timeout: int = 180) -> Path | None:
    """Return this project's interpreter, creating .venv only when requested."""
    candidate = Path(project_dir)
    if candidate.is_symlink():
        raise ValueError("Project folder cannot be a symbolic link.")
    root = candidate.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project path is not a directory.")

    env_dir = root / ".venv"
    if env_dir.is_symlink():
        raise ValueError("Project virtual environment cannot be a symbolic link.")

    interpreter = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if interpreter.is_file():
        return interpreter
    if env_dir.exists():
        raise RuntimeError("The project's .venv folder exists but is incomplete; refusing to replace it.")
    if not create:
        return None

    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(env_dir)],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(root)
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Creating the project's virtual environment timed out.") from exc

    if result.returncode != 0 or not interpreter.is_file():
        detail = (result.stderr or result.stdout or "venv did not create its Python interpreter.")[:300]
        raise RuntimeError(f"Could not create the project's .venv: {detail}")
    return interpreter
