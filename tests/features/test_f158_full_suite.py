"""S5 — feature 158's acceptance: every Gherkin scenario has a test somewhere.

The suite run this test sits inside proves the pytest ones pass; the mod tests
run under `claude plugin test` and the card under Playwright. What none of those
runs can report is a scenario that never got a test, so this pins each scenario
to a `def test_…` under `tests/`, a `test('test_…'` in a mod's `*.test.ts(x)`,
or one in `frontend/tests/*.spec.js`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCENARIOS = (
    "held permission is allowed from the web over the mod channel",
    "permission ask is held and allowed from the web",
    "permission deny carries the reason to the model",
    "settled permissions are not held",
    "person-required tools are not held",
    "released permission falls back to the native dialog",
    "unreachable regin falls back to the native dialog",
    "held stream ends when the hold process dies",
    "decisions without a held request keep the tmux path",
    "bridge-answer never uses the mod channel",
    "a decision only reaches its own held request",
    "two held permissions without a tool_use_id are refused",
    "a held request resolves at most once",
    "live state lists held requests",
    "held permission card offers allow and deny",
    "a held request has exactly one pending card",
    "rate limit crossing raises one warning",
    "the same window and threshold alerts once across sessions",
    "a new reset window alerts again",
    "jumping past two thresholds alerts once",
    "windows without a reset time never alert",
    "meter skips measurements without rate limit changes",
    "compaction keeps the feature, task and finish command",
    "compaction without context is untouched",
    "subagent compaction is untouched",
    "marketplace lists every plugin dir",
    "probe findings are written back",
)

SOURCES = (
    ("tests", "test_*.py", re.compile(r"^def (test_\w+)\(", re.M)),
    ("regin-plugin/plugins", "*.test.ts*", re.compile(r"\btest\(\s*'(test_\w+)'")),
    ("frontend/tests", "*.spec.js", re.compile(r"\btest\(\s*'(test_\w+)'")),
)


def _test_name(scenario: str) -> str:
    return "test_" + re.sub(r"[^a-z0-9]+", "_", scenario.lower()).strip("_")


def _defined() -> set[str]:
    names: set[str] = set()
    for root, glob, pattern in SOURCES:
        for path in (REPO_ROOT / root).rglob(glob):
            if "node_modules" not in path.parts:
                names.update(pattern.findall(path.read_text()))
    return names


def test_full_suite_passes():
    defined = _defined()
    missing = [s for s in SCENARIOS if _test_name(s) not in defined]
    assert not missing, "acceptance scenarios without a test: " + "; ".join(missing)
