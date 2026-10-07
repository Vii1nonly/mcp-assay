"""Grading: turn an ExecutionResult into a verdict with a reason.

Each check type is one function. `grade` dispatches on the test case's
expectation type, so adding a check type means adding a function and an entry
in CHECKS.

A check may only credit or blame the server for an answer the harness actually
observed. When no answer was observed, every check returns inconclusive.
"""

import re
import unicodedata

from . import schemas
from .models import ExecutionResult, GradedResult

# Long enough to recognise the server's message, short enough for one console line.
_QUOTE_LIMIT = 120


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
# Internal error: the server broke, which says nothing about whether it validated the input.
_INTERNAL_ERROR = -32603


def _quote(text: str | None) -> str | None:
    """The server's text as one short quoted line, or None when nothing is left.

    Reasons quote what the server said so a real rejection can be told apart
    from an unrelated error; the full text stays in content and error_message.
    """
    if not text:
        return None
    line = re.sub(r"\s+", " ", text)
    # Drop control and format characters (terminal escapes, zero-width, bidi marks).
    line = "".join(ch for ch in line if unicodedata.category(ch) not in ("Cc", "Cf")).strip()
    if not line:
        return None
    if len(line) > _QUOTE_LIMIT:
        line = line[: _QUOTE_LIMIT - 3].rstrip() + "..."
    return f'"{line}"'


def _server_text(execution: ExecutionResult) -> str | None:
    """The quoted text of the result's text content blocks, if any."""
    texts = [
        block["text"]
        for block in execution.content
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    return _quote(" ".join(texts))


def _with_server_text(reason: str, execution: ExecutionResult) -> str:
    quoted = _server_text(execution)
    return f"{reason}: {quoted}" if quoted else reason


def _rpc_error(execution: ExecutionResult) -> str:
    message = _quote(execution.error_message)
    suffix = f": {message}" if message else ""
    return f"JSON-RPC error {execution.rpc_error_code}{suffix}"


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
        return "fail", _with_server_text("server returned an error, expected success", execution)
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
    if execution.rpc_error_code == _INTERNAL_ERROR:
        return "fail", f"server broke instead of rejecting the input ({_rpc_error(execution)})"
    if execution.rpc_error_code is not None:
        return "pass", f"server rejected the call with {_rpc_error(execution)}"
    if execution.is_error:
        return "pass", _with_server_text("server returned an error as expected", execution)
    return "fail", _with_server_text("server accepted input it should have rejected", execution)


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
    if execution.is_error:
        reason = "server flagged the result as an error (isError: true)"
        return "fail", _with_server_text(reason, execution)

    payload = execution.structured
    if payload is None:
        return "fail", "server returned no structured content to validate"

    # The schema's own draft, with format checking on: the rules it was loaded under.
    errors = sorted(schemas.validator(expected_schema).iter_errors(payload), key=lambda e: e.path)
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
