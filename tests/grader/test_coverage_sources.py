"""Tests for lib/grader/coverage.py (criterion C) and
lib/grader/source_quality.py (criterion S).

Coverage: the checklist is derived from the task alone (heuristic or
LLM), then assessed item-by-item — mechanical timeline checks for the
verification/applied items, claim-grounding piggyback for the rest.
Source quality: pure classification of the span kind behind each
grounded claim.
"""

from __future__ import annotations

import json

from conftest import StubLLM, bash_span, edit_span, read_span

from lib.grader.coverage import (
    _APPLY_ITEM, _ROOT_CAUSE_ITEM, _TEST_ITEM, _VERIFY_ITEM,
    assess_coverage, derive_checklist, derive_checklist_heuristic,
)
from lib.grader.models import (
    AUTHORITATIVE, COVERED, GROUNDED, MISSING, PARTIAL, PROXY,
    UNGROUNDED, UNVERIFIED, Claim, ClaimVerdict,
)
from lib.grader.source_quality import assess_sources


# ── checklist derivation ─────────────────────────────────────────

def test_heuristic_fix_and_test_task_expands_implied_items():
    task = "fix the login 500 bug and add a regression test"
    items = derive_checklist_heuristic(task)
    # explicit sub-tasks survive the split
    assert "fix the login 500 bug" in items
    assert "add a regression test" in items
    # a fix+test task implies all four obligations
    assert _ROOT_CAUSE_ITEM in items
    assert _APPLY_ITEM in items
    assert _VERIFY_ITEM in items
    assert _TEST_ITEM in items
    # de-dup preserves uniqueness
    assert len(items) == len({i.lower() for i in items})


def test_heuristic_strips_slash_command_prefix():
    items = derive_checklist_heuristic("/bug-fix the parser crash on empty input")
    assert "the parser crash on empty input" in items
    assert not any("bug-fix" in item or item.startswith("/") for item in items)
    # "fix" inside the stripped command name must not leak into implied
    # items, but "crash" in the remaining text still marks it a fix task
    assert _ROOT_CAUSE_ITEM in items


def test_heuristic_empty_task_yields_fallback_item():
    assert derive_checklist_heuristic("") == ["complete the requested task"]


def test_derive_checklist_llm_json_array_wins():
    scripted = ["root cause identified", "fix applied", "suite green"]
    stub = StubLLM("Sure! Here you go: " + json.dumps(scripted))
    items = derive_checklist("fix the flaky login test", llm=stub)
    assert items == scripted
    # the checklist prompt carries the task text
    assert "fix the flaky login test" in stub.prompts[0]


def test_derive_checklist_falls_back_on_unparseable_llm_output():
    task = "fix the login bug and add a regression test"
    stub = StubLLM("I cannot produce a checklist right now")
    items = derive_checklist(task, llm=stub)
    assert stub.prompts, "LLM should have been consulted first"
    assert items == derive_checklist_heuristic(task)


# ── mechanical items: verification run ───────────────────────────

def test_verify_item_covered_when_green_run_postdates_last_edit(make_evidence):
    evidence = make_evidence([
        edit_span("e1", "src/app.py", diff="+return total"),
        bash_span("b1", ".venv/bin/python -m pytest -q", stdout="3 passed"),
    ])
    [item] = assess_coverage([_VERIFY_ITEM], [], {}, evidence)
    assert item.verdict == COVERED
    assert "postdates" in item.reason


def test_verify_item_partial_when_green_run_predates_last_edit(make_evidence):
    evidence = make_evidence([
        bash_span("b1", ".venv/bin/python -m pytest -q", stdout="3 passed"),
        edit_span("e1", "src/app.py", diff="+return total"),
    ])
    [item] = assess_coverage([_VERIFY_ITEM], [], {}, evidence)
    assert item.verdict == PARTIAL
    assert "predates" in item.reason


def test_verify_item_missing_without_successful_run(make_evidence):
    # a failed run does not count …
    failed = make_evidence([
        edit_span("e1", "src/app.py"),
        bash_span("b1", "pytest -q", stderr="2 failed", status="ERROR"),
    ])
    [item] = assess_coverage([_VERIFY_ITEM], [], {}, failed)
    assert item.verdict == MISSING
    # … and neither does no run at all
    no_run = make_evidence([edit_span("e1", "src/app.py")])
    [item] = assess_coverage([_VERIFY_ITEM], [], {}, no_run)
    assert item.verdict == MISSING


# ── mechanical items: applied changes ────────────────────────────

def test_apply_item_covered_when_mutations_recorded(make_evidence):
    evidence = make_evidence([edit_span("e1", "lib/app.py", diff="+x = 1")])
    [item] = assess_coverage([_APPLY_ITEM], [], {}, evidence)
    assert item.verdict == COVERED
    assert "lib/app.py" in item.reason


def test_apply_item_missing_without_mutations(make_evidence):
    evidence = make_evidence([read_span("r1", "lib/app.py", "x = 1")])
    [item] = assess_coverage([_APPLY_ITEM], [], {}, evidence)
    assert item.verdict == MISSING


# ── generic items ────────────────────────────────────────────────

_ITEM = "update the parser tokenizer logic"


def _parser_claim(claim_id: str = "c1", claim_type: str = "state") -> Claim:
    return Claim(id=claim_id, raw_text="updated the parser tokenizer",
                 normalized_text="the parser tokenizer was updated",
                 type=claim_type)


