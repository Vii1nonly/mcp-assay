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
from .preflight import PreflightError, check, one_line, refusal

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
) -> tuple[list[ExecutionResult], str, list[str]]:
    """Check every test against the server's tool list, then run them in order on
    one server session.

    Returns the execution results, the negotiated protocol version, and warnings
    for preflight checks that had to be skipped. Raises PreflightError, before
    any test runs, when a test does not fit the server. The cwd is used as
    given: suite loading has already resolved it.
    """
    results = []
    refused = None
    async with open_stdio_session(command, args, cwd) as (session, init_result):
        try:
            warnings = await _preflight(session, tests, timeout)
        except PreflightError as e:
            refused = e  # raised once the session is closed
        else:
            for test_case in tests:
                results.append(await run_test_case(session, test_case, timeout, session_number=1))
    if refused:
        raise refused
    return results, init_result.protocol_version, warnings


async def _preflight(session: ClientSession, tests: list[TestCase], timeout: float) -> list[str]:
    try:
        tools = await list_tool_schemas(session, timeout)
    except PreflightError:
        raise
    except TimeoutError:
        raise PreflightError(f"tools/list failed: not finished within {timeout}s") from None
    except MCPError as e:
        if e.code in _SDK_LOCAL_CODES:
            # The SDK's own code: the server never sent this error.
            detail = f"the connection closed before the server answered ({one_line(e.message)})"
        else:
            detail = f"JSON-RPC error {e.code}: {one_line(e.message)}"
        raise PreflightError(f"tools/list failed: {detail}") from None
    except Exception as e:  # noqa: BLE001
        # Any other failure is still a server that cannot be checked: refuse the run.
        raise PreflightError(f"tools/list failed: {type(e).__name__}: {one_line(e)}") from None
    preflight = check(tests, tools)
    if preflight.problems:
        raise PreflightError(refusal(preflight.problems))
    return preflight.warnings


# More pages than any real tool list needs; a server past this never stops paging.
_MAX_TOOL_PAGES = 1000


async def list_tool_schemas(session, timeout: float) -> dict[str, dict]:
    """Every tool the server lists, as name -> inputSchema, following pagination.

    Sent as a raw tools/list rather than session.list_tools(), which can drop
    tools and caches per-tool state: the preflight checks what the server sent.
    The whole listing, every page, must finish within `timeout`.
    """
    tools: dict[str, dict] = {}
    cursor = None
    seen = set()
    async with asyncio.timeout(timeout):
        for page_number in range(1, _MAX_TOOL_PAGES + 1):
            params = None if cursor is None else types.PaginatedRequestParams(cursor=cursor)
            request = types.ListToolsRequest(params=params)
            try:
                page = await session.send_request(request, types.ListToolsResult)
            except ValidationError as e:
                # The reply breaks the MCP tools/list shape (for example an inputSchema
                # without type "object"): name the spot so the server can be fixed.
                raise PreflightError(
                    f"tools/list failed: malformed reply on page {page_number}: {_spot(e)}"
                ) from None
            for tool in page.tools:
                tools.setdefault(tool.name, tool.input_schema)
            cursor = page.next_cursor
            if cursor is None:
                return tools
            if cursor in seen:
                raise PreflightError(
                    f"tools/list failed: the server sent cursor {cursor!r} twice,"
                    " so its tool list never ends"
                )
            seen.add(cursor)
    raise PreflightError(
        f"tools/list failed: more than {_MAX_TOOL_PAGES} pages, so its tool list never ends"
    )


def _spot(error: ValidationError) -> str:
    """Where the first validation error is, as `tools[3].inputSchema.type: message`."""
    first = error.errors()[0]
    at = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in first["loc"]).lstrip(".")
    return f"{at}: {one_line(first['msg'])}" if at else one_line(first["msg"])


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
