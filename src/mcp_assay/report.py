"""Render a Scorecard to the console."""

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .models import GradedResult, Scorecard

VERDICT_STYLE = {"pass": "green", "fail": "red", "inconclusive": "yellow"}


def _line(marker: str, style: str, result: GradedResult) -> Text:
    """One "<marker> <id>: <reason>" line with the id and reason as literal text."""
    return Text.assemble(
        "  ", (marker, style), " ", result.execution.test_case.id, ": ", result.reason
    )


def print_scorecard(scorecard: Scorecard, console: Console | None = None) -> None:
    # Ids, reasons, categories and the server label can carry server text. A
    # Text object is printed as-is: rich would otherwise read "[/x]" as markup
    # (and raise) and turn ":x:" into an emoji, changing what the server said.
    console = console or Console()

    table = Table(title=Text(f"mcp-assay: {scorecard.server_label}"))
    table.add_column("test")
    table.add_column("category")
    table.add_column("check")
    table.add_column("ms", justify="right")
    table.add_column("verdict")

    for result in scorecard.results:
        test_case = result.execution.test_case
        style = VERDICT_STYLE[result.verdict]
        table.add_row(
            Text(test_case.id),
            Text(test_case.category),
            test_case.expect.type,
            f"{result.execution.latency_ms:.0f}",
            f"[{style}]{result.verdict.upper()}[/{style}]",
        )

    console.print(table)

    for category, (passed, total) in scorecard.by_category().items():
        console.print(Text(f"  {category}: {passed}/{total}"))

    # Passes are listed with their reasons too, so a server that rejects every
    # call (even for an unrelated cause) shows its own message here.
    passes = [r for r in scorecard.results if r.verdict == "pass"]
    if passes:
        console.print("\n[bold green]Passed[/bold green]")
        for result in passes:
            console.print(_line("+", "green", result))

    if scorecard.failures:
        console.print("\n[bold red]Failures[/bold red]")
        for result in scorecard.failures:
            console.print(_line("x", "red", result))

    unobserved = [r for r in scorecard.inconclusives if not r.harness_error]
    if unobserved:
        console.print("\n[bold yellow]Inconclusive[/bold yellow] (server's answer not observed)")
        for result in unobserved:
            console.print(_line("?", "yellow", result))

    harness_errors = scorecard.harness_errors
    if harness_errors:
        console.print(
            "\n[bold magenta]Harness errors[/bold magenta] (a harness bug, not a server result)"
        )
        for result in harness_errors:
            console.print(_line("!", "magenta", result))
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
        console.print(Text(f"protocol version: {scorecard.protocol_version}", style="dim"))
