"""The four objects that flow through the pipeline.

TestCase -> ExecutionResult -> GradedResult -> Scorecard

Each stage keeps the object before it, so any failure can be traced back to
the raw exchange that produced it.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CheckType = Literal["no_error", "is_error", "schema_valid"]
# inconclusive: the harness never observed the server's answer, so it can
# neither credit nor blame the server.
Verdict = Literal["pass", "fail", "inconclusive"]
# answered: a reply arrived (a result or a JSON-RPC error).
# timeout: no reply before the deadline. transport_error: the session broke.
Outcome = Literal["answered", "timeout", "transport_error"]


class Expectation(BaseModel):
    """What "correct" means for one test case."""

    # Suites are written by hand: an unknown key is a mistake, not something to drop.
    model_config = ConfigDict(extra="forbid")

    type: CheckType
    # Only used by schema_valid: the JSON Schema the result must satisfy.
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    expect: Expectation
    arguments: dict[str, Any] = Field(default_factory=dict)
    category: str = "correctness"
    description: str = ""


class ExecutionResult(BaseModel):
    """What actually happened when the test case was run."""

    test_case: TestCase
    latency_ms: float
    outcome: Outcome
    # True when the server returned a result explicitly flagged as an error.
    is_error: bool = False
    # Set when the server answered with a JSON-RPC error instead of a result.
    rpc_error_code: int | None = None
    # Set when a reply arrived but broke the result shape the protocol requires.
    protocol_violation: str | None = None
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured: dict[str, Any] | None = None
    error_message: str | None = None


class GradedResult(BaseModel):
    execution: ExecutionResult
    verdict: Verdict
    reason: str
    # True when the harness itself failed while grading; the verdict is then
    # inconclusive and says nothing about the server.
    harness_error: bool = False


class Scorecard(BaseModel):
    server_label: str
    protocol_version: str | None = None
    results: list[GradedResult] = Field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.verdict == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.verdict == "fail")

    @property
    def inconclusive(self) -> int:
        return sum(1 for r in self.results if r.verdict == "inconclusive")

    @property
    def failures(self) -> list[GradedResult]:
        return [r for r in self.results if r.verdict == "fail"]

    @property
    def inconclusives(self) -> list[GradedResult]:
        return [r for r in self.results if r.verdict == "inconclusive"]

    @property
    def harness_errors(self) -> list[GradedResult]:
        return [r for r in self.results if r.harness_error]

    def by_category(self) -> dict[str, tuple[int, int]]:
        """category -> (passed, total)"""
        totals: dict[str, tuple[int, int]] = {}
        for r in self.results:
            category = r.execution.test_case.category
            passed, total = totals.get(category, (0, 0))
            totals[category] = (passed + (r.verdict == "pass"), total + 1)
        return totals
