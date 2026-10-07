---
title: A Reason for Every Verdict - Plan
type: feat
date: 2026-10-07
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# A Reason for Every Verdict - Plan

## Goal Capsule

- **Objective:** Every verdict shows why, in the server's own words where it sent any, so a reader can tell a real rejection ("Access denied - path outside allowed directories") from an unrelated error without opening the JSON.
- **Means:**
  - One quoting helper in `graders.py` (KTD1).
  - A Passed section in the report (KTD3).
  - Literal output from `mcp-assay tools` (KTD4).
- **Authority:** the owner's N5 instructions of 2026-10-07, then `CLAUDE.md`, then `docs/reviews/2026-09-29-readiness-review.md` (v0.1.1 item 5: "Show a reason, including the server's message, for every verdict"; top finding 9).
- **Stop and ask if:** any verdict on a shipped suite changes, the JSON shape changes, or the change needs a "server refuses everything" heuristic.
- **Execution profile:** inline, tests first. Commit on `n5-reason-for-every-verdict`. Do not merge or push.

---

## Product Contract

### Problem Frame

Readiness review top finding 9 says the console shows reasons only for failures, and the reasons leave out the server's message. So a server that denies every request looks perfect. On `suites/filesystem.yaml` today, all four `is_error` passes say only "server returned an error as expected". The server's actual text ("Access denied - path outside allowed directories", "ENOENT: no such file or directory", "Input validation error") is visible only in the JSON.

### Requirements

**Grading reasons**
- R1. Each of these reasons quotes the server's own message when the server sent one:
  - an `is_error` pass, from a JSON-RPC rejection or an `isError` result;
  - an `is_error` fail on accepted input, quoting what the server returned;
  - a `no_error` fail;
  - a `schema_valid` fail on a JSON-RPC error or `isError`.

  The message is the JSON-RPC `error_message`, or the joined text of the result's text content blocks.
- R2. A `no_error` pass reason does not quote anything.
- R3. The quote is one line: whitespace collapsed, control and format characters removed, about 120 characters with `...` when longer, and wrapped in double quotes.
- R4. Every verdict is exactly as it was. Only reason text changes.

**Report**
- R5. The console lists every pass with its reason in a "Passed" section, in the same style as Failures and Inconclusive. Server text is escaped as in N3, and the table gets no new column.

**Output contract**
- R6. The JSON has the same shape, and the full server text stays in `content` and `error_message`.

**`mcp-assay tools`**
- R7. The command prints server text literally: no rich markup, no emoji codes, no soft-wrapping inside a JSON line. It also prints a tool's output schema when the tool declares one.

### Key Decisions

All five are the owner's, made in the N5 instructions:
- **Quote the server's own text**, through one helper (R1, R3).
- **Add a Passed section, not a table column** (R5).
- **Leave the JSON shape unchanged** (R6).
- **No "server refuses everything" warning in N5.** That belongs to v0.2's message assertions.
- **Fold in the `tools` fix** (R7).

### Scope Boundaries

- Not covered: reasons for unobserved answers (timeout, transport) and malformed replies. The harness writes that text itself, so there is no server message to quote.
- Not covered: v0.2 message assertions, and any refuse-everything heuristic.
- Not covered: the open issues already in `CLAUDE.md` (the `tools/list` preflight, SDK-converted crashes).

---

## Planning Contract

### Key Technical Decisions

- KTD1. **`_quote(text)` in `graders.py`** returns the one-line quoted snippet, or `None` when nothing remains after cleaning.
  - It collapses every run of whitespace to one space, then drops characters in Unicode categories `Cc` and `Cf`, which removes escape sequences and zero-width or bidi controls.
  - It cuts at 117 characters plus `...`.
  - `_server_text(execution)` joins the `text` of `type: text` content blocks with a space. A reply with no text blocks gives `None`, and the reason keeps today's wording.
