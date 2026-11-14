import asyncio
import os
import sys
from pathlib import Path

import pytest
from aiohttp import ClientSession
from conftest import plan_for
from mcp import ClientSession as McpSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from codex_agy import acp_client
from codex_agy.api import TOOLS
from codex_agy.cli import serve
from codex_agy.ipc import call
from codex_agy.process import capture
from codex_agy.workspace import text

FINDINGS = [
    {
        "severity": "medium",
        "description": "Update the fixture's greeting.",
        "file": "hello0.py",
        "line": 2,
        "expected_behavior": "Return hello again.",
        "actual_behavior": "Returns hello.",
        "reproduction_steps": ["Call greet()."],
    }
]


@pytest.fixture
async def server(settings, monkeypatch, request):
    if "live" not in request.keywords:
        from contextlib import asynccontextmanager

        from acp import spawn_agent_process

        async def fixture_discovery(_):
            return {"transport": "stdio", "agy_version": "fixture"}

        @asynccontextmanager
        async def fixture_connection(_, cwd, events):
            async with spawn_agent_process(
                events,
                sys.executable,
                str(Path(__file__).with_name("fake_agent.py")),
                cwd=cwd,
                transport_kwargs={"stderr": asyncio.subprocess.DEVNULL},
            ) as (conn, _proc):
                yield conn

        monkeypatch.setattr(acp_client, "connection", fixture_connection)
        monkeypatch.setattr(acp_client, "discover", fixture_discovery)
    if request.node.name == "test_recovery_and_timeout":
        settings.hard_seconds = 8
    if "live" in request.keywords:
        settings.agy_model = os.environ.get("CODEX_AGY_LIVE_MODEL")
    running = asyncio.create_task(serve(settings))
    try:
        async with asyncio.timeout(10):
            while not (await call(settings, "health_check"))["ok"]:
                if running.done():
                    await running
                await asyncio.sleep(0.05)
        yield running
    finally:
        if not running.done():
            await call(settings, "shutdown")
        await running


async def invoke(settings, tool, **args):
    payload = await call(settings, tool, args)
    assert payload["ok"], payload
    return payload["data"]


async def submit(settings, plan):
    await invoke(settings, "submit_plan", plan=plan, idempotency_key="plan-once")
    return await invoke(
        settings, "execute_plan", plan_id=plan["plan_id"], idempotency_key="execution-once"
    )


async def finished(settings, task_id, allow=False):
    async with asyncio.timeout(480):
        while True:
            status = await invoke(
                settings, "wait_for_completion", task_id=task_id, timeout_seconds=25
            )
            if status["status"] == "PENDING":
                continue
            if status["status"] == "WAITING_PERMISSION" and allow:
                for pending in status["permissions"]:
                    tool = pending["tool_call"]
                    if allow == "live":
                        print("LIVE permission:", tool, flush=True)
                        expected = Path(status["workspace_path"], "hello0.py").resolve()
                        locations = [
                            Path(entry["path"]).resolve() for entry in tool.get("locations", [])
                        ]
                        assert tool.get("kind") in {"edit", "read"} and locations == [expected], (
                            tool
                        )
                    option = next(
                        item for item in pending["options"] if item["kind"] == "allow_once"
                    )
                    response = await call(
                        settings,
                        "respond_permission",
                        {
                            "task_id": task_id,
                            "permission_id": pending["permission_id"],
                            "option_id": option["optionId"],
                        },
                    )
                    assert response["ok"] or response["error"]["code"] == "PERMISSION_INACTIVE", (
                        response
                    )
                await asyncio.sleep(0.1)
                continue
            return status


async def test_ipc_and_mcp_protocol(settings, server, tmp_path):
    async with ClientSession() as client:
        async with client.post(settings.endpoint + "/health_check", json={}) as response:
            assert response.status == 401
    import yaml

    config = tmp_path / "mcp configuration.yaml"
    config.write_text(yaml.safe_dump(settings.model_dump(mode="json")), encoding="utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codex_agy.cli", "--config", str(config), "mcp"],
    )
    async with stdio_client(params) as (reader, writer), McpSession(reader, writer) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == set(TOOLS)
        health = await session.call_tool("health_check", {})
        assert health.structuredContent["data"]["status"] == "READY"
        error = await session.call_tool("get_task_status", {"unexpected": "arg"})
        assert error.isError and error.structuredContent["error"]["code"] == "VALIDATION"


