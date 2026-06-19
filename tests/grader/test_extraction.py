"""Tests for lib/grader/extraction.py — the claim-extraction pipeline.

Heuristic path: segmentation, checkability filter, keyword typing,
referents, dedup. LLM path (via StubLLM): merge semantics, the
verbatim-provenance guard, type fallback, the completeness critic, and
the load-bearing-first cap.
"""

from __future__ import annotations

import json

import pytest

from conftest import StubLLM, prompt_span, response_span

from lib.grader.extraction import (
    extract_claims,
    extract_claims_heuristic,
    synthetic_top_claim,
)


def _heuristic_claims(make_evidence, text: str, task: str = "do the task"):
    """Heuristic ledger for a summary, with the synthetic c0 dropped."""
    evidence = make_evidence([prompt_span(task), response_span(text)])
    return extract_claims_heuristic(evidence)[1:]


def _single_claim(make_evidence, sentence: str):
    claims = _heuristic_claims(make_evidence, sentence)
    assert len(claims) == 1
    return claims[0]


# ── Heuristic extraction: segmentation + filters + dedup ─────────


def test_heuristic_segmentation_filters_and_dedup(make_evidence):
    summary = (
        "Fixed the login bug in auth.py. All 12 tests pass.\n"
        "- Fixed the login bug in auth.py\n"
        "- The bug was caused by a stale session token.\n"
        "This might be the caching issue in cache.py.\n"
        "Should I also update docs.md?\n"
        "I'll update runner.py next.\n"
        "Thanks for your patience.\n"
        "ALL 12 TESTS PASS.\n"
    )
    claims = _heuristic_claims(make_evidence, summary)

    # Hedge ("might"), plan ("I'll"), question ("?"), non-claim chatter,
    # the bullet duplicate, and the case-variant duplicate all drop.
    assert [c.raw_text for c in claims] == [
        "Fixed the login bug in auth.py.",
        "All 12 tests pass.",
        "The bug was caused by a stale session token.",
    ]
    assert [c.type for c in claims] == ["state", "result", "diagnostic"]
    # Bullet chrome is stripped from the surviving bullet clause.
    assert not claims[2].raw_text.startswith("-")
    for claim in claims:
        assert claim.load_bearing is True
        assert claim.extraction_confidence == 0.7
        assert claim.provenance["surface"] == "final_summary"


def test_heuristic_skips_code_fences_and_records_offsets(make_evidence):
    summary = (
        "Fixed the parser in parse.py.\n"
        "```\nAll 99 tests pass.\n```\n"
        "All 4 tests pass."
    )
    claims = _heuristic_claims(make_evidence, summary)

    assert [c.raw_text for c in claims] == [
        "Fixed the parser in parse.py.",
        "All 4 tests pass.",
    ]
    assert all("99" not in c.raw_text for c in claims)
    # The fence is blanked with same-length padding, so offsets line up
    # with the original artifact text.
    for claim in claims:
        assert claim.provenance["offset"] == summary.find(claim.raw_text)


def test_heuristic_drops_short_clauses(make_evidence):
    # "Tests pass." would classify as result but has fewer than 3 words.
    assert _heuristic_claims(make_evidence, "Tests pass.") == []


# ── Typing routing table ─────────────────────────────────────────


def test_typing_diagnostic_beats_result(make_evidence):
    claim = _single_claim(
        make_evidence, "The tests failed because the fixture was stale.")
    assert claim.type == "diagnostic"


def test_typing_result_keywords_and_commit_hash(make_evidence):
    assert _single_claim(
        make_evidence, "Committed the fix to master.").type == "result"
    # A backticked commit hash asserts a commit landed — result, not the
    # generic backtick → state route.
    assert _single_claim(
        make_evidence, "Landed the fix in `a1b2c3d`.").type == "result"


def test_typing_external_version_and_library(make_evidence):
    assert _single_claim(
        make_evidence, "Upgraded to flask 3.1.2 for the fix.").type == "external"
    assert _single_claim(
        make_evidence, "The library defaults to lazy loading.").type == "external"


def test_typing_state_file_and_symbol(make_evidence):
    file_claim = _single_claim(
        make_evidence, "Renamed the helper in lib/utils.py.")
    assert file_claim.type == "state"

    symbol_claim = _single_claim(
        make_evidence, "The `EvidenceIndex` class stores prompt texts.")
    assert symbol_claim.type == "state"
    assert symbol_claim.referents["symbol"] == "EvidenceIndex"


def test_typing_strictest_first_ordering(make_evidence):
    # diagnostic > result > external > state when triggers stack.
    stacked = ("The build failed because numpy 2.0.0 changed the "
               "defaults in core.py.")
    assert _single_claim(make_evidence, stacked).type == "diagnostic"
    assert _single_claim(
        make_evidence, "All 3 tests pass on numpy 2.0.0.").type == "result"
    assert _single_claim(
        make_evidence, "The api defaults to utf-8 in config.py.").type == "external"


