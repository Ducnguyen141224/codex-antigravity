import asyncio
import contextlib
import hashlib
import logging
import os
from pathlib import Path
from uuid import uuid4

from codex_agy import acp_client, permissions, verification, workspace
from codex_agy.config import Settings
from codex_agy.models import ACTIVE, BUSY, Problem, State
from codex_agy.planning import TaskPlan, graph_order
from codex_agy.storage import Database, decode, encode, now

log = logging.getLogger(__name__)
STOPPED = {State.FAILED, State.BLOCKED, State.TIMED_OUT, State.INTERRUPTED, State.CANCELLED}
WAITING_FOR_AGENT = BUSY - {State.WAITING_PERMISSION, State.WAITING_INPUT}


class Supervisor:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.changed = asyncio.Event()
        self.worker = None
        self.running = {}
        self.permissions = {}
        self.mutations = asyncio.Lock()
        self.capabilities = None
        self.stopping = False

    def start(self):
        self.db.recover()
        self.worker = asyncio.create_task(self.schedule())

    async def close(self):
        self.stopping = True
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        running = list(self.running.values())
        for work, _ in running:
            work.cancel()
        await asyncio.gather(*(work for work, _ in running), return_exceptions=True)
        self.db.recover()

    def notify(self):
        previous = self.changed
        self.changed = asyncio.Event()
        previous.set()

    def move(self, task_id, status, error=None):
        self.db.transition(task_id, status, error)
        self.notify()

    async def dispatch(self, method, data):
        return await getattr(self, method)(data)

    def plan(self, plan_id, version=None):
        row = self.db.one(
            """SELECT body FROM plan_versions WHERE plan_id=? AND (? IS NULL OR version=?)
               ORDER BY version DESC LIMIT 1""",
            (plan_id, version, version),
        )
        if row is None:
            raise Problem("PLAN_NOT_FOUND", "Plan or plan version does not exist.")
        return TaskPlan.model_validate_json(row["body"])

    @staticmethod
    def fingerprint(request):
        return hashlib.sha256(encode(request.model_dump(mode="json")).encode()).hexdigest()

    async def submit_plan(self, request):
        return await self.store_plan("submit_plan", request)

    async def revise_plan(self, request):
        return await self.store_plan("revise_plan", request)

    async def store_plan(self, operation, request):
        # Keep idempotency keys issued before the verification options were added valid.
        original = request.model_dump(mode="json")
        submitted = original["plan"]
        if not submitted["verification_gate"]:
            del submitted["verification_gate"]
        for owner in [submitted, *submitted["tasks"]]:
            if not owner["preflight_commands"]:
                del owner["preflight_commands"]
            for command in owner["verification_commands"]:
                if not command["auto_fix_on_failure"]:
                    del command["auto_fix_on_failure"]
        digest = hashlib.sha256(encode(original).encode()).hexdigest()
        plan = request.plan
        project, key = await workspace.repository(plan.project_path, self.settings.allowed_roots)
        plan = plan.model_copy(update={"project_path": project})
        body = encode(plan.model_dump(mode="json"))
        with self.db.transaction():
            cached = self.db.cached(operation, request.idempotency_key, digest)
            if cached is not None:
                return cached
            saved = self.db.one("SELECT * FROM plans WHERE id=?", (plan.plan_id,))
            response = {
                "plan_id": plan.plan_id,
                "version": plan.plan_version,
                "validation_status": "VALIDATED",
            }
            if saved:
                existing = self.db.one(
                    "SELECT body FROM plan_versions WHERE plan_id=? AND version=?",
                    (plan.plan_id, plan.plan_version),
                )
                if existing and TaskPlan.model_validate_json(existing["body"]) == plan:
                    self.db.cache(operation, request.idempotency_key, digest, response)
                    return response
                if operation == "submit_plan":
                    raise Problem("PLAN_EXISTS", "Use revise_plan to append a new version.")
                if saved["repo_key"] != key or saved["project"] != str(project):
                    raise Problem(
                        "PLAN_PROJECT_CHANGED", "Use a new plan ID for a different project."
                    )
                if plan.plan_version != self.plan(plan.plan_id).plan_version + 1:
                    raise Problem(
                        "PLAN_VERSION", "A revision must increment the current version by one."
                    )
            else:
                if operation == "revise_plan" or plan.plan_version != 1:
                    raise Problem("PLAN_VERSION", "Submit version 1 before revising the plan.")
                self.db.write(
                    "INSERT INTO plans VALUES(?,?,?,?)", (plan.plan_id, str(project), key, now())
                )
            self.db.write(
                "INSERT INTO plan_versions VALUES(?,?,?,?)",
                (plan.plan_id, plan.plan_version, body, now()),
            )
            self.db.cache(operation, request.idempotency_key, digest, response)
        return response

    async def execute_plan(self, request):
        return await self.create_execution("execute_plan", request)

    async def execute_milestone(self, request):
        if not request.execution_id:
            return await self.create_execution("execute_milestone", request)
        digest = self.fingerprint(request)
        with self.db.transaction():
            cached = self.db.cached("execute_milestone", request.idempotency_key, digest)
            if cached is not None:
                return cached
            row = self.db.one("SELECT * FROM executions WHERE id=?", (request.execution_id,))
            if (
                row is None
                or row["plan_id"] != request.plan_id
                or (request.plan_version is not None and row["version"] != request.plan_version)
            ):
                raise Problem("EXECUTION_NOT_FOUND", "Execution does not match the requested plan.")
            task = self.db.one(
                "SELECT id FROM milestones WHERE execution_id=? AND milestone_id=?",
                (row["id"], request.milestone_id),
            )
            if task is None:
                raise Problem("MILESTONE_NOT_FOUND", "Unknown milestone in this execution.")
            item = self.db.task(task["id"])
            if item["status"] == State.VALIDATED:
                self.check_ready(item)
                self.move(item["id"], State.QUEUED)
            result = self.status(item["id"])
            self.db.cache("execute_milestone", request.idempotency_key, digest, result)
            return result

    async def create_execution(self, operation, request):
        digest = self.fingerprint(request)
        cached = self.db.cached(operation, request.idempotency_key, digest)
        if cached is not None:
            return cached
        plan = self.plan(request.plan_id, request.plan_version)
        project = plan.project_path
        base = await workspace.text(project, "rev-parse", "--verify", "HEAD^{commit}")
        graph = plan.milestone_dependencies()
        order = graph_order(graph, "milestone")
        automatic = operation == "execute_plan"
        first = order[0] if automatic else request.milestone_id
        if first not in graph:
            raise Problem("MILESTONE_NOT_FOUND", "Unknown milestone.")
        if graph[first]:
            raise Problem("DEPENDENCIES_PENDING", "Start with a milestone without dependencies.")
        execution_id = uuid4().hex
        path = self.settings.data_dir / "worktrees" / execution_id
        with self.db.transaction():
            cached = self.db.cached(operation, request.idempotency_key, digest)
            if cached is not None:
                return cached
            stamp = now()
            self.db.write(
                """INSERT INTO executions VALUES(?,?,?,?,?,?,?,?)""",
                (
                    execution_id,
                    plan.plan_id,
                    plan.plan_version,
                    str(path),
                    base,
                    base,
                    automatic,
                    stamp,
                ),
            )
            for ordinal, milestone in enumerate(order):
                task_id = uuid4().hex
                self.db.write(
                    """INSERT INTO milestones(id,execution_id,milestone_id,ordinal,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (task_id, execution_id, milestone, ordinal, State.CREATED, stamp, stamp),
                )
                self.db.write(
                    "INSERT INTO task_events(task_id,status,created_at) VALUES(?,?,?)",
                    (task_id, State.CREATED, stamp),
                )
                for task in plan.tasks_for(milestone):
                    self.db.write("INSERT INTO tasks VALUES(?,?)", (task_id, task.task_id))
                self.move(task_id, State.VALIDATED)
                if milestone == first:
                    first_task = task_id
                    self.move(task_id, State.QUEUED)
            response = self.status(first_task)
            self.db.cache(operation, request.idempotency_key, digest, response)
        return response

    def siblings(self, task):
        return self.db.all(
            "SELECT id,milestone_id,status FROM milestones WHERE execution_id=? ORDER BY ordinal",
            (task["execution_id"],),
        )

    def check_ready(self, task):
        siblings = self.siblings(task)
        deps = self.plan(task["plan_id"], task["version"]).milestone_dependencies()[
            task["milestone_id"]
        ]
        pending = [
            row["milestone_id"]
            for row in siblings
            if row["milestone_id"] in deps and row["status"] != State.ACCEPTED
        ]
        if pending:
            raise Problem(
                "DEPENDENCIES_PENDING", "Dependency milestones require acceptance.", pending
            )
        if any(
            row["id"] != task["id"] and row["status"] not in {State.VALIDATED, State.ACCEPTED}
            for row in siblings
        ):
            raise Problem(
                "EXECUTION_BUSY", "Resolve the current milestone before starting another."
            )

    def status(self, task_id):
        task = self.db.task(task_id)
        data = {
            key: task[key]
            for key in (
                "execution_id",
                "milestone_id",
                "status",
                "plan_id",
                "created_at",
                "updated_at",
                "started_at",
                "finished_at",
                "retry_count",
                "session_id",
                "review_revision",
            )
        }
        data.update(
            task_id=task_id,
            workspace_path=task["worktree"],
            plan_version=task["version"],
            review_round=task["round"],
            error=decode(task["error"]),
        )
        if task["status"] == State.WAITING_PERMISSION:
            data["permissions"] = [
                {"permission_id": row["id"], **decode(row["request"])}
                for row in self.db.all(
                    "SELECT id,request FROM permissions WHERE task_id=? AND decision IS NULL",
                    (task_id,),
                )
            ]
        return data

    async def get_task_status(self, request):
        return self.status(request.task_id)

    async def get_task_result(self, request):
        task = self.db.task(request.task_id)
        result = self.status(request.task_id)
        if result["status"] in BUSY:
            return result
        agent_result = decode(task["result"], {})
        if agent_result:
            result["result"] = {"stop_reason": agent_result["stop_reason"]}
        if result["review_revision"]:
            review = self.db.one(
                "SELECT metadata FROM review_rounds WHERE task_id=? AND round=?",
                (task["id"], task["round"]),
            )
            counts = {}
            for check in self.verification_for(task["id"], task["round"]):
                outcome = check["result"]
                counts[outcome] = counts.get(outcome, 0) + 1
            result["review"] = {
                "changed_file_count": len(decode(review["metadata"])["changed_files"]),
                "verification_counts": counts,
            }
        return result

    async def list_tasks(self, request):
        rows = self.db.all(
            """SELECT id FROM milestones WHERE (? IS NULL OR execution_id=?) AND (? IS NULL OR status=?)
               ORDER BY updated_at DESC, id LIMIT ? OFFSET ?""",
            (
                request.execution_id,
                request.execution_id,
                request.status,
                request.status,
                request.limit,
                request.offset,
            ),
        )
        return {"tasks": [self.status(row["id"]) for row in rows]}

    async def wait_for_completion(self, request):
        return await self.wait_for_status(
            request.task_id, min(request.timeout_seconds, self.settings.max_wait_seconds)
        )

    async def wait_background(self, request):
        return await self.wait_for_status(request.task_id, request.timeout_seconds)

    async def wait_for_status(self, task_id, limit):
        try:
            async with asyncio.timeout(limit):
                while True:
                    event = self.changed
                    result = self.status(task_id)
                    if result["status"] not in WAITING_FOR_AGENT:
                        return result
                    await event.wait()
        except TimeoutError:
            result = self.status(task_id)
            if result["status"] in WAITING_FOR_AGENT:
                result.update(task_status=result["status"], status="PENDING")
            return result

    async def schedule(self):
        while True:
            event = self.changed
            slots = self.settings.workers - len(self.running)
            occupied = {key for _, key in self.running.values()}
            for task in self.db.all(
                """SELECT m.id, p.repo_key FROM milestones m JOIN executions e ON e.id=m.execution_id
                   JOIN plans p ON p.id=e.plan_id WHERE m.status IN (?,?) ORDER BY m.updated_at,m.ordinal""",
                (State.QUEUED, State.FIX_REQUESTED),
            ):
                if not slots:
                    break
                if task["repo_key"] in occupied:
                    continue
                task_id = task["id"]
                self.move(task_id, State.STARTING)
                work = asyncio.create_task(self.run(task_id))
                self.running[task_id] = work, task["repo_key"]
                work.add_done_callback(lambda done, key=task_id: self.worker_finished(key, done))
                occupied.add(task["repo_key"])
                slots -= 1
            await event.wait()

    def worker_finished(self, task_id, done):
        self.running.pop(task_id, None)
        if not done.cancelled() and done.exception():
            exc = done.exception()
            log.error(
                "Worker cleanup failed for %s",
                task_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        self.notify()

    async def run(self, task_id):
        try:
            async with asyncio.timeout(self.settings.hard_seconds):
                await self.execute(task_id)
        except asyncio.CancelledError:
            if self.db.task(task_id)["status"] in ACTIVE:
                self.move(task_id, State.INTERRUPTED if self.stopping else State.CANCELLED)
            raise
        except TimeoutError:
            self.move(
                task_id,
                State.TIMED_OUT,
                {"code": "TIMEOUT", "message": "An execution deadline expired."},
            )
        except Problem as exc:
            state = (
                State.FAILED
                if exc.code in {"ACP_CONNECTION_LOST", "ACP_RPC_ERROR"}
                else State.BLOCKED
            )
            self.move(task_id, state, exc.payload())
        except OSError as exc:
            code = (
                "DISK_FULL"
                if exc.errno == 28 or getattr(exc, "winerror", None) == 112
                else "OS_ERROR"
            )
            self.move(task_id, State.FAILED, {"code": code, "message": str(exc)[:400]})
        except Exception:
            log.exception("Execution failed for %s", task_id)
            self.move(
                task_id, State.FAILED, {"code": "EXECUTION_ERROR", "message": "See supervisor log."}
            )
        finally:
            self.db.write(
                "UPDATE sessions SET ended_at=? WHERE task_id=? AND ended_at IS NULL",
                (now(), task_id),
            )

    async def execute(self, task_id):
        task = self.db.task(task_id)
        plan = self.plan(task["plan_id"], task["version"])
        cwd = Path(task["worktree"])
        await workspace.ensure_worktree(plan.project_path, cwd, task["base"], task["repo_key"])
        head = task["head"]
        actual_head = await workspace.text(cwd, "rev-parse", "HEAD")
        if actual_head != head:
            if not task["retrying"] or not await workspace.recover_snapshot(
                cwd, task_id, task["round"] + 1, head, actual_head
            ):
                raise Problem(
                    "WORKTREE_HEAD_CHANGED",
                    "Inspect retained worktree and restore the expected detached HEAD.",
                )
        if not task["retrying"] and await workspace.status(cwd):
            raise Problem(
                "WORKTREE_DIRTY", "Inspect unexpected changes before explicitly retrying."
            )
        self.db.write(
            "UPDATE milestones SET input_base=COALESCE(input_base, ?) WHERE id=?", (head, task_id)
        )
        task = self.db.task(task_id)
        round_no = task["round"] + 1
        preflight = [*plan.preflight_commands]
        for item in plan.tasks_for(task["milestone_id"]):
            preflight.extend(item.preflight_commands)
        if preflight:
            logs = self.settings.data_dir / "artifacts" / task_id / f"preflight-{uuid4().hex}"
            checks = await verification.verify(
                preflight, cwd, logs, head, self.settings.verification_seconds
            )
            (logs / "results.json").write_text(encode(checks), encoding="utf-8")
            failures = [item for item in checks if item["result"] != "PASSED"]
            if failures:
                raise Problem(
                    "PREFLIGHT_FAILED",
                    "Environment or fixture checks failed before starting AGY.",
                    {"failed_count": len(failures), "results_path": str(logs / "results.json")},
                )
        await acp_client.discover(self.settings)
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        idle = loop.time()
        started = False
        waiting = False
        blocker = None
        expected_files = {Path(value) for value in plan.expected_files}
        for item in plan.tasks_for(task["milestone_id"]):
            expected_files.update(Path(value) for value in item.expected_files)

        async def activity(_kind):
            nonlocal idle
            idle = loop.time()
            wake.set()

        async def ask():
            nonlocal blocker
            blocker = Problem(
                "AGENT_INPUT_REQUIRED", "AGY asked for input. Inspect the task before retrying."
            )
            if self.db.task(task_id)["status"] == State.RUNNING:
                self.move(task_id, State.WAITING_INPUT)
            wake.set()

        async def permission(session_id, tool_call, options):
            nonlocal waiting, blocker
            if blocker or self.db.task(task_id)["status"] not in {
                State.RUNNING,
                State.WAITING_PERMISSION,
            }:
                return await acp_client.deny()
            waiting = True
            wake.set()
            try:
                return await self.request_permission(
                    task_id, session_id, tool_call, options, cwd, expected_files
                )
            except TimeoutError:
                blocker = Problem(
                    "PERMISSION_TIMEOUT", "Permission decision expired; inspect before retrying."
                )
                return await acp_client.deny()
            finally:
                waiting = any(entry[0] == task_id for entry in self.permissions.values())
                await activity("permission")

        async def on_session(session_id):
            nonlocal started
            started = True
            self.db.write(
                "UPDATE milestones SET session_id=?,result=NULL WHERE id=?", (session_id, task_id)
            )
            self.db.write("INSERT INTO sessions VALUES(?,?,?,NULL)", (session_id, task_id, now()))
            await activity("session")
            self.move(task_id, State.RUNNING)

        events = acp_client.AgentEvents(permission, activity, ask)
        prompt = self.prompt(plan, task)
        turn = asyncio.create_task(
            acp_client.run_turn(
                self.settings,
                cwd,
                prompt,
                events,
                on_session,
                task["session_id"] if task["round"] and not task["retrying"] else None,
            )
        )
        turn.add_done_callback(lambda _: wake.set())
        try:
            while not turn.done():
                if blocker:
                    raise blocker
                wake.clear()
                if not started or waiting:
                    await wake.wait()
                    continue
                remaining = idle + self.settings.idle_seconds - loop.time()
                if remaining <= 0:
                    raise TimeoutError("AGY produced no events before the idle deadline.")
                try:
                    async with asyncio.timeout(remaining):
                        await wake.wait()
                except TimeoutError:
                    continue
            response = await turn
        finally:
            if not turn.done():
                turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        if blocker:
            raise blocker
        stop_reason = response["stop_reason"]
        self.db.write("UPDATE milestones SET result=? WHERE id=?", (encode(response), task_id))
        if stop_reason != "end_turn":
            raise Problem("INCOMPLETE_TURN", f"AGY stopped with {stop_reason}.")
        self.move(task_id, State.VERIFYING)
        await self.build_review(task, plan, cwd, head, round_no)

    def prompt(self, plan, task):
        tasks = plan.tasks_for(task["milestone_id"])
        milestone = next(m for m in plan.milestones if m.milestone_id == task["milestone_id"])
        plan_context = plan.model_dump(mode="json", exclude={"milestones", "tasks", "project_path"})
        payload = {
            "plan_context": plan_context,
            "milestone": milestone.model_dump(mode="json"),
            "assigned_tasks": [item.model_dump(mode="json") for item in tasks],
        }
        if task["round"]:
            payload["independent_findings"] = [
                decode(row["body"])
                for row in self.db.all(
                    "SELECT body FROM review_findings WHERE task_id=? AND round=? ORDER BY ordinal",
                    (task["id"], task["round"]),
                )
            ]
        assignment = (
            "Fix only the independent findings in this milestone. Preserve existing working changes. "
            if task["round"]
            else "Implement only the assigned milestone in this detached worktree. "
        )
        if task["round"] and self.db.one(
            "SELECT 1 FROM review_rounds WHERE task_id=? AND round=? AND decision='AUTO_FIX'",
            (task["id"], task["round"]),
        ):
            assignment = (
                "Fix the implementation behind the failed verification commands in the findings. "
                "Run those commands if needed to diagnose the failure. Do not weaken or bypass "
                "the checks or change their expected behavior. "
            )
        return (
            assignment + "Complete all assigned tasks in dependency order as one functional phase "
            "before ending your turn. End early only if a blocker prevents further progress. "
            "Acceptance verification and independent review happen after this whole milestone. "
            "Preserve the plan and "
            "acceptance criteria. Avoid reading or writing outside this worktree. Do not commit, "
            "switch branches, push, deploy, delete independent tests or run destructive commands. "
            "The supervisor runs the plan's verification commands after your turn. Leave those "
            "commands to the supervisor unless you need one to diagnose a specific implementation "
            "failure. Keep your end-of-turn report brief.\n\n" + encode(payload)
        )

    async def request_permission(
        self, task_id, session_id, tool_call, options, cwd, expected_files
    ):
        permission_id = uuid4().hex
        choices = [
            option.model_dump(mode="json", by_alias=True)
            for option in options
            if option.kind in {"allow_once", "reject_once"}
        ]
        if not choices:
            return await acp_client.deny()
        tool = tool_call.model_dump(mode="json", by_alias=True, exclude_none=True)
        details = {
            key: tool[key] for key in ("title", "kind", "toolCallId", "locations") if key in tool
        }
        raw_input = tool.get("rawInput")
        if isinstance(raw_input, dict):
            preview = {
                key: str(value)[:800]
                for key, value in raw_input.items()
                if key.casefold()
                in {"command", "commandline", "cwd", "path", "file_path", "tools_summary"}
            }
            if preview:
                details["input_preview"] = encode(preview)[:1600]
        request = {"session_id": session_id, "tool_call": details, "options": choices}
        mode = self.settings.permission_mode
        allow = next((item for item in choices if item["kind"] == "allow_once"), None)
        if allow and (
            mode == "allow_all"
            or (
                mode == "worktree"
                and self.settings.sandbox
                and permissions.routine_operation(tool, cwd.resolve(), expected_files)
            )
        ):
            choice = allow["optionId"]
            timestamp = now()
            self.db.write(
                "INSERT INTO permissions VALUES(?,?,?,?,?,?)",
                (
                    permission_id,
                    task_id,
                    encode({**request, "decision_source": mode}),
                    choice,
                    timestamp,
                    timestamp,
                ),
            )
            return {"outcome": {"outcome": "selected", "optionId": choice}}
        future = asyncio.get_running_loop().create_future()
        self.permissions[permission_id] = task_id, future, {item["optionId"] for item in choices}
        with self.db.transaction():
            self.db.write(
                "INSERT INTO permissions VALUES(?,?,?,NULL,?,NULL)",
                (permission_id, task_id, encode(request), now()),
            )
            if self.db.task(task_id)["status"] == State.RUNNING:
                self.move(task_id, State.WAITING_PERMISSION)
        self.notify()
        try:
            async with asyncio.timeout(self.settings.permission_seconds):
                choice = await future
            return {"outcome": {"outcome": "selected", "optionId": choice}}
        finally:
            self.permissions.pop(permission_id, None)
            with self.db.transaction():
                self.db.write(
                    "UPDATE permissions SET decision='expired_or_cancelled',decided_at=? WHERE id=? AND decision IS NULL",
                    (now(), permission_id),
                )
                if not any(item[0] == task_id for item in self.permissions.values()) and (
                    self.db.task(task_id)["status"] == State.WAITING_PERMISSION
                ):
                    self.move(task_id, State.RUNNING)
            self.notify()

    async def respond_permission(self, request):
        entry = self.permissions.get(request.permission_id)
        if not entry or entry[0] != request.task_id or entry[1].done():
            raise Problem("PERMISSION_INACTIVE", "This permission request is no longer active.")
        if request.option_id not in entry[2]:
            raise Problem(
                "INVALID_PERMISSION_OPTION", "Use one of the offered one-time option IDs."
            )
        self.db.write(
            "UPDATE permissions SET decision=?,decided_at=? WHERE id=?",
            (request.option_id, now(), request.permission_id),
        )
        entry[1].set_result(request.option_id)
        return {"permission_id": request.permission_id, "status": "RESOLVED"}

    async def build_review(self, task, plan, cwd, head, round_no):
        task_id = task["id"]
        revision = await workspace.snapshot(cwd, head, task_id, round_no)
        base = task["input_base"]
        changed, summary, diff, digest = await workspace.changes(cwd, base, revision)
        tasks = plan.tasks_for(task["milestone_id"])
        paths = set(plan.expected_files).union(*(item.expected_files for item in tasks))
        outside = sorted(set(changed) - paths)
        warnings = []
        if outside:
            warnings.append("Changes outside expected_files require independent review.")
        commands = [
            *plan.verification_commands,
            *(cmd for item in tasks for cmd in item.verification_commands),
        ]
        if not commands:
            commands = verification.discover_commands(cwd)
        previous_review = self.db.one(
            "SELECT revision FROM review_rounds WHERE task_id=? AND decision='FIX' "
            "ORDER BY round DESC LIMIT 1",
            (task_id,),
        )
        previous_revision = previous_review["revision"] if previous_review else None
        artifacts = self.settings.data_dir / "artifacts" / task_id / str(round_no)
        artifacts.mkdir(parents=True, exist_ok=True)
        diff_path = artifacts / "review.diff"
        diff_path.write_bytes(diff[: workspace.MAX_ARTIFACT])
        delta_path = None
        if previous_revision:
            _, _, delta, _ = await workspace.changes(cwd, previous_revision, revision)
            delta_path = artifacts / "fix.diff"
            delta_path.write_bytes(delta[: workspace.MAX_ARTIFACT])
        results = await verification.verify(
            commands, cwd, artifacts, revision, self.settings.verification_seconds
        )
        verification_valid = not (
            await workspace.status(cwd)
            or await workspace.text(cwd, "rev-parse", "HEAD") != revision
        )
        if not verification_valid:
            warnings.append("Verification or another process modified the worktree after snapshot.")
        if any(item["result"] != "PASSED" for item in results):
            warnings.append("Some configured verification checks did not pass.")
        metadata = {
            "changed_files": changed,
            "diff_summary": summary,
            "warnings": warnings,
            "verification_valid_for_revision": verification_valid,
            "outside_expected_files": outside,
            "diff_truncated": len(diff) > workspace.MAX_ARTIFACT,
            "diff_artifact_hash": hashlib.sha256(diff[: workspace.MAX_ARTIFACT]).hexdigest(),
            "fix_diff_truncated": bool(delta_path and len(delta) > workspace.MAX_ARTIFACT),
            "fix_diff_artifact_hash": hashlib.sha256(delta[: workspace.MAX_ARTIFACT]).hexdigest()
            if delta_path
            else None,
            "acceptance_criteria": [
                *plan.acceptance_criteria,
                *(c for item in tasks for c in item.acceptance_criteria),
            ],
            "definition_of_done": plan.definition_of_done,
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "milestone_id": task["milestone_id"],
            "previous_revision": previous_revision,
        }
        with self.db.transaction():
            self.db.write(
                "INSERT INTO review_rounds VALUES(?,?,?,?,?,?,NULL,NULL)",
                (task_id, round_no, base, revision, digest, encode(metadata)),
            )
            self.db.write(
                "INSERT INTO artifacts VALUES(?,?,?,?)", (task_id, round_no, "diff", str(diff_path))
            )
            if delta_path:
                self.db.write(
                    "INSERT INTO artifacts VALUES(?,?,?,?)",
                    (task_id, round_no, "fix_diff", str(delta_path)),
                )
            for ordinal, result in enumerate(results):
                self.db.write(
                    "INSERT INTO verification_results VALUES(?,?,?,?)",
                    (task_id, round_no, ordinal, encode(result)),
                )
                if result.get("log_path"):
                    self.db.write(
                        "INSERT INTO artifacts VALUES(?,?,?,?)",
                        (task_id, round_no, f"verification-{ordinal}", result["log_path"]),
                    )
            self.db.write(
                "UPDATE executions SET head=? WHERE id=?", (revision, task["execution_id"])
            )
            self.db.write(
                "UPDATE milestones SET round=?,review_revision=?,retrying=0 WHERE id=?",
                (round_no, revision, task_id),
            )
            if plan.verification_gate:
                self.finish_gated_verification(
                    task_id, round_no, commands, results, verification_valid
                )
            else:
                self.move(task_id, State.REVIEW_REQUIRED)

    def finish_gated_verification(self, task_id, round_no, commands, results, valid):
        failures = [item for item in results if item["result"] != "PASSED"]
        if valid and not failures:
            self.move(task_id, State.REVIEW_REQUIRED)
            return

        failed_checks = [
            {
                "check_number": number,
                "command_preview": " ".join(item.get("command", []))[:240],
                "cwd": item.get("cwd", "."),
                "result": item["result"],
                "exit_code": item.get("exit_code"),
            }
            for number, item in enumerate(results, 1)
            if item["result"] != "PASSED"
        ]
        already_retried = self.db.one(
            "SELECT 1 FROM review_rounds WHERE task_id=? AND decision='AUTO_FIX' LIMIT 1",
            (task_id,),
        )
        manual_commands = {(tuple(c.argv), c.cwd) for c in commands if not c.auto_fix_on_failure}
        repairable = bool(failures) and all(
            item["result"] == "FAILED"
            and (tuple(item["command"]), item["cwd"]) not in manual_commands
            for item in failures
        )
        if (
            valid
            and repairable
            and not already_retried
            and round_no < self.settings.max_review_rounds
        ):
            self.db.write(
                "UPDATE review_rounds SET decision='AUTO_FIX',review_summary=? WHERE task_id=? AND round=?",
                ("Supervisor requested one repair for failed required checks.", task_id, round_no),
            )
            for ordinal, check in enumerate(failed_checks):
                original = results[check["check_number"] - 1]
                check["command"] = original["command"]
                log_path = original.get("log_path")
                if log_path:
                    check["diagnostic_tail"] = (
                        Path(log_path).read_bytes()[-2000:].decode("utf-8", "replace")
                    )
                self.db.write(
                    "INSERT INTO review_findings VALUES(?,?,?,?)",
                    (task_id, round_no, ordinal, encode(check)),
                )
            self.move(task_id, State.FIX_REQUESTED)
            return

        self.move(
            task_id,
            State.BLOCKED,
            {
                "code": "VERIFICATION_BLOCKED",
                "message": "Required checks did not pass or the snapshot changed. Workspace retained.",
                "details": {
                    "snapshot_valid": valid,
                    "failed_check_count": len(failed_checks),
                    "checks": failed_checks[:10],
                },
            },
        )

    def verification_for(self, task_id, round_no):
        return [
            decode(item["body"])
            for item in self.db.all(
                "SELECT body FROM verification_results WHERE task_id=? AND round=? ORDER BY ordinal",
                (task_id, round_no),
            )
        ]

    async def get_review_context(self, request):
        task = self.db.task(request.task_id)
        row = self.db.one(
            """SELECT * FROM review_rounds WHERE task_id=? AND revision=?""",
            (task["id"], request.review_revision or task["review_revision"]),
        )
        if row is None:
            raise Problem("REVIEW_NOT_FOUND", "No matching review snapshot is available.")
        result = {
            "task_id": task["id"],
            "review_revision": row["revision"],
            "review_round": row["round"],
            "base_commit": row["base"],
            "diff_hash": row["diff_hash"],
            "decision": row["decision"],
        }
        if request.view == "summary":
            return {
                **result,
                **decode(row["metadata"]),
                "verification_results": self.verification_for(task["id"], row["round"]),
                "workspace_path": task["worktree"],
                "review_summary": row["review_summary"],
            }
        if request.view in {"diff", "fix_diff"}:
            artifact = self.db.one(
                "SELECT path FROM artifacts WHERE task_id=? AND round=? AND name=?",
                (task["id"], row["round"], request.view),
            )
            if artifact is None:
                raise Problem("ARTIFACT_MISSING", "No diff for this view is available.")
            try:
                data = Path(artifact["path"]).read_bytes()
            except OSError as exc:
                raise Problem("ARTIFACT_MISSING", "The saved diff is unavailable.") from exc
            metadata = decode(row["metadata"])
            truncated = metadata[request.view + "_truncated"]
            digest = metadata.get(request.view + "_artifact_hash")
            if digest and hashlib.sha256(data).hexdigest() != digest:
                raise Problem("ARTIFACT_CHANGED", "The saved diff no longer matches the snapshot.")
            result["truncated"] = truncated
        else:
            if not request.path:
                raise Problem("PATH_REQUIRED", "Supply a repository-relative file path.")
            data = await workspace.blob(Path(task["worktree"]), row["revision"], request.path)
        text_data = data.decode("utf-8", "replace")
        end = request.offset + request.limit
        return {
            **result,
            "content": text_data[request.offset : end],
            "next_offset": end if end < len(text_data) else None,
        }

    async def accept_milestone(self, request):
        return await self.decide(request, "ACCEPT")

    async def reject_milestone(self, request):
        return await self.decide(request, "REJECT")

    async def request_fix(self, request):
        return await self.decide(request, "FIX")

    async def decide(self, request, decision):
        async with self.mutations:
            task = self.db.task(request.task_id)
            if task["review_revision"] != request.review_revision:
                raise Problem(
                    "STALE_REVIEW", "The submitted revision is not the current review revision."
                )
            if task["status"] != State.REVIEW_REQUIRED:
                raise Problem("INVALID_STATE", "This task is not awaiting review.")
            if decision == "ACCEPT":
                row = self.db.one(
                    "SELECT metadata FROM review_rounds WHERE task_id=? AND round=?",
                    (task["id"], task["round"]),
                )
                metadata = decode(row["metadata"])
                if metadata.get("verification_valid_for_revision") is False:
                    raise Problem(
                        "VERIFICATION_STALE",
                        "The worktree changed during verification. Obtain a fresh review snapshot.",
                    )
            if decision == "ACCEPT" and any(
                check["result"] in {"FAILED", "ERROR"}
                for check in self.verification_for(task["id"], task["round"])
            ):
                raise Problem(
                    "VERIFICATION_FAILED", "Resolve verification failures before acceptance."
                )
            if decision != "REJECT":
                cwd = Path(task["worktree"])
                if (
                    await workspace.status(cwd)
                    or await workspace.text(cwd, "rev-parse", "HEAD") != request.review_revision
                ):
                    raise Problem(
                        "WORKTREE_CHANGED", "Inspect changes made after the review snapshot."
                    )
            with self.db.transaction():
                if self.db.task(task["id"])["status"] != State.REVIEW_REQUIRED:
                    raise Problem("INVALID_STATE", "This task changed while checking the worktree.")
                self.db.write(
                    "UPDATE review_rounds SET decision=?,review_summary=? WHERE task_id=? AND round=?",
                    (decision, request.review_summary, task["id"], task["round"]),
                )
                if decision == "FIX":
                    for ordinal, finding in enumerate(request.findings):
                        self.db.write(
                            "INSERT INTO review_findings VALUES(?,?,?,?)",
                            (task["id"], task["round"], ordinal, finding.model_dump_json()),
                        )
                    if task["round"] >= self.settings.max_review_rounds:
                        self.move(
                            task["id"],
                            State.BLOCKED,
                            {
                                "code": "REVIEW_LIMIT",
                                "message": "Automatic fix limit reached. Workspace retained.",
                            },
                        )
                    else:
                        self.move(task["id"], State.FIX_REQUESTED)
                elif decision == "REJECT":
                    self.move(
                        task["id"],
                        State.BLOCKED,
                        {
                            "code": "REVIEW_REJECTED",
                            "message": request.review_summary,
                        },
                    )
                else:
                    self.move(task["id"], State.ACCEPTED)
                    if task["automatic"]:
                        pending = [
                            item
                            for item in self.siblings(task)
                            if item["status"] == State.VALIDATED
                        ]
                        if pending:
                            upcoming = self.db.task(pending[0]["id"])
                            self.check_ready(upcoming)
                            self.move(upcoming["id"], State.QUEUED)
                            return {**self.status(task["id"]), "next_task_id": upcoming["id"]}
            return self.status(task["id"])

    async def cancel_task(self, request):
        task_id = request.task_id
        status = self.db.task(task_id)["status"]
        if status == State.CANCELLED:
            return self.status(task_id)
        running = self.running.get(task_id)
        if running and status in ACTIVE:
            running[0].cancel()
            await asyncio.gather(running[0], return_exceptions=True)
            if self.db.task(task_id)["status"] in ACTIVE:
                self.move(task_id, State.CANCELLED)
        elif status in {State.VALIDATED, State.QUEUED, State.FIX_REQUESTED, State.REVIEW_REQUIRED}:
            self.move(task_id, State.CANCELLED)
        else:
            raise Problem("INVALID_STATE", "The task is already stopped.")
        return self.status(task_id)

    async def retry_task(self, request):
        task_id = request.task_id
        with self.db.transaction():
            task = self.db.task(task_id)
            if task["status"] not in STOPPED:
                raise Problem("INVALID_STATE", "Only a stopped task can be retried.")
            if task["round"] >= self.settings.max_review_rounds:
                raise Problem("REVIEW_LIMIT", "Create a revised plan for another execution.")
            self.check_ready(task)
            self.db.write(
                "UPDATE milestones SET retry_count=retry_count+1,retrying=1 WHERE id=?", (task_id,)
            )
            self.move(task_id, State.QUEUED, {"code": "EXPLICIT_RETRY", "message": request.reason})
        return self.status(task_id)

    async def health_check(self, _):
        return {
            "status": "STOPPING"
            if self.stopping
            else ("DEGRADED" if self.worker is None or self.worker.done() else "READY"),
            "pid": os.getpid(),
            "database": self.db.one("PRAGMA user_version")[0],
            "active_workers": len(self.running),
            "permission_mode": self.settings.permission_mode,
            "sandbox": self.settings.sandbox,
            "supervisor_version": "0.1.0",
        }

    async def get_system_capabilities(self, request):
        if request.refresh or request.verify_session or self.capabilities is None:
            try:
                folder = self.settings.data_dir / "probe"
                folder.mkdir(exist_ok=True)
                data = await acp_client.probe(self.settings, folder, request.verify_session)
                self.capabilities = {"available": True, **data}
            except Problem as exc:
                self.capabilities = {"available": False, "error": exc.payload()}
            except (TimeoutError, OSError, ValueError):
                self.capabilities = {"available": False, "error": {"code": "ACP_PROBE_FAILED"}}
        return {
            "agy": self.capabilities,
            "mcp_transport": "stdio",
            "ipc": "authenticated_loopback_http_v1",
            "max_wait_seconds": self.settings.max_wait_seconds,
            "background_execution": True,
            "desktop_auto_resume": False,
            "workers": self.settings.workers,
            "sandbox": self.settings.sandbox,
        }


@contextlib.contextmanager
def instance_lock(path):
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Problem(
                "SUPERVISOR_RUNNING", "Another supervisor is using this data directory."
            ) from exc
        yield
