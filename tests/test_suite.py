"""A malformed suite must be refused at load, with one line naming the file and
the spot. A silently dropped key turns a suite mistake into a verdict."""

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcp_assay.cli import app
from mcp_assay.suite import SuiteError, load_suite

REPO = Path(__file__).parent.parent
SUITES = REPO / "suites"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "suite.yaml"
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def _load_error(path: Path) -> str:
    with pytest.raises(SuiteError) as excinfo:
        load_suite(path)
    message = str(excinfo.value)
    assert "\n" not in message
    assert str(path) in message
    return message


def test_misspelled_arguments_key_is_refused(tmp_path):
    # The readiness review's false pass: `args` was dropped, `{}` was sent, and
    # the server's rejection of the empty call was credited under is_error.
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - id: ok
            tool: echo
            expect: {type: no_error}
          - id: read-file-missing-path
            tool: read_file
            args: {}
            expect: {type: is_error}
        """,
    )
    message = _load_error(path)
    assert "tests[1] (id 'read-file-missing-path'): unknown key 'args'" in message
    assert "did you mean 'arguments'?" in message


def test_unknown_server_key_is_refused(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        server: {command: python, env: {A: "1"}}
        tests:
          - {id: a, tool: echo, expect: {type: no_error}}
        """,
    )
    assert "server: unknown key 'env'" in _load_error(path)


def test_misspelled_top_level_key_is_reported_before_the_missing_one(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        test:
          - {id: a, tool: echo, expect: {type: no_error}}
        """,
    )
    message = _load_error(path)
    assert "(top level): unknown key 'test' (did you mean 'tests'?) (+1 more)" in message


def test_schema_at_test_level_points_under_expect(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - id: a
            tool: get_status
            schema: {type: object}
            expect: {type: schema_valid}
        """,
    )
    assert "unknown key 'schema' (belongs under expect:)" in _load_error(path)


def test_json_schema_spelling_is_refused(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - id: a
            tool: get_status
            expect: {type: schema_valid, json_schema: {type: object}}
        """,
    )
    message = _load_error(path)
    assert "tests[0] (id 'a'): expect: unknown key 'json_schema'" in message
    assert "did you mean 'schema'?" in message


def test_repeated_key_is_refused_with_its_position(tmp_path):
    # YAML keeps only the last of two equal keys, which drops the first silently.
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - id: a
            tool: read_file
            arguments: {path: README.md}
            arguments: {}
            expect: {type: is_error}
        """,
    )
    assert "line 6, column 5: duplicate key 'arguments'" in _load_error(path)


def test_merge_override_is_not_a_repeated_key(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - id: a
            tool: read_file
            arguments: &shared {path: README.md, mode: text}
            expect: {type: no_error}
          - id: b
            tool: read_file
            arguments: {<<: *shared, path: other.md}
            expect: {type: no_error}
        """,
    )
    suite = load_suite(path)
    assert suite.tests[1].arguments == {"path": "other.md", "mode": "text"}


def test_test_entry_that_is_not_a_mapping_is_refused(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - foo
        """,
    )
    message = _load_error(path)
    assert "tests[0]: expected a mapping" in message
    assert "(id" not in message


def test_non_string_id_is_located_by_index_only(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - {id: 7, tool: echo, expect: {type: no_error}}
        """,
    )
    message = _load_error(path)
    assert "tests[0]: id:" in message
    assert "(id" not in message


def test_empty_file_is_refused(tmp_path):
    path = _write(tmp_path, "")
    assert "(top level): suite file must be a mapping" in _load_error(path)


def test_broken_yaml_is_refused_with_its_position(tmp_path):
    path = _write(tmp_path, "name: [s\n")
    message = _load_error(path)
    assert "line " in message
    assert "column " in message


def test_missing_suite_file_is_refused(tmp_path):
    assert "cannot read suite file" in _load_error(tmp_path / "missing.yaml")


def test_several_problems_report_the_first_and_a_count(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - {id: a, tool: echo, expect: {type: no_error}, args: {}, tol: x, desc: z}
        """,
    )
    assert "unknown key 'args' (did you mean 'arguments'?) (+2 more)" in _load_error(path)


