import json

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from codex_agy.api import TOOLS, BackgroundWait
from codex_agy.config import Settings
from codex_agy.ipc import call, failure
from codex_agy.models import Problem

READ_ONLY = {
    "wait_for_completion",
    "get_task_status",
    "get_task_result",
    "get_review_context",
    "list_tasks",
    "health_check",
    "get_system_capabilities",
}


def make_server(settings: Settings, wait_only: bool = False) -> Server:
    operations = (
        {
            "wait_for_review": (
                BackgroundWait,
                "Wait for an AGY milestone to need review, permission, or recovery. "
                "Keeps one call open for up to 1800 seconds. Invoke when the user selects Antigravity.",
            )
        }
        if wait_only
        else TOOLS
    )
    server = Server(
        "agy_wait" if wait_only else "codex_agy",
        version="0.1.0",
        instructions="Delegate implementation to AGY. Review the immutable revision before accepting.",
    )

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=name,
                description=description,
                inputSchema=model.model_json_schema(),
                annotations=ToolAnnotations(
                    readOnlyHint=wait_only or name in READ_ONLY, openWorldHint=False
                ),
            )
            for name, (model, description) in operations.items()
        ]

    @server.call_tool(validate_input=False)
    async def invoke(name, arguments):
        result = (
            await call(settings, "wait_background" if wait_only else name, arguments)
            if name in operations
            else failure(Problem("UNKNOWN_TOOL", "Unknown supervisor operation."))
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
            structuredContent=result,
            isError=not result["ok"],
        )

    return server


async def run(settings: Settings, wait_only: bool = False):
    server = make_server(settings, wait_only)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