def test_generic_item_covered_by_grounded_overlapping_claim(make_evidence):
    claim = _parser_claim()
    verdicts = {"c1": ClaimVerdict("c1", GROUNDED, "e1", "src/parser.py",
                                   source_kind="edit")}
    [item] = assess_coverage([_ITEM], [claim], verdicts, make_evidence([]))
    assert item.verdict == COVERED
    assert "[c1]" in item.reason


def test_generic_item_partial_when_overlapping_claim_ungrounded(make_evidence):
    claim = _parser_claim()
    verdicts = {"c1": ClaimVerdict("c1", UNGROUNDED, reason="no span")}
    [item] = assess_coverage([_ITEM], [claim], verdicts, make_evidence([]))
    assert item.verdict == PARTIAL
    assert "not grounded" in item.reason


def test_generic_item_partial_on_trace_activity_alone(make_evidence):
    evidence = make_evidence([
        edit_span("e1", "src/parser.py", diff="+ tokenizer logic update"),
    ])
    [item] = assess_coverage([_ITEM], [], {}, evidence)
    assert item.verdict == PARTIAL
    assert "trace activity" in item.reason


def test_generic_item_missing_when_nothing_addresses_it(make_evidence):
    evidence = make_evidence([read_span("r1", "docs/setup.md", "welcome")])
    [item] = assess_coverage([_ITEM], [], {}, evidence)
    assert item.verdict == MISSING


def test_generic_item_ignores_aggregate_claims(make_evidence):
    aggregate = Claim(id="c0", raw_text=_ITEM, normalized_text=_ITEM,
                      type="aggregate")
    verdicts = {"c0": ClaimVerdict("c0", GROUNDED, source_kind="checklist")}
    [item] = assess_coverage([_ITEM], [aggregate], verdicts, make_evidence([]))
    assert item.verdict == MISSING


# ── source quality ───────────────────────────────────────────────

def _grounded(claim_id: str, *, kind: str, ref: str) -> ClaimVerdict:
    return ClaimVerdict(claim_id, GROUNDED, evidence_span_id="s1",
                        evidence_ref=ref, source_kind=kind)


def _claim(claim_id: str, claim_type: str, referents: dict | None = None) -> Claim:
    return Claim(id=claim_id, raw_text=f"claim {claim_id}",
                 normalized_text=f"claim {claim_id}", type=claim_type,
                 referents=referents or {})


def test_result_claim_grounded_by_bash_is_authoritative():
    [source] = assess_sources(
        [_claim("c1", "result")],
        {"c1": _grounded("c1", kind="bash", ref="pytest -q")})
    assert source.verdict == AUTHORITATIVE
    assert source.source == "pytest -q"
    assert "ran it" in source.reason


def test_state_claim_doc_read_is_proxy_real_path_read_authoritative():
    claims = [_claim("c1", "state"), _claim("c2", "state")]
    verdicts = {
        "c1": _grounded("c1", kind="read", ref="docs/README.md"),
        "c2": _grounded("c2", kind="read", ref="lib/grader/coverage.py"),
    }
    by_id = {s.claim_id: s for s in assess_sources(claims, verdicts)}
    assert by_id["c1"].verdict == PROXY
    assert "documentation" in by_id["c1"].reason
    assert by_id["c2"].verdict == AUTHORITATIVE


def test_grep_grounding_is_proxy():
    [source] = assess_sources(
        [_claim("c1", "state")],
        {"c1": _grounded("c1", kind="grep", ref="derive_checklist")})
    assert source.verdict == PROXY
    assert "Grep" in source.reason


def test_edit_and_judge_groundings_are_authoritative():
    claims = [_claim("c1", "state"), _claim("c2", "diagnostic")]
    verdicts = {
        "c1": _grounded("c1", kind="edit", ref="lib/app.py"),
        "c2": _grounded("c2", kind="judge", ref="lib/app.py"),
    }
    sources = assess_sources(claims, verdicts)
    assert [s.verdict for s in sources] == [AUTHORITATIVE, AUTHORITATIVE]


def test_external_snippet_domains_are_proxy():
    claims = [_claim("c1", "external"), _claim("c2", "external")]
    verdicts = {
        "c1": _grounded("c1", kind="webfetch",
                        ref="https://stackoverflow.com/questions/123"),
        "c2": _grounded("c2", kind="webfetch",
                        ref="https://example.com/blog/async-tips"),
    }
    sources = assess_sources(claims, verdicts)
    assert [s.verdict for s in sources] == [PROXY, PROXY]


def test_external_official_docs_fetch_is_authoritative():
    [source] = assess_sources(
        [_claim("c1", "external")],
        {"c1": _grounded("c1", kind="webfetch",
                         ref="https://docs.python.org/3/library/json.html")})
    assert source.verdict == AUTHORITATIVE
    assert "official" in source.reason


def test_external_search_query_grounding_is_proxy():
    [source] = assess_sources(
        [_claim("c1", "external")],
        {"c1": _grounded("c1", kind="webfetch",
                         ref="pydantic settings reload singleton")})
    assert source.verdict == PROXY
    assert "search query" in source.reason


def test_named_but_unconsulted_url_is_unverified():
    claims = [
        _claim("c1", "external", {"url": "https://docs.example.com/api"}),
        _claim("c2", "external"),          # ungrounded, no url referent
        _claim("c3", "state"),             # no verdict at all
    ]
    verdicts = {
        "c1": ClaimVerdict("c1", UNGROUNDED, reason="never fetched"),
        "c2": ClaimVerdict("c2", UNGROUNDED, reason="never fetched"),
    }
    sources = assess_sources(claims, verdicts)
    assert len(sources) == 1
    assert sources[0].claim_id == "c1"
    assert sources[0].verdict == UNVERIFIED
    assert sources[0].source == "https://docs.example.com/api"
