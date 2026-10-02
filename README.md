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

The exit code is non-zero when any test fails or is inconclusive, so it works in CI.

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
| `report.py` | renders the scorecard |

## Checks

| Check | Passes when |
| --- | --- |
| `no_error` | the call completes and the server does not flag an error |
| `is_error` | the server rejects the call, as it should for invalid input — with an error result or a JSON-RPC error reply. Parse error, invalid request and method not found (-32700, -32600, -32601) do not count: the server never evaluated the input |
| `schema_valid` | structured content validates against the supplied JSON Schema |

Every check returns **inconclusive** instead when the harness never observed the
server's answer: the call timed out or the connection broke. A timeout is not a
rejection, so a server that hangs on bad input is never credited with refusing it.

A reply that arrives but breaks the protocol's result shape is observed, and
every check grades it **fail**.

`is_error` is the one that finds real bugs: a server that cheerfully accepts
input its own schema declares invalid.

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
