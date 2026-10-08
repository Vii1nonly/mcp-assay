---
title: Connector Owns the Test Loop (ADR 0001 Step 1) - Plan
type: refactor
date: 2026-09-29
refreshed: 2026-10-08
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Connector Owns the Test Loop (ADR 0001 Step 1) - Plan

## Goal Capsule

- **Objective:** Every result in a scorecard names the server session that produced it, and every existing suite still gets exactly the verdicts it got before.
- **Means:** Move the per-suite test loop from `runner.py` into a new `connector.run_tests` that records a session number on each result (KTD1). Grading stays in `runner.py` through `grade_safely` (KTD4).
- **Authority:** the owner's N7 instructions of 2026-10-08, then `CLAUDE.md`, then `docs/adr/0001-recovery-after-timeout.md`, then this plan.
- **Stop and ask if:** the work needs a ping, a restart, a second session, the handshake bound, or any change to `graders.py`; or any existing verdict, reason or exit code changes.
- **Execution profile:** inline, tests first. Commit on `n7-connector-owns-loop`. Do not merge or push.

## Refresh Notes (2026-10-08)

This plan was written before N2-N6 and v0.1.1. Refreshed against main `4be2215`:

- **U1 (Python floor 3.12) dropped:** `pyproject.toml` already says `requires-python = ">=3.12"` (shipped in v0.1.1). R6 and KTD6 are removed.
- **Grading goes through `grade_safely`:** N3 added `runner.grade_safely`, so one grading crash costs only that test. KTD4 and U3 now keep it; a bare `grade()` would undo N3.
- **cwd arrives resolved:** since N6, `load_suite` resolves and checks `server.cwd`, and `--command` leaves it `None`. `run_tests` passes it through unchanged.
- **Package and command renamed:** `mcp_eval` is now `mcp_assay`, and the command is `mcp-assay`.
- **Verification:** the `--json` output must differ from before only by the new `session` key on each result; both shipped suites must keep identical verdicts, reasons and exit codes.
- **Delivery:** commit on the branch (the owner reviews and merges), instead of "do not commit".

## Product Contract

### Summary

This is step 1 of ADR 0001. The connector takes over running a suite's tests in a single loop, and each result carries its session number, which is always 1 for now. The example server gains the two test tools that step 2's recovery will need. No verdict changes.

### Problem Frame

ADR 0001 fixes defect 1, where a timed-out call stalls the tests after it, by recovering in the connector. That needs the connector to own the loop, and needs results to show which session they ran on. Doing the restructure alone first keeps the behavior change in step 2 reviewable on its own.

### Requirements

**Loop ownership**
- R1. The connector runs a suite's tests and hands back the results and the negotiated protocol version. The runner only grades (through `grade_safely`) and builds the scorecard. `runner.py` imports and touches no MCP protocol object.
- R2. Every existing suite produces the same verdicts, reasons, and exit code as before.
- R3. Every result records the number of the server session it ran on. In this step that is always 1.
- R4. The `--json` scorecard differs from before only by a `session` key on each result's execution.

**Test fixtures for recovery**
- R5. The example server's `hang` tool accepts an optional `seconds` argument and still sleeps 5 seconds when it is absent.
- R6. The example server has a `crash` tool that ends the server process mid-call without replying.

### Scope Boundaries

#### Deferred to Follow-Up Work

The later ADR 0001 steps, in order:

| Step | Builds | ADR tests it carries |
|---|---|---|
| 2 | Outer restart loop around this step's single loop; a ping bounded by the per-call `--timeout` after any unobserved result; restart when the ping fails; the `recovery` note on the first result after a restart | Slow tool followed by echo and get_status; short stall stays on session 1; `ambiguous_reject` does not restart; crash mid-suite; timeout on the last test; failed restart |
| 3 | Handshake bounded at 60s with `asyncio.timeout`; `ServerStartError`; CLI exit code 2 on start failure | Server that exits at start; missing command; stalled handshake |

- The stateful safeguard for write-then-read suites after a restart is a later follow-up, per the ADR.
- The unknown-tool `is_error` false pass needs a `tools/list` preflight and is not part of ADR 0001.

## Planning Contract

### Key Technical Decisions

