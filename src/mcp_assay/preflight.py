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


# The name check exists to catch typos. Refuse a name only when no part of the
# schema could ever allow it. When a construct makes that uncertain, accept the
# name or skip the check.

# Subschemas that apply to the same object, so names they declare are argument
# names too. allOf always applies; the alternatives and conditions only sometimes.
_ALWAYS_LISTS = ("allOf",)
_SOMETIMES_LISTS = ("anyOf", "oneOf")
_CONDITIONS = ("if", "then", "else")
# Keyed by an argument name; a value is a schema or a list of names.
# `dependencies` is the pre-2019 spelling of dependentSchemas and dependentRequired.
_DEPENDENCY_MAPS = ("dependentSchemas", "dependentRequired", "dependencies")
# Keywords that, set to true or to a schema, allow extra names outright.
_OPEN_KEYWORDS = ("additionalProperties", "unevaluatedProperties")


def _declared_names(schema: dict) -> tuple[set[str], list[str]] | None:
    """Every argument name and name pattern the schema allows, or None when unsure.

    Names come from `properties` and `patternProperties` of the root and of every
    part reached through allOf, anyOf, oneOf, if, then, else, dependency schemas
    and local `$ref`s. Names a part only mentions (in `required`, or as or in a
    dependency) are accepted too, but never turn the check on alone. A part that
    declares nothing adds nothing. The answer is None, so names are not checked,
    when:
    - a reached part sets `additionalProperties` or `unevaluatedProperties` to
      true or to a schema;
    - a reached part is the schema `true`, or not a schema at all;
    - a `$ref` cannot be resolved, a nested `$id` changes what it means, or a
      `$dynamicRef` / `$recursiveRef` is reached;
    - no part declares a name, and no part that always applies closes the tool
      (`additionalProperties` or `unevaluatedProperties` false).
    A closed tool that declares nothing takes no arguments: empty sets.
    """
    known = schemas.keywords(schemas.validator_class(schema))  # the schema's own draft
    names: set[str] = set()
    patterns: list[str] = []
    declares = False  # some part lists names in properties or patternProperties
    closed = False  # a part that always applies allows no extra names
    pending = [(schema, True)]  # (part, whether it applies to every call)
    visited = set()
    while pending:
        part, always = pending.pop()
        if (id(part), True) in visited or (id(part), always) in visited:
            continue  # a reference loop: this part is already counted
        visited.add((id(part), always))
        if part is False:
            continue  # allows nothing, declares nothing
        if not isinstance(part, dict):
            return None  # the schema `true` (any name), or not a schema
        if part is not schema and "$id" in part:
            return None  # an embedded resource: its local $refs are its own
        if "$dynamicRef" in part or "$recursiveRef" in part:
            return None  # resolved at run time: which names it adds is unknown here
        for keyword in _OPEN_KEYWORDS:
            # A keyword the draft does not have (unevaluatedProperties before
            # 2019-09) is ignored by the validator, so it closes nothing.
            if keyword in part and keyword in known:
                if part[keyword] is not False:
                    return None  # extra names are allowed outright
                # An alternative or a condition that is closed closes only itself.
                closed = closed or always
        if isinstance(part.get("properties"), dict) and part["properties"]:
            names.update(part["properties"])
            declares = True
        if isinstance(part.get("patternProperties"), dict) and part["patternProperties"]:
            patterns.extend(part["patternProperties"])
            declares = True
        if isinstance(part.get("required"), list):
            names.update(name for name in part["required"] if isinstance(name, str))
        for keyword in _ALWAYS_LISTS:
            if isinstance(part.get(keyword), list):
                pending += [(sub, always) for sub in part[keyword]]
        for keyword in _SOMETIMES_LISTS:
            if isinstance(part.get(keyword), list):
                pending += [(sub, False) for sub in part[keyword]]
        pending += [(part[keyword], False) for keyword in _CONDITIONS if keyword in part]
        for keyword in _DEPENDENCY_MAPS:
            if isinstance(part.get(keyword), dict):
                names.update(part[keyword])
                for value in part[keyword].values():
                    if isinstance(value, list):
                        names.update(name for name in value if isinstance(name, str))
                    else:
                        pending.append((value, False))
        if "$ref" in part:
            target = _resolve_local(schema, part["$ref"])
            if target is None:
                return None  # remote, an anchor, or nothing there
            pending.append((target, always))
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
