"""Executor: run every test case in a suite against one server, then aggregate."""

from .connector import open_stdio_session, run_test_case
from .graders import grade
from .models import Scorecard
from .suite import ServerSpec, Suite


async def run_suite(suite: Suite, server: ServerSpec, timeout: float = 10.0) -> Scorecard:
    results = []
    async with open_stdio_session(server.command, server.args, server.cwd) as (
        session,
        init_result,
    ):
        for test_case in suite.tests:
            execution = await run_test_case(session, test_case, timeout)
            results.append(grade(execution))

    return Scorecard(
        server_label=server.label,
        protocol_version=init_result.protocol_version,
        results=results,
    )
