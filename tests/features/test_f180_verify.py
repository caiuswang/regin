"""S11 — the full suites cover every Acceptance Scenario of feature 180.

As in `test_f178_verify.py`: running pytest and Playwright end to end is the
slice's act. What a test can hold is that each Scenario's name is a test the
spec's own `TestIndex` finds, and that a full run executes it rather than
skipping or parking it: every pytest one passes in a fresh process, every
Playwright one is collected on chromium with an expected status of passed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from lib.specs.test_index import build_test_index
from tests.features.test_f176_verify import PLAYWRIGHT, REPO_ROOT, _list_tests, collected_statuses

#: Feature 180's Acceptance Scenario names, in spec order.
SCENARIOS = (
    "test_feature_defect_row_is_one_line",
    "test_feature_defect_row_expands_in_place",
    "test_defects_page_uses_the_one_line_head",
    "test_slices_rows_align_in_columns",
    "test_slices_card_has_header_and_branch_footer",
    "test_tasks_rows_align_in_columns",
    "test_feature_page_has_no_horizontal_overflow",
    "test_comment_pasted_image_is_cited_and_drawn",
    "test_comment_discarded_draft_uploads_nothing",
    "test_comment_citing_missing_image_is_refused",
    "test_comment_with_only_an_image_is_accepted",
    "test_comment_image_ref_resolves_to_its_own_comment",
    "test_comment_mention_inserts_reference_chip",
    "test_comment_reference_appears_in_backlinks",
    "test_launch_sends_open_thread_comment_images",
    "test_launch_lists_overflow_comment_images_as_fetchable",
    "test_image_get_reads_comment_owner",
    "test_comment_images_schema_parity",
    "test_defect_row_shows_fix_task_chip_with_status",
    "test_open_defect_without_fix_task_says_so",
    "test_fix_task_row_is_accented_in_tasks_list",
    "test_graph_node_marks_fix_task",
    "test_feature_page_previews_task_graph_above_spec",
    "test_graph_preview_is_read_only",
    "test_graph_preview_node_opens_task",
    "test_graph_preview_hidden_without_tasks",
    "test_viewer_can_read_task_graph",
    "test_declare_dialog_opens_rich_despite_page_source_mode",
    "test_compose_mode_choice_does_not_leak_to_document",
    "test_feature_step_dialog_uses_rich_editor",
    "test_feature_step_dialog_pasted_image_lands_on_step_task",
    "test_spec_reader_imports_no_re",
    "test_nested_fence_scenario_is_not_extracted",
    "test_docstring_scenario_is_not_extracted",
    "test_untitled_gherkin_block_is_refused",
    "test_non_ascii_title_without_test_name_is_refused",
    "test_duplicate_scenario_names_are_refused",
    "test_uncovered_scenario_refuses_slicing",
    "test_scenario_owned_by_two_slices_is_refused",
    "test_tests_entry_not_an_acceptance_name_is_refused",
    "test_tail_rows_may_relist_acceptance_names",
    "test_transcribe_refuses_unsliceable_spec_on_every_path",
    "test_feature_slices_report_lists_problems",
    "test_test_index_finds_pytest_and_playwright_tests",
    "test_slice_finish_refused_when_named_test_missing",
    "test_spec_write_reports_problems_without_refusing",
    "test_finish_refused_while_message_queued_sdk",
    "test_finish_refused_while_message_queued_acp",
    "test_finish_refused_while_message_queued_tmux",
    "test_idle_one_shot_finish_seals_the_run",
    "test_send_after_seal_is_refused_and_draft_kept",
    "test_refused_send_posts_as_task_comment",
    "test_finish_and_send_race_has_one_winner",
    "test_acp_end_after_turn_drops_nothing_silently",
    "test_tmux_stop_keeps_pane_while_queue_nonempty",
    "test_dropped_message_is_shown_as_dropped",
    "test_finish_prompt_states_auto_close_truthfully",
)


def _by_runner(names: tuple[str, ...]) -> tuple[dict[str, set[str]], list[str]]:
    index = build_test_index(REPO_ROOT)
    runners: dict[str, set[str]] = {}
    missing = []
    for name in names:
        locations = index.get(name)
        if not locations:
            missing.append(name)
        for loc in locations or ():
            runners.setdefault(loc.runner, set()).add(name)
    return runners, missing


def junit_outcomes(xml: str) -> dict[str, list[str]]:
    """Test function name → the outcome of each case junit reports for it."""
    outcomes: dict[str, list[str]] = {}
    for case in ET.fromstring(xml).iter("testcase"):
        verdicts = [child.tag for child in case if child.tag in ("failure", "error", "skipped")]
        name = case.get("name", "").split("[", 1)[0]
        outcomes.setdefault(name, []).append(verdicts[0] if verdicts else "passed")
    return outcomes


def _pytest_gaps(names: set[str], tmp_path) -> dict[str, list[str]]:
    if not names:
        return {}
    index = build_test_index(REPO_ROOT)
    files = sorted({loc.path for name in names for loc in index[name] if loc.runner == "pytest"})
    report = tmp_path / "junit.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         f"--junitxml={report}", "-k", " or ".join(sorted(names)), *files],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)
    outcomes = junit_outcomes(report.read_text()) if report.exists() else {}
    return {name: outcomes.get(name, ["missing"]) for name in sorted(names)
            if set(outcomes.get(name, [])) != {"passed"}}


def test_full_suite_passes(tmp_path):
    if not PLAYWRIGHT.exists() or shutil.which("node") is None:
        pytest.skip("frontend/node_modules not installed")
    runners, missing = _by_runner(SCENARIOS)
    assert not missing, f"Scenarios the spec's test index cannot find: {missing}"

    collected = collected_statuses(_list_tests())
    e2e = {name: collected.get(name, ["missing"]) for name in sorted(runners.get("playwright", ()))
           if set(collected.get(name, [])) != {"passed"}}
    assert not e2e, f"Scenarios with no running same-named E2E test: {e2e}"

    units = _pytest_gaps(runners.get("pytest", set()), tmp_path)
    assert not units, f"Scenarios whose pytest test does not pass: {units}"


def test_junit_outcomes_reads_skips_and_params_as_not_passing():
    xml = ("<testsuites><testsuite>"
           "<testcase name=\"test_a\"/>"
           "<testcase name=\"test_b\"><skipped/></testcase>"
           "<testcase name=\"test_c[1]\"/><testcase name=\"test_c[2]\"><failure/></testcase>"
           "</testsuite></testsuites>")
    assert junit_outcomes(xml) == {
        "test_a": ["passed"], "test_b": ["skipped"], "test_c": ["passed", "failure"]}
