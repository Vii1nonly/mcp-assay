---
title: A tools/list Preflight - Plan
type: feat
date: 2026-10-09
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# A tools/list Preflight - Plan

## Goal Capsule

- **Objective:** An `is_error` test can no longer pass because it named a tool the server does not have, or misspelled an argument, and a success test can no longer run with arguments the tool's own schema rejects. Every test is checked against the server's tool list before any test runs.
- **Means:**
  - `connector.run_tests` fetches `tools/list` once, following pagination, as plain data (name to `inputSchema`) (KTD1).
  - A pure `preflight.check` compares the tests with that data (KTD2).
  - Two opt-out keys, `unknown_tool` and `unknown_arguments`, are allowed on `is_error` tests only (KTD4).
- **Authority:** the owner's N8 instructions of 2026-10-09 (decisions 1-9), then `CLAUDE.md`, then the readiness review's v0.1.2 list.
- **Stop and ask if:** a shipped suite's verdict, reason or exit code changes, or the work needs message assertions, a "which schema rule broke" report, or `schema: from_server` (v0.2).
- **Execution profile:** inline, tests first. Commit on `n8-tools-preflight`. Do not merge or push.

---

## Product Contract

### Problem Frame

`CLAUDE.md` lists two open false passes:

- An `is_error` test with an unknown tool name passes, because the server rejects the unknown tool and the rejection is credited.
- An `is_error` test with a misspelled argument name (`pth:` for `path:`) passes, because the server rejects the call for the missing `path` and that rejection is credited.

A success test (`no_error`, `schema_valid`) whose arguments break the tool's `inputSchema` has a related problem: it tests the suite author's mistake, not the server.

### Requirements

