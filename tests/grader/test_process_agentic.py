"""Tests for lib/grader/process_agentic.py — the agentic process judge's
deterministic seam: JSON parsing, span-id validation (cited spans must
exist; reliability findings intersect the spans that actually errored),
the P1–P3 → gate handoff, and the mechanical fallback on failure. The
judge's own trace investigation runs in the live subprocess; a StubLLM
returning the final findings drives the seam here.
"""

from __future__ import annotations

import json

from conftest import (
    StubLLM, bash_span, edit_span, prompt_span, read_span, response_span,
)

from lib.grader.process_agentic import grade_process_agentic


def _trajectory():
    return [
        prompt_span("fix the failing test"),
        read_span("s1", "util.py", content="def f(): pass"),
        read_span("s2", "util.py", content="def f(): pass"),
        bash_span("s3", "pytest", stderr="boom", status="ERROR"),
        edit_span("s4", "util.py", diff="+ fixed"),
        response_span("Fixed the test."),
    ]


def _judge(obj):
    return StubLLM(json.dumps(obj))


def test_process_agentic_maps_findings_to_grade(make_evidence):
    evidence = make_evidence(_trajectory())
    judge = _judge({
        "tool_use": [{"span_id": "s1", "verdict": "WASTED",
                      "reason": "dead-end read"}],
        "redundant_reads": [{"path": "util.py", "spans": ["s1", "s2"],
                             "reason": "re-read, no edit between"}],
        "thrash_episodes": [],
        "reliability": {"recovered": ["s3"], "ignored": [],
                        "ignored_feeding_claim": []},
    })

    grade = grade_process_agentic(evidence, judge, evidence.trace_id)

    assert grade is not None
    assert grade.tier == "deep" and grade.judge == "stub"
    tu = grade.detail["tool_use"]
    assert tu["total"] == 4 and tu["counts"]["WASTED"] == 1
    assert tu["counts"]["APPROPRIATE"] == 3
    assert len(grade.detail["redundancy"]["redundant_reads"]) == 1
    rel = grade.detail["reliability"]
    assert (rel["errored"], rel["recovered"], rel["ignored"]) == (1, 1, 0)


def test_process_agentic_drops_unknown_span_ids(make_evidence):
    evidence = make_evidence(_trajectory())
    judge = _judge({
        "tool_use": [{"span_id": "ghost", "verdict": "WASTED",
                      "reason": "hallucinated span"}],
        "reliability": {},
    })

    grade = grade_process_agentic(evidence, judge, evidence.trace_id)

    # the cited span doesn't exist → finding dropped, nothing flagged
    assert grade is not None
    assert grade.detail["tool_use"]["counts"]["WASTED"] == 0
    assert grade.detail["tool_use"]["findings"] == []


def test_process_agentic_reliability_intersects_errored(make_evidence):
    evidence = make_evidence(_trajectory())
    # judge marks a Read (s1, not an error) as ignored — must be dropped
    judge = _judge({
        "tool_use": [],
        "reliability": {"recovered": [], "ignored": ["s1"],
                        "ignored_feeding_claim": ["s1"]},
    })

    grade = grade_process_agentic(evidence, judge, evidence.trace_id)

    rel = grade.detail["reliability"]
    assert rel["ignored"] == 0 and rel["ignored_feeding_claim"] == []


def test_process_agentic_returns_none_on_unparseable(make_evidence):
    evidence = make_evidence(_trajectory())
    assert grade_process_agentic(
        evidence, StubLLM("not json"), evidence.trace_id) is None


def test_process_agentic_returns_none_without_ledger_keys(make_evidence):
    evidence = make_evidence(_trajectory())
    judge = StubLLM(json.dumps({"unrelated": 1}))
    assert grade_process_agentic(
        evidence, judge, evidence.trace_id) is None


def test_process_agentic_returns_none_without_judge(make_evidence):
    evidence = make_evidence(_trajectory())
    assert grade_process_agentic(evidence, None, evidence.trace_id) is None
