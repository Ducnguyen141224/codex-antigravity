from typing import Literal

from pydantic import Field, field_validator

from codex_agy.models import Input, State
from codex_agy.planning import Id, TaskPlan, Text, relative_path


class PlanInput(Input):
    plan: TaskPlan
    idempotency_key: Id | None = None


class ExecutePlan(Input):
    plan_id: Id
    plan_version: int | None = Field(None, ge=1)
    idempotency_key: Id | None = None


class ExecuteMilestone(ExecutePlan):
    milestone_id: Id
    execution_id: Id | None = None


class TaskRef(Input):
    task_id: Id


class ExecutionRef(Input):
    execution_id: Id


class McpCall(TaskRef):
    method: Id


class Wait(TaskRef):
    timeout_seconds: float = Field(40, ge=0, le=50)


class BackgroundWait(TaskRef):
    timeout_seconds: float = Field(1800, ge=0, le=1800)


class Review(TaskRef):
    view: Literal["summary", "diff", "fix_diff", "file"] = "summary"
    path: str | None = None
    review_revision: str | None = Field(None, pattern=r"^[a-f0-9]{40,64}$")
    offset: int = Field(0, ge=0, le=4_000_000)
    limit: int = Field(24000, ge=256, le=64000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value):
        return relative_path(value) if value is not None else None


class Decision(TaskRef):
    review_revision: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    review_summary: Text


class Finding(Input):
    severity: Literal["critical", "high", "medium", "low"]
    description: Text
    file: str | None = None
    line: int | None = Field(None, ge=1)
    expected_behavior: Text
    actual_behavior: Text
    reproduction_steps: list[Text] = Field(min_length=1, max_length=40)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value):
        return relative_path(value) if value is not None else None


class Fix(Decision):
    findings: list[Finding] = Field(min_length=1, max_length=40)


class Retry(TaskRef):
    reason: Text


class TaskList(Input):
    execution_id: Id | None = None
    status: State | None = None
    limit: int = Field(30, ge=1, le=100)
    offset: int = Field(0, ge=0, le=100_000)


class Permission(TaskRef):
    permission_id: Id
    option_id: str = Field(min_length=1, max_length=256)


class Capabilities(Input):
    refresh: bool = False
    verify_session: bool = False


TOOLS = {
    "get_execution_metrics": (
        ExecutionRef,
        "Read persisted review, IPC and MCP counts. Token usage is unavailable unless measured by the Codex host.",
    ),
    "record_mcp_call": (McpCall, "Record a task-bound MCP invocation for local measurement."),
    "submit_plan": (PlanInput, "Validate and store an inspected project plan."),
    "execute_plan": (
        ExecutePlan,
        "Start the plan in a detached worktree and pause at each review.",
    ),
    "execute_milestone": (
        ExecuteMilestone,
        "Start one milestone or continue an existing execution.",
    ),
    "wait_for_completion": (Wait, "Wait for review, a permission request, or a terminal status."),
    "get_task_status": (TaskRef, "Read milestone state and timestamps."),
    "get_task_result": (TaskRef, "Read milestone status and a compact outcome when available."),
    "get_review_context": (Review, "Read the immutable review summary, paginated diff, or file."),
    "request_fix": (Fix, "Submit revision-bound independent findings and queue an AGY fix."),
    "revise_plan": (PlanInput, "Append a new plan version for future executions."),
    "accept_milestone": (Decision, "Accept the current revision after independent review."),
    "reject_milestone": (Decision, "Reject the current revision and halt the execution."),
    "cancel_task": (TaskRef, "Cancel a pending or active task and preserve its workspace."),
    "retry_task": (Retry, "Explicitly retry a stopped task after inspecting its workspace."),
    "list_tasks": (TaskList, "List recent tasks with optional execution and status filters."),
    "respond_permission": (
        Permission,
        "Resolve an active permission request with an offered option.",
    ),
    "health_check": (Input, "Check the running supervisor and its database."),
    "get_system_capabilities": (
        Capabilities,
        "Discover AGY, ACP transport, and supervisor capabilities.",
    ),
}
