"""The four objects that flow through the pipeline.

TestCase -> ExecutionResult -> GradedResult -> Scorecard

Each stage keeps the object before it, so any failure can be traced back to
the raw exchange that produced it.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CheckType = Literal["no_error", "is_error", "schema_valid"]
Verdict = Literal["pass", "fail"]


class Expectation(BaseModel):
    """What "correct" means for one test case."""

    model_config = ConfigDict(populate_by_name=True)

    type: CheckType
    # Only used by schema_valid: the JSON Schema the result must satisfy.
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")


class TestCase(BaseModel):
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
    # False when the call failed at the protocol level (McpError, timeout, crash).
    completed: bool
    # True when the server returned a result explicitly flagged as an error.
    is_error: bool = False
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured: dict[str, Any] | None = None
    error_message: str | None = None


class GradedResult(BaseModel):
    execution: ExecutionResult
    verdict: Verdict
    reason: str


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
    def failures(self) -> list[GradedResult]:
        return [r for r in self.results if r.verdict == "fail"]

    def by_category(self) -> dict[str, tuple[int, int]]:
        """category -> (passed, total)"""
        totals: dict[str, tuple[int, int]] = {}
        for r in self.results:
            category = r.execution.test_case.category
            passed, total = totals.get(category, (0, 0))
            totals[category] = (passed + (r.verdict == "pass"), total + 1)
        return totals
