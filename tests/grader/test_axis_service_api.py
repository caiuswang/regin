"""Correctness gates, grading service, store, pareto, and the grades API.

Covers `lib/grader/correctness.py` (gate ordering + report shape),
`lib/grader/service.py` (tiering / escalation / persistence),
`lib/grader/store.py` (append-only rows, latest-per-axis reads),
`lib/grader/pareto.py` (off-frontier flags + summary), and
`web/blueprints/grades.py` (list / detail / run endpoints).
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import (
    StubLLM, bash_span, edit_span, prompt_span, response_span, seed_spans,
)

from lib.grader import store
from lib.grader.correctness import (
    FAIL, NEEDS_REVISION, SATISFIED, grade_correctness,
)
from lib.grader.models import AxisGrade, CONTRADICTED, UNGROUNDED
from lib.grader.pareto import pareto_points
from lib.grader.service import GradingError, grade_session

_REPORT_LINE_RE = re.compile(
    r"Groundedness \d+/\d+\. Coverage \d+/\d+\. "
    r"Source-quality \d+/\d+\.  Verdict: \w+")


def _satisfied_spans() -> list[dict]:
    """A session whose only claim is grounded by its own Edit diff and
    whose single checklist item is covered by that grounded claim."""
    return [
        prompt_span("update the parser to handle commas"),
        edit_span("e1", "src/parser.py",
                  diff="+def parse_row():  # handle commas"),
        response_span("Updated the parser in `parse_row` to handle "
                      "commas in src/parser.py."),
    ]


def _contradicted_spans() -> list[dict]:
    """A positive result claim whose only matching test run errored."""
    return [
        prompt_span("fix the failing parser tests"),
        bash_span("b1", ".venv/bin/python -m pytest tests/ -q",
                  stderr="2 failed, 1 passed", status="ERROR"),
        response_span("All 3 tests pass under pytest."),
    ]


def _ungrounded_spans() -> list[dict]:
    """A state claim about a file no span ever read, edited, or grepped."""
    return [
        prompt_span("describe the token validation flow"),
        response_span("The `validate_token` function in src/auth.py "
                      "rejects empty tokens."),
    ]


def _grade(axis: str, verdict: str) -> AxisGrade:
    return AxisGrade(axis=axis, verdict=verdict, tier="screen",
                     scoreboard={"x": 1}, report="line",
                     detail={"d": 2}, rubric_version="v1")


def _set_session_cost(trace_id: str, cost: float) -> None:
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE sessions SET cost_usd = ? WHERE trace_id = ?",
                     (cost, trace_id))
        conn.commit()
    finally:
        conn.close()


# ── correctness gates ────────────────────────────────────────────

def test_contradicted_load_bearing_claim_gates_to_fail(make_evidence):
    grade = grade_correctness(make_evidence(_contradicted_spans()))
    assert grade.verdict == FAIL
    claim_verdicts = {v["verdict"] for k, v in
                      grade.detail["verdicts"].items() if k != "c0"}
    assert CONTRADICTED in claim_verdicts
    assert _REPORT_LINE_RE.fullmatch(grade.report.splitlines()[0])
    assert grade.report.splitlines()[0].endswith("Verdict: fail")


def test_grounded_and_covered_session_is_satisfied(make_evidence):
    grade = grade_correctness(make_evidence(_satisfied_spans()))
    assert grade.verdict == SATISFIED
    assert grade.tier == "screen"          # no llm → screen
    assert grade.scoreboard["groundedness"] == {"grounded": 2, "total": 2}
    assert grade.scoreboard["coverage"] == {"covered": 1, "total": 1}
    assert grade.scoreboard["source_quality"]["authoritative"] == \
        grade.scoreboard["source_quality"]["total"]


def test_ungrounded_claim_needs_revision(make_evidence):
    grade = grade_correctness(make_evidence(_ungrounded_spans()))
    assert grade.verdict == NEEDS_REVISION
    non_aggregate = {k: v for k, v in grade.detail["verdicts"].items()
                     if k != "c0"}
    assert non_aggregate
    assert all(v["verdict"] == UNGROUNDED for v in non_aggregate.values())


def test_report_first_line_scoreboard_format(make_evidence):
    grade = grade_correctness(make_evidence(_satisfied_spans()))
    # the synthetic c0 counts toward groundedness but is never scored as
    # a source (it is grounded by the checklist, not a span)
    assert grade.report.splitlines()[0] == (
        "Groundedness 2/2. Coverage 1/1. Source-quality 1/1.  "
        "Verdict: satisfied")


# ── service.grade_session ────────────────────────────────────────

def test_grade_session_screen_persists_and_returns_both_axes():
    seed_spans("t-svc-screen", _satisfied_spans())
    result = grade_session("t-svc-screen", tier="screen")
    assert result["trace_id"] == "t-svc-screen"
    assert set(result["grades"]) == {"correctness", "process"}
    assert result["grades"]["correctness"]["verdict"] == "satisfied"
    assert result["grades"]["correctness"]["judge"] == "mechanical"
    assert result["grades"]["process"]["verdict"] == "efficient"

    persisted = store.latest_grades("t-svc-screen")
    assert set(persisted) == {"correctness", "process"}
    assert persisted["correctness"]["verdict"] == "satisfied"
    assert persisted["correctness"]["tier"] == "screen"
    assert persisted["process"]["verdict"] == \
        result["grades"]["process"]["verdict"]


def test_auto_tier_escalates_needs_revision_to_stub_judge(monkeypatch):
    import lib.grader.adapters as adapters
    from lib.settings import settings

    seed_spans("t-svc-auto", _ungrounded_spans())
    # the combined judge returns one JSON; an UNGROUNDED claim keeps the
    # correctness verdict at needs_revision after escalation.
    combined = json.dumps({"correctness": {
        "claims": [{"id": "c1",
                    "text": "validate_token rejects empty tokens",
                    "type": "state", "load_bearing": True,
                    "verdict": "UNGROUNDED", "span_id": None, "quote": "",
                    "reason": "no span shows it"}],
        "coverage": [{"item": "describe the token validation flow",
                      "verdict": "MISSING", "reason": "not shown"}]}})
    stub = StubLLM(combined)
    monkeypatch.setattr(adapters, "resolve_judge", lambda agent_id=None: stub)
    monkeypatch.setattr(settings.grader, "auto_escalate", True)

    result = grade_session("t-svc-auto", axes=("correctness",), tier="auto")
    corr = result["grades"]["correctness"]
    assert corr["verdict"] == "needs_revision"
    assert corr["tier"] == "deep"          # escalated past the screen pass
    assert corr["judge"] == "stub"         # judge label lands on the grade
    assert stub.prompts                    # the judge was actually consulted
    assert store.latest_grades("t-svc-auto")["correctness"]["judge"] == "stub"


def test_deep_tier_routes_process_to_agentic_judge(monkeypatch):
    import lib.grader.adapters as adapters

    seed_spans("t-svc-proc", _satisfied_spans())
    combined = json.dumps({"process": {
        "tool_use": [], "redundant_reads": [], "thrash_episodes": [],
        "reliability": {"recovered": [], "ignored": [],
                        "ignored_feeding_claim": []}}})
    stub = StubLLM(combined)
    monkeypatch.setattr(adapters, "resolve_judge", lambda agent_id=None: stub)

    result = grade_session("t-svc-proc", axes=("process",), tier="deep")
    proc = result["grades"]["process"]
    assert proc["tier"] == "deep"          # process now has a judge path
    assert proc["judge"] == "stub"
    assert stub.prompts and "<process>" in stub.prompts[0]


def test_deep_process_falls_back_to_mechanical_on_judge_failure(monkeypatch):
    import lib.grader.adapters as adapters

    seed_spans("t-svc-proc-fb", _satisfied_spans())
    stub = StubLLM()                       # no scripted answer → complete()=None
    monkeypatch.setattr(adapters, "resolve_judge", lambda agent_id=None: stub)

    result = grade_session("t-svc-proc-fb", axes=("process",), tier="deep")
    proc = result["grades"]["process"]
    assert proc["tier"] == "screen"        # fell back to the mechanical pass
    assert proc["judge"] == "mechanical"


def test_grade_session_unknown_trace_raises_grading_error():
    with pytest.raises(GradingError):
        grade_session("t-no-such-trace", tier="screen")


def test_grade_session_unknown_tier_raises_grading_error():
    with pytest.raises(GradingError):
        grade_session("t-whatever", tier="bogus")


# ── store ────────────────────────────────────────────────────────

def test_latest_grades_round_trip_picks_newest_row_per_axis():
    store.save_grade("t-store", _grade("correctness", "fail"))
    store.save_grade("t-store", _grade("process", "acceptable"))
    newest = store.save_grade("t-store", _grade("correctness", "satisfied"))

    out = store.latest_grades("t-store")
    assert set(out) == {"correctness", "process"}
    assert out["correctness"]["verdict"] == "satisfied"
    assert out["correctness"]["id"] == newest
    assert out["process"]["verdict"] == "acceptable"
    assert out["correctness"]["detail"] == {"d": 2}    # with_detail default
    assert out["correctness"]["scoreboard"] == {"x": 1}


def test_list_grades_filters_and_attaches_session_meta():
    seed_spans("t-list-a", _satisfied_spans())
    seed_spans("t-list-b", _ungrounded_spans())
    store.save_grade("t-list-a", _grade("correctness", "satisfied"))
    store.save_grade("t-list-a", _grade("process", "efficient"))
    store.save_grade("t-list-b", _grade("correctness", "fail"))
    store.save_grade("t-list-b", _grade("correctness", "needs_revision"))

    corr = store.list_grades(axis="correctness")
    assert [(g["trace_id"], g["verdict"]) for g in corr] == [
        ("t-list-b", "needs_revision"),    # newest row supersedes the fail
        ("t-list-a", "satisfied"),
    ]
    assert all(g["session"]["title"] == "grader test session" for g in corr)
    assert corr[0]["session"]["cost_usd"] == 0.5

    by_verdict = store.list_grades(verdict="needs_revision")
    assert [(g["trace_id"], g["axis"]) for g in by_verdict] == [
        ("t-list-b", "correctness")]


# ── pareto ───────────────────────────────────────────────────────

def test_pareto_points_summary_counts_and_cheaply_wrong_flag():
    seed_spans("t-par-good", _satisfied_spans())
    seed_spans("t-par-bad", _ungrounded_spans())
    _set_session_cost("t-par-good", 2.0)
    _set_session_cost("t-par-bad", 0.4)
    store.save_grade("t-par-good", _grade("correctness", "satisfied"))
    store.save_grade("t-par-bad", _grade("correctness", "fail"))

    out = pareto_points()
    points = {p["trace_id"]: p for p in out["points"]}
    assert set(points) == {"t-par-good", "t-par-bad"}
    assert points["t-par-bad"]["cheaply_wrong"] is True
    assert points["t-par-good"]["cheaply_wrong"] is False
    assert points["t-par-bad"]["correctness"] == "fail"

    summary = out["summary"]
    assert summary["sessions"] == 2
    assert summary["satisfied"] == 1
    assert summary["total_cost_usd"] == 2.4
    assert summary["cost_per_correct_outcome"] == 2.4


# ── web API ──────────────────────────────────────────────────────

def test_api_grades_list_and_session_detail(flask_client):
    seed_spans("t-api-1", _satisfied_spans())
    store.save_grade("t-api-1", _grade("correctness", "satisfied"))

    resp = flask_client.get("/api/grades")
    assert resp.status_code == 200
    rows = resp.get_json()["grades"]
    assert [r["trace_id"] for r in rows] == ["t-api-1"]
    assert rows[0]["session"]["title"] == "grader test session"

    resp = flask_client.get("/api/sessions/t-api-1/grades")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["trace_id"] == "t-api-1"
    assert body["grades"]["correctness"]["verdict"] == "satisfied"
    assert "detail" in body["grades"]["correctness"]


def test_api_post_grade_runs_screen_tier_and_persists(flask_client):
    seed_spans("t-api-run", _satisfied_spans())
    resp = flask_client.post("/api/sessions/t-api-run/grade",
                             json={"tier": "screen"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["trace_id"] == "t-api-run"
    assert set(body["grades"]) == {"correctness", "process"}
    assert body["grades"]["correctness"]["verdict"] == "satisfied"

    follow = flask_client.get("/api/sessions/t-api-run/grades").get_json()
    assert follow["grades"]["correctness"]["verdict"] == "satisfied"
    assert follow["grades"]["correctness"]["tier"] == "screen"


def test_api_post_grade_axes_list_grades_only_selected(flask_client):
    seed_spans("t-api-axes", _satisfied_spans())
    resp = flask_client.post("/api/sessions/t-api-axes/grade",
                             json={"tier": "screen", "axes": ["correctness"]})
    assert resp.status_code == 200
    assert set(resp.get_json()["grades"]) == {"correctness"}


def test_api_post_grade_bad_axes_is_400(flask_client):
    resp = flask_client.post("/api/sessions/t-any/grade",
                             json={"tier": "screen", "axes": ["bogus"]})
    assert resp.status_code == 400
    assert "axes" in resp.get_json()["error"]


def test_api_post_grade_empty_axes_is_400(flask_client):
    resp = flask_client.post("/api/sessions/t-any/grade",
                             json={"tier": "screen", "axes": []})
    assert resp.status_code == 400


def test_api_post_grade_unknown_aspect_is_400(flask_client):
    resp = flask_client.post("/api/sessions/t-any/grade",
                             json={"tier": "screen", "aspects": ["nonesuch"]})
    assert resp.status_code == 400
    assert "nonesuch" in resp.get_json()["error"]


def test_api_post_grade_distill_flag_threads_through(flask_client, monkeypatch):
    seed_spans("t-api-distill", _ungrounded_spans())
    spy = _wire_distill(monkeypatch, on_fail=False)   # global off
    resp = flask_client.post("/api/sessions/t-api-distill/grade",
                             json={"tier": "screen", "distill": True})
    assert resp.status_code == 200
    assert len(spy.calls) == 1                         # per-run opt-in honored


def test_api_post_grade_bad_tier_is_400(flask_client):
    resp = flask_client.post("/api/sessions/t-any/grade",
                             json={"tier": "bogus"})
    assert resp.status_code == 400
    assert "tier" in resp.get_json()["error"]


def test_api_post_grade_unknown_session_is_404(flask_client):
    resp = flask_client.post("/api/sessions/t-missing/grade",
                             json={"tier": "screen"})
    assert resp.status_code == 404
    assert "t-missing" in resp.get_json()["error"]


def test_api_post_grade_unknown_provider_is_400(flask_client, monkeypatch):
    from lib.settings import TopicProposalExternalAgent, settings
    monkeypatch.setattr(settings, "topic_proposal_external_agents",
                        {"claude": TopicProposalExternalAgent(command="claude")})
    seed_spans("t-api-prov", _satisfied_spans())
    resp = flask_client.post("/api/sessions/t-api-prov/grade",
                             json={"tier": "screen", "provider": "nope"})
    assert resp.status_code == 400
    assert "nope" in resp.get_json()["error"]


def test_api_post_grade_accepts_known_provider(flask_client, monkeypatch):
    # provider names a configured judge agent; the screen tier ignores it but
    # validation must pass so the deep/auto path can use it.
    from lib.settings import TopicProposalExternalAgent, settings
    monkeypatch.setattr(settings, "topic_proposal_external_agents",
                        {"kimi": TopicProposalExternalAgent(command="kimi")})
    seed_spans("t-api-prov-ok", _satisfied_spans())
    resp = flask_client.post("/api/sessions/t-api-prov-ok/grade",
                             json={"tier": "screen", "provider": "kimi"})
    assert resp.status_code == 200
    assert resp.get_json()["grades"]["correctness"]["verdict"] == "satisfied"


# ── Slice 2 of the grade→memory loop: distill-on-fail trigger ────────

class _DistillSpy:
    """Stands in for distill_session; records how the trigger called it."""

    def __init__(self):
        self.calls = []

    def __call__(self, store_, trace_id, *, llm=None, grade=None,
                 importance_bonus=0.0, scope=None):
        from lib.memory.distill import DistillResult
        self.calls.append({"trace_id": trace_id, "grade": grade,
                           "importance_bonus": importance_bonus})
        return DistillResult(trace_id=trace_id, proposed=1, source="llm")


def _wire_distill(monkeypatch, *, enabled=True, on_fail=True, spy=None):
    """Patch the lazy memory imports the trigger reaches for, plus settings."""
    import lib.memory as memory
    import lib.memory.adapters as mem_adapters
    import lib.memory.distill as mem_distill
    from lib.settings import settings

    spy = spy or _DistillSpy()
    monkeypatch.setattr(memory, "enabled", lambda: enabled)
    monkeypatch.setattr(memory, "get_store", lambda: object())
    monkeypatch.setattr(mem_adapters, "resolve_distiller", lambda: object())
    monkeypatch.setattr(mem_distill, "distill_session", spy)
    monkeypatch.setattr(settings.grader, "distill_on_fail", on_fail)
    monkeypatch.setattr(settings.grader, "distill_importance_bonus", 0.15)
    return spy


def test_failing_grade_triggers_distill_with_findings(monkeypatch):
    seed_spans("t-distill-fail", _ungrounded_spans())
    spy = _wire_distill(monkeypatch)
    grade_session("t-distill-fail", tier="screen")
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["trace_id"] == "t-distill-fail"
    assert call["importance_bonus"] == 0.15
    # the full grade payload (with detail) is handed to the distiller
    assert call["grade"]["correctness"]["verdict"] == "needs_revision"
    assert "verdicts" in call["grade"]["correctness"]["detail"]


def test_satisfied_grade_does_not_trigger_distill(monkeypatch):
    seed_spans("t-distill-clean", _satisfied_spans())
    spy = _wire_distill(monkeypatch)
    grade_session("t-distill-clean", tier="screen")
    assert spy.calls == []          # both axes passed → nothing to learn


def test_distill_on_fail_setting_off_skips(monkeypatch):
    seed_spans("t-distill-off", _ungrounded_spans())
    spy = _wire_distill(monkeypatch, on_fail=False)
    grade_session("t-distill-off", tier="screen")
    assert spy.calls == []


def test_per_run_distill_true_overrides_setting_off(monkeypatch):
    # explicit per-run opt-in distills even when the global setting is off
    seed_spans("t-distill-optin", _ungrounded_spans())
    spy = _wire_distill(monkeypatch, on_fail=False)
    grade_session("t-distill-optin", tier="screen", distill=True)
    assert len(spy.calls) == 1


def test_per_run_distill_false_overrides_setting_on(monkeypatch):
    # explicit per-run opt-out wins even when the global setting is on
    seed_spans("t-distill-optout", _ungrounded_spans())
    spy = _wire_distill(monkeypatch, on_fail=True)
    grade_session("t-distill-optout", tier="screen", distill=False)
    assert spy.calls == []


def test_distill_skipped_when_memory_disabled(monkeypatch):
    seed_spans("t-distill-memoff", _ungrounded_spans())
    spy = _wire_distill(monkeypatch, enabled=False)
    grade_session("t-distill-memoff", tier="screen")
    assert spy.calls == []


def test_distill_not_triggered_when_not_persisting(monkeypatch):
    seed_spans("t-distill-nopersist", _ungrounded_spans())
    spy = _wire_distill(monkeypatch)
    grade_session("t-distill-nopersist", tier="screen", persist=False)
    assert spy.calls == []


def test_distill_failure_does_not_break_grading(monkeypatch):
    seed_spans("t-distill-boom", _ungrounded_spans())

    def _boom(*a, **k):
        raise RuntimeError("judge subprocess died")

    _wire_distill(monkeypatch, spy=_boom)
    # the grade still returns despite the distill blowing up
    result = grade_session("t-distill-boom", tier="screen")
    assert result["grades"]["correctness"]["verdict"] == "needs_revision"
