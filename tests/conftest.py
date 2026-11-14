import socket
from pathlib import Path

import pytest

from codex_agy.config import Settings
from codex_agy.workspace import git


@pytest.fixture
def settings(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return Settings(data_dir=tmp_path / "supervisor data", port=port)


@pytest.fixture
async def repo(tmp_path):
    path = tmp_path / "project có khoảng trắng"
    path.mkdir()
    await git(path, "init", "-b", "main")
    (path / "README.md").write_text("smoke fixture\n", encoding="utf-8")
    await git(path, "add", "--", ".")
    await git(path, "commit", "-m", "fixture")
    return path


def plan_for(repo: Path, count=1) -> dict:
    return {
        "plan_id": "smoke",
        "plan_version": 1,
        "project_path": str(repo),
        "project_context": "Disposable Git fixture.",
        "user_goal": "Add a small Python function.",
        "functional_requirements": ["Return a greeting."],
        "nonfunctional_requirements": [],
        "scope": ["One small Python file per milestone."],
        "out_of_scope": ["External services."],
        "constraints": ["Use only Python standard library."],
        "architecture_decisions": [],
        "milestones": [
            {
                "milestone_id": f"m{i}",
                "title": f"Function {i}",
                "description": f"Create hello{i}.py.",
                "dependencies": [f"m{i - 1}"] if i else [],
            }
            for i in range(count)
        ],
        "tasks": [
            {
                "task_id": f"t{i}",
                "milestone_id": f"m{i}",
                "title": f"Add hello{i}.py",
                "description": f"Create hello{i}.py with a function greet() returning exactly 'hello'.",
                "objective": "Return hello from greet().",
                "implementation_steps": [f"Write only hello{i}.py. Run the verification command."],
                "expected_files": [f"hello{i}.py"],
                "dependencies": [f"t{i - 1}"] if i else [],
                "acceptance_criteria": [f"hello{i}.greet() returns 'hello'."],
                "verification_commands": [
                    {
                        "argv": [
                            "python",
                            "-c",
                            f"import hello{i}; assert hello{i}.greet() == 'hello'",
                        ]
                    }
                ],
                "estimated_complexity": "low",
                "risk_level": "low",
            }
            for i in range(count)
        ],
        "dependencies": [],
        "expected_files": [],
        "implementation_steps": ["Implement in order."],
        "acceptance_criteria": ["Verification passes."],
        "verification_commands": [],
        "risk_assessment": "Low risk.",
        "definition_of_done": ["Independent review accepted."],
    }
