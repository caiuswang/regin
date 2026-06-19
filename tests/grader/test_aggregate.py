"""Slice 3 of the grade→memory loop: cross-session failure-mode
aggregation (`lib/grader/aggregate.py` + `lib/grader/failure_modes.py`).

A failure mode that recurs across enough distinct sessions is consolidated
into a single, idempotent agent-memory lesson; rarer modes stay below the
fold.
"""

from __future__ import annotations

import json

import lib.memory as memory
from lib.grader import store
from lib.grader.aggregate import aggregate_failure_modes
from lib.grader.failure_modes import remediation_for, session_modes


def _save_grade(trace_id, axis, verdict, detail, *, is_test=0):
    from lib.grader.models import AxisGrade
    store.save_grade(trace_id, AxisGrade(
        axis=axis, verdict=verdict, tier="screen", scoreboard={},
        report="", detail=detail, rubric_version="v1"), is_test=is_test)


def _agg_rows():
    """Consolidated (grade-aggregate) memories currently in the store."""
    rows = memory.get_store().list_memories(include_tests=True)
    return [r for r in rows if "grade-aggregate" in (r.get("tags") or [])]


def _ungrounded_state_detail(text="src/auth.py validates the token"):
    """A needs_revision correctness detail with one UNGROUNDED state claim."""
    return {
        "claims": [{"id": "c1", "type": "state", "normalized_text": text,
                    "referents": {"file": "src/auth.py"}, "load_bearing": True}],
        "verdicts": {"c1": {"verdict": "UNGROUNDED", "reason": "no Read span"},
                     "c0": {"verdict": "UNGROUNDED"}},
        "checklist": [], "sources": [],
    }


# ── failure_modes.session_modes ──────────────────────────────────────

def test_session_modes_keys_and_examples():
    grades = {
        "correctness": {"verdict": "needs_revision", "detail": {
            "claims": [
                {"id": "c1", "type": "state", "normalized_text": "X does Y",
                 "load_bearing": True},
                {"id": "c2", "type": "result", "normalized_text": "tests pass",
                 "load_bearing": True},
                {"id": "c3", "type": "state", "normalized_text": "grounded one",
                 "load_bearing": True},
                {"id": "c4", "type": "state", "normalized_text": "incidental",
                 "load_bearing": False},
            ],
            "verdicts": {
                "c1": {"verdict": "UNGROUNDED"}, "c2": {"verdict": "STALE"},
                "c3": {"verdict": "GROUNDED"}, "c4": {"verdict": "UNGROUNDED"},
                "c0": {"verdict": "UNGROUNDED"}},
            "checklist": [{"item": "add a test", "verdict": "MISSING"}],
            "sources": [{"source": "a blog", "verdict": "PROXY"}]}},
        "process": {"verdict": "wasteful", "detail": {
            "tool_use": {"findings": [
                {"verdict": "WASTED", "reason": "cat output unused"}]},
            "redundancy": {"redundant_reads": [{"p": 1}, {"p": 2}],
                           "thrash_episodes": []},
            "reliability": {"ignored_feeding_claim": ["s1"]}}},
    }
    modes = session_modes(grades)
    assert set(modes) == {
        "claim:state:UNGROUNDED", "claim:result:STALE",
        "coverage:MISSING", "source:PROXY",
        "process:WASTED", "process:redundancy:redundant_reads",
        "process:ignored_error_feeds_claim",
    }
    # grounded, non-load-bearing, c0, and empty buckets never become modes
    assert "claim:state:GROUNDED" not in modes
    assert modes["claim:state:UNGROUNDED"] == "X does Y"


def test_clean_session_has_no_modes():
    grades = {"correctness": {"verdict": "satisfied", "detail": {
        "claims": [{"id": "c1", "type": "state", "load_bearing": True}],
        "verdicts": {"c1": {"verdict": "GROUNDED"}},
        "checklist": [{"item": "x", "verdict": "COVERED"}], "sources": []}}}
    assert session_modes(grades) == {}


