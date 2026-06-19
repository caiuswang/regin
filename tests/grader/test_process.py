"""Tests for lib/grader/process.py — the process/efficiency axis.

Covers the four criteria (P1 tool-use appropriateness, P2 redundancy /
thrash, P3 reliability, P4 cost proportionality) and the
efficient / acceptable / wasteful verdict ladder, using EvidenceIndex
objects built straight from span dicts (`make_evidence`) and the tmp
primary DB (`seed_spans`) for the percentile path.
"""

from __future__ import annotations

import pytest

import lib.grader.process as process_mod
from conftest import (
    bash_span, edit_span, grep_span, read_span, response_span, seed_spans,
)
from lib.grader.models import (
    APPROPRIATE, ELEVATED, PROPORTIONATE, RUNAWAY, SUBOPTIMAL, WASTED,
    AxisGrade,
)
from lib.grader.process import (
    ACCEPTABLE, EFFICIENT, WASTEFUL, assess_redundancy, assess_reliability,
    assess_tool_use, cost_percentile, grade_process,
)


def _correctness(verdict: str = "satisfied", claims: list | None = None,
                 covered: int | None = None) -> AxisGrade:
    """Fabricated correctness AxisGrade to condition grade_process on."""
    scoreboard = {"coverage": {"covered": covered}} if covered else {}
    return AxisGrade(axis="correctness", verdict=verdict, tier="screen",
                     scoreboard=scoreboard, detail={"claims": claims or []})


# ── P1: tool-use appropriateness ─────────────────────────────────

def test_p1_shell_substitute_bash_is_suboptimal(make_evidence):
    evidence = make_evidence([
        bash_span("b1", "cat foo.py", stdout="x = 1"),
        bash_span("b2", "grep -r needle src/", stdout="src/foo.py:needle"),
    ])
    result = assess_tool_use(evidence)
    assert result["counts"][SUBOPTIMAL] == 2
    verdicts = {f["span_id"]: f["verdict"] for f in result["findings"]}
    assert verdicts == {"b1": SUBOPTIMAL, "b2": SUBOPTIMAL}


def test_p1_unused_read_is_wasted(make_evidence):
    # orphan.py never shows up in a later span or the final text.
    evidence = make_evidence([
        read_span("s1", "src/orphan.py", content="x = 1"),
        edit_span("s2", "src/other.py", diff="+answer = 42"),
        response_span("Edited the other module."),
    ])
    result = assess_tool_use(evidence)
    assert result["counts"][WASTED] == 1
    finding = next(f for f in result["findings"] if f["span_id"] == "s1")
    assert finding["verdict"] == WASTED


def test_p1_read_feeding_a_later_edit_is_appropriate(make_evidence):
    evidence = make_evidence([
        read_span("s1", "src/foo.py", content="x = 1"),
        edit_span("s2", "src/foo.py", diff="+x = 2"),
        response_span("Bumped the constant."),
    ])
    result = assess_tool_use(evidence)
    assert result["total"] == 2
    assert result["counts"][APPROPRIATE] == 2
    assert result["findings"] == []


# ── P2: redundancy / thrash ──────────────────────────────────────

def test_p2_same_path_read_twice_without_mutation_is_redundant(make_evidence):
    evidence = make_evidence([
        read_span("r1", "src/a.py", content="x = 1"),
        read_span("r2", "src/a.py", content="x = 1"),
    ])
    redundancy = assess_redundancy(evidence, thrash_k=3)
    assert redundancy["redundant_reads"] == [
        {"path": "src/a.py", "spans": ["r1", "r2"]}]


def test_p2_read_edit_read_is_not_redundant(make_evidence):
    evidence = make_evidence([
        read_span("r1", "src/a.py", content="x = 1"),
        edit_span("m1", "src/a.py", diff="+x = 2"),
        read_span("r2", "src/a.py", content="x = 2"),
    ])
    redundancy = assess_redundancy(evidence, thrash_k=3)
    assert redundancy["redundant_reads"] == []


def test_p2_thrash_run_survives_interleaved_reads(make_evidence):
    evidence = make_evidence([
        bash_span("b1", "pytest -q", status="ERROR", stderr="1 failed"),
        read_span("r1", "tests/test_x.py"),
        bash_span("b2", "pytest -q", status="ERROR", stderr="1 failed"),
        read_span("r2", "src/x.py"),
        bash_span("b3", "pytest -q", status="ERROR", stderr="1 failed"),
    ])
    redundancy = assess_redundancy(evidence, thrash_k=3)
    assert len(redundancy["thrash_episodes"]) == 1
    assert redundancy["thrash_episodes"][0]["spans"] == ["b1", "b2", "b3"]


