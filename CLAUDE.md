# mcp-assay

An eval harness for MCP servers: start a server, send it a suite of test cases
(valid and deliberately invalid), judge the replies, print a scorecard.

The product is **the verdict**, not the run. A wrong verdict in either direction —
a bad server marked pass, or a good server marked fail — is the worst class of bug
in this repo. Treat any such case as a correctness bug, not a nitpick.

## Layout

```
src/mcp_assay/
  models.py     the four objects: TestCase -> ExecutionResult -> GradedResult -> Scorecard
  suite.py      loads suite YAML into TestCase objects
  connector.py  the ONLY module that touches the MCP protocol
  runner.py     runs each test through the connector, then grades it
  graders.py    one function per check type, dispatched via CHECKS
  schemas.py    JSON Schema rules (draft, keywords, formats) shared by suite.py and graders.py
  report.py     renders the scorecard
  cli.py        typer entrypoint (`mcp-assay run`, `mcp-assay tools`)
suites/         test suites as YAML data
examples/       broken_server.py — a deliberately careless server used as a baseline
tests/          pytest suite for the harness itself
```

## Invariants

- Each stage takes the previous object and produces the next one. Every object keeps
  the one before it, so any verdict traces back to the raw exchange.
- Protocol types stay inside `connector.py`. Downstream code sees `ExecutionResult` only.
- Suites are data. A new test case is YAML, never Python.
- A new check type is a function in `graders.py` plus an entry in `CHECKS` plus a
  `CheckType` literal in `models.py`. Nothing else changes.
- Adding a transport means producing an `ExecutionResult`; graders must not change.
- A schema is loaded and graded under the same draft rules, both taken from `schemas.py`.

## How to work with me

I am learning this codebase as I build it, and I review every section myself.

- Explain the reasoning before writing code. Say which stage the change belongs to
  and why it belongs there rather than in a neighbouring stage.
- Keep changes surgical: one stage per change, matching the existing style.
- Point out the trade-off you chose and what you rejected.
- Never push, never open a PR, never commit without being asked. I review section by
  section first, then push myself.

## Honesty rules

- Never make a test pass by weakening it: no hardcoded expected values, no mocking
  away the real connector, no `skip`/`xfail` to hide a failure, no broadening a
  schema until it matches.
- Never report work as done without running it. Paste the real command output.
- If something is broken or you are unsure, say so plainly. An honest "this still
  fails" is worth more than a green summary.
- A harness bug that produces a wrong verdict must be reported even when all tests pass.

## Known open issue: a timeout spoils the next test

Reproduce: `uv run mcp-assay run suites/broken_server.yaml --timeout 2` (`hang`
sleeps 5s, so the default 10s timeout hides it).

- **Defect 2 (false passes): fixed.** Each result records `outcome`; an unobserved
  answer grades `inconclusive`. Review: `docs/reviews/2026-09-26-defect-2-code-review.md`.
- **Defect 1 (the test after a timeout inherits the stall): design decided in
  `docs/adr/0001-recovery-after-timeout.md` (accepted), not yet implemented.** Follow
  that ADR; ask me before deviating from it.
- **Still open:** an `is_error` test with an unknown tool name passes (needs a
  `tools/list` preflight; ask first). So does one with a misspelled argument name
  (`pth:` for `path:`): the server rejects the call for the missing argument and the
  rejection is credited. YAML-converted values (unquoted yes/no/on/off/y/n, dates) are
  now refused when the suite loads. The v0.1.2 preflight must still check each test's
  argument names, and value types, against that tool's `inputSchema`.
- **Still open:** the MCP Python SDK turns an unexpected tool exception into a result
  with `isError: true`, so `is_error` credits a crash as a rejection when it does not
  reach the wire as JSON-RPC -32603. Message assertions (v0.2) are the planned fix.
- **Known limit:** `format: regex` and `pattern` use Python `re`, so an ECMA-262-only
  pattern such as `\p{L}` can give a wrong fail.

## Commands

Requires Python 3.12+ (`requires-python = ">=3.12"`, per ADR 0001).

```bash
uv sync                                          # install
uv run mcp-assay run suites/broken_server.yaml   # run a suite (exit code 1 if any fail)
uv run mcp-assay run suites/filesystem.yaml --json scorecard.json
uv run mcp-assay tools npx -- -y @modelcontextprotocol/server-filesystem .
uv run pytest                                    # harness's own tests
uv run ruff check . && uv run ruff format --check .
```

Verify against a real third-party server, not only `examples/broken_server.py`.

## Privacy

Nothing committed may contain my name, my email, absolute paths such as
`C:\Users\...`, machine hostnames, tokens, or API keys. `scorecard.json` is
git-ignored: it embeds raw server exchanges and stays local.
