"""Path boundary checks for files generated inside a new project."""
from pathlib import Path, PureWindowsPath


def validate_project_root(projects_dir: Path, project_dir: Path) -> Path:
    """Reject a project directory that redirects writes outside its parent."""
    parent = Path(projects_dir).resolve()
    candidate = Path(project_dir)
    if candidate.is_symlink():
        raise ValueError("Project folder cannot be a symbolic link.")
    root = candidate.resolve(strict=True)
    if root.parent != parent:
        raise ValueError("Project folder must be a direct child of JarvisProjects.")
    return root


def validate_relative_project_path(relative_path: str) -> tuple[str, ...]:
    """Validate cross-platform relative file names before touching the filesystem."""
    if not isinstance(relative_path, str):
        raise ValueError("Project file path must be text.")

    raw = relative_path.strip()
    normalized = raw.replace("\\", "/")
    if not raw or "\0" in raw:
        raise ValueError("Project file path is empty or invalid.")
    if len(normalized) > 220 or normalized.startswith("/") or PureWindowsPath(raw).drive:
        raise ValueError("Project file path is absolute or too long.")

    parts = normalized.split("/")
    reserved = {".venv", "venv", ".git", "__pycache__", "node_modules", ".jarvis_backup"}
    windows_devices = {"CON", "PRN", "AUX", "NUL"}
    windows_devices.update(f"COM{i}" for i in range(1, 10))
    windows_devices.update(f"LPT{i}" for i in range(1, 10))
    for part in parts:
        if part in {"", ".", ".."} or len(part) > 100:
            raise ValueError("Project file path must stay inside the project folder.")
        if part.lower() in reserved:
            raise ValueError("Generated project files cannot use reserved environment or metadata paths.")
        if part.endswith((" ", ".")) or any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part):
            raise ValueError("Project file path contains characters unsupported on Windows.")
        if part.split(".", 1)[0].upper() in windows_devices:
            raise ValueError("Project file path uses a reserved Windows device name.")
    return tuple(parts)


def resolve_project_file(project_dir: Path, relative_path: str) -> Path:
    """Resolve a model-provided path without allowing it outside its project."""
    parts = validate_relative_project_path(relative_path)
    root = validate_project_root(Path(project_dir).parent, project_dir)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symbolic links are not allowed in generated project paths.")

    try:
        current.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ValueError("Project file path resolves outside the project folder.") from exc
    if current == root:
        raise ValueError("Project file path must name a file.")
    return current
def require_new_project_file(project_dir: Path, relative_path: str) -> Path:
    """Return a contained path only when no file or symlink already occupies it."""
    target = resolve_project_file(project_dir, relative_path)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing project file: {relative_path}")
    return target
