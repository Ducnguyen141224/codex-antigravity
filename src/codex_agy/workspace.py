import hashlib
import os
import shutil
from pathlib import Path

from codex_agy.models import Problem
from codex_agy.process import capture, child_env

MAX_ARTIFACT = 4_000_000


async def git(cwd: Path, *args: str, timeout=25, data: bytes | None = None) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise Problem("GIT_NOT_FOUND", "Git must be installed.")
    env = child_env()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "NUL" if os.name == "nt" else "/dev/null",
            "GIT_CONFIG_KEY_1": "core.fsmonitor",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "core.quotePath",
            "GIT_CONFIG_VALUE_2": "false",
            "GIT_AUTHOR_NAME": "Codex AGY Supervisor",
            "GIT_AUTHOR_EMAIL": "codex-agy@localhost",
            "GIT_COMMITTER_NAME": "Codex AGY Supervisor",
            "GIT_COMMITTER_EMAIL": "codex-agy@localhost",
        }
    )
    code, stdout, stderr = await capture([executable, *args], cwd, timeout, data, env)
    if code:
        raise Problem(
            "GIT_ERROR", f"git {args[0]} failed.", stderr.decode("utf-8", "replace")[:1000]
        )
    return stdout


async def text(cwd, *args) -> str:
    return (await git(cwd, *args)).decode("utf-8").strip()


async def repository(path: Path, allowed_roots: list[Path]):
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise Problem("INVALID_WORKSPACE", "Project directory does not exist.")
    root = Path(await text(root, "rev-parse", "--show-toplevel")).resolve()
    if allowed_roots and not any(root.is_relative_to(parent) for parent in allowed_roots):
        raise Problem("WORKSPACE_DENIED", "Project is outside allowed_roots.")
    common = await text(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return root, os.path.normcase(str(Path(common).resolve()))


async def ensure_worktree(project: Path, path: Path, base: str, repo_key: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        await git(project, "worktree", "add", "--detach", "--", str(path), base, timeout=120)
    root, common = await repository(path, [])
    if (
        root != path.resolve()
        or common != repo_key
        or path.is_symlink()
        or not (path / ".git").is_file()
    ):
        raise Problem("INVALID_WORKTREE", "Stored workspace does not match its Git repository.")
    head = await text(path, "rev-parse", "--abbrev-ref", "HEAD")
    if head != "HEAD":
        raise Problem("WORKTREE_BRANCH_CHANGED", "The managed worktree must remain detached.")


async def status(path: Path) -> bytes:
    return await git(
        path, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"
    )


async def snapshot(path: Path, input_head: str, task_id: str, round_no: int) -> str:
    actual = await text(path, "rev-parse", "HEAD")
    if actual != input_head or await text(path, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
        raise Problem("WORKTREE_HEAD_CHANGED", "AGY or another process changed Git HEAD.")
    await git(path, "add", "-A", "--", ".", timeout=120)
    tree = await text(path, "write-tree")
    commit = (
        (
            await git(
                path,
                "commit-tree",
                tree,
                "-p",
                input_head,
                "-F",
                "-",
                data=f"Codex AGY review {task_id} round {round_no}\n".encode(),
            )
        )
        .decode()
        .strip()
    )
    await git(path, "update-ref", f"refs/codex-agy/reviews/{task_id}/{round_no}", commit)
    await git(path, "update-ref", "--no-deref", "HEAD", commit, input_head)
    return commit


async def recover_snapshot(
    path: Path, task_id: str, round_no: int, expected: str, actual: str
) -> bool:
    try:
        saved = await text(
            path, "rev-parse", "--verify", f"refs/codex-agy/reviews/{task_id}/{round_no}"
        )
        parent = await text(path, "rev-parse", "--verify", f"{saved}^")
    except Problem:
        return False
    if saved != actual or parent != expected:
        return False
    # Restore the recorded base while preserving both the index and working files.
    await git(path, "update-ref", "--no-deref", "HEAD", expected, actual)
    return True


async def changes(path: Path, base: str, revision: str):
    flags = ["--no-ext-diff", "--no-textconv", "--no-renames"]
    names = await git(path, "diff", *flags, "--name-only", "-z", base, revision, "--")
    diff = await git(path, "diff", *flags, "--binary", base, revision, "--", timeout=120)
    summary = await git(path, "diff", *flags, "--stat", base, revision, "--")
    return (
        [value.decode("utf-8", "replace") for value in names.split(b"\0") if value],
        summary.decode("utf-8", "replace")[:12000],
        diff,
        hashlib.sha256(diff).hexdigest(),
    )


async def blob(path: Path, revision: str, name: str) -> bytes:
    spec = f"{revision}:{name}"
    try:
        size = int(await text(path, "cat-file", "-s", spec))
        kind = await text(path, "cat-file", "-t", spec)
    except Problem as exc:
        raise Problem(
            "REVIEW_FILE_NOT_FOUND", "The file is absent from the requested revision."
        ) from exc
    if kind != "blob":
        raise Problem("REVIEW_FILE_NOT_FOUND", "The review path must name a file.")
    if size > MAX_ARTIFACT:
        raise Problem("FILE_TOO_LARGE", "Inspect this file through Git in the review worktree.")
    return await git(path, "cat-file", "blob", spec)
