import asyncio
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from .report import print_scorecard
from .runner import run_suite
from .suite import ServerSpec, SuiteError, load_suite

app = typer.Typer(help="Evaluate MCP servers against a suite of test cases.")
console = Console()


def _safe_streams() -> None:
    # A console that can't encode a character (cp1252 on a piped Windows console)
    # shows "?" instead of crashing and losing every verdict. The encoding stays.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def _json_path_problem(json_out: Path) -> str | None:
    if json_out.is_dir():
        return f"{json_out}: --json must name a file, not a folder"
    if not json_out.parent.is_dir():
        return f"{json_out}: --json folder {json_out.parent} does not exist"
    return None


@app.command()
def run(
    suite_path: Path = typer.Argument(..., help="Path to a suite YAML file."),
    command: str = typer.Option(None, help="Override the server command."),
    args: list[str] = typer.Option(None, "--arg", help="Server argument (repeatable)."),
    timeout: float = typer.Option(10.0, help="Per-call timeout in seconds."),
    json_out: Path = typer.Option(None, "--json", help="Write the full scorecard here."),
):
    _safe_streams()
    try:
        suite = load_suite(suite_path)
    except SuiteError as e:
        # Exit 2: the suite could not be evaluated. Exit 1 stays "a test failed".
        # Plain echo, not rich, so a long message stays on one line.
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from None

    if command:
        server = ServerSpec(command=command, args=args or [])
    elif suite.server:
        server = suite.server
    else:
        raise typer.BadParameter("suite has no server block; pass --command")

    # Refuse an unwritable --json path now, not after the whole run.
    if json_out and (problem := _json_path_problem(json_out)):
        typer.echo(problem, err=True)
        raise typer.Exit(code=2)

    scorecard = asyncio.run(run_suite(suite, server, timeout))

    # Exit 2: a result could not be delivered. Exit 1 stays "a test failed or
    # was inconclusive". The JSON is written first so rendering cannot lose it.
    output_failed = False
    if json_out:
        try:
            json_out.write_text(scorecard.model_dump_json(indent=2), encoding="utf-8")
        # OSError: the file can't be written. ValueError: the scorecard can't be
        # serialised, such as a lone surrogate in a test id (pydantic raises a ValueError).
        except (OSError, ValueError) as e:
            detail = getattr(e, "strerror", None) or str(e).strip().splitlines()[0]
            typer.echo(f"{json_out}: cannot write the scorecard: {detail}", err=True)
            output_failed = True

    if scorecard.harness_errors:
        count = len(scorecard.harness_errors)
        tests = "test" if count == 1 else "tests"
        typer.echo(f"warning: {count} {tests} could not be graded (harness error)", err=True)

    try:
        print_scorecard(scorecard, console)
        if json_out and not output_failed:
            console.print(Text(f"wrote {json_out}", style="dim"))
    # Whatever breaks the display, the verdicts are already in the JSON when asked for.
    except Exception as e:  # noqa: BLE001
        first_line = str(e).strip().splitlines()[0] if str(e).strip() else ""
        typer.echo(f"cannot display the scorecard: {type(e).__name__}: {first_line}", err=True)
        output_failed = True

    if output_failed:
        raise typer.Exit(code=2)
    raise typer.Exit(code=1 if scorecard.failed or scorecard.inconclusive else 0)


@app.command()
def tools(
    command: str = typer.Argument(..., help="Server command, e.g. 'python'."),
    args: list[str] = typer.Argument(None, help="Arguments to the server command."),
):
    """List the tools a server exposes. Useful when writing a new suite."""
    from .connector import open_stdio_session

    _safe_streams()

    async def _list():
        async with open_stdio_session(command, list(args or [])) as (session, _):
            return await session.list_tools()

    result = asyncio.run(_list())
    for tool in result.tools:
        typer.echo(_describe_tool(tool))


def _describe_tool(tool) -> str:
    """A tool as plain text, exactly as the server declared it.

    Printed with typer.echo, not rich: authors copy these schemas into suites,
    so markup (a regex like ^[a-z0-9]+$ losing [a-z0-9]), emoji codes and
    soft-wrapping inside a JSON line would all change what they copy.
    """
    lines = [f"{tool.name}  {tool.description or ''}".rstrip()]
    lines += ["input schema:", json.dumps(tool.input_schema, indent=2)]
    if tool.output_schema is not None:
        lines += ["output schema:", json.dumps(tool.output_schema, indent=2)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    app()
