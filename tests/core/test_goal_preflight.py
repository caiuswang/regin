"""Tests for the deterministic goal-preflight roadmap router."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from lib.goal_preflight import (
    AREA_RULES,
    BASE_GATES,
    build_roadmap,
    detect_areas,
    render_markdown,
    roadmap_to_dict,
    roadmap_warning,
    _ident_tokens,
)


def _area_names(goal: str) -> list[str]:
    return [r.name for r in detect_areas(goal)]


def test_frontend_goal_routes_to_frontend_area():
    areas = _area_names("Make the inbox UI good with a kind filter")
    assert "frontend" in areas


def test_python_goal_routes_to_python_area():
    areas = _area_names("Fix the CLI endpoint in lib so it returns JSON")
    assert "python" in areas


def test_cross_area_goal_routes_to_multiple_areas():
    areas = _area_names("Fix the trace span ingest in lib/trace")
    assert "python" in areas and "trace" in areas


def test_unmatched_goal_yields_no_areas():
    assert _area_names("ponder the meaning of the universe") == []


def test_path_glob_signal_fires_area():
    # No frontend keyword, but a *.vue path token should still route.
    areas = _area_names("touch foo.vue")
    assert "frontend" in areas


def test_keyword_is_whole_word_not_substring():
    # "apize" must not trip the "api" keyword.
    assert "python" not in _area_names("apize the widget")


def test_ident_tokens_splits_camelcase():
    assert _ident_tokens("InboxView") == {"inbox", "view"}
    assert _ident_tokens("InboxMessageCard") == {"inbox", "message", "card"}


def test_frontend_roadmap_has_skills_tokens_and_gates():
    rm = build_roadmap("redesign the dashboard view", repo_root=os.getcwd())
    assert "vue-complexity" in rm.skills
    assert "frontend/src/assets/style.css" in rm.tokens
    assert any("vite build" in g for g in rm.gates)


def test_base_gates_always_present():
    rm = build_roadmap("update the readme docs", repo_root=os.getcwd())
    for gate in BASE_GATES:
        assert gate in rm.gates


def test_roadmap_is_deterministic():
    goal = "Refactor inbox message filters"
    a = roadmap_to_dict(build_roadmap(goal, repo_root=os.getcwd()))
    b = roadmap_to_dict(build_roadmap(goal, repo_root=os.getcwd()))
    assert a == b


def test_skills_are_deduped_across_areas():
    # python + trace both contribute python-complexity; must appear once.
    rm = build_roadmap("fix lib/trace span python bug", repo_root=os.getcwd())
    assert rm.skills.count("python-complexity") == 1


def test_references_capped_and_ranked(tmp_path):
    # Build a fake frontend tree; the goal-relevant file must rank first.
    views = tmp_path / "frontend" / "src" / "views"
    views.mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "assets").mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "assets" / "style.css").write_text("")
    for name in ["AaaView.vue", "InboxView.vue", "ZzzView.vue"]:
        (views / name).write_text("<template></template>")
    rm = build_roadmap("fix the inbox view", repo_root=str(tmp_path))
    assert rm.references
    assert "InboxView" in rm.references[0]


def test_render_markdown_contains_all_sections():
    md = render_markdown(build_roadmap("style the button view", repo_root=os.getcwd()))
    for header in ("## Standards", "## Reference components",
                   "## Design tokens", "## Hard gates", "## Acceptance checklist"):
        assert header in md


def test_warning_on_empty_goal():
    rm = build_roadmap("", repo_root=os.getcwd())
    warn = roadmap_warning(rm)
    assert warn is not None and "empty" in warn


def test_warning_on_whitespace_goal():
    rm = build_roadmap("   \t ", repo_root=os.getcwd())
    warn = roadmap_warning(rm)
    assert warn is not None and "empty" in warn


def test_warning_on_no_area_match():
    rm = build_roadmap("ponder the meaning of the universe", repo_root=os.getcwd())
    warn = roadmap_warning(rm)
    assert warn is not None and "no known area" in warn


def test_no_warning_when_area_matches():
    rm = build_roadmap("fix the inbox view", repo_root=os.getcwd())
    assert roadmap_warning(rm) is None


def test_warning_is_actionable():
    # No-area warning must tell the user how to recover (name area or file).
    rm = build_roadmap("frobnicate the doohickey", repo_root=os.getcwd())
    warn = roadmap_warning(rm)
    assert "Rephrase" in warn and (".py" in warn or "*.vue" in warn)


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


def test_cli_json_stdout_stays_pure_with_warning():
    # The hollow-roadmap warning must not corrupt --json stdout.
    proc = _run_cli("zzz no area here", "--json")
    assert "warning:" in proc.stderr
    parsed = json.loads(proc.stdout)  # raises if stdout is polluted
    assert parsed["areas"] == []


def test_cli_no_warning_for_matched_goal():
    proc = _run_cli("redesign the dashboard view")
    assert "warning:" not in proc.stderr


def test_table_keywords_are_lowercase():
    # Routing lowercases the goal; uppercase table keywords would never match.
    for rule in AREA_RULES:
        for kw in rule.keywords:
            assert kw == kw.lower()