def test_p2_edit_between_failures_breaks_thrash_run(make_evidence):
    evidence = make_evidence([
        bash_span("b1", "pytest -q", status="ERROR", stderr="1 failed"),
        bash_span("b2", "pytest -q", status="ERROR", stderr="1 failed"),
        edit_span("m1", "src/x.py", diff="+fix"),
        bash_span("b3", "pytest -q", status="ERROR", stderr="1 failed"),
    ])
    redundancy = assess_redundancy(evidence, thrash_k=3)
    assert redundancy["thrash_episodes"] == []


def test_p2_duplicate_grep_pattern_is_re_derivation(make_evidence):
    evidence = make_evidence([
        grep_span("g1", "def grade_process"),
        read_span("r1", "lib/grader/process.py"),
        grep_span("g2", "def grade_process"),
    ])
    redundancy = assess_redundancy(evidence, thrash_k=3)
    assert redundancy["re_derivations"] == [
        {"pattern": "def grade_process", "spans": ["g1", "g2"]}]


# ── P3: reliability ──────────────────────────────────────────────

def test_p3_successful_retry_of_same_command_recovers_error(make_evidence):
    evidence = make_evidence([
        bash_span("b1", "pytest tests/test_x.py", status="ERROR",
                  stderr="ImportError"),
        bash_span("b2", "pytest tests/test_x.py", stdout="1 passed"),
    ])
    reliability = assess_reliability(evidence, [])
    assert reliability["errored"] == 1
    assert reliability["recovered"] == 1
    assert reliability["ignored"] == 0


def test_p3_trailing_unrecovered_error_is_ignored(make_evidence):
    evidence = make_evidence([
        bash_span("b1", "echo build", stdout="build"),
        bash_span("b2", "make lint", status="ERROR", stderr="boom"),
    ])
    reliability = assess_reliability(evidence, [])
    assert reliability["errored"] == 1
    assert reliability["ignored"] == 1
    assert reliability["ignored_spans"] == ["b2"]
    assert reliability["ignored_feeding_claim"] == []


def test_p3_ignored_error_feeding_claim_caps_grade_at_acceptable(make_evidence):
    evidence = make_evidence([
        read_span("s1", "src/auth.py", content="def login(): ..."),
        edit_span("s2", "src/auth.py", diff="+fix"),
        bash_span("b1", "pytest tests/test_auth.py", status="ERROR",
                  stderr="2 failed"),
        response_span("Fixed src/auth.py; the auth suite passes."),
    ])
    correctness = _correctness(claims=[{
        "normalized_text": "pytest tests/test_auth.py passes",
        "load_bearing": True,
    }], covered=1)
    grade = grade_process(evidence, correctness)
    assert grade.detail["reliability"]["ignored_feeding_claim"] == ["b1"]
    assert grade.verdict == ACCEPTABLE


def test_p3_ignored_error_without_claim_overlap_does_not_cap(make_evidence):
    evidence = make_evidence([
        read_span("s1", "src/auth.py", content="def login(): ..."),
        edit_span("s2", "src/auth.py", diff="+fix"),
        bash_span("b1", "make docs", status="ERROR", stderr="boom"),
        response_span("Fixed src/auth.py."),
    ])
    correctness = _correctness(claims=[{
        "normalized_text": "the login form renders correctly",
        "load_bearing": True,
    }])
    grade = grade_process(evidence, correctness)
    assert grade.detail["reliability"]["ignored"] == 1
    assert grade.detail["reliability"]["ignored_feeding_claim"] == []
    assert grade.verdict == EFFICIENT


# ── P4: cost proportionality ─────────────────────────────────────

def test_p4_cache_read_share_flags_bloat_and_report_mentions_compaction(
        make_evidence):
    evidence = make_evidence(
        [read_span("s1", "src/foo.py", content="x = 1"),
         edit_span("s2", "src/foo.py", diff="+x = 2"),
         response_span("Edited src/foo.py.")],
        session_row={"cost_usd": 0, "prompts": 1, "input_tokens": 500,
                     "cache_read_tokens": 9000, "cache_creation_tokens": 500},
    )
    grade = grade_process(evidence)
    cost = grade.detail["cost"]
    assert cost["cache_read_share"] == pytest.approx(0.9)
    assert cost["cache_bloat"] is True
    assert cost["percentile"] is None     # cost 0 skips the DB lookup
    assert grade.verdict == ACCEPTABLE
    assert "compacted" in grade.report


