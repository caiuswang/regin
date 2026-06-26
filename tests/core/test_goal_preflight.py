"""Tests for the goal-preflight recall + hard-gates kernel.

Area-routing (`AREA_RULES`/`detect_areas`/`resolve_references`) was removed
2026-06: it was regin-specific and merely restated the file-keyed convention
table in CLAUDE.local.md. Preflight now emits only the universal gate floor
plus (opt-in) recalled lessons.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from lib.goal_preflight import (
    BASE_GATES,
    build_roadmap,
    render_markdown,
    roadmap_to_dict,
    roadmap_warning,
)


def test_base_gates_always_present():
    rm = build_roadmap("anything at all")
    for gate in BASE_GATES:
        assert gate in rm.gates


def test_no_lessons_by_default():
    # The deterministic core stays pure/offline unless lessons are requested.
    rm = build_roadmap("refactor the inbox filter")
    assert rm.lessons == []


def test_roadmap_is_deterministic():
    goal = "Refactor inbox message filters"
    a = roadmap_to_dict(build_roadmap(goal))
    b = roadmap_to_dict(build_roadmap(goal))
    assert a == b


def test_roadmap_dict_has_no_area_keys():
    # The old area-scaffold keys must be gone from the serialized view.
    d = roadmap_to_dict(build_roadmap("style the button view"))
    assert set(d) == {"goal", "gates", "lessons"}


def test_render_markdown_contains_sections():
    md = render_markdown(build_roadmap("style the button view"))
    for header in ("## Lessons recalled", "## Hard gates",
                   "## Acceptance checklist"):
        assert header in md


def test_render_markdown_drops_area_sections():
    md = render_markdown(build_roadmap("style the button view"))
    for gone in ("## Standards", "## Reference components", "## Design tokens",
                 "_Areas:"):
        assert gone not in md


def test_warning_on_empty_goal():
    warn = roadmap_warning(build_roadmap(""))
    assert warn is not None and "empty" in warn


def test_warning_on_whitespace_goal():
    warn = roadmap_warning(build_roadmap("   \t "))
    assert warn is not None and "empty" in warn


def test_no_warning_for_real_goal():
    # With area-routing gone, any non-empty goal gets the gate floor — never
    # "hollow", so no warning regardless of wording.
    assert roadmap_warning(build_roadmap("ponder the meaning of the universe")) is None
    assert roadmap_warning(build_roadmap("fix the inbox view")) is None


def _run_cli(goal: str, *extra: str) -> subprocess.CompletedProcess:
    repo_root = os.getcwd()
    return subprocess.run(
        [sys.executable, "cli/regin.py", "goal", "preflight", goal, *extra],
        cwd=repo_root, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": repo_root},
    )


def test_cli_warning_goes_to_stderr_not_stdout():
    proc = _run_cli("")
    assert "warning:" in proc.stderr
    assert "warning:" not in proc.stdout


def test_cli_json_stdout_is_pure_and_well_formed():
    proc = _run_cli("redesign the dashboard view", "--json")
    parsed = json.loads(proc.stdout)  # raises if stdout is polluted
    assert "gates" in parsed and "areas" not in parsed


def test_cli_no_warning_for_real_goal():
    proc = _run_cli("redesign the dashboard view")
    assert "warning:" not in proc.stderr