@pytest.mark.parametrize(("name", "count"), [("broken_server.yaml", 7), ("filesystem.yaml", 6)])
def test_shipped_suites_still_load(name, count):
    assert len(load_suite(SUITES / name).tests) == count


def _one_test(tmp_path: Path, expect: str, extra: str = "") -> Path:
    return _write(
        tmp_path,
        f"""
        name: s
        tests:
          - id: a
            tool: get_status
            expect: {expect}
        {extra}
        """,
    )


def test_schema_valid_without_schema_is_refused(tmp_path):
    path = _one_test(tmp_path, "{type: schema_valid}")
    assert "tests[0] (id 'a'): expect.schema: required by schema_valid" in _load_error(path)


def test_schema_on_another_check_is_refused(tmp_path):
    # The author believes the schema is being checked; is_error never reads it.
    path = _one_test(tmp_path, "{type: is_error, schema: {type: object}}")
    assert "tests[0] (id 'a'): expect.schema: only schema_valid uses a schema" in _load_error(path)


def test_duplicate_id_is_refused_at_the_second_use(tmp_path):
    path = _one_test(
        tmp_path, "{type: no_error}", "  - {id: a, tool: echo, expect: {type: no_error}}"
    )
    assert "tests[1] (id 'a'): id: 'a' is already used by tests[0]" in _load_error(path)


def test_suite_without_tests_is_refused(tmp_path):
    path = _write(tmp_path, "name: s\ntests: []\n")
    assert "tests: suite has no tests" in _load_error(path)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ("{type: strng}", "expect.schema.type: 'strng' is not valid"),
        ("{properties: {x: {pattern: '('}}}", "expect.schema.properties.x.pattern:"),
        ("{$ref: '#/nope'}", "expect.schema.$ref: '#/nope' does not resolve"),
        (
            "{$ref: 'http://example.com/x'}",
            "expect.schema.$ref: 'http://example.com/x' is not local",
        ),
        ("{required: [a], properties: {x: {$ref: '#/required'}}}", "does not point to a schema"),
        ("{$dynamicRef: '#/nope'}", "expect.schema.$dynamicRef: '#/nope' does not resolve"),
        (
            "{allOf: [{type: string}], properties: {x: {$ref: '#/allOf/x'}}}",
            "expect.schema.properties.x.$ref: '#/allOf/x' does not resolve",
        ),
        ("{prefixItems: [{}], properties: {x: {$ref: '#/prefixItems/-'}}}", "does not resolve"),
        ("{minimum: 1, properties: {x: {$ref: '#/minimum/x'}}}", "does not resolve"),
        ("&s {anyOf: [{type: string}, *s]}", "refers to itself through a YAML anchor"),
        # A keyword map is not a schema; grading would read its names as unknown keywords.
        ("{$defs: {s: {}}, properties: {x: {$ref: '#/$defs'}}}", "does not point to a schema"),
        ("{properties: {x: {$ref: '#/properties'}}}", "does not point to a schema"),
        # YAML reads `200` as an int; the grader never sees that property name.
        ("{properties: {200: {type: string}}}", "key 200 is not a string"),
    ],
)
def test_broken_schema_is_refused(tmp_path, schema, expected):
    # Each of these used to load and then crash grading after the server had answered.
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    message = _load_error(path)
    assert "tests[0] (id 'a'): " in message
    assert expected in message


@pytest.mark.parametrize(
    "schema",
    [
        "{$defs: {s: {type: string}}, properties: {x: {$ref: '#/$defs/s'}}}",
        "{properties: {child: {$ref: '#'}}}",
        # Data positions hold values, not schemas, so a `$ref` key there is not a reference.
        "{const: {$ref: 'http://example.com/x'}}",
        "{enum: [{$ref: '#/nope'}]}",
        "{default: {$ref: '#/nope'}}",
        "{examples: [{$ref: '#/nope'}]}",
        # An anchor used twice is shared, not self-referring.
        "{$defs: {s: &s {type: string}}, properties: {x: *s, y: *s}}",
    ],
)
def test_valid_local_refs_still_load(tmp_path, schema):
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    assert load_suite(path).tests[0].expect.json_schema is not None


