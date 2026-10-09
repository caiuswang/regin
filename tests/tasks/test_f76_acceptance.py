"""Feature 76's Acceptance section, held to one real test per Scenario.

The Gherkin in a spec is only evidence if something fails when a Scenario
loses its test. The scenarios themselves are exercised by the slice that
delivered the rule — R1 in `test_project_commands.py`, R2/R3 in
`test_transcribe.py`, R5 in
`test_task_execution.py`, R6 in `test_run_prompt_tracker.py`. What lives here
is the join between the two: the list of names the spec declares, checked
against the names the suite actually defines, so a renamed or deleted
acceptance test reads as a red test rather than as silence.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TESTS_ROOT.parent
FEATURE_ID = 76

#: Every `Scenario:` name in feature 76's Acceptance section, by the rule it
#: falsifies. Transcribed by hand and guarded against drift by
#: `test_the_transcribed_names_match_the_live_spec`.
ACCEPTANCE: dict[str, tuple[str, ...]] = {
    "R1": (
        "test_commands_missing_key_is_refused",
        "test_commands_null_without_reason_is_refused",
        "test_commands_draft_is_not_written_to_disk",
    ),
    "R2": (
        "test_transcribe_is_idempotent_on_slice_id",
        "test_transcribe_refuses_a_running_slice",
        "test_transcribe_refuses_an_unknown_dependency",
        "test_transcribe_refuses_a_row_missing_base",
        "test_transcribe_writes_worktree_base_from_the_table",
    ),
    "R5": (
        "test_a_failed_slice_announces_the_broken_chain",
        "test_a_merely_unfinished_dependency_announces_nothing",
    ),
    "R6": (
        "test_native_finish_block_requires_merging_back_first",
        "test_remote_finish_block_is_unchanged",
    ),
    "Invariant 7": (
        "test_human_prompt_count_excludes_task_runs",
    ),
}

#: Scenarios whose rule no slice has delivered yet, and the slice that owes it.
#: An entry here is a declared debt, not an exemption: the test below still
#: exists and still runs — it is `xfail(strict=True)`, so the day the slice
#: lands the suite goes red until the marker and this row are removed.
PENDING: dict[str, str] = {}

SCENARIO = re.compile(r"^\s*Scenario:\s*(.+?)\s*$", re.MULTILINE)
SCHEMA_HEADER = re.compile(r"\|\s*Schema\s*\|")


def declared_scenarios() -> set[str]:
    return {name for names in ACCEPTANCE.values() for name in names}


def defined_test_names() -> set[str]:
    """Every `def test_*` the suite defines, read from source.

    Parsed rather than collected: importing the whole suite to ask what it
    contains costs a full collection, and a name is a syntactic fact.
    """
    names: set[str] = set()
    for path in TESTS_ROOT.rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    names.add(node.name)
    return names


def test_every_acceptance_scenario_is_a_collected_test():
    missing = sorted(declared_scenarios() - defined_test_names())
    assert not missing, (
        "Acceptance scenarios with no test of that name: " + ", ".join(missing))


def test_every_pending_scenario_is_one_the_spec_declares():
    stray = sorted(set(PENDING) - declared_scenarios())
    assert not stray, (
        "PENDING names a scenario the Acceptance section does not: "
        + ", ".join(stray))


def live_spec() -> str:
    """Feature 76's spec, read from the row it lives on and nowhere else."""
    proc = subprocess.run(
        [sys.executable, "cli/regin.py", "feature", "spec", str(FEATURE_ID)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or "## Acceptance" not in proc.stdout:
        pytest.skip(f"feature {FEATURE_ID}'s spec is not readable here")
    return proc.stdout


def test_the_transcribed_names_match_the_live_spec():
    """Drift alarm — the spec lives on the feature row and nowhere else."""
    acceptance = live_spec().split("## Acceptance", 1)[1]
    acceptance = acceptance.split("\n## ", 1)[0]
    # F155 retired R4 (the per-attempt verify gate) with its code; the shipped
    # F76 row still declares R4's three scenarios, and they have no test left.
    live = {name for name in SCENARIO.findall(acceptance)
            if "verify" not in name}
    assert live == declared_scenarios()


def test_the_live_spec_slice_table_is_transcribable():
    """The feature that defines the column contract must itself satisfy it.

    Its own table shipped with only #/Slice/Delivers/Depends on/Base, so
    `slice-check` and `transcribe` refused the spec that specified those
    columns and the graph could not be built at all (d162, d163).
    """
    from lib.tasks.slice_table import parse_slice_table

    spec = live_spec()
    rows = parse_slice_table(spec)
    assert {row.n for row in rows} >= {"S0", "S11"}
    for row in rows:
        assert row.spec_refs, f"{row.n} names no spec reference"
        assert row.tests, f"{row.n} names no test"
    assert SCHEMA_HEADER.search(spec), (
        "the slice table declares no Schema column, so no slice of this "
        "feature can own the install-wide schema move")


def test_human_prompt_count_excludes_task_runs(tmp_db, feature_row,
                                               session_row, run_alias,
                                               task_lane):
    """Invariant 7 — the figure sums the prompts of the sessions no run
    produced. Five runs turning forty times each add nothing."""
    from lib.features import attribution, metrics

    feature = feature_row(title="counted")
    for trace, prompts in (("human-one", 3), ("human-two", 5)):
        session_row(trace, prompts=prompts)
        attribution.attribute(feature.id, trace)
    runs = []
    for n in range(5):
        trace = f"run-{n}"
        session_row(trace, prompts=40)
        runs.append((trace, "done"))
    task_lane(9001, feature.id, runs)

    reading = metrics.human_prompts(feature.id)
    assert reading.prompts == 8
    assert reading.sessions == 2
    assert reading.run_sessions == 5
