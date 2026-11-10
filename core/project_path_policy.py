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


def resolve_project_path_without_symlinks(project_dir: Path, relative_path: str) -> Path:
    """Resolve an existing-project path while refusing every symlink component."""
    root_arg = Path(project_dir)
    if root_arg.is_symlink():
        raise ValueError("Project root cannot be a symbolic link.")
    root = root_arg.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project root is not a directory.")

    if not isinstance(relative_path, str):
        raise ValueError("Project path must be text.")
    raw = relative_path.strip()
    normalized = raw.replace("\\", "/")
    if (not raw or "\0" in raw or normalized.startswith("/")
            or PureWindowsPath(raw).drive):
        raise ValueError("Project path must be relative.")

    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Project path contains an unsafe component.")

    current = root
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            raise ValueError("Symbolic links are not allowed in project paths.")
        if index < len(parts) - 1 and current.exists() and not current.is_dir():
            raise ValueError("A project path component is not a directory.")
        try:
            current.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ValueError("Project path resolves outside its root.") from exc
    return current


def atomic_write_project_text(
    project_dir: Path,
    relative_path: str,
    content: str,
    *,
    expected_text: str | None = None,
    expect_missing: bool = False,
) -> Path:
    """Atomically write only if the target still matches the scanned state.

    For edits, pass expected_text. For creates, pass expect_missing=True.
    Symlink components, path escapes and concurrent user edits are refused.
    """
    if expect_missing == (expected_text is not None):
        raise ValueError("Specify either expected_text for an edit or expect_missing for a create.")
    if not isinstance(content, str):
        raise ValueError("Project file content must be text.")

    parts = relative_path.replace("\\", "/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Project path contains an unsafe component.")

    root = Path(project_dir).resolve(strict=True)
    # Create parent directories one component at a time, rechecking each one.
    for index in range(1, len(parts)):
        parent_rel = "/".join(parts[:index])
        parent = resolve_project_path_without_symlinks(root, parent_rel)
        if parent.exists():
            if not parent.is_dir():
                raise ValueError("A project path component is not a directory.")
        else:
            parent.mkdir()
        resolve_project_path_without_symlinks(root, parent_rel)

    target = resolve_project_path_without_symlinks(root, relative_path)

    def verify_expected(path: Path) -> int | None:
        if expect_missing:
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"Refusing to overwrite a file created during this operation: {relative_path}")
            return None
        if not path.is_file():
            raise FileNotFoundError(f"Project file disappeared during this operation: {relative_path}")
        existing = path.read_bytes()
        if existing != expected_text.encode("utf-8"):
            raise RuntimeError(f"Project file changed while the agent was working: {relative_path}")
        return stat.S_IMODE(path.stat().st_mode)

    mode = verify_expected(target)
    fd, temp_name = tempfile.mkstemp(
        prefix=".mark-project-write-", suffix=".tmp", dir=str(target.parent)
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp, mode)

        # Re-resolve after generation and re-check the user's original version.
        target = resolve_project_path_without_symlinks(root, relative_path)
        verify_expected(target)
        if expect_missing:
            # Linking is atomic and refuses to replace a concurrently-created file.
            try:
                os.link(temp, target)
            except FileExistsError:
                raise
            except OSError:
                # Portable fallback: exclusive creation never overwrites another file.
                create_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    with os.fdopen(create_fd, "wb") as stream:
                        stream.write(content.encode("utf-8"))
                        stream.flush()
                        os.fsync(stream.fileno())
                except Exception:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
        else:
            os.replace(temp, target)
        return target
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
