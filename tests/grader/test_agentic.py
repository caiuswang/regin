"""Tests for lib/grader/agentic.py — the self-fetching agentic judge's
deterministic seam: JSON-verdict parsing, the anti-paraphrase quote guard
(verbatim → punctuation-fold → ≥80% word-subset, else downgrade),
source-kind mapping, and the rubric-gate handoff. The judge's own
tool-driven investigation runs in the live subprocess; a StubLLM returning
the final verdict drives the seam here.
"""

from __future__ import annotations

import json

from conftest import StubLLM, prompt_span, read_span, response_span

from lib.grader.agentic import _build_prompt, grade_correctness_agentic


def _judge(claims, coverage=None):
    return StubLLM(json.dumps({
        "claims": claims,
        "coverage": coverage or [{"item": "do the task",
                                  "verdict": "COVERED", "reason": "ok"}],
    }))


def test_build_prompt_is_self_fetch_not_embed():
    prompt = _build_prompt("TRACE123", ".venv/bin/python")
    # the judge is told to fetch, and no evidence is embedded
    assert "TRACE123" in prompt
    assert "trace dump TRACE123 --index" in prompt
    assert "trace span TRACE123" in prompt
    assert len(prompt) < 6000          # tiny — content lives in the spans it fetches


def test_agentic_grounds_claim_with_verbatim_quote(make_evidence):
    evidence = make_evidence([
        prompt_span("fix the retry backoff"),
        read_span("rd1", "lib/foo.py",
                  content="def frobnicate(retries):\n    return retries * 2"),
        response_span("`frobnicate` in lib/foo.py doubles the retry count."),
    ])
    judge = _judge([{
        "id": "c1", "text": "frobnicate doubles the retry count",
        "type": "state", "load_bearing": True, "verdict": "GROUNDED",
        "span_id": "rd1", "quote": "def frobnicate(retries):",
        "reason": "read shows it",
    }])

    grade = grade_correctness_agentic(evidence, judge, evidence.trace_id)

    assert grade is not None
    assert grade.tier == "deep" and grade.judge == "stub"
    assert grade.detail["verdicts"]["c1"]["verdict"] == "GROUNDED"
    assert grade.detail["verdicts"]["c1"]["source_kind"] == "read"


def test_agentic_downgrades_unverifiable_quote(make_evidence):
    evidence = make_evidence([
        read_span("rd1", "lib/foo.py", content="def frobnicate(retries):"),
        response_span("frobnicate doubles the retry count"),
    ])
    judge = _judge([{
        "id": "c1", "text": "frobnicate doubles the retry count",
        "type": "state", "verdict": "GROUNDED", "span_id": "rd1",
        "quote": "it doubles the retry count", "reason": "paraphrase",
    }])

    grade = grade_correctness_agentic(evidence, judge, evidence.trace_id)

    verdict = grade.detail["verdicts"]["c1"]
    assert verdict["verdict"] == "UNGROUNDED"
    assert "not substantiated" in verdict["reason"]


def test_agentic_token_subset_grounds_reordered_quote(make_evidence):
    evidence = make_evidence([
        read_span("rd1", "lib/foo.py",
                  content="the retry backoff doubles on each failed attempt"),
        response_span("foo.py doubles the backoff on each retry"),
    ])
    judge = _judge([{
        "id": "c1", "text": "foo.py doubles the backoff on retry",
        "type": "state", "verdict": "GROUNDED", "span_id": "rd1",
        "quote": "backoff doubles on each retry attempt",   # reordered, all present
        "reason": "read shows it",
    }])

    grade = grade_correctness_agentic(evidence, judge, evidence.trace_id)

    assert grade.detail["verdicts"]["c1"]["verdict"] == "GROUNDED"


def test_agentic_token_subset_rejects_fabricated_quote(make_evidence):
    evidence = make_evidence([
        read_span("rd1", "lib/foo.py", content="def frobnicate(retries):"),
        response_span("the cache is invalidated on write"),
    ])
    judge = _judge([{
        "id": "c1", "text": "the cache is invalidated on write",
        "type": "state", "verdict": "GROUNDED", "span_id": "rd1",
        "quote": "cache invalidated on every write through the proxy",
        "reason": "fabricated",
    }])

    grade = grade_correctness_agentic(evidence, judge, evidence.trace_id)

    assert grade.detail["verdicts"]["c1"]["verdict"] == "UNGROUNDED"


def test_agentic_grounds_negative_claim_by_absence(make_evidence):
    # "nothing pushed" — no span performs a push, so the absence confirms
    # the negative claim; GROUNDED with no quote, source_kind judge.
    evidence = make_evidence([
        read_span("rd1", "a.py", content="x = 1"),
        response_span("Committed the fix. Nothing pushed."),
    ])
    judge = _judge([{
        "id": "c1", "text": "Nothing pushed", "type": "state",
        "load_bearing": False, "verdict": "GROUNDED", "span_id": None,
        "quote": "", "by_absence": True, "reason": "no git push span",
    }])

    grade = grade_correctness_agentic(evidence, judge, evidence.trace_id)

    verdict = grade.detail["verdicts"]["c1"]
    assert verdict["verdict"] == "GROUNDED"
    assert verdict["evidence_span_id"] is None
    assert "absence" in verdict["evidence_ref"]


def test_agentic_absence_flag_ignored_on_positive_claim(make_evidence):
    # by_absence must not ground a POSITIVE claim — the negation guard
    # rejects it, and with no quote it falls through to UNGROUNDED.
    evidence = make_evidence([response_span("Added the helper.")])
    judge = _judge([{
        "id": "c1", "text": "Added the helper", "type": "state",
        "verdict": "GROUNDED", "span_id": None, "quote": "",
        "by_absence": True, "reason": "claims absence falsely",
    }])

    grade = grade_correctness_agentic(evidence, judge, evidence.trace_id)

    assert grade.detail["verdicts"]["c1"]["verdict"] == "UNGROUNDED"


def test_agentic_returns_none_on_unparseable(make_evidence):
    evidence = make_evidence([response_span("done")])
    assert grade_correctness_agentic(
        evidence, StubLLM("no json here"), evidence.trace_id) is None


def test_agentic_returns_none_without_judge(make_evidence):
    evidence = make_evidence([response_span("done")])
    assert grade_correctness_agentic(
        evidence, None, evidence.trace_id) is None


def test_agentic_returns_none_on_empty_ledger(make_evidence):
    evidence = make_evidence([response_span("done")])
    judge = StubLLM(json.dumps({"claims": [], "coverage": []}))
    assert grade_correctness_agentic(
        evidence, judge, evidence.trace_id) is None
