"""Transport layer: opens a session to an MCP server and makes tool calls.

This is the only module that touches the MCP protocol directly. Everything
downstream sees ExecutionResult objects instead of protocol types.
"""

import asyncio
from contextlib import asynccontextmanager
from time import perf_counter

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from .models import ExecutionResult, TestCase


@asynccontextmanager
async def open_stdio_session(command: str, args: list[str], cwd: str | None = None):
    """Spawn an MCP server as a subprocess and complete the handshake."""
    params = StdioServerParameters(command=command, args=args, cwd=cwd)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init_result = await session.initialize()
        yield session, init_result


async def run_test_case(
    session: ClientSession, test_case: TestCase, timeout: float = 10.0
) -> ExecutionResult:
    """Execute one test case and record what happened, whatever happens."""
    # Deliberately not session.call_tool(): that validates the result against the
    # server's declared output schema and raises, so the harness would never see
    # a schema-violating payload. An eval tool has to observe what the server
    # actually sent and judge it itself.
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=test_case.tool, arguments=test_case.arguments),
    )

    start = perf_counter()
    try:
        result = await asyncio.wait_for(
            session.send_request(request, types.CallToolResult), timeout
        )
    except asyncio.TimeoutError:
        return ExecutionResult(
            test_case=test_case,
            latency_ms=(perf_counter() - start) * 1000,
            completed=False,
            error_message=f"timed out after {timeout}s",
        )
    except Exception as exc:  # noqa: BLE001
        # Blind catch is deliberate: a server under test may fail in any way,
        # and every failure has to become a result rather than stop the run.
        return ExecutionResult(
            test_case=test_case,
            latency_ms=(perf_counter() - start) * 1000,
            completed=False,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    latency_ms = (perf_counter() - start) * 1000
    return ExecutionResult(
        test_case=test_case,
        latency_ms=latency_ms,
        completed=True,
        is_error=result.is_error,
        content=[block.model_dump(mode="json", exclude_none=True) for block in result.content],
        structured=result.structured_content,
    )
