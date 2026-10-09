"""The preflight checks every test against the server's tool list before any test
runs, so a typo in a suite cannot turn into a credited rejection."""

import asyncio

import pytest

from mcp_assay.models import TestCase
from mcp_assay.preflight import PreflightError, check

TOOLS = {
    "read_file": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    "echo": {"type": "object", "properties": {"text": {"type": "string"}}},
    "free": {"type": "object"},
    "no_args": {"type": "object", "properties": {}},
    "headers": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "patternProperties": {"^x-": {"type": "string"}},
    },
}


def _test(tool, check_type="is_error", arguments=None, **flags) -> TestCase:
    return TestCase(
        id="t", tool=tool, arguments=arguments or {}, expect={"type": check_type}, **flags
    )


def _problems(*tests, tools=TOOLS) -> list[str]:
    return check(list(tests), tools).problems


# --- unknown tools ---------------------------------------------------------------


def test_unknown_tool_is_refused_with_a_hint():
    [problem] = _problems(_test("reed_file"))
    assert problem.startswith("tests[0] (id 't'): tool 'reed_file' is not on the server")
    assert "(did you mean 'read_file'?)" in problem


def test_unknown_tool_flag_runs_the_test_without_argument_checks():
    test = _test("no_such_tool", arguments={"pth": "a"}, unknown_tool=True)
    assert _problems(test) == []


def test_unknown_tool_flag_on_a_listed_tool_is_refused():
    # A stale flag would switch off the name check for a real tool.
    [problem] = _problems(_test("read_file", arguments={"pth": "a"}, unknown_tool=True))
    assert "unknown_tool: tool 'read_file' is on the server" in problem


# --- argument names --------------------------------------------------------------


def test_misspelled_argument_is_refused_with_a_hint():
    [problem] = _problems(_test("read_file", arguments={"pth": "a.txt"}))
    assert problem.startswith("tests[0] (id 't'): arguments.pth: not an argument of tool")
    assert "(did you mean 'path'?)" in problem


def test_unknown_arguments_flag_allows_a_name_the_schema_does_not_list():
    test = _test("read_file", arguments={"pth": "a.txt"}, unknown_arguments=True)
    assert _problems(test) == []


def test_pattern_properties_allow_a_matching_name():
    assert _problems(_test("headers", arguments={"x-trace": "1"})) == []
    [problem] = _problems(_test("headers", arguments={"trace": "1"}))
    assert "arguments.trace" in problem


@pytest.mark.parametrize("tool", ["free", "no_args"])
def test_a_schema_that_lists_no_properties_gets_no_name_check(tool):
    assert _problems(_test(tool, arguments={"anything": 1})) == []


# --- argument names declared outside root `properties` ----------------------------

_STRING = {"type": "string"}


def test_names_declared_only_in_all_of_are_accepted():
    schema = {
        "type": "object",
        "properties": {"mode": _STRING},
        "allOf": [{"properties": {"extra": _STRING}}],
    }
    ok = _test("t", check_type="no_error", arguments={"mode": "a", "extra": "b"})
    assert _problems(ok, tools={"t": schema}) == []
    [problem] = _problems(_test("t", arguments={"extr": "b"}), tools={"t": schema})
    assert "arguments.extr: not an argument of tool 't' (did you mean 'extra'?)" in problem


@pytest.mark.parametrize(
    "schema",
    [
        {
            "$ref": "#/$defs/Args",
            "$defs": {"Args": {"type": "object", "properties": {"path": _STRING}}},
        },
        {  # the shape zod-to-json-schema emits
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$ref": "#/definitions/Args",
            "definitions": {"Args": {"type": "object", "properties": {"path": _STRING}}},
        },
    ],
)
def test_a_typo_behind_a_root_ref_is_refused(schema):
    [problem] = _problems(_test("t", arguments={"pth": "a"}), tools={"t": schema})
    assert "arguments.pth: not an argument of tool 't' (did you mean 'path'?)" in problem
    assert _problems(_test("t", arguments={"path": "a"}), tools={"t": schema}) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": {"path": _STRING}, "allOf": [{"$ref": "#"}]},
        {
            "$ref": "#/$defs/A",
            "$defs": {"A": {"properties": {"path": _STRING}, "anyOf": [{"$ref": "#/$defs/A"}]}},
        },
    ],
)
def test_a_ref_loop_is_followed_once(schema):
    [problem] = _problems(_test("t", arguments={"pth": "a"}), tools={"t": schema})
    assert "(did you mean 'path'?)" in problem


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": {"a": {}}, "allOf": [{"$ref": "#/$defs/missing"}]},
        {"properties": {"a": {}}, "allOf": [{"$ref": "https://example.com/remote.json"}]},
        {"properties": {"a": {}}, "anyOf": [True]},
        {"properties": {"a": {}}, "anyOf": [{"required": ["a"]}]},
    ],
    ids=["missing-ref", "remote-ref", "boolean-branch", "branch-allows-any-name"],
)
def test_a_part_that_cannot_be_followed_skips_the_name_check(schema):
    # Never refuse when unsure: a name the harness cannot rule out is let through.
    assert _problems(_test("t", arguments={"zzz": 1}), tools={"t": schema}) == []


