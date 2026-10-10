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
from urllib.parse import unquote

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
    declared = _declared_names(schema)
    if declared is None:
        return []  # the schema allows names the harness cannot rule out
    names, patterns = declared
    problems = []
    for name in test.arguments:
        if name in names or any(re.search(pattern, name) for pattern in patterns):
            continue
        if names or patterns:
            hint = _unknown_hint(test, name, names, "unknown_arguments")
        else:
            hint = " (it takes no arguments)"
        problems.append(f"arguments.{name}: not an argument of tool '{test.tool}'{hint}")
    return problems


# Keywords whose subschemas apply to the same object, so names they declare are
# argument names too. `dependencies` is the pre-2019 spelling of dependentSchemas.
_IN_PLACE_LISTS = ("allOf", "anyOf", "oneOf")
_IN_PLACE_ONE = ("if", "then", "else")
_IN_PLACE_MAPS = ("dependentSchemas", "dependencies")


def _declared_names(schema: dict) -> tuple[set[str], list[str]] | None:
    """Every argument name and name pattern the schema declares, or None when unsure.

    Names come from `properties` and `patternProperties` of the root and of every
    part reached through in-place keywords and local `$ref`s, plus the names a
    dependency is keyed by (accepted, but they never turn the check on alone). A
    part that declares nothing (only `required`, say) adds nothing. The answer is
    None, so names are not checked, when:
    - a reached part sets `additionalProperties` to true or to a schema;
    - a reached part is the schema `true`, or not a schema at all;
    - a `$ref` cannot be resolved, or a nested `$id` changes what it means;
    - no part declares a name and none sets `additionalProperties: false`.
    A tool that declares nothing and sets `additionalProperties: false` takes no
    arguments: empty sets.
    """
    names: set[str] = set()
    patterns: list[str] = []
    declares = False  # some part lists names in properties or patternProperties
    closed = False  # some part sets additionalProperties: false
    pending, visited = [schema], set()
    while pending:
        part = pending.pop()
        if id(part) in visited:
            continue  # a reference loop: this part is already counted
        visited.add(id(part))
        if part is False:
            continue  # allows nothing, declares nothing
        if not isinstance(part, dict):
            return None  # the schema `true` (any name), or not a schema
        if part is not schema and "$id" in part:
            return None  # an embedded resource: its local $refs are its own
        if "additionalProperties" in part:
            if part["additionalProperties"] is not False:
                return None  # extra names are allowed outright
            closed = True
        if isinstance(part.get("properties"), dict) and part["properties"]:
            names.update(part["properties"])
            declares = True
        if isinstance(part.get("patternProperties"), dict) and part["patternProperties"]:
            patterns.extend(part["patternProperties"])
            declares = True
        for keyword in _IN_PLACE_LISTS:
            if isinstance(part.get(keyword), list):
                pending += part[keyword]
        pending += [part[keyword] for keyword in _IN_PLACE_ONE if keyword in part]
        for keyword in _IN_PLACE_MAPS:
            if isinstance(part.get(keyword), dict):
                # A dependency is keyed by an argument name: accepted, but on its
                # own it does not list the names the tool takes.
                names.update(part[keyword])
                # dependencies may map a name to a list of names: not a schema.
                pending += [v for v in part[keyword].values() if not isinstance(v, list)]
        if "$ref" in part:
            target = _resolve_local(schema, part["$ref"])
            if target is None:
                return None  # remote, an anchor, or nothing there
            pending.append(target)
    if not declares and not closed:
        return None  # the schema lists no argument names to check against
    return names, patterns


def _resolve_local(root: dict, ref) -> object | None:
    """The part of `root` a local JSON Pointer reference ("#/$defs/A") names, or None."""
    if not isinstance(ref, str) or not ref.startswith("#"):
        return None
    pointer = unquote(ref[1:])
    if pointer == "":
        return root
    if not pointer.startswith("/"):
        return None  # an anchor name, not a pointer
    node = root
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return None
    return node


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
