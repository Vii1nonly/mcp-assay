# Code Review: Defect 2 Fix (`is_error` false pass)

- **Date:** 2026-09-26
- **Process:** Multi-agent code review (`/ce-code-review`) of the uncommitted defect 2 changes, checking for false passes and that each change stays in its stage
- **Branch:** `fix/is-error-false-pass`, base `fc25f52` (changes were uncommitted at review time)
- **Verdict:** Ready with fixes

---

## Code Review Results

**Scope:** standalone, base `fc25f52` -> working tree on `fix/is-error-false-pass` (9 files, 258 executable changed lines)
**Intent:** Close the defect 2 false pass. `is_error` may pass only on a rejection the server actually sent, never on a timeout or a broken session. connector.py records each call's outcome (answered / timeout / transport_error). A JSON-RPC error reply counts as answered, with its code. SDK-local codes -32000/-32001 are never credited to the server. Any unobserved answer grades inconclusive under every check and makes the CLI exit non-zero. Defect 1 (session contamination after a timeout) is out of scope.
**Mode:** markdown report-only (no apply authority; no project file was changed)

**Reviewers:** correctness, project-standards, testing, maintainability, reliability, adversarial
- project-standards -- CLAUDE.md governs every changed file and defines the stage rules you asked about
- testing -- test files changed, and the change is itself a set of wrong-verdict guards
- maintainability -- 258 executable changed lines and a new type crossing stage boundaries
- reliability -- timeouts and error classification are the core of the change
- adversarial -- this is a verification tool whose failure mode is a silent pass (ran in-process; no cross-model peer was available)

