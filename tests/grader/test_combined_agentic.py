"""The combined deep judge: one investigation, many per-dimension verdicts.

Covers `lib/grader/combined_agentic.py` and its wiring into
`service.grade_session` — a single judge call grades the requested axes and
gradeable aspects, each parsed independently and gated by the same builders
as the standalone judges. Aspects are LLM-only dimensions with their own
`satisfied`/`needs_revision`/`fail` verdict.
"""

from __future__ import annotations

import json

import pytest

from conftest import StubLLM, edit_span, prompt_span, response_span, seed_spans

from lib.grader.service import GradingError, grade_session


def _satisfied_spans() -> list[dict]:
    return [
        prompt_span("update the parser to handle commas"),
        edit_span("e1", "src/parser.py",
                  diff="+def parse_row():  # handle commas"),
        response_span("Updated the parser in `parse_row` to handle "
                      "commas in src/parser.py."),
    ]


def _combined_json(*, correctness=True, process=True, aspects=None) -> str:
    obj: dict = {}
    if correctness:
        obj["correctness"] = {
            "claims": [{"id": "c1",
                        "text": "Updated parse_row to handle commas",
                        "type": "state", "load_bearing": True,
                        "verdict": "GROUNDED", "span_id": "e1",
                        "quote": "+def parse_row():  # handle commas",
                        "reason": "edit diff adds it"}],
            "coverage": [{"item": "update the parser to handle commas",
                          "verdict": "COVERED", "reason": "c1 grounded"}]}
    if process:
        obj["process"] = {"tool_use": [], "redundant_reads": [],
                          "thrash_episodes": [],
                          "reliability": {"recovered": [], "ignored": [],
                                          "ignored_feeding_claim": []}}
    if aspects:
        obj["aspects"] = aspects
    return json.dumps(obj)


def _patch_judge(monkeypatch, stub):
    import lib.grader.adapters as adapters
    monkeypatch.setattr(adapters, "resolve_judge", lambda agent_id=None: stub)


def test_one_judge_call_grades_axes_and_aspect(monkeypatch):
    seed_spans("t-cmb-all", _satisfied_spans())
    stub = StubLLM(_combined_json(aspects={
        "safety": {"verdict": "satisfied", "summary": "no destructive actions",
                   "findings": [{"reason": "only an edit + a test",
                                 "span_id": "e1", "quote": ""}]}}))
    _patch_judge(monkeypatch, stub)

    result = grade_session("t-cmb-all", tier="deep", aspects=["safety"], persist=False)
    grades = result["grades"]
    assert set(grades) == {"correctness", "process", "safety"}
    assert grades["correctness"]["verdict"] == "satisfied"
    assert grades["safety"]["verdict"] == "satisfied"
    assert grades["safety"]["tier"] == "deep"
    assert grades["safety"]["judge"] == "stub"
    # the whole point: ONE subprocess for all three dimensions
    assert len(stub.prompts) == 1
    assert "<correctness>" in stub.prompts[0]
    assert "<process>" in stub.prompts[0]
    assert "safety" in stub.prompts[0]


def test_aspect_only_run_needs_no_axis(monkeypatch):
    seed_spans("t-cmb-aspect", _satisfied_spans())
    stub = StubLLM(json.dumps({"aspects": {
        "safety": {"verdict": "fail", "summary": "ran rm -rf /",
                   "findings": []}}}))
    _patch_judge(monkeypatch, stub)

    result = grade_session("t-cmb-aspect", axes=(), tier="deep",
                           aspects=["safety"], persist=False)
    assert set(result["grades"]) == {"safety"}
    assert result["grades"]["safety"]["verdict"] == "fail"


def test_unknown_aspect_verdict_defaults_to_needs_revision(monkeypatch):
    seed_spans("t-cmb-badverdict", _satisfied_spans())
    stub = StubLLM(json.dumps({"aspects": {
        "safety": {"verdict": "looks fine", "summary": "", "findings": []}}}))
    _patch_judge(monkeypatch, stub)

    result = grade_session("t-cmb-badverdict", axes=(), tier="deep",
                           aspects=["safety"], persist=False)
    assert result["grades"]["safety"]["verdict"] == "needs_revision"


def test_hallucinated_aspect_span_is_dropped(monkeypatch):
    seed_spans("t-cmb-halluc", _satisfied_spans())
    stub = StubLLM(json.dumps({"aspects": {"safety": {
        "verdict": "needs_revision", "summary": "concern",
        "findings": [{"reason": "made up", "span_id": "nope-999",
                      "quote": ""}]}}}))
    _patch_judge(monkeypatch, stub)

    result = grade_session("t-cmb-halluc", axes=(), tier="deep",
                           aspects=["safety"], persist=False)
    findings = result["grades"]["safety"]["detail"]["findings"]
    assert findings and findings[0]["span_id"] is None   # invalid id stripped


def test_judge_failure_falls_back_to_mechanical_per_axis(monkeypatch):
    seed_spans("t-cmb-fallback", _satisfied_spans())
    stub = StubLLM()                       # complete() → None → empty parse
    _patch_judge(monkeypatch, stub)

    result = grade_session("t-cmb-fallback", tier="deep", persist=False)
    # axes still graded — they fell back to the mechanical screen
    assert result["grades"]["correctness"]["tier"] == "screen"
    assert result["grades"]["process"]["tier"] == "screen"


def test_aspect_skipped_on_screen_tier(monkeypatch):
    seed_spans("t-cmb-screen", _satisfied_spans())
    result = grade_session("t-cmb-screen", tier="screen", aspects=["safety"], persist=False)
    assert "safety" not in result["grades"]   # aspects are LLM-only
    assert set(result["grades"]) == {"correctness", "process"}


def test_unknown_aspect_raises():
    seed_spans("t-cmb-unknown", _satisfied_spans())
    with pytest.raises(GradingError):
        grade_session("t-cmb-unknown", tier="deep", aspects=["nonesuch"])


def test_builtin_axis_not_gradeable_as_aspect():
    seed_spans("t-cmb-builtin", _satisfied_spans())
    with pytest.raises(GradingError):
        grade_session("t-cmb-builtin", aspects=["correctness"])


def test_no_dimension_selected_raises():
    seed_spans("t-cmb-nodim", _satisfied_spans())
    with pytest.raises(GradingError):
        grade_session("t-cmb-nodim", axes=(), aspects=[])
