"""The harness has to fail a broken server. If these pass trivially, it is broken."""

from pathlib import Path

import pytest

from mcp_assay.models import ExecutionResult, TestCase
from mcp_assay.runner import run_suite
from mcp_assay.suite import Suite, load_suite

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


@pytest.mark.asyncio
async def test_timeout_under_is_error_is_not_credited_as_a_rejection():
    # Regression: a call that times out never received a rejection from the
    # server, so the harness cannot credit one. Runs through the real connector.
    server = load_suite(SUITE).server
    suite = Suite(
        name="timeout-under-is-error",
        server=server,
        tests=[TestCase(id="hang-bad-input", tool="hang", expect={"type": "is_error"})],
    )
    scorecard = await run_suite(suite, server, timeout=1.0)

    [result] = scorecard.results
    assert result.verdict == "inconclusive"
    assert result.execution.outcome == "timeout"


async def _run_one(test_case: TestCase):
    server = load_suite(SUITE).server
    suite = Suite(name="one", server=server, tests=[test_case])
    [result] = (await run_suite(suite, server)).results
    return result


@pytest.mark.asyncio
async def test_json_rpc_rejection_is_credited_under_is_error():
    # Guard against the opposite wrong verdict: rejecting bad input with a
    # standard JSON-RPC error is a real rejection and must still pass.
    result = await _run_one(TestCase(id="strict", tool="strict_echo", expect={"type": "is_error"}))
    assert result.execution.outcome == "answered"
    assert result.execution.rpc_error_code == -32602
    assert result.verdict == "pass"


@pytest.mark.asyncio
async def test_error_code_shared_with_the_sdk_is_not_credited():
    # -32000 is also the SDK's local "connection closed" code, so the harness
    # cannot tell who sent it and must not credit it as a rejection.
    result = await _run_one(
        TestCase(id="ambiguous", tool="ambiguous_reject", expect={"type": "is_error"})
    )
    assert result.execution.outcome == "transport_error"
    assert result.verdict == "inconclusive"


@pytest.mark.asyncio
async def test_server_sent_request_timeout_code_is_credited():
    # The harness never arms an SDK read timeout, so -32001 can only come
    # from the server, and a rejection sent with it is a real rejection.
    result = await _run_one(
        TestCase(id="t32001", tool="timeout_code_reject", expect={"type": "is_error"})
    )
    assert result.execution.outcome == "answered"
    assert result.execution.rpc_error_code == -32001
    assert result.verdict == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["no_error", "is_error"])
async def test_malformed_reply_fails_every_check(check):
    # The reply arrived but broke the result shape: that is a server bug the
    # harness observed, never "not observed" and never a pass.
    result = await _run_one(TestCase(id="bad", tool="malformed_reply", expect={"type": check}))
    assert result.execution.outcome == "answered"
    assert result.verdict == "fail"


@pytest.mark.asyncio
async def test_method_not_found_is_not_a_rejection_of_the_input():
    # -32601 means the server never evaluated the arguments, so it cannot
    # count as refusing them.
    result = await _run_one(
        TestCase(id="missing", tool="method_missing", expect={"type": "is_error"})
    )
    assert result.execution.rpc_error_code == -32601
    assert result.verdict == "fail"


def _execution(**kwargs) -> ExecutionResult:
    defaults = {
        "test_case": TestCase(id="t", tool="x", expect={"type": "no_error"}),
        "latency_ms": 1.0,
        "outcome": "answered",
    }
    return ExecutionResult(**{**defaults, **kwargs})


def _case(check: str) -> TestCase:
    expect = {"type": check}
    if check == "schema_valid":
        expect["schema"] = {"type": "object"}
    return TestCase(id="t", tool="x", expect=expect)


def test_is_error_fails_when_server_accepts_bad_input():
    from mcp_assay.graders import grade

    execution = _execution(test_case=_case("is_error"), is_error=False)
    assert grade(execution).verdict == "fail"