def test_several_rule_problems_report_the_first_in_test_order(tmp_path):
    path = _one_test(
        tmp_path,
        "{type: schema_valid, schema: {type: strng}}",
        "  - {id: a, tool: echo, expect: {type: no_error}}",
    )
    message = _load_error(path)
    assert "tests[0] (id 'a'): expect.schema.type:" in message
    assert message.endswith("(+1 more)")


def _run_cli(*args: str):
    return CliRunner().invoke(app, ["run", *args])


def test_cli_refuses_a_bad_suite_before_starting_the_server(tmp_path):
    # The server command does not exist, so if loading let this suite through,
    # starting the server would fail with a different exit code.
    path = _write(
        tmp_path,
        """
        name: s
        server: {command: definitely-not-a-real-command}
        tests:
          - {id: a, tool: read_file, args: {}, expect: {type: is_error}}
        """,
    )
    result = _run_cli(str(path))
    assert result.exit_code == 2
    assert result.stdout == ""
    [line] = result.stderr.splitlines()
    assert str(path) in line
    assert "unknown key 'args'" in line


def test_cli_refuses_a_missing_suite_file(tmp_path):
    result = _run_cli(str(tmp_path / "missing.yaml"))
    assert result.exit_code == 2
    [line] = result.stderr.splitlines()
    assert "cannot read suite file" in line


def test_cli_still_exits_1_when_a_test_fails(tmp_path):
    # Exit 2 is new; exit 1 keeps meaning that a test failed or was inconclusive.
    path = _write(
        tmp_path,
        f"""
        name: s
        server:
          command: python
          args: [examples/broken_server.py]
          cwd: '{REPO.as_posix()}'
        tests:
          - {{id: a, tool: read_file, arguments: {{}}, expect: {{type: is_error}}}}
        """,
    )
    result = _run_cli(str(path))
    assert result.exit_code == 1, result.output
    assert "1 failed" in result.stdout


# A misspelled schema keyword: the grader ignores keywords it does not know, so
# the check it was meant to add silently checks nothing and the test passes.
@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            "{type: object, requird: [status]}",
            "expect.schema: unknown keyword 'requird' (did you mean 'required'?)",
        ),
        ("{propertis: {status: {type: string}}}", "did you mean 'properties'?"),
        (
            "{properties: {n: {minimun: 1}}}",
            "expect.schema.properties.n: unknown keyword 'minimun' (did you mean 'minimum'?)",
        ),
        # The draft-07 spelling: the default 2020-12 draft ignores it.
        ("{dependencies: {a: [b]}}", "'dependencies' is not used by draft 2020-12"),
    ],
)
def test_unknown_schema_keyword_is_refused(tmp_path, schema, expected):
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    message = _load_error(path)
    assert "tests[0] (id 'a'): " in message
    assert expected in message


def test_known_schema_keywords_still_load(tmp_path):
    schema = (
        "{title: t, description: d, $comment: c, if: {required: [a]}, then: {required: [b]},"
        " else: {required: [c]}, properties: {e: {type: string, format: email, examples: [x]}}}"
    )
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    assert load_suite(path).tests[0].expect.json_schema["then"] == {"required": ["b"]}


# YAML 1.1 reads unquoted yes/no/on/off as booleans and dates as date objects, so
# the value sent or checked is not the text the author wrote.
@pytest.mark.parametrize("word", ["on", "no", "Yes", "OFF", "y", "N"])
def test_unquoted_boolean_word_is_refused(tmp_path, word):
    path = _write(
        tmp_path,
        f"""
        name: s
        tests:
          - id: a
            tool: echo
            arguments: {{text: {word}}}
            expect: {{type: no_error}}
        """,
    )
    assert f"line 5, column 23: unquoted '{word}'" in _load_error(path)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # `on` became True, so the `if` never matched and `then` was never enforced.
        ("{if: {properties: {mode: {const: on}}}, then: {required: [x]}}", "unquoted 'on'"),
        ("{properties: {on: {type: boolean}}}", "unquoted 'on'"),
        # A date object never equals the JSON string, so `not` always held.
        ("{properties: {d: {not: {const: 2024-01-01}}}}", "unquoted '2024-01-01'"),
    ],
)
def test_yaml_converted_schema_value_is_refused(tmp_path, schema, expected):
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    message = _load_error(path)
    assert "line 5, column " in message
    assert expected in message


