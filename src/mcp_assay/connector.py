"""Transport layer: opens a session to an MCP server and makes tool calls.

This is the only module that touches the MCP protocol directly. Everything
downstream sees ExecutionResult objects instead of protocol types.
"""

import asyncio
from contextlib import asynccontextmanager
from time import perf_counter

from mcp import ClientSession, MCPError, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from .models import ExecutionResult, TestCase

# The SDK raises MCPError both for a real JSON-RPC error reply and for its own
# local failures, which it reports with these codes. A server may legitimately
# send the same codes, so an error carrying one cannot be attributed to the
# server and is never treated as the server answering.
# REQUEST_TIMEOUT (-32001) is absent on purpose: the SDK only raises it when a
# read timeout is armed, and this harness never arms one (asyncio.wait_for does
# the timing). Add it back here if an SDK read timeout is ever configured.
_SDK_LOCAL_CODES = {
    types.CONNECTION_CLOSED: "transport_error",
}


@asynccontextmanager
async def open_stdio_session(command: str, args: list[str], cwd: str | None = None):
    """Spawn an MCP server as a subprocess and complete the handshake."""
    params = StdioServerParameters(command=command, args=args, cwd=cwd)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init_result = await session.initialize()
        yield session, init_result


async def run_tests(
    command: str, args: list[str], cwd: str | None, tests: list[TestCase], timeout: float
) -> tuple[list[ExecutionResult], str]:
    """Run every test case in order on one server session.

    Returns the execution results and the negotiated protocol version. The cwd
    is used as given: suite loading has already resolved it.
    """
    results = []
    async with open_stdio_session(command, args, cwd) as (session, init_result):
        for test_case in tests:
            results.append(await run_test_case(session, test_case, timeout, session_number=1))
    return results, init_result.protocol_version


async def run_test_case(
    session: ClientSession, test_case: TestCase, timeout: float, session_number: int
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
            session=session_number,
            latency_ms=(perf_counter() - start) * 1000,
            outcome="timeout",
            error_message=f"timed out after {timeout}s",
        )
    except MCPError as exc:
        latency_ms = (perf_counter() - start) * 1000
        local = _SDK_LOCAL_CODES.get(exc.code)
        if local:
            return ExecutionResult(
                test_case=test_case,
                session=session_number,
                latency_ms=latency_ms,
                outcome=local,
                error_message=f"{exc.message} (code {exc.code}; not attributable to the server)",
            )
        return ExecutionResult(
            test_case=test_case,
            session=session_number,
            latency_ms=latency_ms,
            outcome="answered",
            rpc_error_code=exc.code,
            error_message=exc.message,
        )
    except ValidationError as exc:
        # send_request validates the result only after the matching reply has
        # arrived, so this is an observed answer that broke the protocol shape.
        return ExecutionResult(
            test_case=test_case,
            session=session_number,
            latency_ms=(perf_counter() - start) * 1000,
            outcome="answered",
            protocol_violation=str(exc).splitlines()[0],
            error_message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        # Blind catch is deliberate: a server under test may fail in any way,
        # and every failure has to become a result rather than stop the run.
        return ExecutionResult(
            test_case=test_case,
            session=session_number,
            latency_ms=(perf_counter() - start) * 1000,
            outcome="transport_error",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    latency_ms = (perf_counter() - start) * 1000
    return ExecutionResult(
        test_case=test_case,
        session=session_number,
        latency_ms=latency_ms,
        outcome="answered",
        is_error=result.is_error,
        content=[block.model_dump(mode="json", exclude_none=True) for block in result.content],
        structured=result.structured_content,
    )