# ── Referent extraction ──────────────────────────────────────────


def test_referents_file_symbol_and_extra_files(make_evidence):
    claim = _single_claim(
        make_evidence,
        "Renamed `parse_config` in lib/settings.py and lib/extra.py.")
    assert claim.referents["file"] == "lib/settings.py"
    assert claim.referents["_extra_files"] == ["lib/extra.py"]
    assert claim.referents["symbol"] == "parse_config"
    assert claim.referents["url"] is None
    assert claim.referents["command"] is None


def test_referents_url_trailing_punctuation_stripped(make_evidence):
    claim = _single_claim(
        make_evidence, "Verified against https://docs.example.com/api/v2.")
    assert claim.referents["url"] == "https://docs.example.com/api/v2"
    assert claim.referents["file"] is None


# ── Synthetic c0 ─────────────────────────────────────────────────


def test_synthetic_top_claim_shape():
    claim = synthetic_top_claim("Fix the login flow")
    assert claim.id == "c0"
    assert claim.type == "aggregate"
    assert claim.load_bearing is True
    assert claim.raw_text == "Fix the login flow"
    assert claim.normalized_text == "the session accomplished: Fix the login flow"
    assert claim.provenance == {"surface": "task"}

    assert synthetic_top_claim("").raw_text == "the user's task"
    assert len(synthetic_top_claim("x" * 400).raw_text) == 300


def test_c0_always_heads_the_ledger(make_evidence):
    evidence = make_evidence(
        [prompt_span("Fix things please"), response_span("All 4 tests pass.")])
    claims = extract_claims(evidence)
    assert claims[0].id == "c0"
    assert claims[0].type == "aggregate"
    assert claims[0].load_bearing is True

    # Even with no prompt and no summary, c0 is the (whole) ledger.
    bare = extract_claims(make_evidence([]))
    assert len(bare) == 1
    assert bare[0].id == "c0"
    assert bare[0].raw_text == "the user's task"


# ── LLM path ─────────────────────────────────────────────────────


def test_llm_skipped_when_no_final_text(make_evidence):
    stub = StubLLM("[]", "[]")
    evidence = make_evidence([prompt_span("fix it")])
    claims = extract_claims(evidence, llm=stub)
    assert [c.id for c in claims] == ["c0"]
    assert stub.prompts == []


_MERGE_ARTIFACT = ("Fixed the race in scheduler.py. All 7 tests pass. "
                   "The cache layer defaults to LRU eviction.")
_MERGE_EXTRACT_ANSWER = json.dumps([
    {"raw_text": "All 7 tests pass",
     "normalized_text": "All 7 tests pass",
     "type": "result",
     "referents": {"file": None, "symbol": None,
                   "command": "pytest", "url": None},
     "load_bearing": True},
    # Invalid type → falls back to heuristic classification (state).
    {"raw_text": "Fixed the race in scheduler.py",
     "normalized_text": "Fixed the race in scheduler.py",
     "type": "opinion",
     "referents": {}, "load_bearing": True},
    # Not a verbatim substring → provenance guard drops it.
    {"raw_text": "This text is not in the artifact",
     "normalized_text": "invented claim",
     "type": "state", "referents": {}, "load_bearing": True},
])
_MERGE_CRITIC_ANSWER = json.dumps([
    {"raw_text": "defaults to LRU eviction",
     "normalized_text": "The cache layer uses LRU eviction by default",
     "type": "external", "referents": {}, "load_bearing": True},
])


@pytest.fixture
def llm_merge_run(make_evidence):
    """One scripted deep-tier extraction; tests pick at the outcome."""
    stub = StubLLM(_MERGE_EXTRACT_ANSWER, _MERGE_CRITIC_ANSWER)
    evidence = make_evidence(
        [prompt_span("fix the race"), response_span(_MERGE_ARTIFACT)])
    claims = extract_claims(evidence, llm=stub)
    return {"claims": claims, "stub": stub,
            "by_raw": {c.raw_text: c for c in claims}}


def test_llm_claim_wins_over_heuristic_near_duplicate(llm_merge_run):
    # "All 7 tests pass." (heuristic) dedups away; the LLM version's
    # referents/confidence survive.
    by_raw = llm_merge_run["by_raw"]
    assert "All 7 tests pass." not in by_raw
    llm_result = by_raw["All 7 tests pass"]
    assert llm_result.type == "result"
    assert llm_result.referents["command"] == "pytest"
    assert llm_result.extraction_confidence == 0.9
    assert llm_result.provenance["offset"] == \
        _MERGE_ARTIFACT.find("All 7 tests pass")


