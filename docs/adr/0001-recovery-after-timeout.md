# ADR 0001: Recovery after a timed-out or failed call

Status: accepted
Date: 2026-09-28

## Context

The harness runs all tests in a suite over one stdio session. When a call times out,
the SDK drops the late reply, but the server may still be busy. A single-threaded
server (examples/broken_server.py `hang`) leaves later requests unread in the pipe, so
every test sent before it wakes also times out. With `--timeout 2` the test after
`hang` grades INCONCLUSIVE; with the default timeout the same test FAILs for its real
reason (a schema violation). A crashed server has the same effect: every later test
inherits `transport_error`. Since defect 2 was fixed, the symptom is a wrong
inconclusive, not a wrong pass. It is still a wrong verdict.

## Constraints

- connector.py is the only module that touches the MCP protocol.
- Every object keeps the one before it, so a verdict traces back to the raw exchange.
- Suites are YAML data; graders must not change for a new transport.
- Changes are surgical and reviewable by the owner, section by section.
- A wrong verdict in either direction is the worst bug. Lost coverage (inconclusive)
  is acceptable; a false pass or false fail is not.

## Options considered

1. **Recovery in runner.py.** Small diff, but moves session lifecycle and recovery
   policy into runner, which already holds protocol objects.
2. **Connector-owned session class.** Clean boundary, but a mutable class with
   manual exit-stack handling that must stay in one task; the largest diff.
3. **Per-test isolation by default, with opt-in shared/sequence modes.** Strongest
   isolation, but about 3x slower on npx servers, three modes to maintain, and a
   stateful suite that forgets to declare `sequence` gets a deterministic false fail.
4. **Always restart / circuit breaker.** Rejected: always restarting pays startup
   after every error reply and discards state. A circuit breaker turns one slow tool
   into N inconclusives.

## Decision

connector.py gains `run_tests(...)`, which owns the loop over tests. The outer loop
opens a session with `async with open_stdio_session(...)`; the inner loop runs tests.
After any result whose outcome is not `answered`, and only if tests remain, the
connector sends one ping bounded by the per-call timeout:

- Any reply the server sent, including a JSON-RPC error such as -32601, means alive:
  keep the session.
- No reply, an SDK-local code (-32000/-32001), or any other exception means stuck or
  dead. The connector breaks out of `async with`; SDK teardown closes stdin, waits 2s,
  then kills the process tree. The outer loop starts a fresh server.

The handshake is bounded separately (60s) so a short `--timeout` cannot kill a slow
npx start. If the first start fails, `ServerStartError` is raised and the CLI prints
a clear message and exits with code 2 (code 1 stays reserved for failed or
inconclusive tests). If a restart fails, the remaining tests are recorded as
`transport_error` and grade inconclusive. Every `ExecutionResult` records its
`session` number; the first result after a restart carries a `recovery` note naming
the test that caused it. runner.py calls `run_tests` and grades. Graders do not
change.

Resolved choices:
- Ping bound: the per-call `--timeout`, not a separate setting.
- Server start failure: exit code 2.
- Stateful safeguard (write-then-read suites after a restart): a later follow-up,
  not part of this change.
- Python: 3.10 is not supported; `requires-python` is raised to `>=3.12`.

## Consequences

Good:
- The test after a stall or crash reports its own verdict.
- The happy path costs nothing.
- A stall that ends within the ping bound keeps the session and its state.
- runner.py no longer touches protocol objects.
- The harness never hangs.
- A server that fails to start gives a message, not a traceback.
- Every restart is visible in the result and in the report.

Bad:
- A stuck test costs up to timeout + timeout + about 2s + startup (about 22s at the
  default).
- A restart discards in-memory state, so a write-then-read suite can false-fail after a
  restart. It is flagged, not prevented; a stateful safeguard is a follow-up.
- A server that answers ping but has wedged its tool workers yields inconclusives
  rather than a restart.
- On a concurrent server a timed-out call may keep running.
- A server that answers ping with -32000 is restarted unnecessarily.
- POSIX behaviour is not yet verified.

Unchanged and still open: an unknown-tool `isError` result still passes an
`is_error` test (defect-2 review follow-up). The -32601/-32600/-32700 case is fixed.

## Test plan

All tests use the real connector and examples/broken_server.py:
- `hang` gets an optional `seconds` argument.
- A new `crash` tool is added.

Regression tests:
- A slow tool followed by echo and get_status (timeout 1s): echo passes, get_status
  fails for the schema reason, session 2.
- A short stall that ends within the ping bound: session 1.
- `ambiguous_reject` followed by echo: no restart.
- A crash mid-suite: the next test passes on session 2.
- The happy path: all results on session 1.
- A timeout on the last test: no recovery.
- A failed restart: the remaining tests are inconclusive.
- A server that exits at start, a missing command, or a stalled handshake:
  `ServerStartError`.

## Evidence (Windows 11, Python 3.13, mcp 2.2.0)

- Reproduction: with `--timeout 2`, hang and get-status are INCONCLUSIVE; with the
  default timeout, get-status FAILs for its real schema reason.
- Ping on a blocked single-threaded server: answered only when the sleep ended, at
  5002ms. On a concurrent SDK server, ping took 2–3ms and the next call 1–2ms.
- Closing a busy server costs a fixed 2.02s (SDK grace period, then Job Object kill);
  an idle close takes 20–50ms. Reopen takes about 60ms.
- Crash: `transport_error` in 3ms; ping fails with -32000 in 0.1ms.
- A server that fails during open raises a nested ExceptionGroup;
  a missing command raises FileNotFoundError.
- filesystem server via npx: about 1.2s per start warm, 5–6s cold. The 6-test suite
  takes 2.5s on a shared session versus about 7.7s estimated per-test.
