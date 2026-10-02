# Readiness review: is mcp-eval good enough to use?

Date: 2026-09-29
Version reviewed: v0.1.0 (main, commit ae6b952)

## Verdict

mcp-eval is usable today by its author, and by a careful MCP server author who
reads the code, as a local spot-check of hand-written suites against stdio
servers. On well-formed suites against the four real servers tested
(server-filesystem, server-everything, server-memory, mcp-server-time), every
verdict was correct except one: an `is_error` test that names an unknown tool.
The harness also caught a real bug in server-memory.

It is not yet ready as a CI gate, as a security sign-off, for strangers writing
suites from the README, for servers that need API keys or use HTTP, or for agent
and cost evaluation. The main reasons: unvalidated suite input and a too-generous
`is_error` rule produce false passes; a harness crash loses every result; and a
server that never finishes its handshake hangs the run.

## Scorecard (1–5)

| Dimension | Score | Reason |
| --- | --- | --- |
| Verdict trust, well-formed input | 4 | Outcome model is sound; `is_error` credit rule is too broad |
| Verdict trust, messy input | 2 | Typo'd keys and unknown tools become false passes |
| Robustness | 2 | Unbounded handshake; grading errors and console encoding lose all results |
| Usability for a stranger | 2 | Long tracebacks, wrong `mcp` floor, cwd rule, thin suite docs |
| CI readiness | 2 | Exit codes exist; no workflow, versioned JSON or CLI tests |
| Portfolio signal vs stated goal | 3 | Strong reliability design; no agent or cost yet |

## Top findings

1. An `is_error` test on an unknown tool passes. Real servers answer with
   `isError: true`, not JSON-RPC -32601. Fix: a `tools/list` preflight.
2. Suite files are not validated. A typo such as `args:` for `arguments:` is
   silently dropped, `{}` is sent, and the server's rejection is credited as a pass.
   Duplicate ids, empty suites and misplaced `schema:` keys are also accepted.
3. `is_error` credits -32603 internal errors and a server that errors on every
   call; `schema_valid` passes results flagged `isError: true`.
4. JSON Schema `format` is not enforced and `$schema` is ignored. An invalid
   schema aborts the whole run with a traceback.
5. The JSON report is written after console rendering, so a Unicode error on a
   piped Windows console, or a missing output directory, loses every verdict.
6. The handshake has no timeout, and a server that fails to start produces a
   200+ line traceback with exit code 1 (the same code as a failed test).
7. `pyproject.toml` declares `mcp>=1.2.0`; the code needs SDK 2.x.
8. The server cwd is the suite file's grandparent, which is wrong for a suite
   in the project root.
9. The console shows reasons only for failures, and reasons omit the server's
   message, so a server that denies every request looks perfect.
10. There is no CI workflow, `cli.py` and `report.py` have no tests, and
    `suites/filesystem.yaml` uses Windows-only paths.

Defect 1 (a stall or crash spoils later tests) is real but was not reproduced
on the concurrent Node servers. It produces inconclusive verdicts, not false
passes. Its design is accepted in ADR 0001.

## Roadmap

Order principle: fix false passes and lost results first, then the in-flight
ADR 0001 steps, then features.

### v0.1.1: trust fixes (before ADR 0001 step 1)

- Set the dependency floor to `mcp>=2.2,<3`.
- Strict suite validation at load (`extra="forbid"`, schema rules, unique ids,
  non-empty tests, `check_schema`); an invalid suite exits 2 with one line.
- Never lose verdicts: check the output path first, write JSON before
  rendering, guard each grade, use a UTF-8-safe console.
- Tighten grading: `schema_valid` fails on `isError: true`; -32603 is not
  credited; `format` is checked; `$schema` is honoured.
- Show a reason, including the server's message, for every verdict.
- Fix the cwd rule and set `cwd:` explicitly in both suites.

### v0.1.2: ADR 0001 plus preflight

- ADR step 1: the connector owns the test loop (planned, behaviour-neutral).
- `tools/list` preflight inside `connector.run_tests`; an unlisted tool is a
  suite error.
- ADR step 3: 60s handshake bound, `ServerStartError`, exit code 2.
- ADR step 2: ping and restart after an unobserved result.
- CI on Ubuntu and Windows; CLI tests; portable filesystem suite.

### v0.2: features

- Assertions on error message and content text.
- Validate against the server's own `outputSchema`.
- Generate negative suites from each tool's `inputSchema`.
- `env:` for servers; versioned JSON; quiet mode.
- HTTP transport, then a GitHub Action with SARIF output.

### Later

- `tools/list` snapshot diff (rug-pull detection) and scorecard diff.
- A security canary pack based on the MCPSecBench taxonomy.
- Agent and cost evaluation through integration (for example an Inspect task
  against the same servers), not a new agent framework.

## Positioning and name

Proposed positioning: a deterministic contract test for MCP servers, with no
LLM, no API key, and verdicts that do not turn timeouts, typos or missing tools
into passes. The name `mcp-eval` collides with lastmile-ai/mcp-eval and with
a PyPI package; a rename is recommended (candidates: mcp-assay, mcp-verdict).

## How this review was produced

A panel of four agents, each with one brief: a field tester (four real
servers, verdicts checked by hand), an adversarial verdict auditor (a
purpose-built misbehaving server plus an official-SDK control), a first-user
and engineering-quality tester (working from the public repository), and a
landscape scout (comparable tools and open issues). A judge checked the key
claims against the source code, resolved disagreements, and wrote this review.
Two claims were verified independently: the name collision and the open
conformance-suite issue #515. Security prevalence statistics cited by the scout
were not verified.

Evidence (transcripts, suites and scorecards) was kept in local scratch
directories and is not committed.

## Decisions

Recorded 2026-10-02 by the project owner.

1. **-32603 is not credited under `is_error`.** An internal error does not show
   the server evaluated and refused the input. This confirms the v0.1.1 grading
   item above.
2. **ADR 0001 step 3 may land before step 2.** The handshake bound,
   `ServerStartError` and exit code 2 do not depend on ping-and-restart, matching
   the v0.1.2 order above.
3. **Positioning: a deterministic contract test for MCP servers.** This replaces
   the "reliability eval harness" wording now in the README.
4. **Rename to `mcp-assay` now.** The rename itself (package, CLI command,
   README) is a separate change; until it lands, the code still says `mcp-eval`.
