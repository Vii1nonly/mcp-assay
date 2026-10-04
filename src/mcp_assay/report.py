"""Render a Scorecard to the console."""

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .models import Scorecard

VERDICT_STYLE = {"pass": "green", "fail": "red", "inconclusive": "yellow"}


def print_scorecard(scorecard: Scorecard, console: Console | None = None) -> None:
    # Ids, reasons and the server label can carry server text; escape() prints
    # them literally, since rich would read "[/x]" as markup and raise.
    console = console or Console()

    table = Table(title=f"mcp-assay: {escape(scorecard.server_label)}")
    table.add_column("test")
    table.add_column("category")
    table.add_column("check")
    table.add_column("ms", justify="right")
    table.add_column("verdict")

    for result in scorecard.results:
        test_case = result.execution.test_case
        style = VERDICT_STYLE[result.verdict]
        table.add_row(
            escape(test_case.id),
            escape(test_case.category),
            test_case.expect.type,
            f"{result.execution.latency_ms:.0f}",
            f"[{style}]{result.verdict.upper()}[/{style}]",
        )

    console.print(table)

    for category, (passed, total) in scorecard.by_category().items():
        console.print(f"  {escape(category)}: {passed}/{total}")

    if scorecard.failures:
        console.print("\n[bold red]Failures[/bold red]")
        for result in scorecard.failures:
            console.print(
                f"  [red]x[/red] {escape(result.execution.test_case.id)}: {escape(result.reason)}"
            )

    unobserved = [r for r in scorecard.inconclusives if not r.harness_error]
    if unobserved:
        console.print("\n[bold yellow]Inconclusive[/bold yellow] (server's answer not observed)")
        for result in unobserved:
            console.print(
                f"  [yellow]?[/yellow] {escape(result.execution.test_case.id)}: "
                f"{escape(result.reason)}"
            )

    harness_errors = scorecard.harness_errors
    if harness_errors:
        console.print(
            "\n[bold magenta]Harness errors[/bold magenta] (a harness bug, not a server result)"
        )
        for result in harness_errors:
            console.print(
                f"  [magenta]![/magenta] {escape(result.execution.test_case.id)}: "
                f"{escape(result.reason)}"
            )
        count = len(harness_errors)
        tests = "test" if count == 1 else "tests"
        console.print(
            f"\n[bold magenta]warning: {count} {tests} could not be graded because of a harness "
            f"error; their verdicts are inconclusive.[/bold magenta]"
        )

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
        console.print(f"[dim]protocol version: {escape(scorecard.protocol_version)}[/dim]")
