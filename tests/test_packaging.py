"""The declared SDK range must match the SDK the code is written against."""

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def _mcp_requirement() -> Requirement:
    # Read the source pip and uv build metadata from, not the installed copy,
    # which goes stale when pyproject.toml changes without a re-sync.
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    [mcp] = [r for r in map(Requirement, deps) if r.name == "mcp"]
    return mcp


@pytest.mark.parametrize(
    ("version", "allowed"),
    [
        ("2.2.0", True),
        ("2.9.0", True),
        ("2.1.1", False),
        ("1.30.0", False),
        ("3.0.0", False),
    ],
)
def test_mcp_sdk_range(version, allowed):
    assert _mcp_requirement().specifier.contains(version) is allowed