def test_quoted_words_and_plain_y_keys_still_load(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - id: a
            tool: echo
            arguments: {mode: 'on', flag: "no", day: '2024-01-01', y: 1}
            expect: {type: schema_valid, schema: {properties: {"on": {}, y: {type: number}}}}
        """,
    )
    test = load_suite(path).tests[0]
    assert test.arguments == {"mode": "on", "flag": "no", "day": "2024-01-01", "y": 1}
    assert set(test.expect.json_schema["properties"]) == {"on", "y"}


# A value YAML cannot build used to escape as a traceback with exit 1.
def test_unbuildable_yaml_value_is_refused_with_its_position(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - {id: !!int abc, tool: echo, expect: {type: no_error}}
        """,
    )
    assert "line 3, column 10: invalid value:" in _load_error(path)


def test_cli_refuses_an_impossible_date_with_exit_2(tmp_path):
    path = _write(
        tmp_path,
        """
        name: 2024-13-45
        tests: []
        """,
    )
    result = _run_cli(str(path))
    assert result.exit_code == 2
    [line] = result.stderr.splitlines()
    assert "line 1, column 7:" in line


# --- N4: the schema's own draft decides what loads ----------------------------

_DRAFT_07 = "'http://json-schema.org/draft-07/schema#'"


@pytest.mark.parametrize("keyword", ["minContains", "maxContains"])
def test_contains_bounds_load(tmp_path, keyword):
    schema = f"{{type: array, contains: {{type: string}}, {keyword}: 2}}"
    load_suite(_one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}"))


def test_declared_draft_07_keyword_loads(tmp_path):
    schema = f"{{$schema: {_DRAFT_07}, dependencies: {{a: [b]}}}}"
    load_suite(_one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}"))


def test_keyword_of_another_draft_is_refused_with_a_hint(tmp_path):
    path = _one_test(tmp_path, "{type: schema_valid, schema: {dependencies: {a: [b]}}}")
    message = _load_error(path)
    assert "expect.schema: 'dependencies' is not used by draft 2020-12" in message
    assert "draft-07" in message


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("'http://json-schema.org/draft-03/schema#'", "is not supported"),
        ("'https://example.com/schema'", "is not a JSON Schema draft"),
        ("5", "$schema must be a string"),
    ],
)
def test_unusable_schema_dialect_is_refused(tmp_path, value, expected):
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {{$schema: {value}}}}}")
    message = _load_error(path)
    assert "expect.schema.$schema:" in message
    assert expected in message


def test_schema_dialect_below_the_root_is_refused(tmp_path):
    schema = f"{{properties: {{a: {{$schema: {_DRAFT_07}}}}}}}"
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    assert "only the root schema may declare $schema" in _load_error(path)


def test_misspelled_format_is_refused(tmp_path):
    path = _one_test(tmp_path, "{type: schema_valid, schema: {type: string, format: emial}}")
    message = _load_error(path)
    assert "expect.schema.format: 'emial' is not a format mcp-assay can check" in message
    assert "(did you mean 'email'?)" in message


def test_custom_format_is_refused(tmp_path):
    # An unchecked format would make the test pass whatever the value is.
    path = _one_test(tmp_path, "{type: schema_valid, schema: {format: semver}}")
    assert "'semver' is not a format mcp-assay can check" in _load_error(path)


def test_format_the_declared_draft_cannot_check_is_refused(tmp_path):
    schema = "{$schema: 'http://json-schema.org/draft-04/schema#', format: uuid}"
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    assert "'uuid' is not a format mcp-assay can check under draft-04" in _load_error(path)


@pytest.mark.parametrize(
    "schema",
    [
        "{$ref: '#'}",
        "{allOf: [{$ref: '#'}]}",
        "{$defs: {a: {$ref: '#/$defs/b'}, b: {anyOf: [{$ref: '#/$defs/a'}]}}, $ref: '#/$defs/a'}",
    ],
)
def test_schema_that_loops_without_consuming_data_is_refused(tmp_path, schema):
    # The grader would recurse until RecursionError (N3 records that as a harness error).
    path = _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")
    assert "loops back to itself without checking any data" in _load_error(path)