- R1. Before any test runs, the harness fetches the server's tool list once through `tools/list`, following `nextCursor` until the last page.
- R2. A test whose tool is not on the server is refused before any test runs: one line naming the test and the tool, with a did-you-mean hint, exit 2. An `is_error` test with `unknown_tool: true` runs instead, and its argument checks are skipped.
- R3. An argument name that is not in the tool's `inputSchema` `properties` is refused (one line, did-you-mean, exit 2), unless it matches a `patternProperties` pattern or the test sets `unknown_arguments: true`. A tool whose schema lists no properties gets no name check.
- R4. A `no_error` or `schema_valid` test's arguments must validate against the tool's `inputSchema`, under `schemas.validator` (the schema's own `$schema`, default 2020-12). A violation is refused with one line naming it, exit 2.
- R5. An `is_error` test's arguments are checked only by R2 and R3.
- R6. A `tools/list` that fails (JSON-RPC error, timeout, malformed reply) refuses the run with one line, exit 2.
- R7. A tool whose own `inputSchema` is not valid JSON Schema does not refuse the suite. Its argument checks are skipped, and one warning line on stderr names the tool.
- R8. `unknown_tool` and `unknown_arguments` are suite keys (the suite model keeps `extra="forbid"`). Either one on a non-`is_error` test is a suite error at load. The README's "Writing a suite" section shows each with a short example. CLAUDE.md's known issues mark both false passes fixed.
- R9. Both shipped suites keep identical verdicts, reasons and exit codes.

### Scope Boundaries

- Out of scope (v0.2): showing which schema rule an `is_error` test's arguments break, message assertions, `schema: from_server`.
- `mcp-assay tools` keeps its single-page listing; it is a display command and is not part of the run.
- No change to graders, the report layout, or recovery (ADR 0001 steps 2-3).

---

## Key Technical Decisions

- **KTD1. The connector fetches, as plain data.** `connector.list_tool_schemas(session, timeout)` sends `tools/list` itself (`session.send_request`, as `run_test_case` does) rather than `session.list_tools()`. The SDK's helper can drop tools under the 2026 protocol and caches per-tool state, and the preflight must see exactly what the server sent. It follows `nextCursor` and returns `{name: inputSchema}`. The whole listing must finish within the per-call timeout, and a repeated cursor or more than 1000 pages is refused (a server that would page forever). The SDK checks each reply against the MCP wire model, so a tool whose `inputSchema` breaks the MCP shape (no `type: "object"`) makes the reply malformed: the run is refused under decision 6, with the page and field named (review fix).
- **KTD2. A pure checker.** `preflight.py` has `check(tests, tools) -> Preflight(problems, warnings)` and `PreflightError`. It imports no protocol module, so it is unit-tested without a server. `run_tests` calls it after the handshake and before the first test, in the same session. When there are problems, it raises `PreflightError` with the first problem and a "(+N more)" count, matching suite refusals.
- **KTD3. Warnings travel on the scorecard.** `Scorecard` gains `warnings: list[str]` (default empty). `run_tests` returns it with the results, and the CLI prints each one to stderr before rendering. The JSON keeps the record of which checks were skipped.
- **KTD4. Opt-outs are narrow.**
  - `unknown_tool` and `unknown_arguments` default to false and are allowed only on `is_error` tests.
  - `unknown_tool: true` on a tool the server does list is refused. Otherwise a stale flag would silently switch off the name check, which is the false pass this item closes.
- **KTD5. The name check reads only `properties` and `patternProperties`.** `additionalProperties` is not consulted (decision 3). A pattern is matched with Python `re.search`, the harness's documented regex limit. A schema with no `properties` key, or an empty one, gets no name check (decision 3: "lists no properties at all").
- **KTD6. An unusable server schema is the server's bug.** This covers a schema that passes the MCP shape but not JSON Schema. If `check_schema` fails, `$schema` names an unsupported draft, a pattern is not a valid regex, or validation itself raises (an unresolvable `$ref`), the tool's tests skip argument checks, and one warning names the tool. Only tools that some test uses are checked.
- **KTD7. Refusal text.** Each problem starts with the test spot used by load errors (`tests[3] (id 'x')`). The CLI prefixes the suite path, so the line reads like a load refusal.

---

## Implementation Units

### U1. Tests first (red)

- `tests/test_preflight.py` (pure, no server):
  - Unknown tool refused with a did-you-mean hint; `unknown_tool` on an `is_error` test skips it and its argument checks.
  - `unknown_tool` on a tool the server lists is refused.
  - A misspelled name is refused with a did-you-mean hint; `patternProperties` and `unknown_arguments` allow it; no `properties` means no check.
  - A `no_error` or `schema_valid` test with a wrong-type or missing required argument is refused; an `is_error` test with the same arguments is not.
  - A draft-07 schema is honoured.
  - An invalid tool schema gives one warning, and no refusal.
  - Pagination: a fake session serving two pages, where a tool on page two is found. A repeated cursor is refused.
- `tests/test_suite.py`: either flag on a non-`is_error` test is refused at load; on `is_error` it loads.
- `tests/test_cli.py` (real example server):
  - An unknown tool exits 2 with one line and no stdout.
  - The same test with `unknown_tool: true` runs, and the server's rejection is credited.
  - A `pth:` typo exits 2 with did-you-mean.
  - A wrong-type `no_error` argument exits 2.
  - A `tools/list` failure exits 2: the example server gains no new behaviour for this, so the test points `--command` at a server that answers `tools/list` with an error.

### U2. Build

`models.py` (two `TestCase` keys, `Scorecard.warnings`), `suite.py` (load rule), `preflight.py` (new), `connector.py` (`list_tool_schemas`, call `check` in `run_tests`), `runner.py` (carry warnings), `cli.py` (catch `PreflightError` and print warnings).

### U3. Docs

README "Writing a suite": the preflight, both keys with an example each. README module table: `preflight.py`. CLAUDE.md: the layout line and the known issues.

---

## Verification

- `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- Both shipped suites: verdicts, reasons and exit codes identical to the baselines recorded before the change. The filesystem run is also the real-server proof that real `inputSchema`s pass the preflight.
- CLI output for:
  - an unknown tool;
  - the same with `unknown_tool: true`;
  - a `pth:` typo;
  - a wrong-type `no_error` argument;
  - `unknown_tool` on a `no_error` test.
- `/ce-code-review`, fix confirmed findings, privacy scan, commit on the branch.