_DRAFT_07 = "http://json-schema.org/draft-07/schema#"


@pytest.mark.parametrize(
    ("schema", "arguments"),
    [
        (
            {"type": "object", "dependentSchemas": {"card": {"properties": {"billing": {}}}}},
            {"card": 1, "billing": 2},
        ),
        (
            {
                "$schema": _DRAFT_07,
                "type": "object",
                "dependencies": {"card": {"properties": {"billing": {}}}},
            },
            {"card": 1, "billing": 2},
        ),
        (
            {
                "type": "object",
                "if": {"properties": {"kind": {"const": "a"}}},
                "then": {"properties": {"a_opt": {}}},
            },
            {"kind": "a", "a_opt": 1},
        ),
        ({"type": "object", "then": {"properties": {"a": {}}}}, {"b": 1}),
    ],
    ids=["dependentSchemas", "dependencies", "if-then", "then-without-if"],
)
def test_a_root_with_only_conditional_parts_gets_no_name_check(schema, arguments):
    # Conditional parts apply only sometimes, so the names they declare are not
    # every allowed name: an open root with nothing else allows any name.
    test = _test("t", check_type="no_error", arguments=arguments)
    assert _problems(test, tools={"t": schema}) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": {"x": {}}, "oneOf": [{"properties": {"limit": {}}}]},
        {"properties": {"x": {}}, "if": {"required": ["x"]}, "then": {"properties": {"limit": {}}}},
        {"properties": {"x": {}}, "if": {"required": ["x"]}, "else": {"properties": {"limit": {}}}},
        {"properties": {"x": {}}, "dependentSchemas": {"x": {"properties": {"limit": {}}}}},
        {"properties": {"x": {}}, "dependentSchemas": {"limit": {"properties": {"x": {}}}}},
        {
            "properties": {"x": {}},
            "allOf": [{"$ref": "#/$defs/a~1b"}],
            "$defs": {"a/b": {"properties": {"limit": {}}}},
        },
        {
            "properties": {"x": {}},
            "allOf": [{"$ref": "#/%24defs/L"}],
            "$defs": {"L": {"properties": {"limit": {}}}},
        },
    ],
    ids=[
        "oneOf",
        "then",
        "else",
        "dependentSchemas-value",
        "dependentSchemas-key",
        "pointer-escape",
        "pointer-percent",
    ],
)
def test_names_from_every_followed_part_are_declared(schema):
    assert _problems(_test("t", arguments={"limit": 1}), tools={"t": schema}) == []
    [problem] = _problems(_test("t", arguments={"limt": 1}), tools={"t": schema})
    assert "(did you mean 'limit'?)" in problem


def test_a_ref_inside_an_embedded_resource_skips_the_name_check():
    # Under a nested $id, "#/$defs/B" means that resource's B, not the root's.
    schema = {
        "type": "object",
        "$ref": "#/$defs/A",
        "$defs": {
            "A": {
                "$id": "https://example.com/a",
                "allOf": [{"$ref": "#/$defs/B"}],
                "$defs": {"B": {"properties": {"good": {}}}},
            },
            "B": {"properties": {"other": {}}},
        },
    }
    assert _problems(_test("t", arguments={"good": 1}), tools={"t": schema}) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}, "additionalProperties": False},
        {"type": "object", "additionalProperties": False},
    ],
)
def test_a_tool_that_takes_no_arguments_refuses_any_name(schema):
    tools = {"none": schema}
    [problem] = _problems(_test("none", arguments={"x": 1}), tools=tools)
    assert "arguments.x: not an argument of tool 'none' (it takes no arguments)" in problem
    flagged = _test("none", arguments={"x": 1}, unknown_arguments=True)
    assert _problems(flagged, tools=tools) == []
    assert _problems(_test("none"), tools=tools) == []


# --- argument values for tests that expect success --------------------------------


@pytest.mark.parametrize("check_type", ["no_error", "schema_valid"])
def test_success_test_arguments_must_match_the_input_schema(check_type):
    extra = {"schema": {"type": "object"}} if check_type == "schema_valid" else {}
    test = TestCase(
        id="t",
        tool="read_file",
        arguments={"path": 5},
        expect={"type": check_type, **extra},
    )
    [problem] = _problems(test)
    assert "arguments.path: 5 is not of type 'string'" in problem
    assert "inputSchema of tool 'read_file'" in problem


def test_missing_required_argument_is_refused_for_a_success_test():
    [problem] = _problems(_test("read_file", check_type="no_error"))
    assert "arguments: 'path' is a required property" in problem


def test_is_error_test_may_break_the_input_schema():
    # Sending invalid values is what an is_error test is for.
    assert _problems(_test("read_file", arguments={"path": 5})) == []
    assert _problems(_test("read_file")) == []


