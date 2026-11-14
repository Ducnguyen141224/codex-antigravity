"""ACP SDK fixture used only by automated tests."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from acp import Agent, run_agent
from acp.schema import (
    AgentMessageChunk,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallUpdate,
)


class Fixture(Agent):
    def on_connect(self, connection):
        self.connection = connection

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(self, cwd, mcp_servers, **kwargs):
        self.cwd = Path(cwd)
        return NewSessionResponse(session_id=str(uuid4()))

    async def prompt(self, session_id, prompt, **kwargs):
        data = json.loads(prompt[0].text.split("\n\n", 1)[1])
        goal = data["plan_context"]["user_goal"]
        if goal == "timeout":
            await asyncio.sleep(90)
        if goal == "quota":
            for chunk in ("⚠ Individual quota ", "reached. Resets later."):
                await self.connection.session_update(
                    session_id=session_id,
                    update=AgentMessageChunk(content=TextContentBlock(text=chunk)),
                )
            return PromptResponse(stop_reason="end_turn")
        await self.connection.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                content=TextContentBlock(text="Fixture transcript must stay local.\n")
            ),
        )
        if goal == "permission":
            result = await self.connection.request_permission(
                session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id="test-call", title="Write fixture file"),
                options=[
                    PermissionOption(kind="allow_once", name="Allow", option_id="yes"),
                    PermissionOption(kind="reject_once", name="Reject", option_id="no"),
                ],
            )
            if result.outcome.outcome != "selected" or result.outcome.option_id != "yes":
                return PromptResponse(stop_reason="refusal")
        name = data["assigned_tasks"][0]["expected_files"][0]
        value = "hello again" if data.get("independent_findings") else "hello"
        (self.cwd / name).write_text(f"def greet():\n    return {value!r}\n", encoding="utf-8")
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id, **kwargs):
        pass


asyncio.run(run_agent(Fixture()))
