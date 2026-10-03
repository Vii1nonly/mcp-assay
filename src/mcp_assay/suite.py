"""Load a suite file (YAML) into TestCase objects.

Suites are data, not code: adding a test means editing YAML. A suite is checked
strictly: a silently dropped key can turn a suite mistake into a verdict, so any
mistake is refused with one line naming the file and the spot.
"""

import difflib
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from .models import Expectation, TestCase

# Every keyword the grader's draft (2020-12) acts on or defines, plus annotations it
# allows. The grader ignores any other key, so a misspelled keyword would check nothing.
# N4 will honour `$schema`; this list must then follow the draft each schema declares.
_SCHEMA_KEYWORDS = frozenset(Draft202012Validator.VALIDATORS) | {
    "then", "else", "$schema", "$id", "$anchor", "$dynamicAnchor", "$defs", "definitions",
    "$comment", "$vocabulary", "title", "description", "default", "examples", "deprecated",
    "readOnly", "writeOnly", "contentEncoding", "contentMediaType", "contentSchema",
}  # fmt: skip


class SuiteError(Exception):
    """A suite that cannot be loaded. The message is one line naming the file and the spot."""


class ServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None

    @property
    def label(self) -> str:
        return " ".join([self.command, *self.args])


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tests: list[TestCase]
    server: ServerSpec | None = None


def load_suite(path: str | Path) -> Suite:
    path = Path(path)
    data = _read_yaml(path)
    try:
        suite = Suite.model_validate(data)
    except ValidationError as e:
        # A misspelled key usually also causes a missing one; report the misspelling first.
        errors = sorted(e.errors(), key=lambda err: err["type"] != "extra_forbidden")
        raise _refusal(path, [_describe(err, data) for err in errors]) from None
    # These rules span fields or tests, so they run once the structure is valid.
    problems = _rule_problems(suite, data)
    if problems:
        raise _refusal(path, problems)
    if suite.server and suite.server.cwd is None:
        # Server paths in a suite file are written relative to the suite.
        suite.server.cwd = str(path.parent.parent.resolve())
    return suite


def _rule_problems(suite: Suite, data) -> list[str]:
    if not suite.tests:
        return ["tests: suite has no tests"]
    problems = []
    first_use: dict[str, int] = {}
    for i, test in enumerate(suite.tests):
        at = ("tests", i, "expect", "schema")
        schema = test.expect.json_schema
        if test.expect.type == "schema_valid" and schema is None:
            problems.append(f"{_where(at, data)}: required by schema_valid")
        elif test.expect.type != "schema_valid" and schema is not None:
            problems.append(f"{_where(at, data)}: only schema_valid uses a schema")
        elif schema is not None:
            problems += [
                f"{_where(at + sub, data)}: {msg}" for sub, msg in _schema_problems(schema)
            ]
        if test.id in first_use:
            used = f"'{test.id}' is already used by tests[{first_use[test.id]}]"
            problems.append(f"{_where(('tests', i, 'id'), data)}: {used}")
        else:
            first_use[test.id] = i
    return problems


def _schema_problems(schema) -> list[tuple[tuple, str]]:
    """Problems that would otherwise surface as a crash or a false pass while grading."""
    shape = _shape_problem(schema)
    if shape:
        return [shape]
    # Same validator class as the grader, so a schema that loads is one it can use.
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        return [(tuple(e.path), _first_line(e.message))]
    root = DRAFT202012.create_resource(schema)
    resolver = Registry().with_resource("", root).resolver()
    subschemas = _subschemas(root)
    problems = []
    for at, node in _schema_dicts(schema, subschemas):
        for key in node:
            if key not in _SCHEMA_KEYWORDS:
                close = difflib.get_close_matches(key, sorted(_SCHEMA_KEYWORDS), n=1)
                hint = f" (did you mean '{close[0]}'?)" if close else ""
                problems.append((at, f"unknown keyword '{key}'{hint}"))
    for at, keyword, ref in _refs(schema, subschemas):
        where = (*at, keyword)
        if not ref.startswith("#"):
            # Grading would try to fetch it from the network.
            problems.append((where, f"'{ref}' is not local to this schema"))
            continue
        try:
            target = resolver.lookup(ref).contents
        except (Unresolvable, ValueError, TypeError):
            # ValueError: a non-number step into a list; TypeError: a step into a number.
            problems.append((where, f"'{ref}' does not resolve"))
            continue
        if id(target) not in subschemas:
            problems.append((where, f"'{ref}' does not point to a schema"))
    return problems