def test_remediation_maps_claim_type_prefix():
    # claim modes key off the type, independent of the verdict
    assert remediation_for("claim:state:UNGROUNDED") == \
        remediation_for("claim:state:STALE")
    assert "Read or Grep" in remediation_for("claim:state:UNGROUNDED")
    assert remediation_for("totally:unknown:mode") == ""


# ── aggregate_failure_modes ──────────────────────────────────────────

def test_recurring_mode_consolidated_into_one_lesson():
    for i in range(3):
        _save_grade(f"agg-{i}", "correctness", "needs_revision",
                    _ungrounded_state_detail(f"file{i}.py does thing {i}"))
    result = aggregate_failure_modes(min_sessions=3)
    assert result.trace_count == 3
    assert result.recurring == 1
    assert result.created == 1 and result.refreshed == 0

    agg = _agg_rows()
    assert len(agg) == 1
    lesson = agg[0]
    assert "claim:state:UNGROUNDED" in lesson["tags"]
    assert lesson["status"] == "proposed"          # human-gated
    assert "3 distinct sessions" in lesson["body"]
    assert "Read or Grep" in lesson["body"]         # remediation embedded


def test_below_threshold_mode_is_not_consolidated():
    for i in range(2):
        _save_grade(f"rare-{i}", "correctness", "needs_revision",
                    _ungrounded_state_detail())
    result = aggregate_failure_modes(min_sessions=3)
    assert result.recurring == 0 and result.created == 0
    assert memory.get_store().list_memories(include_tests=True) == []


def test_aggregation_is_idempotent_refresh_not_duplicate():
    for i in range(3):
        _save_grade(f"idem-{i}", "correctness", "needs_revision",
                    _ungrounded_state_detail())
    first = aggregate_failure_modes(min_sessions=3)
    assert first.created == 1
    # a fourth failing session arrives, then we re-run
    _save_grade("idem-3", "correctness", "needs_revision",
                _ungrounded_state_detail())
    second = aggregate_failure_modes(min_sessions=3)
    assert second.created == 0 and second.refreshed == 1
    rows = _agg_rows()
    assert len(rows) == 1                           # one row, updated in place
    assert "4 distinct sessions" in rows[0]["body"]


def test_satisfied_grades_are_not_scanned():
    _save_grade("ok-1", "correctness", "satisfied", _ungrounded_state_detail())
    _save_grade("ok-2", "process", "efficient", {})
    result = aggregate_failure_modes(min_sessions=1)
    assert result.trace_count == 0 and result.recurring == 0


def test_superseded_satisfied_grade_drops_from_pool():
    # an early failing grade later upgraded to satisfied must not count
    _save_grade("up-1", "correctness", "needs_revision",
                _ungrounded_state_detail())
    _save_grade("up-1", "correctness", "satisfied",
                _ungrounded_state_detail())     # newer row wins
    result = aggregate_failure_modes(min_sessions=1)
    assert result.trace_count == 0 and result.recurring == 0
    assert _agg_rows() == []


def test_dry_run_reports_without_writing():
    for i in range(3):
        _save_grade(f"dry-{i}", "correctness", "needs_revision",
                    _ungrounded_state_detail())
    result = aggregate_failure_modes(min_sessions=3, persist=False)
    assert result.recurring == 1
    assert result.created == 0 and result.refreshed == 0
    assert memory.get_store().list_memories(include_tests=True) == []


def test_test_grades_excluded_by_default():
    for i in range(3):
        _save_grade(f"t-{i}", "correctness", "needs_revision",
                    _ungrounded_state_detail(), is_test=1)
    result = aggregate_failure_modes(min_sessions=3)
    assert result.trace_count == 0 and result.created == 0


def test_disabled_memory_is_a_noop(monkeypatch):
    monkeypatch.setattr(memory, "enabled", lambda: False)
    for i in range(3):
        _save_grade(f"off-{i}", "correctness", "needs_revision",
                    _ungrounded_state_detail())
    result = aggregate_failure_modes(min_sessions=3)
    assert result.trace_count == 0 and result.created == 0
