"""The harness has to fail a broken server. If these pass trivially, it is broken."""

from pathlib import Path

import pytest

from mcp_eval.models import ExecutionResult, TestCase
from mcp_eval.runner import run_suite
from mcp_eval.suite import load_suite

SUITE = Path(__file__).parent.parent / "suites" / "broken_server.yaml"


@pytest.mark.asyncio
async def test_broken_server_is_caught():
    suite = load_suite(SUITE)
    scorecard = await run_suite(suite, suite.server)

    verdicts = {r.execution.test_case.id: r.verdict for r in scorecard.results}
    assert verdicts["echo-happy-path"] == "pass"
    assert verdicts["echo-missing-required-arg"] == "pass"
    # The two planted bugs.
    assert verdicts["read-file-missing-required-arg"] == "fail"
    assert verdicts["get-status-matches-output-schema"] == "fail"


def _execution(**kwargs) -> ExecutionResult:
    defaults = {
        "test_case": TestCase(id="t", tool="x", expect={"type": "no_error"}),
        "latency_ms": 1.0,
        "completed": True,
    }
    return ExecutionResult(**{**defaults, **kwargs})


def test_is_error_fails_when_server_accepts_bad_input():
    from mcp_eval.graders import grade

    execution = _execution(
        test_case=TestCase(id="t", tool="x", expect={"type": "is_error"}),
        is_error=False,
    )
    assert grade(execution).verdict == "fail"


def test_timeout_is_recorded_as_incomplete():
    from mcp_eval.graders import grade

    execution = _execution(completed=False, error_message="timed out after 10.0s")
    assert grade(execution).verdict == "fail"
