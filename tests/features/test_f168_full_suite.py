"""S9 — feature 168's acceptance: every Gherkin scenario has a test somewhere.

The suite run this test sits inside proves the tests pass; what no run can
report is a scenario that never got a test, so this pins each one to a
`def test_…` under `tests/`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCENARIOS = (
    "providers registered",
    "workbuddy transcript import builds tool spans",
    "legacy transcript without startsNewUserRequest still splits turns",
    "desktop prompt is unwrapped",
    "transcript import is idempotent",
    "unpaired function call stays pending",
    "unknown record type is skipped",
    "hook with agent_type cli is attributed by the flag",
    "codebuddy-family hooks install is claude shaped and merge safe",
    "skills deploy to codebuddy family dirs",
    "mcp registration respects hooks capability",
    "workbuddy mcp registration leaves trust to the user",
    "acp command comes from provider",
    "codebuddy task run launches over acp",
    "workbuddy cannot be launched",
)

_DEF = re.compile(r"^(?:async )?def (test_\w+)\(", re.M)


def _test_name(scenario: str) -> str:
    return "test_" + re.sub(r"[^a-z0-9]+", "_", scenario.lower()).strip("_")


def _defined() -> set[str]:
    names: set[str] = set()
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        names.update(_DEF.findall(path.read_text()))
    return names


def test_full_suite_passes():
    defined = _defined()
    missing = [s for s in SCENARIOS if _test_name(s) not in defined]
    assert not missing, "acceptance scenarios without a test: " + "; ".join(missing)