def test_p4_cost_percentile_reads_comparable_sessions_from_db(monkeypatch):
    monkeypatch.setattr(process_mod, "_MIN_PERCENTILE_SAMPLE", 1)
    # No comparable sessions yet → no percentile.
    assert cost_percentile("t-main", 0.5, "single-shot") is None
    for tid in ("cmp-1", "cmp-2", "cmp-3"):
        seed_spans(tid, [])               # each: cost 0.5, prompts 1
    # Mid-rank: a cohort of equal costs reads as the median, not the top.
    assert cost_percentile("t-main", 0.5, "single-shot") == 0.5
    assert cost_percentile("t-main", 0.6, "single-shot") == 1.0
    assert cost_percentile("t-main", 0.4, "single-shot") == 0.0
    # Different task class compares against a different prompt band.
    assert cost_percentile("t-main", 0.5, "interactive") is None


def test_p4_cost_percentile_abstains_below_min_sample():
    for tid in ("cmp-1", "cmp-2", "cmp-3"):
        seed_spans(tid, [])
    # 3 comparable sessions < _MIN_PERCENTILE_SAMPLE → abstain, so a tiny
    # cohort can never gate the whole axis to wasteful.
    assert cost_percentile("t-main", 99.0, "single-shot") is None


def test_p4_runaway_percentile_grades_wasteful(make_evidence, monkeypatch):
    monkeypatch.setattr(process_mod, "_MIN_PERCENTILE_SAMPLE", 1)
    for tid in ("cmp-1", "cmp-2", "cmp-3"):
        seed_spans(tid, [])
    evidence = make_evidence(
        [read_span("s1", "src/foo.py", content="x = 1"),
         edit_span("s2", "src/foo.py", diff="+x = 2"),
         response_span("Edited src/foo.py.")],
        trace_id="t-main",
        session_row={"cost_usd": 5.0, "prompts": 1, "input_tokens": 1000,
                     "cache_read_tokens": 100, "cache_creation_tokens": 50},
    )
    grade = grade_process(evidence)
    assert grade.detail["cost"]["percentile"] == 1.0
    assert grade.detail["cost"]["verdict"] == RUNAWAY
    assert grade.verdict == WASTEFUL


def test_p4_elevated_downgraded_when_correctness_satisfied(
        make_evidence, monkeypatch):
    monkeypatch.setattr(process_mod, "cost_percentile",
                        lambda *args, **kwargs: 0.95)
    evidence = make_evidence([
        read_span("s1", "src/foo.py", content="x = 1"),
        edit_span("s2", "src/foo.py", diff="+x = 2"),
        response_span("Edited src/foo.py."),
    ])

    ungraded = grade_process(evidence)    # no correctness verdict yet
    assert ungraded.detail["cost"]["verdict"] == ELEVATED
    assert ungraded.verdict == ACCEPTABLE

    graded = grade_process(evidence, _correctness(verdict="satisfied"))
    assert graded.detail["cost"]["verdict"] == PROPORTIONATE
    assert graded.verdict == EFFICIENT


# ── verdict ladder ───────────────────────────────────────────────

def test_verdict_clean_trace_is_efficient(make_evidence):
    evidence = make_evidence([
        read_span("s1", "src/foo.py", content="x = 1"),
        edit_span("s2", "src/foo.py", diff="+x = 2"),
        bash_span("b1", "pytest -q", stdout="3 passed"),
        response_span("Updated src/foo.py; pytest -q reports 3 passed."),
    ])
    grade = grade_process(evidence)
    assert grade.verdict == EFFICIENT
    assert grade.axis == "process"
    assert grade.tier == "screen"
    assert grade.rubric_version == "v1"
    assert grade.scoreboard["tool_use"]["total"] == 3
    assert grade.scoreboard["tool_use"][APPROPRIATE] == 3


def test_verdict_moderate_waste_share_is_acceptable(make_evidence):
    # 1 suboptimal of 3 spans: waste share 0.33 — over the 0.25
    # efficient bar but under the 0.5 wasteful bar.
    evidence = make_evidence([
        bash_span("b1", "cat src/foo.py", stdout="x = 1"),
        read_span("s1", "src/foo.py", content="x = 1"),
        edit_span("s2", "src/foo.py", diff="+x = 2"),
        response_span("Updated src/foo.py."),
    ])
    grade = grade_process(evidence)
    assert grade.detail["tool_use"]["counts"][SUBOPTIMAL] == 1
    assert grade.verdict == ACCEPTABLE


def test_verdict_majority_waste_is_wasteful(make_evidence):
    # 2 suboptimal of 3 spans: waste share 0.67 > 0.5.
    evidence = make_evidence([
        bash_span("b1", "cat src/a.py", stdout="x = 1"),
        bash_span("b2", "grep -r needle src/", stdout="src/a.py:needle"),
        edit_span("s1", "src/a.py", diff="+x = 2"),
        response_span("Edited src/a.py."),
    ])
    grade = grade_process(evidence)
    assert grade.detail["tool_use"]["counts"][SUBOPTIMAL] == 2
    assert grade.verdict == WASTEFUL
