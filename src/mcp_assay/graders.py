"""Grading: turn an ExecutionResult into a verdict with a reason.

Each check type is one function. `grade` dispatches on the test case's
expectation type, so adding a check type means adding a function and an entry
in CHECKS.

A check may only credit or blame the server for an answer the harness actually
observed. When no answer was observed, every check returns inconclusive.
"""

from jsonschema import Draft202012Validator

from .models import ExecutionResult, GradedResult


def grade(execution: ExecutionResult) -> GradedResult:
    check = CHECKS[execution.test_case.expect.type]
    verdict, reason = check(execution)
    return GradedResult(execution=execution, verdict=verdict, reason=reason)


def _unobserved(execution: ExecutionResult):
    return (
        "inconclusive",
        f"server's answer was not observed ({execution.outcome}): {execution.error_message}",
    )


# JSON-RPC codes meaning the request never reached argument evaluation:
# parse error, invalid request, method not found.
_INPUT_NOT_EVALUATED = {-32700, -32600, -32601}


def _rpc_error(execution: ExecutionResult) -> str:
    return f"JSON-RPC error {execution.rpc_error_code}: {execution.error_message}"


def _malformed(execution: ExecutionResult):
    return "fail", f"server sent a malformed reply: {execution.protocol_violation}"


def _no_error(execution: ExecutionResult):
    if execution.outcome != "answered":
        return _unobserved(execution)
    if execution.protocol_violation:
        return _malformed(execution)
    if execution.rpc_error_code is not None:
        return "fail", f"server returned {_rpc_error(execution)}, expected success"
    if execution.is_error:
        return "fail", "server returned an error, expected success"
    return "pass", "completed without error"


def _is_error(execution: ExecutionResult):
    """The server SHOULD have rejected this call.

    A server that happily accepts invalid input is the failure this check
    exists to catch, so a clean success here is a fail. Only a rejection the
    server actually sent earns a pass.
    """
    if execution.outcome != "answered":
        return _unobserved(execution)
    if execution.protocol_violation:
        return _malformed(execution)
    if execution.rpc_error_code in _INPUT_NOT_EVALUATED:
        return "fail", f"server did not evaluate the input ({_rpc_error(execution)})"
    if execution.rpc_error_code is not None:
        return "pass", f"server rejected the call with {_rpc_error(execution)}"
    if execution.is_error:
        return "pass", "server returned an error as expected"
    return "fail", "server accepted input it should have rejected"


def _schema_valid(execution: ExecutionResult):
    expected_schema = execution.test_case.expect.json_schema
    if expected_schema is None:
        return "fail", "test case declares schema_valid but provides no schema"
    if execution.outcome != "answered":
        return _unobserved(execution)
    if execution.protocol_violation:
        return _malformed(execution)
    if execution.rpc_error_code is not None:
        return "fail", f"server returned {_rpc_error(execution)} instead of a result"

    payload = execution.structured
    if payload is None:
        return "fail", "server returned no structured content to validate"

    errors = sorted(
        Draft202012Validator(expected_schema).iter_errors(payload), key=lambda e: e.path
    )
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.path) or "(root)"
        return "fail", f"schema violation at {location}: {first.message}"
    return "pass", "structured content matches schema"


CHECKS = {
    "no_error": _no_error,
    "is_error": _is_error,
    "schema_valid": _schema_valid,
}
