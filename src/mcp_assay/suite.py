"""Load a suite file (YAML) into TestCase objects.

Suites are data, not code: adding a test means editing YAML.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import TestCase


class ServerSpec(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None

    @property
    def label(self) -> str:
        return " ".join([self.command, *self.args])


class Suite(BaseModel):
    name: str
    tests: list[TestCase]
    server: ServerSpec | None = None


def load_suite(path: str | Path) -> Suite:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    suite = Suite.model_validate(data)
    if suite.server and suite.server.cwd is None:
        # Server paths in a suite file are written relative to the suite.
        suite.server.cwd = str(path.parent.parent.resolve())
    return suite
