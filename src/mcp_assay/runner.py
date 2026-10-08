"""Executor: have the connector run a suite against one server, then grade and aggregate."""

from .connector import run_tests
from .graders import grade
from .models import ExecutionResult, GradedResult, Scorecard
from .suite import ServerSpec, Suite


async def run_suite(suite: Suite, server: ServerSpec, timeout: float = 10.0) -> Scorecard:
    executions, protocol_version = await run_tests(
        server.command, server.args, server.cwd, suite.tests, timeout
    )
    return Scorecard(
        server_label=server.label,
        protocol_version=protocol_version,
        results=[grade_safely(execution) for execution in executions],
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