def test_the_schema_s_own_draft_is_used():
    # `dependencies` is enforced under draft-07 and ignored under 2020-12.
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"a": {}, "b": {}},
        "dependencies": {"a": ["b"]},
    }
    test = _test("dep", check_type="no_error", arguments={"a": 1})
    [problem] = _problems(test, tools={"dep": schema})
    assert "'b' is a dependency of 'a'" in problem
    without_draft = {k: v for k, v in schema.items() if k != "$schema"}
    assert _problems(test, tools={"dep": without_draft}) == []


def test_every_problem_is_reported_in_test_order():
    tests = [
        TestCase(id="first", tool="reed_file", expect={"type": "is_error"}),
        TestCase(id="ok", tool="echo", expect={"type": "is_error"}),
        TestCase(id="last", tool="echo", arguments={"txt": "a"}, expect={"type": "is_error"}),
    ]
    problems = check(tests, TOOLS).problems
    assert [p.split(":")[0] for p in problems] == ["tests[0] (id 'first')", "tests[2] (id 'last')"]


# --- a server schema that cannot be used ------------------------------------------


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": 5},
        {"type": "object", "properties": {"n": {"type": "nonsense"}}},
        {"$schema": "https://example.com/not-a-draft", "type": "object"},
        {"type": "object", "patternProperties": {"(": {}}},
        {"type": "object", "properties": {"n": {"$ref": "https://example.com/remote.json"}}},
    ],
)
def test_an_unusable_tool_schema_warns_and_skips_its_checks(schema):
    tests = [
        _test("odd", check_type="no_error", arguments={"n": 1, "typo": 2}),
        _test("odd", arguments={"other": 1}),
    ]
    result = check(tests, {"odd": schema})
    assert result.problems == []
    [warning] = result.warnings
    assert warning.startswith("warning: tool 'odd' has an unusable inputSchema")
    assert "\n" not in warning


def test_unused_tools_are_not_checked():
    tools = {**TOOLS, "odd": {"type": "object", "properties": 5}}
    result = check([_test("echo")], tools)
    assert result.problems == [] and result.warnings == []


# --- fetching the tool list ---------------------------------------------------------


class _PagedSession:
    """Serves tools/list pages keyed by cursor, the way a paginating server would."""

    def __init__(self, pages):
        self.pages = pages
        self.cursors = []

    async def send_request(self, request, result_type):
        cursor = request.params.cursor if request.params else None
        self.cursors.append(cursor)
        return result_type.model_validate(self.pages[cursor])


def _tool(name):
    return {"name": name, "inputSchema": {"type": "object", "properties": {}}}


@pytest.mark.asyncio
async def test_a_tool_on_the_second_page_is_found():
    from mcp_assay.connector import list_tool_schemas

    session = _PagedSession(
        {
            None: {"tools": [_tool("echo")], "nextCursor": "page-2"},
            "page-2": {"tools": [_tool("read_file")]},
        }
    )
    tools = await list_tool_schemas(session, timeout=5.0)
    assert set(tools) == {"echo", "read_file"}
    assert tools["read_file"] == {"type": "object", "properties": {}}
    assert session.cursors == [None, "page-2"]


@pytest.mark.asyncio
async def test_a_repeated_cursor_is_refused_instead_of_looping():
    from mcp_assay.connector import list_tool_schemas

    session = _PagedSession(
        {
            None: {"tools": [_tool("echo")], "nextCursor": "again"},
            "again": {"tools": [_tool("echo")], "nextCursor": "again"},
        }
    )
    with pytest.raises(PreflightError, match="cursor"):
        await list_tool_schemas(session, timeout=5.0)


class _EndlessSession:
    """A paging bug: every page names a next page, each with a new cursor."""

    async def send_request(self, request, result_type):
        cursor = int(request.params.cursor) if request.params else 0
        return result_type.model_validate({"tools": [], "nextCursor": str(cursor + 1)})


@pytest.mark.asyncio
async def test_a_tool_list_that_never_ends_is_refused():
    from mcp_assay.connector import list_tool_schemas

    with pytest.raises(PreflightError, match="more than 1000 pages"):
        await list_tool_schemas(_EndlessSession(), timeout=30.0)


class _SilentSession:
    async def send_request(self, request, result_type):
        await asyncio.sleep(10)


class _ClosedSession:
    async def send_request(self, request, result_type):
        from mcp import MCPError, types

        raise MCPError(types.CONNECTION_CLOSED, "Connection closed")


@pytest.mark.asyncio
async def test_a_tools_list_with_no_answer_is_refused_in_one_line():
    from mcp_assay.connector import _preflight

    with pytest.raises(PreflightError) as excinfo:
        await _preflight(_SilentSession(), [], timeout=0.1)
    assert str(excinfo.value) == "tools/list failed: not finished within 0.1s"


@pytest.mark.asyncio
async def test_a_closed_connection_is_not_reported_as_a_server_error():
    # -32000 is the SDK's own code for a closed connection, not a reply.
    from mcp_assay.connector import _preflight

    with pytest.raises(PreflightError) as excinfo:
        await _preflight(_ClosedSession(), [], timeout=5.0)
    message = str(excinfo.value)
    assert "connection closed before the server answered" in message
    assert "JSON-RPC error" not in message
