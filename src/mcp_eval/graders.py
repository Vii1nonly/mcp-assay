"""Grading: turn an ExecutionResult into a pass or fail with a reason.

Each check type is one function. `grade` dispatches on the test case's
expectation type, so adding a check type means adding a function and an entry
in CHECKS.
"""

from jsonschema import Draft202012Validator

from .models import ExecutionResult, GradedResult


def grade(execution: ExecutionResult) -> GradedResult:
    check = CHECKS[execution.test_case.expect.type]
    verdict, reason = check(execution)
    return GradedResult(execution=execution, verdict=verdict, reason=reason)


def _no_error(execution: ExecutionResult):
    if not execution.completed:
        return "fail", f"call did not complete: {execution.error_message}"
    if execution.is_error:
        return "fail", "server returned an error, expected success"
    return "pass", "completed without error"


def _is_error(execution: ExecutionResult):
    """The server SHOULD have rejected this call.

    A server that happily accepts invalid input is the failure this check
    exists to catch, so a clean success here is a fail.
    """
    if not execution.completed:
        # A protocol-level error still counts as a rejection.
        return "pass", f"rejected at protocol level: {execution.error_message}"
    if execution.is_error:
        return "pass", "server returned an error as expected"
    return "fail", "server accepted input it should have rejected"


def _schema_valid(execution: ExecutionResult):
    expected_schema = execution.test_case.expect.json_schema
    if expected_schema is None:
        return "fail", "test case declares schema_valid but provides no schema"
    if not execution.completed:
        return "fail", f"call did not complete: {execution.error_message}"

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