- KTD1. **`connector.run_tests` owns the loop and takes plain values.** It receives the server command, arguments, working directory, the test cases, and the timeout, and returns the execution results plus the protocol version. It does not take a `ServerSpec`, so `connector.py` keeps importing nothing from `suite.py`. The cwd is passed through as `load_suite` resolved it. (session-settled: user-approved — chosen over recovery in `runner.py` or a connector-owned session class: accepted in ADR 0001 for a clean protocol boundary with a small diff)
- KTD2. **A single loop in this step.** One session, one pass over the tests, no outer loop. (session-settled: user-directed — chosen over building the ADR's two-level restart loop now: structure with nothing to do yet waits for step 2)
- KTD3. **`session` is a required integer on `ExecutionResult`, with no default.** Every place that builds a result must state it, so a result can never silently claim session 1 once step 2 adds session 2. `run_test_case` receives the session number from `run_tests` and passes it into every result it builds. (session-settled: user-directed)
- KTD4. **Grading stays in `runner.py` through `grade_safely`, after `run_tests` returns.** The runner grades each result in order and builds the scorecard as today. `graders.py` does not change. (session-settled: user-directed — chosen over a bare `grade()`: N3's guard keeps one grading crash from losing the other verdicts)
- KTD5. **Test tools land before recovery.** `hang` takes `seconds`, and `crash` exits the process immediately without writing a reply. (session-settled: user-directed — chosen over adding them together with recovery: the fixtures are proven on today's behavior before recovery depends on them)
- KTD6. **Step 3's handshake bound uses `asyncio.timeout`.** Recorded here so step 3 follows it; the project already requires Python 3.12. (session-settled: user-directed — chosen over `asyncio.wait_for`)

### Assumptions

- The crash tool's exact exception on the client side (an SDK connection-closed error or another transport exception) is observed during implementation. Either one is recorded as `transport_error`, which is what the tests assert.
- Grading now runs after the session closes instead of between calls. Grading is a pure function of the `ExecutionResult`, so verdicts cannot depend on that order.

## Implementation Units

### U1. Test tools: `hang(seconds)` and `crash`

- **Goal:** The example server can stall for a chosen time and can crash mid-call, and today's grading of both is pinned by tests.
- **Requirements:** R5, R6, KTD5
- **Dependencies:** none
- **Files:** `examples/broken_server.py`, `tests/test_harness.py`
- **Stage:** test fixture and harness tests only. No `src/` change.
- **Approach:**
  1. `hang` reads an optional numeric `seconds` from its arguments and defaults to 5. Its input schema declares the optional property.
  2. Add `crash`, which exits the process immediately with a non-zero status and writes no reply. List it in `TOOLS` and the module docstring.
- **Execution note:** Characterization. The tests describe how today's code grades these tools, so step 2 has a known baseline.
- **Test scenarios:**
  - `hang` with `seconds` 0.2 under `no_error` answers: outcome `answered`, verdict `pass`.
  - `crash` under `no_error`: outcome `transport_error`, verdict `inconclusive`.
  - `crash` under `is_error`: verdict `inconclusive`, never `pass`.
  - `hang` with no argument still sleeps 5s: covered by the unchanged timeout test and the shipped suite.
- **Verification:** the new tests pass, and existing tests and suite verdicts are unchanged.

### U2. Connector owns the loop and records the session

- **Goal:** `run_tests` runs the suite in `connector.py`, every result carries `session`, and the runner only grades.
- **Requirements:** R1, R2, R3, R4, KTD1, KTD2, KTD3, KTD4
- **Dependencies:** order after U1 so the new crash tests also run through the new loop.
- **Files:** `src/mcp_assay/models.py`, `src/mcp_assay/connector.py`, `src/mcp_assay/runner.py`, `tests/test_harness.py`, `tests/test_report.py`
- **Stage:**
  - `models.py` (the objects): gains the `session` field.
  - `connector.py` (transport): gains `run_tests`, the only place a session is opened for a suite.
  - `runner.py` (runner): stops touching protocol objects; grades through `grade_safely`.
- **Approach:**
  1. `ExecutionResult` gains a required integer `session` (KTD3).
  2. `run_test_case` takes the session number and sets it on every result it builds.
  3. `run_tests(command, args, cwd, tests, timeout)` opens one session with `open_stdio_session`, runs each test in order through `run_test_case` with session 1, and returns the results and the protocol version (KTD1, KTD2).
  4. `run_suite` keeps its signature, calls `run_tests` with `server.command`, `server.args`, `server.cwd`, grades each result through `grade_safely`, and builds the scorecard (KTD4).
  5. The test helpers that build an `ExecutionResult` directly (`_execution` in `test_harness.py`, `_result` in `test_report.py`) set `session` to 1.
- **Execution note:** Test-first. Add the session assertions and watch them fail before the field exists.
- **Test scenarios:**
  - Running `suites/broken_server.yaml` gives every result `session` 1, with the verdicts `test_broken_server_is_caught` already asserts.
  - `run_tests` called directly returns one result per test, in order, each with session 1, and the negotiated protocol version.
  - `ExecutionResult` without `session` is refused (no default).
  - `runner.py` source imports nothing from `mcp`.
  - Every existing real-connector test and every `grade_safely` test still passes unchanged.
- **Verification:** `runner.py` imports nothing from `mcp`, `graders.py` has no diff, and the suite runs show the same verdicts as before.

## Verification Contract

| Check | Command | Expected |
|---|---|---|
| Harness tests | `uv run pytest` | all pass |
| Lint | `uv run ruff check .` | clean |
| Format | `uv run ruff format --check .` | clean |
| Broken server, default timeout | `uv run mcp-assay run suites/broken_server.yaml --json ...` | same verdicts, reasons, exit 1; every result session 1 |
| Broken server, short timeout | `uv run mcp-assay run suites/broken_server.yaml --timeout 2` | same verdicts as before: defect 1 is not fixed in this step |
| Real third-party server | `uv run mcp-assay run suites/filesystem.yaml --json ...` | 6/6 passed, exit 0; every result session 1 |
| JSON shape | key diff of before/after `--json` | only `session` added on each result's execution |

## Definition of Done

- U1 and U2 are complete and every check in the Verification Contract holds.
- No existing verdict, reason, or exit code changed.
- `graders.py` is unchanged, and `runner.py` no longer imports from `mcp` or touches `ClientSession` / `InitializeResult`.
- No outer loop, ping, restart, or handshake bound exists yet.
- `/ce-code-review` has run and its confirmed findings are fixed; committed on the branch, not merged or pushed.
