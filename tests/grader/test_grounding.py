"""Tests for lib/grader/grounding.py — per-claim-type grounding.

Covers the grounding map (state/result/external/diagnostic), the
timeline STALE rule (evidence predating a later mutation), negative
result claims and the negation guard, and the deep-tier LLM rescue
with its verbatim-quote anti-paraphrase guard.

Evidence is built in-memory via the `make_evidence` fixture; claims are
constructed directly except for one end-to-end heuristic-extraction
test.
"""

from __future__ import annotations

import json

from conftest import (
    StubLLM, bash_span, edit_span, fetch_span, grep_span, prompt_span,
    read_span, response_span,
)

from lib.grader.extraction import extract_claims_heuristic
from lib.grader.grounding import claim_is_negative, ground_claims
from lib.grader.models import (
    CONTRADICTED, GROUNDED, STALE, UNGROUNDED, Claim,
)


def _claim(ctype: str, raw: str, **referents) -> Claim:
    return Claim(id="c1", raw_text=raw, normalized_text=raw,
                 type=ctype, referents=referents)


def _ground_one(claim, evidence, llm=None):
    return ground_claims([claim], evidence, llm=llm)[claim.id]


# ── state claims ─────────────────────────────────────────────────

def test_state_claim_grounded_by_read_containing_symbol(make_evidence):
    claim = _claim("state", "`frobnicate` in lib/foo.py handles retry backoff",
                   file="lib/foo.py", symbol="frobnicate")
    evidence = make_evidence(
        [read_span("rd1", "lib/foo.py", content="def frobnicate(retries):")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "rd1"
    assert verdict.source_kind == "read"
    # file:line — the symbol is on line 1 of the recorded content (§11.2)
    assert verdict.evidence_ref == "lib/foo.py:1"


def test_state_claim_stale_when_later_edit_touches_same_path(make_evidence):
    claim = _claim("state", "`frobnicate` in lib/foo.py handles retry backoff",
                   file="lib/foo.py", symbol="frobnicate")
    evidence = make_evidence([
        read_span("rd1", "lib/foo.py", content="def frobnicate(retries):"),
        edit_span("ed1", "lib/foo.py", diff="+    return retries * 2"),
    ])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == STALE
    assert verdict.evidence_span_id == "rd1"
    assert "ed1" in verdict.reason


def test_state_claim_ungrounded_with_no_supporting_span(make_evidence):
    claim = _claim("state", "`frobnicate` in lib/foo.py handles retry backoff",
                   file="lib/foo.py", symbol="frobnicate")
    evidence = make_evidence([])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == UNGROUNDED
    assert verdict.evidence_span_id is None
    assert "no Read/Grep/Edit span" in verdict.reason


def test_change_claim_grounded_by_edit_diff_containing_symbol(make_evidence):
    claim = _claim("state", "Added `frobnicate` to lib/foo.py",
                   file="lib/foo.py", symbol="frobnicate")
    evidence = make_evidence(
        [edit_span("ed1", "lib/foo.py", diff="+def frobnicate(retries):")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "ed1"
    assert verdict.source_kind == "edit"


def test_state_claim_falls_back_to_grep_pattern(make_evidence):
    claim = _claim("state", "`frobnicate` is referenced from three call sites",
                   symbol="frobnicate")
    evidence = make_evidence([grep_span("g1", r"frobnicate\(")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "g1"
    assert verdict.source_kind == "grep"
    assert verdict.evidence_ref == r"frobnicate\("


# ── result claims ────────────────────────────────────────────────

def test_result_claim_grounded_by_successful_matching_run(make_evidence):
    claim = _claim("result", "All 12 tests pass")
    evidence = make_evidence(
        [bash_span("b1", ".venv/bin/python -m pytest -q",
                   stdout="12 passed in 1.2s")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "b1"
    assert verdict.source_kind == "bash"


def test_positive_result_claim_contradicted_by_latest_failed_run(make_evidence):
    claim = _claim("result", "All tests pass")
    evidence = make_evidence([
        bash_span("b1", "pytest -q", stdout="12 passed"),
        bash_span("b2", "pytest -q", stderr="1 failed, 11 passed",
                  status="ERROR"),
    ])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == CONTRADICTED
    assert verdict.evidence_span_id == "b2"   # the latest run decides
    assert "1 failed" in verdict.reason


def test_result_claim_stale_when_run_predates_later_edit(make_evidence):
    # The survey worked example: "all tests pass" but the last pytest
    # run happened before the final edit.
    claim = _claim("result", "All tests pass")
    evidence = make_evidence([
        bash_span("b1", "pytest -q", stdout="12 passed"),
        edit_span("ed1", "lib/foo.py", diff="+x = 1"),
    ])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == STALE
    assert verdict.evidence_span_id == "b1"
    assert "re-run" in verdict.reason


def test_negative_result_claim_grounded_by_error_run(make_evidence):
    claim = _claim("result", "the build fails with a type error")
    evidence = make_evidence(
        [bash_span("b1", "npm run build", stderr="TS2345: bad argument",
                   status="ERROR")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "b1"


def test_commit_hash_claim_matched_via_bash_stdout(make_evidence):
    claim = _claim("result", "Committed as `4f9c2ab`")
    evidence = make_evidence(
        [bash_span("b1", "git commit -m 'feat: frobnicate'",
                   stdout="[master 4f9c2ab] feat: frobnicate")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "b1"


def test_result_claim_ungrounded_without_matching_run(make_evidence):
    claim = _claim("result", "All tests pass")
    evidence = make_evidence([])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == UNGROUNDED
    assert "no Bash span" in verdict.reason


def test_claim_is_negative_and_negation_guard():
    assert claim_is_negative("the build fails")
    assert claim_is_negative("pytest errored out")
    assert not claim_is_negative("all 12 tests pass")
    # Negation guard: these mention failure words but assert success.
    assert not claim_is_negative("no failures remained")
    assert not claim_is_negative("zero errors in the log")
    assert not claim_is_negative("fixed the failing test")


# ── external claims ──────────────────────────────────────────────

def test_external_claim_grounded_by_webfetch_url(make_evidence):
    claim = _claim("external", "per the docs the SDK defaults to streaming",
                   url="https://docs.example.com/sdk/streaming")
    evidence = make_evidence(
        [fetch_span("f1", "https://docs.example.com/sdk/streaming")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "f1"
    assert verdict.source_kind == "webfetch"


def test_external_claim_ungrounded_without_matching_fetch(make_evidence):
    claim = _claim("external", "per the docs the SDK defaults to streaming",
                   url="https://docs.example.com/sdk/streaming")
    evidence = make_evidence(
        [fetch_span("f1", "https://unrelated.example.org/changelog")])

    verdict = _ground_one(claim, evidence)

    assert verdict.verdict == UNGROUNDED
    assert "no WebFetch/WebSearch span" in verdict.reason


# ── diagnostic claims ────────────────────────────────────────────

_DIAG_CAUSE = read_span("rd1", "lib/net.py",
                        content="def retry_loop():\n    while True: pass")
_DIAG_EFFECT = bash_span("b1", "pytest tests/test_net.py -q",
                         stdout="1 failed: test_timeout", status="ERROR")


def _diagnostic_claim() -> Claim:
    return _claim("diagnostic",
                  "the timeout is caused by `retry_loop` in lib/net.py",
                  file="lib/net.py", symbol="retry_loop", command="pytest")


def test_diagnostic_grounded_when_cause_and_effect_both_present(make_evidence):
    evidence = make_evidence([_DIAG_CAUSE, _DIAG_EFFECT])

    verdict = _ground_one(_diagnostic_claim(), evidence)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "rd1"
    assert "rd1" in verdict.reason and "b1" in verdict.reason


def test_diagnostic_ungrounded_when_either_half_is_missing(make_evidence):
    cause_only = _ground_one(_diagnostic_claim(),
                             make_evidence([_DIAG_CAUSE]))
    assert cause_only.verdict == UNGROUNDED
    assert "no repro/result span shows the effect" in cause_only.reason

    effect_only = _ground_one(_diagnostic_claim(),
                              make_evidence([_DIAG_EFFECT]))
    assert effect_only.verdict == UNGROUNDED
    assert "no span shows the cited cause" in effect_only.reason


# ── deep-tier LLM rescue ─────────────────────────────────────────

_UTIL_CONTENT = "def clamp_delay(delay):\n    return min(delay * 2, MAX_BACKOFF)"


def _rescue_setup(make_evidence):
    """A state claim that grounds mechanically to UNGROUNDED, with one
    lexically-related Read span as the rescue candidate."""
    claim = _claim("state", "`cap_backoff` in lib/util.py caps the delay",
                   file="lib/util.py", symbol="cap_backoff")
    evidence = make_evidence([read_span("rd1", "lib/util.py",
                                        content=_UTIL_CONTENT)])
    return claim, evidence


def test_llm_rescue_supports_with_verbatim_quote_grounds(make_evidence):
    claim, evidence = _rescue_setup(make_evidence)
    llm = StubLLM(json.dumps({
        "verdict": "SUPPORTS", "span_id": "rd1",
        "quote": "return min(delay * 2, MAX_BACKOFF)",   # verbatim
    }))

    verdict = _ground_one(claim, evidence, llm=llm)

    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "rd1"
    assert verdict.source_kind == "judge"
    assert len(llm.prompts) == 1
    assert claim.normalized_text in llm.prompts[0]


def test_llm_rescue_rejects_paraphrased_quote(make_evidence):
    claim, evidence = _rescue_setup(make_evidence)
    llm = StubLLM(json.dumps({
        "verdict": "SUPPORTS", "span_id": "rd1",
        "quote": "it clamps the delay to the maximum backoff",  # paraphrase
    }))

    verdict = _ground_one(claim, evidence, llm=llm)

    assert verdict.verdict == UNGROUNDED
    assert verdict.source_kind != "judge"
    assert len(llm.prompts) == 1   # consulted, but the answer was discarded


def test_llm_rescue_contradicts_maps_to_contradicted(make_evidence):
    claim, evidence = _rescue_setup(make_evidence)
    llm = StubLLM(json.dumps({
        "verdict": "CONTRADICTS", "span_id": "rd1",
        "quote": "def clamp_delay(delay):",                 # verbatim
    }))

    verdict = _ground_one(claim, evidence, llm=llm)

    assert verdict.verdict == CONTRADICTED
    assert verdict.evidence_span_id == "rd1"
    assert verdict.source_kind == "judge"


def test_llm_rescue_recovers_stale_state_claim(make_evidence):
    """A mechanically-STALE state claim (read predates a later edit whose
    diff doesn't name the symbol) is offered to the judge, which can
    re-verify it against the edit's recorded diff."""
    claim = _claim("state", "`frobnicate` in lib/foo.py handles retry backoff",
                   file="lib/foo.py", symbol="frobnicate")
    evidence = make_evidence([
        read_span("rd1", "lib/foo.py", content="def frobnicate(retries):"),
        edit_span("ed1", "lib/foo.py", diff="+    return retries * 2"),
    ])
    # No LLM: the later edit makes the read stale.
    assert _ground_one(claim, evidence).verdict == STALE
    # With the judge: a verbatim quote from the edit diff rescues it.
    llm = StubLLM(json.dumps({
        "verdict": "SUPPORTS", "span_id": "ed1",
        "quote": "+    return retries * 2",
    }))

    verdict = _ground_one(claim, evidence, llm=llm)

    assert verdict.verdict == GROUNDED
    assert verdict.source_kind == "judge"


def test_llm_not_consulted_when_mechanically_grounded(make_evidence):
    claim = _claim("state", "`frobnicate` in lib/foo.py handles retries",
                   file="lib/foo.py", symbol="frobnicate")
    evidence = make_evidence(
        [read_span("rd1", "lib/foo.py", content="def frobnicate():")])
    llm = StubLLM()

    verdict = _ground_one(claim, evidence, llm=llm)

    assert verdict.verdict == GROUNDED
    assert llm.prompts == []


# ── end to end via heuristic extraction ──────────────────────────

def test_heuristic_claims_ground_end_to_end(make_evidence):
    evidence = make_evidence([
        prompt_span("make the tests pass"),
        bash_span("b1", "pytest -q", stdout="12 passed"),
        response_span("All 12 tests pass."),
    ])
    claims = extract_claims_heuristic(evidence)

    verdicts = ground_claims(claims, evidence)

    assert "c0" not in verdicts   # the aggregate claim is not grounded here
    result_claims = [c for c in claims if c.type == "result"]
    assert result_claims
    verdict = verdicts[result_claims[0].id]
    assert verdict.verdict == GROUNDED
    assert verdict.evidence_span_id == "b1"
