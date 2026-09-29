import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console

from .report import print_scorecard
from .runner import run_suite
from .suite import ServerSpec, load_suite

app = typer.Typer(help="Evaluate MCP servers against a suite of test cases.")
console = Console()


@app.command()
def run(
    suite_path: Path = typer.Argument(..., help="Path to a suite YAML file."),
    command: str = typer.Option(None, help="Override the server command."),
    args: list[str] = typer.Option(None, "--arg", help="Server argument (repeatable)."),
    timeout: float = typer.Option(10.0, help="Per-call timeout in seconds."),
    json_out: Path = typer.Option(None, "--json", help="Write the full scorecard here."),
):
    suite = load_suite(suite_path)

    if command:
        server = ServerSpec(command=command, args=args or [])
    elif suite.server:
        server = suite.server
    else:
        raise typer.BadParameter("suite has no server block; pass --command")

    scorecard = asyncio.run(run_suite(suite, server, timeout))
    print_scorecard(scorecard, console)

    if json_out:
        json_out.write_text(scorecard.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[dim]wrote {json_out}[/dim]")

    raise typer.Exit(code=1 if scorecard.failed else 0)


@app.command()
def tools(
    command: str = typer.Argument(..., help="Server command, e.g. 'python'."),
    args: list[str] = typer.Argument(None, help="Arguments to the server command."),
):
    """List the tools a server exposes. Useful when writing a new suite."""
    from .connector import open_stdio_session

    async def _list():
        async with open_stdio_session(command, list(args or [])) as (session, _):
            return await session.list_tools()

    result = asyncio.run(_list())
    for tool in result.tools:
        console.print(f"[bold]{tool.name}[/bold]  {tool.description or ''}")
        console.print(json.dumps(tool.input_schema, indent=2))


if __name__ == "__main__":
    app()
