# N4: Tighten grading

Roadmap item N4 of `docs/reviews/2026-09-29-readiness-review.md`, plus the
leftovers folded into it. Branch `n4-tighten-grading`.

## Goal

A check never passes because a rule was too generous, and a valid schema is
never refused or misread because the harness assumed the wrong draft.

## Baseline (reproduced on main 79beee5 before any change)

| Case | Today | After N4 |
| --- | --- | --- |
| `is_error`, server replies -32603 | pass | fail (decision 1) |
| `schema_valid`, result has `isError: true` and matching data | pass | fail |
| `format: email` / `uri` / `date-time` violated | pass (not checked) | fail |
| `format: emial` (typo) | loads, checks nothing | refused at load |
| `$schema: draft-07` with `dependencies` | refused at load | loads, graded as draft-07 |
| `minContains` / `maxContains` | refused at load | loads |
| `dependencies` under draft 2020-12 | refused (correct) | refused, with a draft hint |
| `{$ref: '#'}` | loads, grader hits RecursionError | refused at load |
| `!!bool maybe` | KeyError traceback, exit 1 | one line, exit 2 |

Without the jsonschema format extras only 8 formats are checkable; `uri`,
`date-time` and `hostname` would silently pass. N4 therefore adds
`jsonschema[format-nongpl]` (all permissive licences) and refuses any `format`
the active draft cannot check, so a format is either checked or rejected.

## Design

New module `schemas.py`, used by both `suite.py` (load) and `graders.py`
(grade), so a schema is loaded and graded under the same rules:

- `validator_class(schema)`: the draft named by `$schema`, default 2020-12.
  Supported: draft-04, -06, -07, 2019-09, 2020-12. Draft-03, unknown URIs,
  non-string values and `$schema` below the root are refused.
- `keywords(cls)`: the keywords of that draft's meta-schema and vocabularies,
  minus those its validator ignores (`dependencies` in 2019-09 and 2020-12;
  `$recursiveRef`/`$recursiveAnchor` in 2020-12), plus `writeOnly` for
  draft-07, which its bundled meta-schema omits. Derived, not hand-listed.
- `format_names(cls)`: formats the draft's checker can actually check.
- `validator(schema)`: an instance with format checking on.

`suite.py` load checks use the schema's own draft for `check_schema`,
`$ref` resolution and the keyword allow-list; a keyword of another draft gets
a hint naming the draft. A cycle of in-place applicators (`$ref`, `allOf`,
`anyOf`, `oneOf`, `not`, `if`/`then`/`else`, `dependentSchemas`/
`dependencies`, `$dynamicRef`/`$recursiveRef`) that returns to a schema
without stepping into a property or item is refused as infinite recursion.
Legitimate recursion (`properties: {child: {$ref: '#'}}`) still loads. In
draft-07 and earlier a `$ref` ignores its siblings, so only the `$ref` edge
counts there.

`graders.py`: `is_error` fails on -32603; `schema_valid` fails when the
server flagged `isError`; validation uses `schemas.validator`. The N3 harness
error guard stays as the safety net for anything load misses.

`suite.py` YAML: a tagged value YAML cannot build through a lookup
(`!!bool maybe` raises KeyError) is reported like `!!int abc`.

## Tests (written first, red on main)

Graders: -32603 under `is_error`; `isError` under `schema_valid`; email,
uri and date-time violations fail and valid values pass; draft-07
`dependencies` violation fails; draft-04 boolean `exclusiveMaximum` does not
false-fail.

Load: `minContains`/`maxContains` load; draft-07 `dependencies` loads;
2020-12 `dependencies` refused with a draft hint; unknown, draft-03, nested
and non-string `$schema` refused; format typo refused with a hint; custom
format refused; a draft-04 `uuid` format refused; `{$ref: '#'}` and
`{allOf: [{$ref: '#'}]}` refused; tree recursion loads; draft-07 `$ref` with
an ignored looping sibling loads; `!!bool maybe` refused with a position and
exit 2.

## Verification

- `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`
- Both shipped suites match the recorded baseline verdict by verdict.
- Each baseline row above reproduced by hand through the CLI.
- `/ce-code-review` on the diff before committing.

## Out of scope

Unknown tool names and argument names (v0.1.2 preflight). Reasons quoting the
server (N5). The cwd rule (N6).
