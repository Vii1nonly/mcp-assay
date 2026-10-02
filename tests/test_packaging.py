"""The declared SDK range must match the SDK the code is written against."""

from importlib.metadata import requires

import pytest
from packaging.requirements import Requirement


def _mcp_requirement() -> Requirement:
    # Read the installed project's metadata: the same requirement pip and uv resolve.
    reqs = [Requirement(r) for r in requires("mcp-eval")]
    [mcp] = [r for r in reqs if r.name == "mcp"]
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
