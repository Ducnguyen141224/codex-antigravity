import re
from graphlib import CycleError, TopologicalSorter
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from codex_agy.models import Input

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=16000)]
Risk = Literal["low", "medium", "high"]
Steps = Annotated[list[Text], Field(min_length=1, max_length=100)]


def relative_path(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"", ".", "..", ".git"} for part in value.lower().split("/"))
        or re.search(r'[<>:"|?*\x00-\x1f]', value)
    ):
        raise ValueError("Use a repository-relative file path without glob or traversal.")
    return value


class VerificationCommand(Input):
    argv: Annotated[list[Text], Field(min_length=1, max_length=50)]
    cwd: str = "."
    timeout_seconds: int | None = Field(None, ge=1, le=1800)
    auto_fix_on_failure: bool = False

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value):
        return value if value == "." else relative_path(value)


class Milestone(Input):
    milestone_id: Id
    title: Text
    description: Text
    dependencies: list[Id] = Field(default_factory=list, max_length=50)


class Task(Input):
    task_id: Id
    milestone_id: Id
    title: Text
    description: Text
    objective: Text
    implementation_steps: Steps
    expected_files: list[Text] = Field(max_length=200)
    dependencies: list[Id] = Field(max_length=100)
    acceptance_criteria: Steps
    verification_commands: list[VerificationCommand] = Field(max_length=30)
    estimated_complexity: Risk
    risk_level: Risk
    preflight_commands: list[VerificationCommand] = Field(default_factory=list, max_length=10)
    critical_change: (
        Literal["architecture", "database", "authentication", "payment", "integration"] | None
    ) = None

    @field_validator("expected_files")
    @classmethod
    def validate_files(cls, values):
        return [relative_path(value) for value in values]


def graph_order(graph: dict[str, set[str]], label: str) -> list[str]:
    unknown = {dep for deps in graph.values() for dep in deps} - graph.keys()
    if unknown:
        raise ValueError(f"Unknown {label} dependencies: {sorted(unknown)}")
    try:
        return list(
            TopologicalSorter({key: sorted(deps) for key, deps in graph.items()}).static_order()
        )
    except CycleError as exc:
        raise ValueError(f"Circular {label} dependency: {exc.args[1]}") from exc


class TaskPlan(Input):
    plan_id: Id
    plan_version: int = Field(1, ge=1)
    project_path: Path
    project_context: Text
    user_goal: Text
    functional_requirements: Steps
    nonfunctional_requirements: list[Text] = Field(max_length=100)
    scope: Steps
    out_of_scope: list[Text] = Field(max_length=100)
    constraints: list[Text] = Field(max_length=100)
    architecture_decisions: list[Text] = Field(max_length=100)
    milestones: Annotated[list[Milestone], Field(min_length=1, max_length=50)]
    tasks: Annotated[list[Task], Field(min_length=1, max_length=200)]
    dependencies: list[Id] = Field(max_length=100)
    expected_files: list[Text] = Field(max_length=400)
    implementation_steps: Steps
    acceptance_criteria: Steps
    verification_commands: list[VerificationCommand] = Field(max_length=30)
    risk_assessment: Text
    definition_of_done: Steps
    verification_gate: bool = False
    preflight_commands: list[VerificationCommand] = Field(default_factory=list, max_length=10)
    review_strategy: Literal["legacy", "hybrid"] = "legacy"
    work_size: Literal["small", "medium", "large"] = "small"
    checkpoint_exception_reason: str | None = Field(None, min_length=1, max_length=2000)

    @field_validator("expected_files")
    @classmethod
    def validate_files(cls, values):
        return [relative_path(value) for value in values]

    @model_validator(mode="after")
    def validate_dependencies(self):
        if not self.project_path.is_absolute():
            raise ValueError("project_path must be absolute.")
        task_ids = {task.task_id for task in self.tasks}
        milestone_ids = {milestone.milestone_id for milestone in self.milestones}
        if len(task_ids) != len(self.tasks) or len(milestone_ids) != len(self.milestones):
            raise ValueError("Task and milestone IDs must be unique within their respective lists.")
        if {task.milestone_id for task in self.tasks} != milestone_ids:
            raise ValueError(
                "Every milestone must contain tasks and each task must name a milestone."
            )
        graph_order({task.task_id: set(task.dependencies) for task in self.tasks}, "task")
        if set(self.dependencies) - task_ids:
            raise ValueError("Plan dependency IDs must name tasks in the plan.")
        if self.verification_gate and not self.verification_commands:
            covered = {task.milestone_id for task in self.tasks if task.verification_commands}
            if milestone_ids - covered:
                raise ValueError(
                    "Every gated milestone must have an explicit verification command."
                )
        graph_order(self.milestone_dependencies(), "milestone")
        if self.review_strategy == "hybrid":
            if not self.verification_gate:
                raise ValueError("Hybrid plans require verification_gate=true.")
            target = {"small": 1, "medium": 2}.get(self.work_size)
            if target and len(self.milestones) != target and not self.checkpoint_exception_reason:
                raise ValueError(
                    "Explain checkpoint count deviations in checkpoint_exception_reason."
                )
            dependencies = self.milestone_dependencies()
            for task in self.tasks:
                if (task.critical_change or task.risk_level == "high") and not dependencies[
                    task.milestone_id
                ]:
                    raise ValueError(
                        "Critical or high-risk steps need a preceding review milestone."
                    )
        return self

    def milestone_dependencies(self) -> dict[str, set[str]]:
        owner = {task.task_id: task.milestone_id for task in self.tasks}
        graph = {m.milestone_id: set(m.dependencies) for m in self.milestones}
        for task in self.tasks:
            graph[task.milestone_id].update(
                owner[dep] for dep in task.dependencies if owner[dep] != task.milestone_id
            )
        return graph

    def tasks_for(self, milestone_id: str) -> list[Task]:
        tasks = {task.task_id: task for task in self.tasks}
        order = graph_order({key: set(task.dependencies) for key, task in tasks.items()}, "task")
        return [tasks[key] for key in order if tasks[key].milestone_id == milestone_id]
