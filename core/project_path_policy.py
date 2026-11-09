"""Path boundary checks for files generated inside a new project."""
from pathlib import Path, PureWindowsPath


def resolve_project_file(project_dir: Path, relative_path: str) -> Path:
    """Resolve a model-provided path without allowing it outside its project."""
    if not isinstance(relative_path, str):
        raise ValueError("Project file path must be text.")

    raw = relative_path.strip()
    normalized = raw.replace("\\", "/")
    if not raw or "\0" in raw:
        raise ValueError("Project file path is empty or invalid.")
    if normalized.startswith("/") or PureWindowsPath(raw).drive:
        raise ValueError("Absolute project file paths are not allowed.")

    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Project file path must stay inside the project folder.")
    if any(":" in part for part in parts):
        raise ValueError("Drive and alternate-stream paths are not allowed.")

    root = Path(project_dir).resolve()
    target = root.joinpath(*parts).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Project file path resolves outside the project folder.") from exc
    if target == root:
        raise ValueError("Project file path must name a file.")
    return target
