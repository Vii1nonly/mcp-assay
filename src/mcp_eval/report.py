"""Render a Scorecard to the console."""

from rich.console import Console
from rich.table import Table

from .models import Scorecard

VERDICT_STYLE = {"pass": "green", "fail": "red", "inconclusive": "yellow"}


def print_scorecard(scorecard: Scorecard, console: Console | None = None) -> None:
    console = console or Console()

    table = Table(title=f"MCP eval: {scorecard.server_label}")
    table.add_column("test")
    table.add_column("category")
    table.add_column("check")
    table.add_column("ms", justify="right")
    table.add_column("verdict")

    for result in scorecard.results:
        test_case = result.execution.test_case
        style = VERDICT_STYLE[result.verdict]
        table.add_row(
            test_case.id,
            test_case.category,
            test_case.expect.type,
            f"{result.execution.latency_ms:.0f}",
            f"[{style}]{result.verdict.upper()}[/{style}]",
        )

    console.print(table)

    for category, (passed, total) in scorecard.by_category().items():
        console.print(f"  {category}: {passed}/{total}")

    if scorecard.failures:
        console.print("\n[bold red]Failures[/bold red]")
        for result in scorecard.failures:
            console.print(f"  [red]x[/red] {result.execution.test_case.id}: {result.reason}")

    if scorecard.inconclusives:
        console.print("\n[bold yellow]Inconclusive[/bold yellow] (server's answer not observed)")
        for result in scorecard.inconclusives:
            console.print(f"  [yellow]?[/yellow] {result.execution.test_case.id}: {result.reason}")

    total = len(scorecard.results)
    if scorecard.failed:
        color = "red"
    elif scorecard.inconclusive:
        color = "yellow"
    else:
        color = "green"
    console.print(
        f"\n[{color}]{scorecard.passed}/{total} passed, {scorecard.failed} failed, "
        f"{scorecard.inconclusive} inconclusive[/{color}]"
    )
    if scorecard.protocol_version:
        console.print(f"[dim]protocol version: {scorecard.protocol_version}[/dim]")
