"""S8 — the full suites cover every Acceptance Scenario of feature 178.

As in `test_f176_verify.py`: running pytest and Playwright end to end is the
slice's act; what a test can hold is that each Scenario's name is a test of the
same name that a full run executes — not missing, renamed or parked — and that
F176's own suites are still wholly live under F178's changes.
"""

from __future__ import annotations

import shutil

import pytest

from tests.features.test_f176_verify import (
    E2E_SCENARIOS as F176_E2E_SCENARIOS,
    PLAYWRIGHT,
    PROJECT,
    UNIT_SCENARIOS as F176_UNIT_SCENARIOS,
    _list_tests,
    _run_unit_suite,
    _specs,
    collected_statuses,
    unit_results,
)

#: Feature 178's Acceptance Scenarios that are Playwright tests, in spec order.
E2E_SCENARIOS = (
    "test_slash_opens_on_empty_paragraph",
    "test_slash_inside_word_does_not_open",
    "test_slash_silent_in_code_block",
    "test_slash_diagram_live_preview",
    "test_mermaid_error_shows_source",
    "test_read_view_renders_mermaid",
    "test_read_view_highlights_gherkin",
    "test_language_select_rewrites_only_that_block",
    "test_bubble_menu_on_selection",
    "test_bubble_menu_hidden_in_code_block",
    "test_shortcuts_popover",
    "test_outline_rail_in_focus",
    "test_outline_dropdown",
    "test_task_body_has_no_outline",
    "test_declare_dialog_uses_rich_editor",
    "test_task_composer_uses_rich_editor",
    "test_dialog_esc_closes_overlay_first",
    "test_legacy_dialog_specs_pass_in_source_mode",
    "test_mermaid_and_lowlight_not_in_entry",
)

#: The corpus round-trip is a property of the parse/serialize core.
UNIT_SCENARIOS = ("test_roundtrip_corpus_still_identity",)

#: The suites the "二期契约不变" Scenario names, every test in them.
F176_SPEC_FILES = (
    "f176-rich.spec.js",
    "f176-refs-images.spec.js",
    "f176-layout.spec.js",
    "defect-symptom-verbatim.spec.js",
)


def _require_frontend():
    if not PLAYWRIGHT.exists() or shutil.which("node") is None:
        pytest.skip("frontend/node_modules not installed")


def _scenario_gaps(listing: dict, names: tuple[str, ...]) -> dict[str, list[str]]:
    collected = collected_statuses(listing)
    return {name: collected.get(name, ["missing"]) for name in names
            if set(collected.get(name, [])) != {"passed"}}


def _unit_gaps(tap: str, names: tuple[str, ...]) -> dict[str, str]:
    units = unit_results(tap)
    return {name: units.get(name, "missing") for name in names
            if units.get(name) != "ok"}


def file_statuses(listing: dict, files: tuple[str, ...]) -> dict[str, list[tuple[str, str]]]:
    """Spec file → (title, expected status) of every chromium test it holds."""
    found: dict[str, list[tuple[str, str]]] = {name: [] for name in files}
    for suite in listing["suites"]:
        for spec in _specs(suite):
            name = spec["file"].rsplit("/", 1)[-1]
            if name not in found:
                continue
            for test in spec["tests"]:
                if test["projectName"] == PROJECT:
                    found[name].append((spec["title"], test["expectedStatus"]))
    return found


def parked_or_absent(statuses: dict[str, list[tuple[str, str]]]) -> dict[str, list[str]]:
    gaps: dict[str, list[str]] = {}
    for name, tests in statuses.items():
        if not tests:
            gaps[name] = ["no tests collected"]
            continue
        parked = [title for title, status in tests if status != "passed"]
        if parked:
            gaps[name] = parked
    return gaps


def test_full_suite_passes():
    _require_frontend()
    e2e = _scenario_gaps(_list_tests(), E2E_SCENARIOS)
    assert not e2e, f"Scenarios with no running same-named E2E test: {e2e}"
    units = _unit_gaps(_run_unit_suite(), UNIT_SCENARIOS)
    assert not units, f"Scenarios whose unit test does not pass: {units}"


def test_f176_suite_still_passes():
    _require_frontend()
    listing = _list_tests()
    files = parked_or_absent(file_statuses(listing, F176_SPEC_FILES))
    assert not files, f"F176 suites with parked or absent tests: {files}"
    e2e = _scenario_gaps(listing, F176_E2E_SCENARIOS)
    assert not e2e, f"F176 Scenarios no longer collected: {e2e}"
    units = _unit_gaps(_run_unit_suite(), F176_UNIT_SCENARIOS)
    assert not units, f"F176 unit Scenarios that no longer pass: {units}"


def test_parked_test_in_a_named_file_is_a_gap():
    statuses = {"a.spec.js": [("x", "passed"), ("y", "skipped")], "b.spec.js": []}
    assert parked_or_absent(statuses) == {"a.spec.js": ["y"], "b.spec.js": ["no tests collected"]}