def test_recursive_tree_schema_loads(tmp_path):
    # Recursion through a property consumes data on every step, so it ends.
    schema = "{type: object, properties: {child: {$ref: '#'}}}"
    load_suite(_one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}"))


def test_draft_07_ref_sibling_is_refused_not_mistaken_for_a_loop(tmp_path):
    # In draft-07 a $ref ignores its siblings: the allOf is dead, not a loop.
    schema = (
        f"{{$schema: {_DRAFT_07}, definitions: {{a: {{type: string}}}}, "
        "$ref: '#/definitions/a', allOf: [{$ref: '#'}]}"
    )
    message = _load_error(_one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}"))
    assert "expect.schema.allOf: is ignored next to $ref in draft-07" in message
    assert "loops back" not in message


def test_unbuildable_tagged_value_is_refused(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - id: a
            tool: echo
            arguments: {text: !!bool maybe}
            expect: {type: no_error}
        """,
    )
    message = _load_error(path)
    assert "line 5, column" in message
    assert "maybe" in message


def test_unbuildable_tagged_value_exits_2(tmp_path):
    path = _write(
        tmp_path,
        """
        name: s
        tests:
          - {id: a, tool: echo, arguments: {text: !!bool maybe}, expect: {type: no_error}}
        """,
    )
    result = CliRunner().invoke(app, ["run", str(path)])
    assert result.exit_code == 2


def test_then_without_if_is_refused_not_mistaken_for_a_loop(tmp_path):
    # Without an `if`, `then` is never evaluated: it is dead, and its reference cannot recurse.
    message = _load_error(_one_test(tmp_path, "{type: schema_valid, schema: {then: {$ref: '#'}}}"))
    assert "expect.schema.then: is ignored without 'if'" in message
    assert "loops back" not in message


def test_if_then_loop_is_refused(tmp_path):
    path = _one_test(
        tmp_path, "{type: schema_valid, schema: {if: {type: object}, then: {$ref: '#'}}}"
    )
    assert "expect.schema.then.$ref: loops back to itself" in _load_error(path)


# --- N4 review fixes ------------------------------------------------------------

_DRAFT_04 = "'http://json-schema.org/draft-04/schema#'"
_DRAFT_06 = "'http://json-schema.org/draft-06/schema#'"
_DRAFT_2019 = "'https://json-schema.org/draft/2019-09/schema'"


def _schema_test(tmp_path: Path, schema: str) -> Path:
    return _one_test(tmp_path, f"{{type: schema_valid, schema: {schema}}}")


@pytest.mark.parametrize(
    ("draft", "name"), [(_DRAFT_04, "draft-04"), (_DRAFT_06, "draft-06"), (_DRAFT_07, "draft-07")]
)
@pytest.mark.parametrize("sibling", ["required: [status]", "format: uri", "enum: [a]"])
def test_asserting_keyword_beside_ref_is_refused_in_old_drafts(tmp_path, draft, name, sibling):
    # These drafts never evaluate a $ref's siblings: the check would silently pass.
    key = sibling.split(":")[0]
    schema = f"{{$schema: {draft}, definitions: {{o: {{}}}}, $ref: '#/definitions/o', {sibling}}}"
    message = _load_error(_schema_test(tmp_path, schema))
    assert f"expect.schema.{key}: is ignored next to $ref in {name}" in message
    assert "(move it into allOf with the $ref)" in message


def test_annotations_beside_ref_load_in_old_drafts(tmp_path):
    schema = (
        f"{{$schema: {_DRAFT_07}, title: t, description: d, "
        "definitions: {o: {type: object}}, $ref: '#/definitions/o'}"
    )
    load_suite(_schema_test(tmp_path, schema))


def test_asserting_keyword_beside_ref_loads_in_2020_12(tmp_path):
    # 2020-12 evaluates a $ref's siblings, so nothing is lost.
    load_suite(_schema_test(tmp_path, "{$defs: {o: {}}, $ref: '#/$defs/o', required: [status]}"))


_EMBEDDED_LOOP = (
    "{$id: 'https://e.com/root', $defs: {inner: {$id: 'https://e.com/inner', "
    "anyOf: [{$ref: '#'}]}}, properties: {p: {$ref: '#/$defs/inner'}}}"
)


def test_loop_inside_a_subschema_with_its_own_id_is_refused(tmp_path):
    # '#' inside `inner` means `inner` itself, so `inner` refers to itself in place.
    message = _load_error(_schema_test(tmp_path, _EMBEDDED_LOOP))
    assert "expect.schema.$defs.inner.anyOf[0].$ref: loops back to itself" in message


def test_reference_scoped_to_a_subschema_with_its_own_id_loads(tmp_path):
    schema = (
        "{$id: 'https://e.com/root', properties: {a: {$id: 'https://e.com/a', "
        "$defs: {x: {type: string}}, $ref: '#/$defs/x'}}}"
    )
    load_suite(_schema_test(tmp_path, schema))


def test_reference_to_an_embedded_id_is_local(tmp_path):
    schema = (
        "{$defs: {s: {$id: 'https://e.com/s', type: string}}, "
        "properties: {a: {$ref: 'https://e.com/s'}}}"
    )
    load_suite(_schema_test(tmp_path, schema))


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            "{type: array, minContains: 2}",
            "expect.schema.minContains: is ignored without 'contains'",
        ),
        (
            f"{{$schema: {_DRAFT_07}, items: {{type: string}}, additionalItems: false}}",
            "expect.schema.additionalItems: is ignored unless 'items' is a list",
        ),
        (
            f"{{$schema: {_DRAFT_04}, exclusiveMaximum: true}}",
            # Draft-04's own meta-schema refuses it.
            "'maximum' is a dependency of 'exclusiveMaximum'",
        ),
        ("{else: {type: string}}", "expect.schema.else: is ignored without 'if'"),
    ],
)
def test_keyword_that_checks_nothing_where_it_sits_is_refused(tmp_path, schema, expected):
    assert expected in _load_error(_schema_test(tmp_path, schema))


@pytest.mark.parametrize(
    "schema",
    [
        f"{{$schema: {_DRAFT_04}, type: object, "
        "properties: {n: {maximum: 10, exclusiveMaximum: true}}}",
        f"{{$schema: {_DRAFT_06}, type: object, examples: [{{}}], "
        "propertyNames: {maxLength: 3}}",
        f"{{$schema: {_DRAFT_07}, type: object, properties: {{p: {{writeOnly: true}}}}}}",
        f"{{$schema: {_DRAFT_2019}, type: object, $recursiveAnchor: true, "
        "properties: {child: {$recursiveRef: '#'}}, dependentRequired: {a: [b]}}",
        f"{{$schema: {_DRAFT_2019}, type: string, format: duration}}",
    ],
)
def test_supported_drafts_load(tmp_path, schema):
    load_suite(_schema_test(tmp_path, schema))


@pytest.mark.parametrize(
    "schema",
    [
        "{not: {$ref: '#'}}",
        "{oneOf: [{$ref: '#'}, {type: string}]}",
        "{dependentSchemas: {a: {$ref: '#'}}}",
        f"{{$schema: {_DRAFT_07}, dependencies: {{a: {{$ref: '#'}}}}}}",
        f"{{$schema: {_DRAFT_2019}, $recursiveAnchor: true, anyOf: [{{$recursiveRef: '#'}}]}}",
        "{$defs: {m: {$dynamicAnchor: meta, allOf: [{$dynamicRef: '#meta'}]}}, $ref: '#/$defs/m'}",
    ],
)
def test_every_in_place_keyword_can_close_a_loop(tmp_path, schema):
    message = _load_error(_schema_test(tmp_path, schema))
    assert "loops back to itself without checking any data" in message


@pytest.mark.parametrize("keyword", ["$recursiveRef: '#'", "$recursiveAnchor: x"])
def test_keywords_2020_12_ignores_are_refused(tmp_path, keyword):
    message = _load_error(_schema_test(tmp_path, f"{{{keyword}}}"))
    assert "is not used by draft 2020-12 (declare $schema for draft 2019-09)" in message


def test_unknown_schema_dialect_below_the_root_is_refused(tmp_path):
    schema = "{properties: {a: {$schema: 'https://example.com/bogus'}}}"
    assert "only the root schema may declare $schema" in _load_error(_schema_test(tmp_path, schema))
