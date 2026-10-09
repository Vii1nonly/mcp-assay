"""The CLI never loses verdicts: the output path is checked before the run, the
JSON is written before rendering, late output failures exit 2, and the console
never crashes on characters the terminal can't encode."""

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

from typer.testing import CliRunner

import mcp_assay.cli
from mcp_assay import graders
from mcp_assay.cli import app

REPO = Path(__file__).parent.parent


def _suite(tmp_path: Path, *, command: str | None = None, cwd: Path = REPO) -> Path:
    """A two-test suite against examples/broken_server.py, run from the repo root."""
    server = (
        f"server: {{command: {command}}}"
        if command
        else "server: {command: python, args: [examples/broken_server.py], "
        f"cwd: '{cwd.as_posix()}'}}"
    )
    path = tmp_path / "suite.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            name: s
            {server}
            tests:
              - {{id: echo, tool: echo, arguments: {{text: hi}}, expect: {{type: no_error}}}}
              - id: status
                tool: get_status
                expect: {{type: schema_valid, schema: {{type: object}}}}
            """
        ),
        encoding="utf-8",
    )
    return path


def _run(*args: str):
    return CliRunner().invoke(app, ["run", *args])


def _break_schema_grader(monkeypatch) -> None:
    """Fault injection: the load rules now refuse every known schema that crashes the
    grader, so a grading crash is simulated to prove the guard still holds."""

    def boom(execution):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setitem(graders.CHECKS, "schema_valid", boom)


def test_missing_json_folder_is_refused_before_the_server_starts(tmp_path):
    # The server command does not exist: if the run had started, it would fail differently.
    suite = _suite(tmp_path, command="definitely-not-a-real-command")
    target = tmp_path / "nope" / "out.json"
    result = _run(str(suite), "--json", str(target))
    assert result.exit_code == 2
    assert result.stdout == ""
    [line] = result.stderr.splitlines()
    assert str(target.parent) in line


def test_json_path_that_is_a_folder_is_refused(tmp_path):
    suite = _suite(tmp_path, command="definitely-not-a-real-command")
    result = _run(str(suite), "--json", str(tmp_path))
    assert result.exit_code == 2
    [line] = result.stderr.splitlines()
    assert str(tmp_path) in line


def test_cp1252_console_does_not_lose_the_verdicts(tmp_path):
    # A real run in a cp1252 console (what a piped Windows console gives), with a
    # character cp1252 lacks in the server label. --arg is only used with
    # --command, which also drops the suite's cwd, so run from the repo root.
    out = tmp_path / "out.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_assay.cli",
            "run",
            str(_suite(tmp_path)),
            "--command",
            sys.executable,
            "--arg",
            "examples/broken_server.py",
            "--arg",
            "✓",
            "--json",
            str(out),
        ],
        cwd=REPO,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        capture_output=True,
        timeout=120,
    )
    stderr = proc.stderr.decode("cp1252", errors="replace")
    assert "Traceback" not in stderr
    assert proc.returncode == 0  # both tests pass; nothing was lost to the console
    assert len(json.loads(out.read_text(encoding="utf-8"))["results"]) == 2
    assert "broken_server.py ?" in proc.stdout.decode("cp1252")


def test_command_option_starts_the_server_in_the_current_folder(tmp_path, monkeypatch):
    # N6: --command replaces the suite's server block, cwd included, so the server
    # starts where the user typed the command. The suite's cwd is an empty folder:
    # had it been used, examples/broken_server.py would not be found.
    empty = tmp_path / "empty"
    empty.mkdir()
    suite = _suite(tmp_path, cwd=empty)
    monkeypatch.chdir(REPO)
    result = _run(str(suite), "--command", sys.executable, "--arg", "examples/broken_server.py")
    assert result.exit_code == 0, result.output
    assert "2/2 passed" in result.stdout
    # The control: without --command the suite's cwd is used and the server is not found.
    assert _run(str(suite)).exit_code == 1


def test_missing_suite_cwd_is_refused_even_with_command(tmp_path, monkeypatch):
    # The suite is checked in full when it loads, so a cwd that does not exist is
    # refused even though --command replaces the server block that holds it.
    missing = tmp_path / "nope"
    suite = _suite(tmp_path, cwd=missing)
    monkeypatch.chdir(REPO)
    result = _run(str(suite), "--command", sys.executable, "--arg", "examples/broken_server.py")
    assert result.exit_code == 2
    assert result.stdout == ""
    [line] = result.stderr.splitlines()
    assert str(missing.resolve()) in line


# N8: every test is checked against the server's tool list before any test runs.
def _preflight_suite(tmp_path: Path, test: str, *server_args: str) -> Path:
    args = ", ".join(["examples/broken_server.py", *server_args])
    path = tmp_path / "suite.yaml"
    path.write_text(
        f"name: s\nserver: {{command: python, args: [{args}], cwd: '{REPO.as_posix()}'}}\n"
        f"tests:\n  - {test}\n",
        encoding="utf-8",
    )
    return path


def _refusal(result, suite: Path) -> str:
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    [line] = result.stderr.splitlines()
    assert line.startswith(f"{suite}: ")
    return line


def test_unknown_tool_is_refused_before_any_test_runs(tmp_path):
    suite = _preflight_suite(tmp_path, "{id: gone, tool: reed_file, expect: {type: is_error}}")
    line = _refusal(_run(str(suite)), suite)
    assert "tests[0] (id 'gone'): tool 'reed_file' is not on the server" in line
    assert "(did you mean 'read_file'?)" in line


def test_unknown_tool_flag_credits_the_server_s_rejection(tmp_path):
    test = "{id: gone, tool: no_such_tool, unknown_tool: true, expect: {type: is_error}}"
    out = tmp_path / "out.json"
    result = _run(str(_preflight_suite(tmp_path, test)), "--json", str(out))
    assert result.exit_code == 0, result.output
    [graded] = json.loads(out.read_text(encoding="utf-8"))["results"]
    assert graded["verdict"] == "pass"
    assert graded["reason"] == 'server returned an error as expected: "unknown tool no_such_tool"'


def test_misspelled_argument_is_refused_with_a_hint(tmp_path):
    # Before the preflight this passed: read_file's missing `path` was the "rejection".
    test = "{id: typo, tool: read_file, arguments: {pth: a.txt}, expect: {type: is_error}}"
    suite = _preflight_suite(tmp_path, test)
    line = _refusal(_run(str(suite)), suite)
    assert "arguments.pth: not an argument of tool 'read_file' (did you mean 'path'?)" in line


def test_wrong_type_argument_on_a_success_test_is_refused(tmp_path):
    test = "{id: num, tool: echo, arguments: {text: 5}, expect: {type: no_error}}"
    suite = _preflight_suite(tmp_path, test)
    assert "arguments.text: 5 is not of type 'string'" in _refusal(_run(str(suite)), suite)


def test_failed_tools_list_is_refused(tmp_path):
    test = "{id: e, tool: echo, arguments: {text: hi}, expect: {type: no_error}}"
    suite = _preflight_suite(tmp_path, test, "--fail-tools-list")
    assert "tools/list" in _refusal(_run(str(suite)), suite)


def test_a_paginated_tool_list_is_read_to_the_end(tmp_path):
    # Three tools per page: method_missing is on the fourth and last page.
    test = "{id: m, tool: method_missing, expect: {type: is_error}}"
    result = _run(str(_preflight_suite(tmp_path, test, "--page-size", "'3'")))
    assert result.exit_code == 1, result.output  # -32601 is not a rejection
    assert "not on the server" not in result.output


def test_unusable_tool_schema_warns_and_still_runs(tmp_path):
    test = "{id: odd, tool: bad_input_schema, arguments: {n: 1}, expect: {type: no_error}}"
    out = tmp_path / "out.json"
    result = _run(str(_preflight_suite(tmp_path, test)), "--json", str(out))
    assert result.exit_code == 0, result.output
    [warning] = result.stderr.splitlines()
    assert warning.startswith("warning: tool 'bad_input_schema' has an unusable inputSchema")
    assert json.loads(out.read_text(encoding="utf-8"))["warnings"] == [warning]


def test_off_spec_tool_list_is_refused_naming_the_spot(tmp_path):
    # An inputSchema without type "object" breaks the MCP tools/list shape: that is
    # a malformed reply, so the run is refused, with the field that broke it.
    test = "{id: e, tool: echo, arguments: {text: hi}, expect: {type: no_error}}"
    suite = _preflight_suite(tmp_path, test, "--off-spec-tool")
    line = _refusal(_run(str(suite)), suite)
    assert "tools/list failed: malformed reply on page 1: tools[" in line
    assert "].inputSchema.type:" in line


def test_render_failure_keeps_the_json_and_exits_2(tmp_path, monkeypatch):
    # Fault injection: once report text is escaped no real rendering crash is
    # known, so rendering is made to raise. The connector is not touched.
    def broken_render(*args, **kwargs):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(mcp_assay.cli, "print_scorecard", broken_render)
    _break_schema_grader(monkeypatch)
    out = tmp_path / "out.json"
    result = _run(str(_suite(tmp_path)), "--json", str(out))
    assert result.exit_code == 2
    assert len(json.loads(out.read_text(encoding="utf-8"))["results"]) == 2
    stderr = result.stderr.splitlines()
    assert any("render exploded" in line for line in stderr)
    # The harness-error warning prints before rendering, so the crash cannot hide it.
    assert any("could not be graded" in line for line in stderr)


def test_late_json_write_failure_still_shows_every_verdict(tmp_path):
    # A read-only file passes the folder check, then the write fails for real.
    out = tmp_path / "out.json"
    out.write_text("{}", encoding="utf-8")
    os.chmod(out, stat.S_IREAD)
    try:
        result = _run(str(_suite(tmp_path)), "--json", str(out))
    finally:
        os.chmod(out, stat.S_IREAD | stat.S_IWRITE)
    assert result.exit_code == 2
    assert "echo" in result.stdout and "status" in result.stdout
    [line] = [text for text in result.stderr.splitlines() if str(out) in text]
    assert "cannot write" in line


def test_harness_error_end_to_end(tmp_path, monkeypatch):
    _break_schema_grader(monkeypatch)
    out = tmp_path / "out.json"
    result = _run(str(_suite(tmp_path)), "--json", str(out))
    assert result.exit_code == 1
    assert "Harness errors" in result.stdout
    assert "warning: 1 test could not be graded" in result.stdout
    assert any("could not be graded" in line for line in result.stderr.splitlines())
    results = {
        r["execution"]["test_case"]["id"]: r
        for r in json.loads(out.read_text(encoding="utf-8"))["results"]
    }
    assert results["status"]["harness_error"] is True
    assert results["echo"]["verdict"] == "pass"


def test_valid_run_is_unchanged_apart_from_the_new_field(tmp_path):
    out = tmp_path / "out.json"
    result = _run(str(_suite(tmp_path)), "--json", str(out))
    assert result.exit_code == 0
    assert "Harness errors" not in result.stdout
    assert result.stderr == ""
    results = json.loads(out.read_text(encoding="utf-8"))["results"]
    assert [r["verdict"] for r in results] == ["pass", "pass"]
    assert [r["harness_error"] for r in results] == [False, False]


def test_unserialisable_scorecard_still_shows_every_verdict(tmp_path):
    # A YAML escape gives the test id a lone surrogate. It loads and runs, but it
    # cannot be written as UTF-8 JSON; that must not cost the console verdicts.
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        _suite(tmp_path)
        .read_text(encoding="utf-8")
        .replace("id: echo", 'id: "echo' + chr(92) + 'ud800"'),
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    result = _run(str(suite), "--json", str(out))
    assert result.exit_code == 2
    assert "status" in result.stdout
    [line] = [text for text in result.stderr.splitlines() if "cannot write the scorecard" in text]
    assert str(out) in line


# N5: `tools` output is copied into suites, so it must be the server's text as-is.
def _tool(**fields):
    from mcp import types

    return types.Tool.model_validate({"name": "read", "inputSchema": {"type": "object"}, **fields})


def test_tool_text_is_printed_literally():
    from mcp_assay.cli import _describe_tool

    tool = _tool(
        description="match [a-z]+ or [/x] :x:",
        inputSchema={"type": "object", "properties": {"p": {"pattern": "^[a-z0-9]+$"}}},
    )
    text = _describe_tool(tool)
    assert "match [a-z]+ or [/x] :x:" in text
    assert '"pattern": "^[a-z0-9]+$"' in text
    assert "output schema:" not in text


def test_declared_output_schema_is_printed():
    from mcp_assay.cli import _describe_tool

    text = _describe_tool(_tool(outputSchema={"type": "object", "required": ["status"]}))
    assert "output schema:" in text
    assert '"status"' in text.split("output schema:", 1)[1]


def test_long_schema_lines_are_not_wrapped():
    from mcp_assay.cli import _describe_tool

    pattern = "^" + "[a-z]" * 30 + "$"
    text = _describe_tool(_tool(inputSchema={"type": "string", "pattern": pattern}))
    assert f'  "pattern": "{pattern}"' in text.splitlines()


def test_tools_lists_a_real_server_with_its_output_schema():
    server = str(REPO / "examples" / "broken_server.py")
    result = CliRunner().invoke(app, ["tools", sys.executable, server])
    assert result.exit_code == 0, result.output
    after_status = result.stdout.split("get_status", 1)[1]
    assert "output schema:" in after_status.split("\n\n", 1)[0]
