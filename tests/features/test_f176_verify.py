"""S7 — the full suites cover every Acceptance Scenario of feature 176.

Running pytest and Playwright end to end is the slice's act, not something a
test can do from inside the suite it would be running. What a test can hold is
the other half of the bar: each Scenario's acceptance name is a test of the
same name that a full run executes — not missing, renamed or parked.

Two Scenarios are properties of the parse/serialize core and live in the node
unit suite; the rest are Playwright tests. The Playwright collection comes from
`playwright test --list --forbid-only` (see `test_f174_verify.py` for why not a
source scan). The node suite has no listing mode, but it runs in about a
second, so it is run outright and read from its TAP stream, where a skipped or
todo test is marked by a directive rather than missing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND = REPO_ROOT / "frontend"
PLAYWRIGHT = FRONTEND / "node_modules" / ".bin" / "playwright"
PROJECT = "chromium"

#: Feature 176's Acceptance Scenarios that are Playwright tests, in spec order.
E2E_SCENARIOS = (
    "test_rich_is_default",
    "test_open_then_save_is_byte_exact",
    "test_table_edit_serializes_table",
    "test_markdown_input_rules",
    "test_mention_in_rich",
    "test_paste_image_in_rich",
    "test_failed_upload_in_rich_leaves_doc_untouched",
    "test_opaque_block_roundtrip_and_edit",
    "test_mode_switch_is_lossless",
    "test_paste_markdown_text_parses",
    "test_inline_grows_and_toolbar_sticks",
    "test_source_textarea_autogrows",
    "test_focus_mode",
    "test_cancel_confirms_when_dirty",
    "test_legacy_specs_pass_in_source_mode",
    "test_rich_editor_is_lazy_chunk",
    "test_task_body_uses_rich",
    "test_defect_symptom_unchanged",
)

#: The Scenarios that are node unit tests over the parse/serialize core.
UNIT_SCENARIOS = (
    "test_roundtrip_corpus_is_identity",
    "test_edit_rewrites_only_touched_block",
)

#: A title is the Scenario name alone, or the name then ` — …` / `: …` when
#: one Scenario is split across several tests.
_NAMED = re.compile(r"^(test_\w+)(?=\s|:|$)")
_TAP_RESULT = re.compile(r"^\s*(not ok|ok) \d+ - (test_\w+)(.*)$")


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


def unit_results(tap: str) -> dict[str, str]:
    """Unit test name → `ok`, `not ok`, or `skip` (a SKIP/TODO directive)."""
    results: dict[str, str] = {}
    for line in tap.splitlines():
        match = _TAP_RESULT.match(line)
        if not match:
            continue
        verdict, name, rest = match.groups()
        directive = re.search(r"#\s*(SKIP|TODO)\b", rest, re.IGNORECASE)
        results[name] = "skip" if directive else verdict
    return results


def _list_tests() -> dict:
    proc = subprocess.run(
        [str(PLAYWRIGHT), "test", "--list", "--forbid-only",
         "--reporter=json", f"--project={PROJECT}"],
        cwd=FRONTEND, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (proc.stderr or proc.stdout)[-2000:]
    listing = json.loads(proc.stdout)
    assert not listing["errors"], listing["errors"]
    return listing


def _run_unit_suite() -> str:
    proc = subprocess.run(
        ["node", "--test", "--test-reporter=tap", "tests/unit/*.test.mjs"],
        cwd=FRONTEND, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (proc.stderr or proc.stdout)[-2000:]
    return proc.stdout


def _e2e_gaps() -> dict[str, list[str]]:
    collected = collected_statuses(_list_tests())
    return {name: collected.get(name, ["missing"]) for name in E2E_SCENARIOS
            if set(collected.get(name, [])) != {"passed"}}


def _unit_gaps() -> dict[str, str]:
    units = unit_results(_run_unit_suite())
    return {name: units.get(name, "missing") for name in UNIT_SCENARIOS
            if units.get(name) != "ok"}


def test_full_suite_passes():
    if not PLAYWRIGHT.exists() or shutil.which("node") is None:
        pytest.skip("frontend/node_modules not installed")
    e2e = _e2e_gaps()
    assert not e2e, f"Scenarios with no running same-named E2E test: {e2e}"
    units = _unit_gaps()
    assert not units, f"Scenarios whose unit test does not pass: {units}"


def test_unit_results_reads_skips_as_not_passing():
    tap = "ok 1 - test_a\nok 2 - test_b # SKIP later\nnot ok 3 - test_c\n"
    assert unit_results(tap) == {"test_a": "ok", "test_b": "skip", "test_c": "not ok"}


def test_scenario_title_with_dash_suffix_counts():
    assert _NAMED.match("test_focus_mode — the draft survives Esc")[1] == "test_focus_mode"
    assert _NAMED.match("test_focus_modes")[1] == "test_focus_modes"
    assert _NAMED.match("a toolbar press is one undo step") is None
