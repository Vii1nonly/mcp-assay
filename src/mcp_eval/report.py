"""Render a Scorecard to the console."""

from rich.console import Console
from rich.table import Table

from .models import Scorecard


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
        style = "green" if result.verdict == "pass" else "red"
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

    total = len(scorecard.results)
    color = "green" if scorecard.failed == 0 else "red"
    console.print(f"\n[{color}]{scorecard.passed}/{total} passed[/{color}]")
    if scorecard.protocol_version:
        console.print(f"[dim]protocol version: {scorecard.protocol_version}[/dim]")
