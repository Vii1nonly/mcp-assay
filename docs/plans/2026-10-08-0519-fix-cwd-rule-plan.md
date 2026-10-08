---
title: The Server Working-Directory Rule - Plan
type: fix
date: 2026-10-08
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# The Server Working-Directory Rule - Plan

## Goal Capsule

- **Objective:** A suite's server starts in the same folder no matter how the suite file is named on the command line, and that folder follows one documented rule.
- **Means:**
  - One resolution step in `suite.py` at load (KTD1).
  - A refusal at load for a cwd that is not a folder (KTD2).
  - An explicit `cwd: ..` in both shipped suites (KTD3).
  - The `server:` block documented in the README with a complete example (KTD4).
- **Authority:** the owner's N6 instructions of 2026-10-08 (decisions 1-7), then `CLAUDE.md`, then `docs/reviews/2026-09-29-readiness-review.md` (v0.1.1 item 6).
- **Stop and ask if:** any verdict or reason on a shipped suite changes, or the fix needs a change outside load, the shipped suites, the docs and `.gitignore`.
- **Execution profile:** inline, tests first. Commit on `n6-cwd-rule`. Do not merge or push.

---

## Product Contract

### Problem Frame

`load_suite` fills a missing `cwd` with `path.parent.parent.resolve()`. It climbs two folders before resolving, so the answer depends on how the path was written:

- `suite.yaml`, loaded from the project root, climbs from `.` and resolves to the project root.
- The same file by absolute path climbs to the project's parent folder.

The two shipped suites only work because they sit exactly one folder down. The comment ("relative to the suite") does not describe the code. A `cwd:` that is written is passed through untouched, so it is read relative to wherever the user happened to run the command. A missing folder only fails later, when the server process cannot start. A first-user tester also found the suite format undocumented.

### Requirements

- R1. With no `cwd:`, the server starts in the suite file's own folder, resolved before any step up (`path.resolve().parent`).
- R2. A relative `cwd:` is read relative to the suite file's folder. An absolute `cwd:` keeps its folder. Either is resolved once, at load.
- R3. The same suite file gives the same cwd by relative and by absolute path, for a nested suite and for one in the project root.
- R4. A `cwd` that does not exist, or is not a folder, is refused at load. The refusal is one line naming the resolved path, and the CLI exits 2.
- R5. With `--command`, the suite's server block is not used and the server starts in the current working directory.
- R6. Both shipped suites gain `cwd: ..`. Their verdicts and reasons stay identical.
- R7. The README documents the `server:` block (command, args, cwd and the relative-path rule) with one complete example suite file. CLAUDE.md changes only where its text would become wrong.
- R8. `.coverage` is git-ignored.

### Scope Boundaries

- No change to the connector, runner, graders, report or JSON shape.
- No `~` or environment-variable expansion in `cwd`.
- No new server keys (such as `env`).

---

## Key Technical Decisions

- **KTD1. Resolve in `load_suite`, store as a string.** `ServerSpec.cwd` stays `str | None`. After the rule checks, `suite.server.cwd` becomes `str((path.resolve().parent / (cwd or "")).resolve())`. Joining an absolute path replaces the base, so one expression covers R1 and R2. The runner and connector already pass `server.cwd` through unchanged.
- **KTD2. Refuse through `_refusal`.** The message is `"{path}: server.cwd: folder {resolved} does not exist"`, or `"... {resolved} is not a folder"` when the path exists. With no `cwd:` written, the suite's own folder always exists, so the check only fires for a written one. `resolve()` is wrapped against `OSError` and `ValueError`, so an unusable name is refused rather than crashing.
- **KTD3. `cwd: ..` in the shipped suites.** Both suites live in `suites/` and name repo-root paths (`examples/broken_server.py`, `.`). `..` keeps them in the repo root under the new rule.
- **KTD4. The README example is tested.** One test loads the README's example suite from a temporary `suites/` folder, so the documented format cannot drift from what the loader accepts.
- **KTD5. `--command` keeps `cwd=None`.** `ServerSpec(command=..., args=...)` already leaves `cwd` unset, and the MCP SDK then inherits the process cwd. This is tested end to end, not changed.

---

## Implementation Units

### U1. Tests first (red)

`tests/test_suite.py`:
- Same cwd by relative and absolute path, parametrised over a suite in the project root and in `suites/`. Uses `monkeypatch.chdir` into a temporary project. Both must equal the suite's own folder.
- A relative `cwd:` is read from the suite's folder even when the process runs elsewhere.
- An absolute `cwd:` keeps its folder.
- A missing cwd and a file-as-cwd are refused in one line naming the resolved path. The CLI exits 2 with nothing on stdout.
- Both shipped suites start in the repo root.
- The README example suite loads.

`tests/test_cli.py`:
- `--command` with a suite whose `cwd` points at an empty folder. The process cwd is the repo, so the server is found and both tests pass.

### U2. Build

`suite.py` (KTD1, KTD2, corrected comment), `suites/*.yaml` (KTD3), `.gitignore`.

### U3. Docs

A README section "Writing a suite": the `server:` keys, the cwd rule, `--command` behaviour and a complete example. CLAUDE.md gets one line under Invariants for the cwd rule.

---

## Verification

- `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- Both shipped suites: verdicts and reasons identical to the baselines recorded before the change (`broken_server` exit 1, `filesystem` exit 0).
- CLI output for a suite in the project root, a suite with a relative `cwd:`, and a suite whose cwd does not exist.
- `/ce-code-review`, fix confirmed findings, privacy scan of the diff, commit on the branch.
