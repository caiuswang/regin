"""S7 — the full suites cover every Acceptance Scenario of feature 174.

Running pytest and Playwright end to end is the slice's act, not something a
test can do from inside the suite it would be running. What a test can hold is
the other half of the bar: each Scenario's acceptance name is a Playwright test
of the same name that the full run executes — not missing, renamed or parked.

The collection comes from `playwright test --list`, not a scan of the spec
source: a regex over the text counts comments, string literals and describe
titles as tests, and cannot see a skip inherited from `test.describe.skip`.
`--forbid-only` because the listing still names every test while a stray
`.only` would keep all but one from running. A skip decided at run time — a
`test.skip()` in a test body, or a file/group skip conditioned on the browser —
stays invisible to any listing.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND = REPO_ROOT / "frontend"
PLAYWRIGHT = FRONTEND / "node_modules" / ".bin" / "playwright"
PROJECT = "chromium"

#: The Scenario names in feature 174's Acceptance block, in spec order.
SCENARIOS = (
    "test_split_default_on_wide_viewport",
    "test_typing_renders_preview",
    "test_mode_is_remembered",
    "test_narrow_viewport_falls_back_to_source",
    "test_save_is_byte_exact",
    "test_toolbar_bold_is_one_undo_step",
    "test_toolbar_line_marks_apply_per_line",
    "test_toolbar_link_prompts_for_url",
    "test_pasted_image_writes_reference_and_previews",
    "test_failed_upload_leaves_source_untouched",
    "test_mention_contract_preserved",
    "test_component_reused_in_task_and_defect_editors",
)

#: A title is the Scenario name alone, or the name then `: …` — the reuse spec
#: splits one Scenario across its two hosts that way.
_NAMED = re.compile(r"^(test_\w+)(?::|$)")


def _specs(suite: dict):
    yield from suite.get("specs", [])
    for child in suite.get("suites", []):
        yield from _specs(child)


def collected_statuses(listing: dict) -> dict[str, list[str]]:
    """Scenario-style test name → the expected status of each collected test."""
    found: dict[str, list[str]] = {}
    for suite in listing["suites"]:
        for spec in _specs(suite):
            named = _NAMED.match(spec["title"])
            if not named:
                continue
            for test in spec["tests"]:
                if test["projectName"] == PROJECT:
                    found.setdefault(named[1], []).append(test["expectedStatus"])
    return found


def _list_tests() -> dict:
    proc = subprocess.run(
        [str(PLAYWRIGHT), "test", "--list", "--forbid-only",
         "--reporter=json", f"--project={PROJECT}"],
        cwd=FRONTEND, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (proc.stderr or proc.stdout)[-2000:]
    listing = json.loads(proc.stdout)
    assert not listing["errors"], listing["errors"]
    return listing


def _parked(collected: dict[str, list[str]]) -> dict[str, list[str]]:
    return {name: collected[name] for name in SCENARIOS
            if set(collected[name]) != {"passed"}}


def test_full_suite_passes():
    if not PLAYWRIGHT.exists():
        pytest.skip("frontend/node_modules not installed")
    collected = collected_statuses(_list_tests())
    missing = [name for name in SCENARIOS if name not in collected]
    assert not missing, f"Scenarios with no same-named E2E test: {missing}"
    parked = _parked(collected)
    assert not parked, f"Scenarios whose E2E test does not run: {parked}"
