from pathlib import Path

from codex_agy.permissions import routine_operation


def test_worktree_auto_permission_limits(tmp_path):
    worktree = tmp_path.resolve()
    expected = {Path("hello.py")}

    def edit(path):
        return {"kind": "edit", "locations": [{"path": str(path)}]}

    def command(value):
        return {"kind": "execute", "title": value, "rawInput": {"CommandLine": value}}

    assert routine_operation(edit(worktree / "hello.py"), worktree, expected)
    assert routine_operation(command("git status"), worktree, expected)
    assert not routine_operation(edit(worktree / "other.py"), worktree, expected)
    assert not routine_operation(edit(worktree.parent / "hello.py"), worktree, expected)
    assert not routine_operation(command("Get-ChildItem ../"), worktree, expected)
    assert not routine_operation(command("git status; npm ci"), worktree, expected)

    other_cwd = command("git status")
    other_cwd["rawInput"]["cwd"] = str(worktree.parent)
    assert not routine_operation(other_cwd, worktree, expected)
