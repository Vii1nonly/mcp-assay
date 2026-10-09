"""Preflight: check every test against the server's tool list before any test runs.

A suite mistake must not become a verdict. An `is_error` test that names a tool
the server lacks, or misspells an argument, is rejected by the server, and that
rejection would be credited. A test that expects success but sends arguments the
tool's own schema refuses tests the suite, not the server.

This module is pure: it sees the tool list as plain data (name -> inputSchema),
never a protocol object.
"""

import difflib
import re
from typing import NamedTuple

from jsonschema.exceptions import SchemaError

from . import schemas
from .models import TestCase


class PreflightError(Exception):
    """The suite cannot run against this server. The message is one line."""


class Preflight(NamedTuple):
    problems: list[str]
    warnings: list[str]


def check(tests: list[TestCase], tools: dict[str, dict]) -> Preflight:
    """Each test's problems against the listed tools, plus warnings for checks skipped."""
    found: list[tuple[str | None, str]] = []  # (tool whose schema produced it, problem)
    unusable: dict[str, str | None] = {}  # tool -> why its schema cannot be used
    for i, test in enumerate(tests):
        where = f"tests[{i}] (id '{test.id}')"
        if test.tool not in tools:
            if not test.unknown_tool:
                found.append(
                    (
                        None,
                        f"{where}: tool '{test.tool}' is not on the server"
                        f"{_unknown_hint(test, test.tool, tools, 'unknown_tool')}",
                    )
                )
            continue
        if test.unknown_tool:
            # A stale flag would switch off the name check for a real tool.
            found.append(
                (
                    None,
                    f"{where}: unknown_tool: tool '{test.tool}' is on the server;"
                    " remove unknown_tool",
                )
            )
            continue
        schema = tools[test.tool]
        if test.tool not in unusable:
            unusable[test.tool] = _schema_problem(schema)
        if unusable[test.tool]:
            continue
        try:
            found += [(test.tool, f"{where}: {p}") for p in _argument_problems(test, schema)]
        # Whatever stops the harness evaluating the tool's schema (an unresolvable
        # $ref, a pattern Python cannot compile) is the server's bug, not the suite's.
        except Exception as e:  # noqa: BLE001
            unusable[test.tool] = one_line(f"{type(e).__name__}: {e}")
    problems = [problem for tool, problem in found if not (tool and unusable.get(tool))]
    warnings = [
        f"warning: tool '{one_line(tool)}' has an unusable inputSchema ({why});"
        " argument checks for its tests are skipped"
        for tool, why in unusable.items()
        if why
    ]
    return Preflight(problems, warnings)


def refusal(problems: list[str]) -> str:
    """The first problem as one line, with a count of the rest."""
    more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
    return f"{problems[0]}{more}"


def _schema_problem(schema) -> str | None:
    try:
        cls = schemas.validator_class(schema)
        cls.check_schema(schema)
    except (schemas.DialectError, SchemaError) as e:
        return one_line(e.message if isinstance(e, SchemaError) else str(e))
    return None


def _argument_problems(test: TestCase, schema: dict) -> list[str]:
    problems = [] if test.unknown_arguments else _name_problems(test, schema)
    # An is_error test may break the schema on purpose: that is what it tests.
    if test.expect.type != "is_error":
        problems += _value_problems(test, schema)
    return problems


def _name_problems(test: TestCase, schema: dict) -> list[str]:
    names = schema.get("properties")
    if not isinstance(names, dict) or not names:
        return []  # the schema lists no argument names to check against
    patterns = schema.get("patternProperties")
    patterns = list(patterns) if isinstance(patterns, dict) else []
    problems = []
    for name in test.arguments:
        if name in names or any(re.search(pattern, name) for pattern in patterns):
            continue
        hint = _unknown_hint(test, name, names, "unknown_arguments")
        problems.append(f"arguments.{name}: not an argument of tool '{test.tool}'{hint}")
    return problems


def _value_problems(test: TestCase, schema: dict) -> list[str]:
    errors = sorted(
        schemas.validator(schema).iter_errors(test.arguments),
        key=lambda e: [str(part) for part in e.path],
    )
    if not errors:
        return []
    first = errors[0]
    at = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in first.path)
    return [f"arguments{at}: {one_line(first.message)} (inputSchema of tool '{test.tool}')"]


def _unknown_hint(test: TestCase, word: str, known, flag: str) -> str:
    close = difflib.get_close_matches(word, sorted(known), n=1)
    if close:
        return f" (did you mean '{one_line(close[0])}'?)"
    if test.expect.type == "is_error":
        return f" (an is_error test that means this sets {flag}: true)"
    return ""


def one_line(text) -> str:
    """Text as one line: every run of whitespace, newlines included, becomes one space."""
    return " ".join(str(text).split())
