import asyncio
import contextlib
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from acp import PROTOCOL_VERSION, connect_to_agent
from acp.exceptions import RequestError
from acp.interfaces import Client
from acp.schema import TextContentBlock

from codex_agy.config import Settings
from codex_agy.models import Problem
from codex_agy.process import ProcessGroup, capture, child_env, terminate

AsyncCallback = Callable[..., Awaitable]


class AgentEvents(Client):
    def __init__(self, permission: AsyncCallback, activity: AsyncCallback, question: AsyncCallback):
        self.permission = permission
        self.activity = activity
        self.question = question
        self.text = ""

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        return await self.permission(session_id, tool_call, options)

    async def session_update(self, session_id, update, **kwargs):
        kind = update.session_update
        if kind == "agent_message_chunk" and isinstance(update.content, TextContentBlock):
            self.text = (self.text + update.content.text)[-8000:]
        await self.activity(kind)

    async def create_elicitation(self, **kwargs):
        await self.question()
        return {"action": "cancel"}


async def ignore(*_):
    pass


async def deny(*_):
    return {"outcome": {"outcome": "cancelled"}}


def executable(settings: Settings) -> str:
    found = shutil.which(settings.agy_executable)
    if not found:
        raise Problem("AGY_NOT_FOUND", "Antigravity CLI was not found on PATH.")
    return str(Path(found).resolve())


async def discover(settings: Settings) -> dict:
    binary = executable(settings)
    version, help_result = await asyncio.gather(
        capture([binary, "--version"], timeout=10),
        capture([binary, "acp", "--help"], timeout=10),
    )
    help_text = (help_result[1] + help_result[2]).decode("utf-8", "replace")
    if version[0] != 0 or help_result[0] != 0 or "ACP" not in help_text or "stdio" not in help_text:
        raise Problem(
            "ACP_UNAVAILABLE", "Installed AGY does not advertise ACP stdio. Run agy update."
        )
    if settings.sandbox and "--sandbox" not in help_text:
        raise Problem(
            "ACP_SANDBOX_UNAVAILABLE", "AGY does not advertise the configured sandbox flag."
        )
    return {"agy_path": binary, "agy_version": version[1].decode().strip(), "transport": "stdio"}


@contextlib.asynccontextmanager
async def connection(settings: Settings, cwd: Path, events: AgentEvents):
    binary = executable(settings)
    args = ["acp"]
    if settings.sandbox:
        args.append("--sandbox")
    env = child_env()
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if name in os.environ:
            env[name] = os.environ[name]

    async with ProcessGroup() as group:
        process = await group.spawn(
            binary,
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=4 * 1024 * 1024,
        )
        conn = connect_to_agent(events, process.stdin, process.stdout, use_unstable_protocol=True)
        try:
            yield conn
        except RequestError as exc:
            if exc.code == -32000 and "auth" in str(exc).lower():
                raise Problem("AUTH_REQUIRED", "Sign in using agy acp --login.") from exc
            if exc.code == -32800:
                raise Problem("AGENT_CANCELLED", "AGY cancelled the ACP request.") from exc
            raise Problem("ACP_RPC_ERROR", str(exc)[:300], {"rpc_code": exc.code}) from exc
        except (ConnectionError, EOFError) as exc:
            raise Problem("ACP_CONNECTION_LOST", "The ACP process disconnected.") from exc
        finally:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2):
                    await conn.close()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(2):
                    await process.wait()
            group.close()
            await terminate(process)


async def initialize(conn):
    response = await conn.initialize(protocol_version=PROTOCOL_VERSION)
    if response.protocol_version != PROTOCOL_VERSION:
        raise Problem(
            "ACP_VERSION", f"Unsupported ACP protocol version {response.protocol_version}."
        )
    return response


async def probe(settings: Settings, cwd: Path, session: bool = False) -> dict:
    result = await discover(settings)
    async with connection(settings, cwd, AgentEvents(deny, ignore, ignore)) as conn:
        async with asyncio.timeout(settings.startup_seconds):
            init = await initialize(conn)
            result["initialize"] = init.model_dump(by_alias=True, exclude_none=True, mode="json")
            result["session_verified"] = False
            if session:
                created = await conn.new_session(cwd=str(cwd), mcp_servers=[])
                capabilities = init.agent_capabilities
                if (
                    capabilities
                    and capabilities.session_capabilities
                    and (capabilities.session_capabilities.close is not None)
                ):
                    await conn.close_session(session_id=created.session_id)
                result["session_verified"] = True
    return result


async def run_turn(
    settings: Settings,
    cwd: Path,
    prompt: str,
    events: AgentEvents,
    on_session: AsyncCallback,
    resume_id: str | None = None,
) -> dict:
    async with connection(settings, cwd, events) as conn:
        async with asyncio.timeout(settings.startup_seconds):
            init = await initialize(conn)
            capabilities = init.agent_capabilities
            if (
                resume_id
                and capabilities
                and capabilities.session_capabilities is not None
                and (capabilities.session_capabilities.resume is not None)
            ):
                session = await conn.resume_session(
                    session_id=resume_id, cwd=str(cwd), mcp_servers=[]
                )
                session_id = resume_id
            else:
                session = await conn.new_session(cwd=str(cwd), mcp_servers=[])
                session_id = session.session_id
            if settings.agy_model:
                option = next(
                    (item for item in (session.config_options or []) if item.category == "model"),
                    None,
                )
                if option is None:
                    raise Problem("AGY_MODEL_UNAVAILABLE", "AGY did not expose a model selector.")
                await conn.set_config_option(
                    config_id=option.id,
                    session_id=session_id,
                    value=settings.agy_model,
                )
        await on_session(session_id)
        turn = asyncio.create_task(
            conn.prompt(session_id=session_id, prompt=[TextContentBlock(text=prompt)])
        )
        try:
            result = await asyncio.shield(turn)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(3):
                    await conn.cancel(session_id=session_id)
                    await asyncio.shield(turn)
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            raise
        if (
            capabilities
            and capabilities.session_capabilities
            and (capabilities.session_capabilities.close is not None)
        ):
            with contextlib.suppress(TimeoutError, RequestError):
                async with asyncio.timeout(3):
                    await conn.close_session(session_id=session_id)
        return {"stop_reason": result.stop_reason, "summary": events.text}