def test_llm_invalid_type_falls_back_and_guard_drops_invented(llm_merge_run):
    by_raw = llm_merge_run["by_raw"]
    # Invalid type "opinion" fell back to the heuristic router.
    assert by_raw["Fixed the race in scheduler.py"].type == "state"
    # Provenance guard: the invented claim never enters the ledger.
    assert "This text is not in the artifact" not in by_raw


def test_llm_critic_claims_added_and_ledger_renumbered(llm_merge_run):
    by_raw = llm_merge_run["by_raw"]
    # Critic claim merged in alongside the heuristic original (distinct
    # normalized texts → both kept).
    critic = by_raw["defaults to LRU eviction"]
    assert critic.normalized_text == "The cache layer uses LRU eviction by default"
    assert by_raw["The cache layer defaults to LRU eviction."] \
        .extraction_confidence == 0.7

    claims = llm_merge_run["claims"]
    assert len(claims) == 5  # c0 + 2 llm + 1 critic + 1 heuristic survivor
    assert [c.id for c in claims] == [f"c{i}" for i in range(len(claims))]


def test_llm_runs_extractor_then_critic(llm_merge_run):
    prompts = llm_merge_run["stub"].prompts
    assert len(prompts) == 2
    assert _MERGE_ARTIFACT in prompts[0]
    assert "completeness critic" in prompts[1]
    assert "- All 7 tests pass" in prompts[1]  # ledger fed to the critic


def test_llm_markdown_fence_tolerated_and_garbage_critic_ignored(make_evidence):
    artifact = "All 2 tests pass."
    fenced = ("```json\n" + json.dumps([
        {"raw_text": "All 2 tests pass",
         "normalized_text": "the pair of unit tests pass",
         "type": "result", "referents": {}, "load_bearing": True},
    ]) + "\n```")
    stub = StubLLM(fenced, "I found nothing further to add.")
    evidence = make_evidence([prompt_span("run tests"), response_span(artifact)])

    claims = extract_claims(evidence, llm=stub)

    normalized = [c.normalized_text for c in claims]
    assert "the pair of unit tests pass" in normalized
    # Heuristic claim has a distinct dedup key, so it coexists.
    assert "All 2 tests pass." in normalized
    assert len(claims) == 3  # c0 + fenced LLM claim + heuristic claim


def test_llm_with_no_answers_degrades_to_heuristic(make_evidence):
    artifact = "Fixed the bug in alpha.py. All 4 tests pass."
    spans = [prompt_span("fix the bug"), response_span(artifact)]
    plain = extract_claims(make_evidence(spans))
    # StubLLM with an empty script returns None from complete().
    degraded = extract_claims(make_evidence(spans), llm=StubLLM())
    assert [(c.raw_text, c.type) for c in degraded] == \
        [(c.raw_text, c.type) for c in plain]


# ── max_claims cap ───────────────────────────────────────────────


def test_max_claims_caps_heuristic_ledger(make_evidence):
    summary = ("Fixed the bug in alpha.py. All 4 tests pass. "
               "Updated the docs in beta.md. Committed the final fix.")
    evidence = make_evidence([prompt_span("fix it"), response_span(summary)])
    claims = extract_claims(evidence, max_claims=3)
    assert [c.id for c in claims] == ["c0", "c1", "c2"]
    assert [c.raw_text for c in claims[1:]] == [
        "Fixed the bug in alpha.py.",
        "All 4 tests pass.",
    ]


def test_max_claims_cap_prefers_load_bearing(make_evidence):
    artifact = "Fixed the bug in alpha.py. All 4 tests pass."
    aside = json.dumps([
        {"raw_text": "Fixed the bug",
         "normalized_text": "a non load bearing aside",
         "type": "state", "referents": {}, "load_bearing": False},
    ])
    spans = [prompt_span("fix the bug"), response_span(artifact)]

    # At the cap, the non-load-bearing LLM claim is evicted first even
    # though it sits ahead of the heuristic tail in merge order.
    capped = extract_claims(make_evidence(spans),
                            llm=StubLLM(aside, "[]"), max_claims=3)
    assert len(capped) == 3
    assert all(c.load_bearing for c in capped)
    assert "a non load bearing aside" not in [c.normalized_text for c in capped]

    # With room, it survives — but sorted behind every load-bearing claim.
    roomy = extract_claims(make_evidence(spans),
                           llm=StubLLM(aside, "[]"), max_claims=10)
    assert roomy[-1].normalized_text == "a non load bearing aside"
    assert roomy[-1].load_bearing is False
    assert roomy[-1].id == f"c{len(roomy) - 1}"
