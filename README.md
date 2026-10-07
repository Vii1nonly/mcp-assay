# mcp-assay

A reliability eval harness for [MCP](https://modelcontextprotocol.io) servers, the
tool servers that AI agents depend on to read files, query data and take actions.

An agent that calls a tool trusts the server to reject bad input, return what its
schema promises, and stay inside its boundaries. `mcp-assay` checks those promises.
It starts a server, runs a suite of valid and deliberately invalid calls against
it, and reports a scorecard with a verdict and a reason for every test.

## Install

```bash
uv sync
```

## Use

```bash
# Run a suite
uv run mcp-assay run suites/filesystem.yaml

# Inspect a server's tools while writing a new suite
uv run mcp-assay tools npx -- -y @modelcontextprotocol/server-filesystem .

# Save the full scorecard, raw exchanges included
uv run mcp-assay run suites/filesystem.yaml --json scorecard.json
```

The exit code is non-zero when any test fails or is inconclusive, so it works in CI. It is 1 for a failed or inconclusive test. It is 2 for a suite that cannot be read, a `--json` path that cannot be written, or a scorecard that cannot be displayed; the verdicts are kept wherever they could be delivered.

If the harness itself fails while grading a test, that test alone is reported as an inconclusive harness error, in its own section, with a warning that counts the affected tests. This is a harness bug, not a server result.

## How it works

Four objects flow through four stages. Each stage keeps the one before it, so
any failure can be traced back to the exchange that produced it.

```
suite YAML  --load-->  TestCase
                          |  executor + connector
                          v
                    ExecutionResult    what the server actually returned
                          |  grader
                          v
                     GradedResult      pass/fail and why
                          |  aggregate
                          v
                      Scorecard        totals by category, plus failures
```

| Module | Stage |
| --- | --- |
| `suite.py` | parses suite YAML into `TestCase` objects |
| `connector.py` | spawns the server, speaks JSON-RPC over stdio |
| `runner.py` | runs every case, then aggregates |
| `graders.py` | decides pass or fail |
| `schemas.py` | JSON Schema rules shared by loading and grading |
| `report.py` | renders the scorecard |

## Checks

| Check | Passes when |
| --- | --- |
| `no_error` | the call completes and the server does not flag an error |
| `is_error` | the server rejects the call, as it should for invalid input — with an error result or a JSON-RPC error reply. Parse error, invalid request and method not found (-32700, -32600, -32601) do not count: the server never evaluated the input. Internal error (-32603) does not count either: the server broke instead of rejecting |
| `schema_valid` | structured content validates against the supplied JSON Schema, and the server did not flag the result as an error |

Every check returns **inconclusive** instead when the harness never observed the
server's answer: the call timed out or the connection broke. A timeout is not a
rejection, so a server that hangs on bad input is never credited with refusing it.

A reply that arrives but breaks the protocol's result shape is observed, and
every check grades it **fail**.

`is_error` is the one that finds real bugs: a server that cheerfully accepts
input its own schema declares invalid.

### Schemas

A `schema_valid` schema uses JSON Schema draft 2020-12 unless it declares another
with `$schema` (draft-04, -06, -07 and 2019-09 are supported). It is checked when
the suite loads, under the same rules the grader uses, so a mistake is refused
before any server starts instead of quietly checking nothing:

- a keyword the draft does not act on, such as a misspelling or a keyword from
  another draft (`dependencies` is draft-07; 2020-12 uses `dependentRequired`);
- a keyword the draft ignores where it sits: an asserting keyword next to `$ref` in
  draft-07 and earlier, `then`/`else` without `if`, `minContains` without `contains`,
  `additionalItems` when `items` is not a list;
- a `format` the draft cannot check, so a format is always either checked or refused;
- a schema that loops back to itself without checking any data, such as `{$ref: '#'}`,
  including through a subschema with its own `$id`;
- a `$ref` that does not resolve inside the schema (grading never fetches one).

`format` is checked, not just noted, with jsonschema's format checkers for the
declared draft. Some checks are loose: `email` only requires an `@`. `regex` and the
`pattern` keyword use Python's regular-expression syntax, so a pattern valid only in
ECMA-262 (such as `\p{L}`) is treated as invalid.

## Suites are data

Adding a test means editing YAML, not Python:

```yaml
- id: read-text-file-missing-required-arg
  category: robustness
  tool: read_text_file
  arguments: {}
  expect:
    type: is_error
```

The suite's own keys and schemas are checked strictly when it loads. A misspelled key, a misplaced `schema`, a duplicate id or a broken schema stops the run before the server starts, with one line naming the file and the spot. Argument names and values inside `arguments` are passed through as written: only the server checks them.

## Testing the harness itself

`examples/broken_server.py` is a minimal MCP server with deliberate bugs: it
declares `path` as required and then accepts calls without it, and it declares an
output schema it then violates. Running the harness against it should produce
failures. If it does not, the harness is broken.

```bash
uv run mcp-assay run suites/broken_server.yaml   # expect 2 failures
```

## Note on the MCP SDK

The connector issues raw `tools/call` requests rather than using the SDK's
`call_tool()` helper. The helper validates results against the server's declared
output schema and raises before returning, which would stop the harness from ever
observing a schema-violating payload. An eval tool has to see what the server
actually sent and judge it itself.

## Status

v0.1: stdio transport, three check types, pass / fail / inconclusive verdicts,
console and JSON reports.

Known limitation: with a short `--timeout`, a test that stalls the server can make the next test grade inconclusive, because that test's request waits behind the stalled one. The fix is designed in [docs/adr/0001-recovery-after-timeout.md](docs/adr/0001-recovery-after-timeout.md) and not yet built.

Roadmap:

- **Recovery:** restart a stalled or crashed server so one bad test cannot affect
  the next (the ADR above).
- **Server checks:** structural argument matching, fuzz suites, a security suite
  based on the [MCPSecBench](https://arxiv.org/abs/2508.13220) taxonomy, and HTTP
  transport.
- **Agent task suites:** run an agent through multi-step tasks against real
  servers and grade whether the task was completed, not only single calls.
- **Cost tracking:** record the time each run takes and, for agent suites, the
  model tokens it uses, so reliability can be weighed against cost.