async def test_worktree_review_gate_and_idempotency(settings, server, repo):
    head = await text(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("local edits", encoding="utf-8")
    first = await submit(settings, plan_for(repo, 2))
    duplicate = await invoke(
        settings, "execute_plan", plan_id="smoke", idempotency_key="execution-once"
    )
    assert first == duplicate
    first_status = await finished(settings, first["task_id"])
    assert first_status["status"] == "REVIEW_REQUIRED", first_status
    rows = (await invoke(settings, "list_tasks", execution_id=first["execution_id"]))["tasks"]
    assert {item["status"] for item in rows} == {"REVIEW_REQUIRED", "VALIDATED"}
    assert await text(repo, "rev-parse", "HEAD") == head
    assert (repo / "README.md").read_text() == "local edits"
    review = await invoke(settings, "get_review_context", task_id=first["task_id"])
    assert review["changed_files"] == ["hello0.py"]
    assert review["verification_results"][0]["result"] == "PASSED"
    accepted = await invoke(
        settings,
        "accept_milestone",
        task_id=first["task_id"],
        review_revision=review["review_revision"],
        review_summary="Read diff and verified fixture.",
    )
    next_status = await finished(settings, accepted["next_task_id"])
    assert next_status["status"] == "REVIEW_REQUIRED", next_status
    assert (Path(first["workspace_path"]) / "hello0.py").is_file()
    assert not (repo / "hello0.py").exists()


async def test_fix_snapshot_and_failed_verification(settings, server, repo):
    first = await submit(settings, plan_for(repo))
    task_id = first["task_id"]
    initial = await finished(settings, task_id)
    assert initial["status"] == "REVIEW_REQUIRED", initial
    old = initial["review_revision"]
    await invoke(
        settings,
        "request_fix",
        task_id=task_id,
        review_revision=old,
        review_summary="Review finding on the greeting.",
        findings=FINDINGS,
    )
    fixed = await finished(settings, task_id)
    assert fixed["status"] == "REVIEW_REQUIRED", fixed
    assert old != fixed["review_revision"]
    delta = await invoke(settings, "get_review_context", task_id=task_id, view="fix_diff")
    assert "hello again" in delta["content"]
    previous = await invoke(
        settings,
        "get_review_context",
        task_id=task_id,
        view="file",
        path="hello0.py",
        review_revision=old,
    )
    assert "'hello'" in previous["content"]
    stale = await call(
        settings,
        "accept_milestone",
        {
            "task_id": task_id,
            "review_revision": old,
            "review_summary": "stale",
        },
    )
    assert stale["error"]["code"] == "STALE_REVIEW"
    failed = await call(
        settings,
        "accept_milestone",
        {
            "task_id": task_id,
            "review_revision": fixed["review_revision"],
            "review_summary": "failure",
        },
    )
    assert failed["error"]["code"] == "VERIFICATION_FAILED"
    Path(first["workspace_path"], "hello0.py").write_text("external edit", encoding="utf-8")
    changed = await call(
        settings,
        "request_fix",
        {
            "task_id": task_id,
            "review_revision": fixed["review_revision"],
            "review_summary": "Check changed workspace.",
            "findings": FINDINGS,
        },
    )
    assert changed["error"]["code"] == "WORKTREE_CHANGED"


async def test_permission_and_cancellation(settings, server, repo):
    plan = plan_for(repo)
    plan["user_goal"] = "permission"
    task_id = (await submit(settings, plan))["task_id"]
    waiting = await finished(settings, task_id)
    assert waiting["status"] == "WAITING_PERMISSION", waiting
    invalid = await call(
        settings,
        "respond_permission",
        {
            "task_id": task_id,
            "permission_id": waiting["permissions"][0]["permission_id"],
            "option_id": "bogus",
        },
    )
    assert invalid["error"]["code"] == "INVALID_PERMISSION_OPTION"
    cancelled = await invoke(settings, "cancel_task", task_id=task_id)
    assert cancelled["status"] == "CANCELLED"


async def test_quota_message_is_a_blocker(settings, server, repo):
    plan = plan_for(repo)
    plan["user_goal"] = "quota"
    task_id = (await submit(settings, plan))["task_id"]
    status = await finished(settings, task_id)
    assert status["status"] == "BLOCKED"
    assert status["error"]["code"] == "AGY_QUOTA"
    assert status["review_revision"] is None


async def test_recovery_and_timeout(settings, server, repo):
    plan = plan_for(repo)
    plan["user_goal"] = "timeout"
    task_id = (await submit(settings, plan))["task_id"]
    result = await invoke(settings, "wait_for_completion", task_id=task_id, timeout_seconds=0)
    assert result["status"] == "PENDING"
    async with asyncio.timeout(5):
        while (await invoke(settings, "get_task_status", task_id=task_id))["status"] != "RUNNING":
            await asyncio.sleep(0.05)
    await invoke(settings, "health_check")
    await call(settings, "shutdown")
    await server
    restarted = asyncio.create_task(serve(settings))
    try:
        async with asyncio.timeout(10):
            while not (await call(settings, "health_check"))["ok"]:
                await asyncio.sleep(0.05)
        recovered = await invoke(settings, "get_task_status", task_id=task_id)
        assert recovered["status"] == "INTERRUPTED"
        retried = await invoke(
            settings, "retry_task", task_id=task_id, reason="Inspected retained fixture."
        )
        assert retried["retry_count"] == 1
        timed_out = await finished(settings, task_id)
        assert timed_out["status"] == "TIMED_OUT", timed_out
    finally:
        await call(settings, "shutdown")
        await restarted


@pytest.mark.live
async def test_real_agy_coding_review_fix_and_acceptance(settings, server, repo):
    if os.environ.get("CODEX_AGY_LIVE") != "1":
        pytest.skip("Set CODEX_AGY_LIVE=1 to use the signed-in Antigravity CLI.")
    plan = plan_for(repo)
    plan["tasks"][0]["acceptance_criteria"].append(
        "The greeting function is documented for callers."
    )
    plan["constraints"].append(
        "Use the built-in file editor for the requested edit. "
        "The supervisor will run verification independently. Do not invoke any terminal commands for this fixture."
    )
    plan["tasks"][0]["implementation_steps"] = [
        "Use the file editor to write hello0.py, then finish."
    ]
    plan["tasks"][0]["verification_commands"] = [
        {
            "argv": [
                "python",
                "-c",
                "import ast,pathlib; ast.parse(pathlib.Path('hello0.py').read_text())",
            ]
        }
    ]
    task_id = (await submit(settings, plan))["task_id"]
    initial = await finished(settings, task_id, allow="live")
    print("LIVE initial:", initial, flush=True)
    assert initial["status"] == "REVIEW_REQUIRED", initial
    old = initial["review_revision"]
    summary = await invoke(settings, "get_review_context", task_id=task_id)
    assert summary["changed_files"] == ["hello0.py"]
    assert summary["verification_results"][0]["result"] == "PASSED"
    diff = await invoke(settings, "get_review_context", task_id=task_id, view="diff")
    assert "def greet(" in diff["content"]
    cwd = Path(initial["workspace_path"])
    code, _, err = await capture(
        [sys.executable, "-B", "-c", "import hello0; assert hello0.greet() == 'hello'"], cwd
    )
    assert code == 0, err
    docstring = "Return the supervisor smoke-test greeting."
    findings = [
        {
            "severity": "low",
            "description": "Document what greet returns for callers.",
            "file": "hello0.py",
            "line": 1,
            "expected_behavior": f"Keep returning 'hello' and add the docstring: {docstring}",
            "actual_behavior": "The exact user-facing function documentation is missing.",
            "reproduction_steps": ["Inspect greet.__doc__."],
        }
    ]
    await invoke(
        settings,
        "request_fix",
        task_id=task_id,
        review_revision=old,
        review_summary="Read the actual diff and verified initial behavior. Improve documentation.",
        findings=findings,
    )
    fixed = await finished(settings, task_id, allow="live")
    print("LIVE fix:", fixed, flush=True)
    assert fixed["status"] == "REVIEW_REQUIRED", fixed
    assert fixed["review_revision"] != old
    revised = await invoke(
        settings, "get_review_context", task_id=task_id, view="file", path="hello0.py"
    )
    assert docstring in revised["content"]
    code, _, err = await capture(
        [
            sys.executable,
            "-B",
            "-c",
            f"import hello0; assert hello0.greet() == 'hello'; assert hello0.greet.__doc__ == {docstring!r}",
        ],
        cwd,
    )
    assert code == 0, err
    accepted = await invoke(
        settings,
        "accept_milestone",
        task_id=task_id,
        review_revision=fixed["review_revision"],
        review_summary="Inspected the snapshot and ran an independent assertion.",
    )
    assert accepted["status"] == "ACCEPTED"
