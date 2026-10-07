"""The console report: harness errors are shown as harness errors with a count,
never blamed on the server, and text from the server is printed literally."""

import io

from rich.console import Console

from mcp_assay.models import ExecutionResult, GradedResult, Scorecard, TestCase
from mcp_assay.report import print_scorecard


def _result(test_id, verdict, reason, harness_error=False, outcome="answered", category="c"):
    execution = ExecutionResult(
        test_case=TestCase(id=test_id, tool="x", expect={"type": "no_error"}, category=category),
        latency_ms=1.0,
        outcome=outcome,
    )
    return GradedResult(
        execution=execution, verdict=verdict, reason=reason, harness_error=harness_error
    )


def _render(results, label="python server.py", protocol_version=None) -> str:
    out = io.StringIO()
    console = Console(file=out, width=200, color_system=None)
    scorecard = Scorecard(server_label=label, protocol_version=protocol_version, results=results)
    print_scorecard(scorecard, console)
    return out.getvalue()


def _section(text: str, heading: str) -> str:
    """The lines under a heading, up to the next blank line."""
    after = text.split(heading, 1)[1]
    return after.split("\n\n", 1)[0]


MIXED = [
    _result("ok", "pass", "completed without error"),
    _result("slow", "inconclusive", "timed out", outcome="timeout"),
    _result("crash-1", "inconclusive", "harness error while grading: X: y", harness_error=True),
    _result("crash-2", "inconclusive", "harness error while grading: X: z", harness_error=True),
]


def test_harness_errors_get_their_own_section_and_a_count():
    text = _render(MIXED)
    assert "warning: 2 tests could not be graded because of a harness error" in text
    harness = _section(text, "Harness errors")
    assert "crash-1" in harness and "crash-2" in harness
    unobserved = _section(text, "Inconclusive")
    assert "slow" in unobserved
    assert "crash-1" not in unobserved and "crash-2" not in unobserved


def test_harness_errors_still_count_as_inconclusive_in_the_totals():
    assert "1/4 passed, 0 failed, 3 inconclusive" in _render(MIXED)


def test_a_single_harness_error_is_counted_in_the_singular():
    text = _render([_result("crash", "inconclusive", "harness error", harness_error=True)])
    assert "warning: 1 test could not be graded because of a harness error" in text


def test_no_harness_error_section_or_warning_without_harness_errors():
    text = _render(MIXED[:2])
    assert "Harness errors" not in text
    assert "warning:" not in text


def test_server_text_that_looks_like_markup_is_printed_literally():
    # Reasons quote server text; rich would read [/x] as a closing tag and raise.
    results = [
        _result("[bold]id", "fail", "schema violation at n: '[/x]' is not of type 'integer'")
    ]
    text = _render(results, label="python [/x] server.py")
    assert "[bold]id" in text
    assert "'[/x]' is not of type 'integer'" in text
    assert "python [/x] server.py" in text


def test_markup_in_every_printed_field_is_printed_literally():
    # "[/x]" raises in rich unless escaped, so every field that can carry it is
    # exercised: ids and category in the table, each section's id and reason,
    # and the protocol version.
    results = [
        _result("[/x]fail", "fail", "[/x]fail-reason", category="[/x]cat"),
        _result("[/x]slow", "inconclusive", "[/x]slow-reason", outcome="timeout"),
        _result("[/x]crash", "inconclusive", "[/x]crash-reason", harness_error=True),
    ]
    text = _render(results, protocol_version="[/x]v")
    for literal in (
        "[/x]fail",
        "[/x]cat",
        "[/x]slow-reason",
        "[/x]crash",
        "[/x]crash-reason",
        "[/x]v",
    ):
        assert literal in text


# N5: every verdict shows its reason, passes included, so a server that
# rejects everything is visible from the console.
def test_passes_are_listed_with_their_reasons():
    results = [
        _result("denied", "pass", 'server returned an error as expected: "Access denied"'),
        _result("broken", "fail", "server accepted input it should have rejected"),
    ]
    passed = _section(_render(results), "Passed")
    assert 'denied: server returned an error as expected: "Access denied"' in passed
    assert "broken" not in passed


def test_markup_in_a_pass_reason_is_printed_literally():
    text = _render([_result("[/x]id", "pass", 'quoted "[/x]" text')])
    assert "[/x]id" in text
    assert 'quoted "[/x]" text' in text


def test_emoji_codes_in_server_text_are_printed_literally():
    # rich turns ":id:" or ":x:" into emoji unless the text is passed literally.
    results = [
        _result("ok:id:", "pass", 'quoted "user:id: must be int"', category="c:x:"),
        _result("bad:x:", "fail", 'quoted ":warning: no"'),
        _result("slow:x:", "inconclusive", "timed out :x:", outcome="timeout"),
        _result("crash:x:", "inconclusive", "harness :x:", harness_error=True),
    ]
    text = _render(results, label="python :x: server.py", protocol_version="v:x:")
    for literal in (
        "ok:id:",
        "c:x:",
        '"user:id: must be int"',
        "bad:x:",
        '":warning: no"',
        "timed out :x:",
        "harness :x:",
        "python :x: server.py",
        "v:x:",
    ):
        assert literal in text


def test_no_passed_section_without_passes():
    text = _render([_result("broken", "fail", "server accepted input it should have rejected")])
    assert "Passed" not in text