- KTD2. **JSON-RPC reasons go through `_rpc_error`, which now quotes `error_message`.** That covers the `is_error` pass, the `no_error` and `schema_valid` fails, and, for consistency, the two existing `is_error` fails on -32601 and -32603. The `isError` and accepted-input reasons append `: <quote>` to today's wording.
- KTD3. **The Passed section is printed before Failures,** so failures stay nearest the totals. It uses a `[green]+[/green]` marker; the ASCII `+` survives cp1252. Ids and reasons are escaped as in N3. The pass list is computed in `report.py`, so `models.py` is unchanged.
- KTD4. **`tools` prints with `typer.echo`, not rich.** Plain echo is literal by construction (no markup, emoji or soft-wrap), and N3's `_safe_streams` already makes it safe on cp1252.
  - One helper builds each tool's text: the name and description line, then `input schema:` and its JSON, then `output schema:` and its JSON when declared, then a blank line.
  - The helper is unit-tested with a constructed `mcp.types.Tool`.

---

## Implementation Units

### U1. Quote the server's text in grading reasons

- **Requirements:** R1, R2, R3, R4, R6; KTD1, KTD2
- **Files:** `src/mcp_assay/graders.py`, `tests/test_harness.py`
- **Stage:** grading. Reasons are produced by the checks.
- **Test scenarios** (written first, seen failing):
  - **`is_error`, JSON-RPC rejection:** the reason is `server rejected the call with JSON-RPC error -32602: "Invalid params"`.
  - **`is_error`, `isError` result:** the reason ends with the quoted content text.
  - **`is_error`, accepted input:** a fail that quotes what the server returned.
  - **`no_error` fails:** an `isError` result and a JSON-RPC error each quote the server text.
  - **`schema_valid` fail on `isError`:** quotes the server text.
  - **`no_error` pass:** exactly `completed without error`.
  - **Cleaning:** newlines and tabs collapse, ESC and zero-width characters are removed, and a 300-character text becomes at most 122 characters including the quotes, ending in `..."`.
  - **No text blocks:** the reason keeps today's wording, with no empty `""`.
- **Verification:** the new tests pass. Both shipped suites give identical verdicts before and after.

### U2. Passed section in the report

- **Requirements:** R5; KTD3
- **Files:** `src/mcp_assay/report.py`, `tests/test_report.py`
- **Stage:** rendering.
- **Test scenarios:**
  - **Listing:** a pass appears under "Passed" with its reason.
  - **Escaping:** markup in a pass's id or reason (`[/x]`) is printed literally.
  - **No passes:** no "Passed" heading is printed.
- **Verification:** the filesystem console output shows the "Access denied" text for the traversal tests.

### U3. `tools` prints literally, with output schemas

- **Requirements:** R7; KTD4
- **Files:** `src/mcp_assay/cli.py`, `tests/test_cli.py`
- **Stage:** entrypoint.
- **Test scenarios:**
  - **Literal text:** a constructed tool whose description holds `[a-z]+`, `[/x]` and `:x:`, with `pattern: "^[a-z0-9]+$"`, prints all four unchanged and does not raise.
  - **Output schema:** it is printed when declared and omitted when absent.
  - **No wrapping:** a schema line over 100 characters stays on one line.
  - **Real server:** `tools` against `examples/broken_server.py` lists `get_status` with its output schema and exits 0.
- **Verification:** `uv run mcp-assay tools npx -- -y @modelcontextprotocol/server-filesystem .` prints a pattern or output schema intact.

---

## Verification Contract

| Check | Command | Expected |
|---|---|---|
| Tests | `uv run pytest` | all pass |
| Lint | `uv run ruff check .` | clean |
| Format | `uv run ruff format --check .` | clean |
| Broken server | `uv run mcp-assay run suites/broken_server.yaml` | verdicts identical to the 2026-10-07 baseline; only reasons differ |
| Filesystem | `uv run mcp-assay run suites/filesystem.yaml` | verdicts identical; Passed shows "Access denied" for the traversal tests |
| Tools | `uv run mcp-assay tools npx -- -y @modelcontextprotocol/server-filesystem .` | schemas printed literally, output schema shown where declared |

## Definition of Done

- U1 to U3 are complete, and every check above holds.
- Each new test was seen failing first, apart from tests that guard behaviour that should stay unchanged.
- The code review has run, and its confirmed findings are fixed.
- Committed on `n5-reason-for-every-verdict`, not merged, not pushed. This plan is committed with N5.