def test_timeout_under_no_error_is_inconclusive():
    from mcp_assay.graders import grade

    execution = _execution(outcome="timeout", error_message="timed out after 10.0s")
    assert grade(execution).verdict == "inconclusive"


@pytest.mark.parametrize("check", ["no_error", "is_error", "schema_valid"])
@pytest.mark.parametrize("outcome", ["timeout", "transport_error"])
def test_unobserved_answer_is_inconclusive_for_every_check(check, outcome):
    from mcp_assay.graders import grade

    execution = _execution(test_case=_case(check), outcome=outcome, error_message="x")
    assert grade(execution).verdict == "inconclusive"


@pytest.mark.parametrize(
    ("check", "expected"),
    [("is_error", "pass"), ("no_error", "fail"), ("schema_valid", "fail")],
)
def test_json_rpc_error_reply_is_a_server_rejection(check, expected):
    from mcp_assay.graders import grade

    execution = _execution(
        test_case=_case(check), rpc_error_code=-32602, error_message="Invalid params"
    )
    assert grade(execution).verdict == expected


# A schema that refers to itself makes jsonschema recurse until RecursionError.
# That is a harness failure, not a server verdict: only the test it hit is lost.
_SELF_REF = {"type": "schema_valid", "schema": {"$ref": "#"}}


@pytest.mark.asyncio
async def test_grading_crash_affects_only_its_own_test():
    server = load_suite(SUITE).server
    suite = Suite(
        name="grading-crash",
        server=server,
        tests=[
            TestCase(id="self-ref", tool="get_status", expect=_SELF_REF),
            TestCase(id="echo", tool="echo", arguments={"text": "hi"}, expect={"type": "no_error"}),
        ],
    )
    scorecard = await run_suite(suite, server)

    results = {r.execution.test_case.id: r for r in scorecard.results}
    crashed = results["self-ref"]
    assert crashed.verdict == "inconclusive"
    assert crashed.harness_error is True
    assert crashed.reason.startswith("harness error while grading: RecursionError")
    assert results["echo"].verdict == "pass"
    assert results["echo"].harness_error is False
    assert scorecard.harness_errors == [crashed]


def test_grade_guard_turns_an_exception_into_a_harness_error():
    from mcp_assay.runner import grade_safely

    execution = _execution(
        test_case=TestCase(id="t", tool="x", expect=_SELF_REF), structured={"a": 1}
    )
    result = grade_safely(execution)
    assert result.verdict == "inconclusive"
    assert result.harness_error is True
    # CPython may add a suffix to the message, so only its start is fixed.
    assert result.reason.startswith(
        "harness error while grading: RecursionError: maximum recursion depth exceeded"
    )


def test_ordinary_grade_is_not_a_harness_error():
    from mcp_assay.runner import grade_safely

    result = grade_safely(_execution())
    assert result.verdict == "pass"
    assert result.harness_error is False
    assert '"harness_error":false' in result.model_dump_json()


def test_harness_error_without_a_message_names_only_the_exception(monkeypatch):
    # Fault injection: no real grader exception with an empty message is known.
    import mcp_assay.runner

    def raise_bare_error(execution):
        raise KeyError()

    monkeypatch.setattr(mcp_assay.runner, "grade", raise_bare_error)
    result = mcp_assay.runner.grade_safely(_execution())
    assert result.reason == "harness error while grading: KeyError"


def test_ctrl_c_while_grading_still_stops_the_run(monkeypatch):
    # The guard catches Exception, not BaseException, so an interrupt is not
    # turned into a harness error. Fault injection: Ctrl+C cannot be sent here.
    import mcp_assay.runner

    def interrupted(execution):
        raise KeyboardInterrupt

    monkeypatch.setattr(mcp_assay.runner, "grade", interrupted)
    with pytest.raises(KeyboardInterrupt):
        mcp_assay.runner.grade_safely(_execution())
