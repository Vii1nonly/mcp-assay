"""Executor: run every test case in a suite against one server, then aggregate."""

from .connector import open_stdio_session, run_test_case
from .graders import grade
from .models import ExecutionResult, GradedResult, Scorecard
from .suite import ServerSpec, Suite


async def run_suite(suite: Suite, server: ServerSpec, timeout: float = 10.0) -> Scorecard:
    results = []
    async with open_stdio_session(server.command, server.args, server.cwd) as (
        session,
        init_result,
    ):
        for test_case in suite.tests:
            execution = await run_test_case(session, test_case, timeout)
            results.append(grade_safely(execution))

    return Scorecard(
        server_label=server.label,
        protocol_version=init_result.protocol_version,
        results=results,
    )


def grade_safely(execution: ExecutionResult) -> GradedResult:
    """Grade one result; a crash in the harness costs only this test's verdict."""
    try:
        return grade(execution)
    # Any harness bug while grading must not throw away the other tests' verdicts.
    # Exception, not BaseException, so Ctrl+C still stops the run.
    except Exception as e:  # noqa: BLE001
        error = type(e).__name__
        if str(e).strip():
            error += f": {str(e).strip().splitlines()[0]}"
        return GradedResult(
            execution=execution,
            verdict="inconclusive",
            reason=f"harness error while grading: {error}",
            harness_error=True,
        )
