import re
from pathlib import Path


def worktree_path(worktree: Path, value: str) -> Path | None:
    if not value or value.startswith(("\\\\", "//", "\\?")):
        return None
    try:
        path = Path(value)
        if not path.is_absolute() and (path.anchor or path.drive):
            return None
        target = path if path.is_absolute() else worktree / path
        lexical = target.relative_to(worktree)
        if any(part.rstrip(" .").casefold() == ".git" or part == ".." for part in lexical.parts):
            return None
        if any(re.search(r'[<>:"|?*\x00-\x1f]', part) for part in lexical.parts):
            return None
        resolved = target.resolve().relative_to(worktree)
        if any(part.casefold() == ".git" for part in resolved.parts):
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def routine_operation(tool: dict, worktree: Path, expected_files: set[Path]) -> bool:
    kind = tool.get("kind")
    if kind in {"read", "edit"}:
        locations = tool.get("locations") or []
        paths = [worktree_path(worktree, location["path"]) for location in locations]
        return bool(paths) and all(
            path is not None and (kind == "read" or path in expected_files) for path in paths
        )
    raw = tool.get("rawInput")
    if kind != "execute" or not isinstance(raw, dict):
        return False
    # AGY uses the ACP session directory for RunCommand without a directory override.
    # Unknown parameters could change how the shell executes the command.
    keys = {key.casefold(): value for key, value in raw.items()}
    if keys.keys() - {"commandline", "cwd", "safetoautorun", "waitmsbeforeasync"}:
        return False
    if "cwd" in keys and (
        not isinstance(keys["cwd"], str) or worktree_path(worktree, keys["cwd"]) != Path(".")
    ):
        return False
    command = keys.get("commandline")
    if not isinstance(command, str) or tool.get("title") != command:
        return False
    if command in {"git status", "git status --short", "git diff --check HEAD"}:
        return True
    listing = re.fullmatch(r"Get-ChildItem ([-\w./]+)", command, re.ASCII)
    return bool(
        listing
        and not listing[1].startswith("-")
        and worktree_path(worktree, listing[1]) is not None
    )