def _shape_problem(node, at=(), enclosing=None) -> tuple[tuple, str] | None:
    """A YAML-converted key or a YAML anchor loop, which check_schema misses or crashes on."""
    enclosing = enclosing or {}
    if id(node) in enclosing:
        return enclosing[id(node)], "refers to itself through a YAML anchor"
    if isinstance(node, dict):
        for key in node:
            if not isinstance(key, str):
                return at, f"key {key!r} is not a string (quote it in YAML)"
        children = node.items()
    elif isinstance(node, list):
        children = enumerate(node)
    else:
        return None
    enclosing = {**enclosing, id(node): at}
    for key, value in children:
        problem = _shape_problem(value, (*at, key), enclosing)
        if problem:
            return problem
    return None


def _subschemas(resource, found=None) -> set[int]:
    """The id of every object the grader treats as a schema, as referencing walks them."""
    found = set() if found is None else found
    if id(resource.contents) not in found:
        found.add(id(resource.contents))
        for sub in resource.subresources():
            _subschemas(sub, found)
    return found


def _schema_dicts(node, subschemas, at=()):
    """Every object the grader treats as a schema, with its path; data in const/enum is skipped."""
    if isinstance(node, dict):
        if id(node) in subschemas:
            yield at, node
        for key, value in node.items():
            yield from _schema_dicts(value, subschemas, (*at, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _schema_dicts(value, subschemas, (*at, i))


def _refs(node, subschemas):
    """Every `$ref` and `$dynamicRef` held by a schema, with the path to that schema."""
    for at, schema in _schema_dicts(node, subschemas):
        for keyword in ("$ref", "$dynamicRef"):
            if isinstance(schema.get(keyword), str):
                yield at, keyword, schema[keyword]


def _refusal(path: Path, problems: list[str]) -> SuiteError:
    more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
    return SuiteError(f"{path}: {problems[0]}{more}")


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses what plain YAML would silently change: a repeated key
    (the last one wins), an unquoted yes/no/on/off/y/n (a boolean in YAML 1.1), and
    a date or other value that is not JSON."""

    def construct_object(self, node, deep=False):
        # A value YAML cannot build (`!!int abc`) raises a plain error; give it a position.
        try:
            return super().construct_object(node, deep)
        except (ValueError, TypeError, OverflowError) as e:
            raise yaml.constructor.ConstructorError(
                None, None, f"invalid value: {_first_line(str(e))}", node.start_mark
            ) from None


# PyYAML reads these as booleans.
_BOOLEAN_WORDS = {"yes", "no", "on", "off"}
# PyYAML keeps these as strings, but other YAML 1.1 readers turn them into booleans.
# Only values are refused: `y` is a common key (coordinates) and stays a string here.
_BOOLEAN_LETTERS = {"y", "n"}


def _refuse_boolean_word(node, is_key: bool) -> None:
    if not isinstance(node, yaml.ScalarNode) or node.style is not None:
        return  # not a scalar, or quoted
    word = node.value.lower()
    read_as_bool = word in _BOOLEAN_WORDS and node.tag == "tag:yaml.org,2002:bool"
    if read_as_bool or (word in _BOOLEAN_LETTERS and not is_key):
        raise yaml.constructor.ConstructorError(
            None,
            None,
            f"unquoted '{node.value}' can be read as a boolean; write true or false, or quote it",
            node.start_mark,
        )


def _construct_strict_mapping(loader, node):
    seen = set()
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue  # `<<: *base` followed by an override is not a repeat
        _refuse_boolean_word(key_node, is_key=True)
        _refuse_boolean_word(value_node, is_key=False)
        key = loader.construct_object(key_node)
        try:
            repeated = key in seen
        except TypeError:  # unhashable key: construct_mapping reports it
            continue
        if repeated:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


def _construct_strict_sequence(loader, node):
    for item in node.value:
        _refuse_boolean_word(item, is_key=False)
    # PyYAML's own two-step build, which an anchor nested inside its own list needs.
    return loader.construct_yaml_seq(node)


def _refuse_non_json(loader, node):
    if node.tag.endswith(":timestamp"):
        message = f"unquoted '{node.value}' would be read as a date; quote it"
    else:
        message = f"!!{node.tag.rsplit(':', 1)[-1]} is not a JSON value"
    raise yaml.constructor.ConstructorError(None, None, message, node.start_mark)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_strict_mapping
)
_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, _construct_strict_sequence
)
for _tag in ("timestamp", "binary", "set", "omap", "pairs"):
    _StrictLoader.add_constructor(f"tag:yaml.org,2002:{_tag}", _refuse_non_json)


def _read_yaml(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SuiteError(f"{path}: cannot read suite file: {e.strerror or e}") from None
    except UnicodeDecodeError:
        raise SuiteError(f"{path}: suite file is not valid UTF-8") from None
    try:
        return yaml.load(text, Loader=_StrictLoader)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "(top level)"
        problem = _first_line(e.problem or e.context or "invalid YAML")
        raise SuiteError(f"{path}: {where}: {problem}") from None
    except yaml.YAMLError as e:
        raise SuiteError(f"{path}: invalid YAML: {_first_line(str(e))}") from None


def _describe(err, data) -> str:
    loc, kind = err["loc"], err["type"]
    if kind == "missing":
        return f"{_where(loc[:-1], data)}: missing key '{loc[-1]}'"
    if kind == "extra_forbidden":
        parent, key = loc[:-1], loc[-1]
        return f"{_where(parent, data)}: unknown key '{key}'{_hint(parent, key)}"
    if loc == ():
        return "(top level): suite file must be a mapping of name, tests and server"
    if kind == "model_type":
        return f"{_where(loc, data)}: expected a mapping"
    return f"{_where(loc, data)}: {_first_line(err['msg'])}"


def _where(loc, data) -> str:
    """`tests[3] (id 'x'): expect.schema` for a spot inside a test, else the field path."""
    parts = []
    if len(loc) >= 2 and loc[0] == "tests" and isinstance(loc[1], int):
        test_id = _raw_id(data, loc[1])
        parts.append(f"tests[{loc[1]}]" + (f" (id '{test_id}')" if test_id else ""))
        loc = loc[2:]
    if loc:
        path = ""
        for part in loc:
            path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else part)
        parts.append(path)
    return ": ".join(parts) or "(top level)"


def _raw_id(data, index: int) -> str | None:
    # Read the id from the YAML itself: the test failed validation, so no TestCase exists.
    try:
        test_id = data["tests"][index]["id"]
    except (KeyError, IndexError, TypeError):
        return None
    return test_id if isinstance(test_id, str) else None


def _hint(parent, key) -> str:
    model = _owner(parent)
    if model is None or not isinstance(key, str):
        return ""
    if model is TestCase and key in _keys(Expectation):
        return " (belongs under expect:)"
    close = difflib.get_close_matches(key, _keys(model), n=1)
    return f" (did you mean '{close[0]}'?)" if close else ""


def _owner(parent) -> type[BaseModel] | None:
    """The model whose keys are valid at this spot."""
    if parent == ():
        return Suite
    if parent == ("server",):
        return ServerSpec
    if len(parent) == 2 and parent[0] == "tests":
        return TestCase
    if len(parent) == 3 and parent[0] == "tests" and parent[2] == "expect":
        return Expectation
    return None


def _keys(model: type[BaseModel]) -> list[str]:
    # As YAML spells them, so `schema` rather than the field name `json_schema`.
    return [field.alias or name for name, field in model.model_fields.items()]


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text