**Your question 1, false passes:** this diff adds no new false pass. The defect 2 path is closed. A timeout under `is_error` now grades inconclusive, and a real-connector test covers it (`test_timeout_under_is_error_is_not_credited_as_a_rejection`). A broken session also grades inconclusive: two reviewers checked this by hand, but no real-connector test covers a crash yet (see testing gaps). An inconclusive run exits 1 (cli.py:40). Two false-pass paths are still open in the code. Both predate this diff and gave the same pass at base `fc25f52`:
- a server that answers `tools/call` with -32601, -32600 or -32700 passes every `is_error` test (#2);
- an `is_error` test whose tool name the server does not know passes (graders.py:54-55).

Both are under Pre-existing Issues. The two findings that remain go the other way: in each, an answer the server did send gets labelled "not observed" (#5, #7). Neither gives a pass or exit 0.

**Your question 2, stages:** yes, every change stays in its stage. The fix spans several files because CLAUDE.md's own defect 2 plan spans `models.py`, `connector.py` and `graders.py`. The `report.py` and `cli.py` edits follow from the new `inconclusive` verdict. Each piece of logic sits in the one stage that owns it. project-standards checked all 9 files against CLAUDE.md and found no violation.

| File | Stage | What changed | In its stage? |
|------|-------|--------------|---------------|
| `src/mcp_eval/models.py` | objects | `Outcome` literal, `rpc_error_code`, `inconclusive` verdict, Scorecard counts | Yes. This is the state CLAUDE.md's defect 2 fix names. |
| `src/mcp_eval/connector.py` | transport | sets `outcome`, catches `MCPError`, maps SDK-local codes | Yes. Still the only `src/` module importing `mcp` (checked with grep). `MCPError` and `types` codes do not leave it. |
| `src/mcp_eval/graders.py` | grading | decides from `outcome` and `rpc_error_code` | Yes. Imports only `jsonschema` and `.models`. The "graders must not change" rule covers adding a transport, which this is not. |
| `src/mcp_eval/report.py` | report | renders the inconclusive verdict, section and count | Yes |
| `src/mcp_eval/cli.py` | entrypoint | exits 1 on any inconclusive result | Yes. Reads Scorecard counts only. |
| `src/mcp_eval/runner.py` | runner | unchanged | Yes. Session-loop ownership (defect 1) is left alone, as CLAUDE.md asks. |
| `suites/broken_server.yaml` | data | two new rows | Yes. YAML, no Python. |
| `examples/broken_server.py` | fixture | `strict_echo`, `ambiguous_reject`, `hang` | Yes |
| `tests/test_harness.py` | tests | real-connector and grader tests | Yes. Uses the real connector, with no mocks, skips or xfails. |
| `README.md` | docs | the `is_error` row and an inconclusive paragraph | Yes (see #2 on the new row's wording) |

One note for when you commit. The `hang` tool and its suite row are your earlier edit, not defect 2 work. `test_timeout_under_is_error_is_not_credited_as_a_rejection` calls `hang`, so both must land together, or `hang` first. The fixes proposed for #5 and #7 also stay inside these stages. The only option in this report that would cross a boundary is a `tools/list` preflight for the unknown-tool path. It needs a hook in the session loop, which CLAUDE.md marks as a pending design decision.

### Triage Groups

| Group | Findings | Context | Preferred Resolution | Why |
|-------|----------|---------|----------------------|-----|
| connector.py labels server replies it did receive as unobserved (mixed: #5 apply-queue, #7 decision-gate) | #5, #7 | Both come from how `run_test_case` classifies an answer the server did send. A reply that fails `CallToolResult` validation falls into the blind catch as `transport_error` (#5). A server-sent -32001 maps to `timeout` through `_SDK_LOCAL_CODES` (#7). Either way graders.py returns inconclusive and blames the transport or a timeout. | Fix #5 first: catch `pydantic.ValidationError` in connector.py, carry the malformed state in models.py, grade it fail in graders.py. Then you decide #7 and pin the choice with a real-connector -32001 test. A fixer stops at #7. | Same stage, same failure mode. #5 is a confirmed regression from fail to inconclusive, with a concrete fix. #7 reverses part of your stated -32000/-32001 rule, so it needs your call. |

### P2 -- Moderate

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 5 | `src/mcp_eval/connector.py:77` | Malformed server reply graded inconclusive as "answer not observed" instead of fail | correctness, adversarial | 100 |

- **#5** -- **What happens:** a server replies to `tools/call` with something that breaks the `CallToolResult` shape: `{"content": "oops"}`, a missing `content`, or an unknown content-block type. In mcp 2.2.0, `session.send_request` raises `pydantic.ValidationError` after that reply arrives (`mcp/client/session.py:557-601`). The blind `except Exception` at connector.py:77-85 records it as `transport_error`. Every check then grades inconclusive with "server's answer was not observed (transport_error)".
  **Why it matters:** at base `fc25f52`, the same reply correctly failed `no_error` and `schema_valid`. The CLI still exits 1, so this is not a false pass. But the scorecard blames the transport for a server bug, and it contradicts the README's new definition of inconclusive ("the call timed out or the connection broke").
  **Fix:** in connector.py, catch `pydantic.ValidationError` ahead of the blind catch and record an observed but malformed answer. Two ways to carry that state: a new `Outcome` value such as `"invalid_reply"` in models.py, or `outcome="answered"` plus a `protocol_violation` field. In graders.py, grade it fail under every check, never pass under `is_error`. Add a real-connector test: a broken_server tool that returns `{"content": "oops"}` must fail under `no_error`.
  **How sure:** the validator reproduced it on the reviewed tree (mcp 2.2.0, protocol 2025-11-25). `validation_status: confirmed`; `validation_reason`: incidence across real servers not measured. Two reviewers agreed, but both ran in-process on one model, so agreement did not raise confidence.

### P3 -- Low

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 7 | `src/mcp_eval/connector.py:22` | Server-sent -32001 labelled an SDK-local timeout and never credited to the server | correctness, adversarial | 75 |

- **#7** -- **Design call for you (owner: human).**
  **What happens:** the harness times calls with `asyncio.wait_for` and never gives the SDK a read timeout: `ClientSession(read, write)` at connector.py:30, and `send_request` at :51 with no `request_read_timeout_seconds`. mcp 2.2.0 raises REQUEST_TIMEOUT (-32001) only inside `anyio.fail_after(opts.get("timeout"))` (`mcp/shared/jsonrpc_dispatcher.py:401,425`). `opts["timeout"]` is set only when a read timeout exists (`mcp/client/session.py:572-578`). So on this path, any -32001 was sent by the server. The harness still records it as `outcome="timeout"` with "not attributable to the server".
  **Why it matters:** a server that rejects bad input with -32001 gets inconclusive instead of pass under `is_error`: the opposite wrong verdict, and CI goes red. A server that fails a valid call with -32001 gets inconclusive instead of fail.
  **Options:**
  - (a) Drop `types.REQUEST_TIMEOUT` from `_SDK_LOCAL_CODES` and keep `CONNECTION_CLOSED`. Add a comment that the entry must come back if an SDK read timeout is ever armed.
  - (b) Keep it as a guard against a future read timeout, and accept the wrong verdict for servers that send -32001.

  Option (a) reverses the -32001 half of your rule that -32000/-32001 are never credited, so it is your decision. The -32000 half is not affected. Whichever you pick, pin it with a real-connector test: a broken_server tool that raises `RpcError(-32001, ...)` under `is_error`.
  **How sure:** not validated. It is P3 and report-only, so it was not in the validator batch. I re-read the SDK lines above in the installed mcp 2.2.0, and they say what the finding claims.

### Actionable Findings

| # | File | Issue | Route | Notes |
|---|------|-------|-------|-------|
| 5 | `src/mcp_eval/connector.py:77` | Malformed reply graded inconclusive instead of fail | `manual -> downstream-resolver` | `suggested_fix` present; confirmed by the validator; `requires_verification: true` (add the real-connector test) |

Outside this queue: #7 is a decision for you (`manual -> human`).

### Pre-existing Issues

These do not count toward the verdict. Both are false passes that are real in the current code. Neither came from this diff, and both passed at base `fc25f52` too. They are the next wrong-verdict bugs in line after this change.

| # | File | Issue | Reviewer |
|---|------|-------|----------|
| 2 | `src/mcp_eval/graders.py:52` | A server that never routes `tools/call` passes every `is_error` test | adversarial (validator: real, pre-existing) |
| -- | `src/mcp_eval/graders.py:54` | An `is_error` test with an unknown or typo'd tool name passes | fast-pass, adversarial, correctness |

- **#2** -- **What happens:** any JSON-RPC error reply earns an `is_error` pass (graders.py:52-53). That includes codes that mean the server never looked at the arguments: -32601 Method not found, -32600 Invalid Request, -32700 Parse error. adversarial reproduced it with a server that answers every `tools/call` with -32601 and advertises no tools. An `is_error`-only suite scored 2/2 PASS and exited 0.
  **Why it is here and not a finding:** the validator reproduced it on the reviewed tree. It rejected it as a finding of this diff because the verdict did not change. At base, connector.py had no `MCPError` handler, so every JSON-RPC error fell into `except Exception` with `completed=False`, and `_is_error` returned pass ("rejected at protocol level"). I checked that against the base side of the diff. The diff narrows the surface (timeouts, broken sessions, -32000 and -32001 are no longer credited) but leaves this path open.
  **Two things in this diff touch it.** The new README row ("with an error result or a JSON-RPC error reply") now states the behavior as the contract. And `test_json_rpc_error_reply_is_a_server_rejection` (tests/test_harness.py:111-121) pins only -32602, so no test would catch this.
  **Fix, as its own change:** in graders.py `_is_error`, grade -32700, -32600 and -32601 as fail ("server did not evaluate the input"). Add a real-connector test with a broken_server tool that raises `RpcError(-32601, ...)`. Both changes stay in graders.py and the tests.
- **Unknown tool name (the preliminary P1 shown during the run)** -- **What happens:** `examples/broken_server.py:121` answers any unknown tool with `isError: true`, and graders.py:54-55 credits any `isError` result. So an `is_error` test with a typo'd tool name passes, though the server never evaluated the input. adversarial reports that the SDK's own server does the same (`ToolError("Unknown tool: ...")` at `mcp/server/mcpserver/tools/tool_manager.py:72`). The same branch credits a blanket configuration error, or a tool crash reported as an error result. Reviewers also note that the rpc branch credits -32603 handler-crash replies.
  **Why it is here and not a P1:** these lines are unchanged by this diff. The path entered the run as a fast-pass preliminary at anchor 50, below the report threshold, and fast-pass never counts toward promotion.
  **Fix:** closing it needs a `tools/list` preflight or a per-tool positive control. A preflight needs a hook before the test loop, and CLAUDE.md marks session ownership (runner.py vs connector.py) as a pending design decision. Ask before building it.

### Coverage

- Report-only: `mode.apply_local` is false, so Stage 5c did not run and no file was changed.
- Run artifacts: the review's working files (reviewer outputs, merged findings, validator verdicts, metadata) were kept in a temporary run directory outside the repository. This file is the saved copy of the report.
- Cross-model: cross-model pass: not run (no different-provider route installed: codex, grok, and cursor-agent are absent; claude is the host's own family; opencode is installed but is not in the default route order and was not requested). The adversarial lens ran in-process on the session model.
- Validator:
  - One batch of 2 findings (#2 P1, #5 P2). The verdicts landed within the bound.
  - Confirmed 1: #5 (incidence across real servers not measured).
  - Rejected 1: #2. It is real on the reviewed tree but predates the diff with the same verdict, so it moved to Pre-existing Issues.
  - Unresolved 0, malformed 0, failed 0.
  - Shortcut-skipped 0. No finding had cross-model corroboration, because no different-provider peer ran.
  - #7 (P3, owner human, report-only) was not selected.
- Protected subjects: I classified #2 and #5 as wrong-verdict grading defects. Neither falls under the eight protected subjects, and the validator returned `null` for both. No reclassification. #2's rejection is an ordinary rejection, and I checked its provenance claim against the base side of the diff.
- No P0/P1 finding is left with degraded validation.
- Suppressed: 1 finding at anchor 50, the fast-pass preliminary P1 on graders.py:54. It is reconciled under Pre-existing Issues.
- Mode-aware demotion: 4 of 7 candidates left the primary set before validation, which is why the numbers skip.
  - #1 (testing P1, -32001 branch untested) became a testing gap. The SDK cannot raise -32001 locally in this setup (see #7), so the missing test does not reopen a false pass.
  - #3 (hang fixture timing) became a residual risk.
  - #4 (CLI exit code untested) and #6 (hang row unasserted) became testing gaps.
- Semantic reconciliation merged 6 reviewer findings into 3 (#3, #5, #7). All reviewers ran in-process on one serving model, so their agreement is recorded but raised no confidence.
- Quote-the-line check: 0 findings at anchor 75/100 demoted for missing `first_evidence`; `first_evidence_backfilled`: 0; malformed findings 0; malformed returns 0.
- Evidence corrections: the test references in #2 and #7 now cite file lines (tests/test_harness.py:111-121 and 61-69) instead of diff lines.
- Full roster ran (lite path refused: 258 executable changed lines, 2 uncounted files). No reviewer failed or timed out.
- An API rate limit cut off the first merge attempt before it wrote its outputs. A second run finished from the saved intermediates.
- Standards criteria came from the instruction-file fallback: CLAUDE.md at the repo root (no CODING_STANDARDS.md exists). CLAUDE.md is untracked in git but present in the reviewed working tree.
- Untracked files excluded from review scope: CLAUDE.md.
- The diff includes your earlier uncommitted edits to examples/broken_server.py and suites/broken_server.yaml (the `hang` tool and its suite entry). They predate the defect 2 work but ride in the same working tree.
- No plan document was discovered (the approved plan was an in-conversation brief), so requirements completeness and settlement suppression were not evaluated.
- Residual risks:
  - **The -32000 trade-off is deliberate.** A correct server that rejects bad input with -32000 (the generic JSON-RPC "Server error" code, a common library default) always grades inconclusive and keeps CI red. That is your stated design, pinned by `test_error_code_shared_with_the_sdk_is_not_credited` (tests/test_harness.py:61-69). A discriminator exists: after an `MCPError(-32000)`, send a bounded `session.send_ping()`. The SDK raises its local `CONNECTION_CLOSED` only after the dispatcher has closed, so a successful ping shows the server sent the -32000.
  - **SDK version coupling.** `_SDK_LOCAL_CODES` and `from mcp import MCPError` assume mcp 2.2.0, whose only local codes are -32000 and -32001. pyproject.toml declares `mcp>=1.2.0`, where `MCPError` is not exported. Today that fails loudly at import. A future SDK could add local codes, and the harness would credit them as server rejections. Raising the floor to the version the code targets closes this.
  - **The hang row never exercises the timeout path in the default run.** `hang` sleeps 5s (examples/broken_server.py:103) against the 10s default, so `hang-responds-in-time` grades PASS. Its description (suites/broken_server.yaml:50) reads like the stalled-call case. The grading is correct, since CLAUDE.md:59-64 documents the 5s sleep with `--timeout 2` as the reproduction. Rewording the description stops a reader from mistaking the PASS for a false pass. Do not lengthen the sleep: that would pull the out-of-scope defect 1 into the next row of the default run.
  - **The handshake has no timeout** (pre-existing, out of scope). `open_stdio_session` awaits `session.initialize()` (connector.py:31) with no bound. A server that never finishes the handshake hangs the whole run instead of producing inconclusive verdicts.
- Testing gaps:
  - No real-connector test has the server process exit mid-call under `is_error`, the crash half of the original defect 2. The blind catch at connector.py:77-85 is covered only by synthetic grader tests. Mirror `test_timeout_under_is_error_is_not_credited_as_a_rejection` with a broken_server tool that exits mid-call.
  - The exit-code change at cli.py:40 has no test. Add a `typer.testing.CliRunner` test: an inconclusive-only run must exit 1, and an all-pass run must exit 0.
  - `test_broken_server_is_caught` (tests/test_harness.py:14-24) asserts nothing for the two new suite rows. Assert `strict-echo-missing-required-arg` and `hang-responds-in-time`.
  - No test sends an `MCPError` with code -32001 through `run_test_case`. #7's fix carries that test for either decision.

---

> **Verdict:** Ready with fixes
>
> **Reasoning:** No P0 or P1 is open. The defect 2 false pass is closed for timeouts and broken sessions, an inconclusive run exits 1, and every change stayed in its stage. One confirmed regression remains: #5 turns a malformed server reply from fail into inconclusive and blames the transport. #7 needs your decision on -32001. Two false passes remain in the code (#2 and the unknown-tool path). Both predate this diff and do not count toward this verdict, but they are the next wrong-verdict bugs to fix.
>
> **Fix order:** #5 (connector.py -> models.py -> graders.py, plus a real-connector test) -> decide #7 and pin it with a -32001 test -> as a separate change, the pre-existing #2 code filter in graders.py. Ask before any `tools/list` preflight.

### Actionable Findings (summary)

- #5 -- P2 -- `src/mcp_eval/connector.py:77` -- Malformed server reply graded inconclusive as "answer not observed" instead of fail -- `manual` -> downstream-resolver -- `suggested_fix` present -- confidence 100 -- validator confirmed
- Decision outside the queue: #7 -- P3 -- `src/mcp_eval/connector.py:22` -- server-sent -32001 labelled a timeout -- `manual` -> human
