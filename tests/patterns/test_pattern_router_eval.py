"""Retrieval-quality eval harness for `pattern_router.route()`.

Opt-in via `pytest -m eval` — it loads the SkillRouter 0.6B encoder
(and optionally the 0.6B reranker), so it's slow and not part of the
default suite. Runs against the **live** regin.db so the numbers are
representative of what the user sees in the dense-search UI; if the
catalog is empty / unembedded, the test self-skips with a hint.

Used during the dense-search hybrid refactor to compare before/after
quality on a fixed set of (query, expected_slug) pairs.

------------------------------------------------------------------
NOT A GENERAL BENCHMARK — local calibration tool only.
------------------------------------------------------------------

The expected slugs below (`base-skill`, `wiki/regin/topic-proposal-
pipeline`, etc.) only exist in one author's local regin.db; the
queries are hand-tuned to that author's pattern catalog and writing
style; N=22 is far below the level at which averaged top-1/MRR is
statistically meaningful (one query flip = ~4–5 percentage points).

That means:

  - Decisions made from these numbers (e.g. whether `rerank=True`
    is the right default) are calibrated to *this* corpus *today*.
  - Different regin users with different catalogs will see different
    numbers and may reach different defaults.
  - This file is *not* a merge criterion for router-algorithm
    changes; treat it as a regression smoke test and a personal
    calibration tool, not a quality bar.

If the corpus grows past ~50 patterns or shifts substantially (e.g.
the ratio of patterns-to-wikis changes, or wiki sibling pairs are
removed), the eval set should be re-curated before any default flip
is justified by these numbers. A more portable replacement would
seed a fixture .db with synthetic patterns + auto-generated
paraphrase queries, scale-tested at N=10/50/100/200.
"""

from __future__ import annotations

from collections import OrderedDict

import pytest


pytestmark = pytest.mark.eval


# ── Eval set ──────────────────────────────────────────────────
#
# Families:
#   T  = pattern title paraphrase    (today's encoder should handle)
#   D  = pattern description-driven  (drops description → lifts after Step A)
#   L  = pattern exact-string lexical (no BM25 leg → lifts after Step B)
#   WT = wiki title paraphrase
#   WD = wiki description / intent-driven
#   WL = wiki exact-string lexical
#
# Wiki rows were added after the "approve proposal" regression
# (2026-05-21) showed the patterns-only set missed the rerank's main
# job: pushing one wiki to a high-confidence top score when several
# patterns share the lexical neighborhood.
#
# Each row: (query, expected_slug, family).
EVAL_SET: list[tuple[str, str, str]] = [
    # pattern title paraphrases
    ("portability scanner for legacy code",     "base-skill",                "T"),
    ("my claude code hook is silently failing", "debug-hooks",               "T"),
    ("create an excalidraw architecture diagram","excalidraw-diagram",       "T"),
    ("configure playwright screenshot output",  "playwright-screenshots",    "T"),
    ("hide sections of a SKILL.md",             "experiments",               "T"),
    ("route through the topic wiki knowledge",  "topic-router",              "T"),

    # pattern description-driven (today's pipeline drops description;
    # the body contains it but it's diluted)
    ("rules for writing repo docs that don't rot",       "doc-hygiene",                "D"),
    ("self-describing rule bundle for vue styling",      "frontend-style-convention",  "D"),

    # pattern exact-string queries that lexical wins on
    ("gritql java lint rules",                  "grit-rules",                "L"),
    ("regin-bundle/v1",                         "frontend-style-convention", "L"),
    ("regin pattern embed CLI",                 "experiments",               "L"),  # ambiguous; multiple ok

    # wiki title paraphrases — the regression query plus two siblings
    ("approve proposal",                                "wiki/regin/topic-proposal-pipeline",  "WT"),
    ("graph-based topic lookup",                        "wiki/regin/topic-routing",            "WT"),
    ("hide skill sections at deploy time",              "wiki/regin/pattern-conceal-experiments", "WT"),

    # wiki description / intent-driven
    ("reviewable funnel from text request to approved topic", "wiki/regin/topic-proposal-pipeline", "WD"),
    ("how the pattern router combines two ranking signals",   "wiki/regin/pattern-routing",         "WD"),

    # wiki exact-string lexical
    (".regin/topics/topic.json",                "wiki/regin/topic-routing",            "WL"),

    # cross-kind disambiguation — query straddles a sibling pattern
    # and its companion wiki. Convention: action/command queries
    # ("run X", "X command", "X workflow") → pattern (procedure
    # guide); bare keywords or "how does X work" → wiki (narrative).
    # All four queries land with <5% rerank gap, so they exercise
    # intent detection, not lexical alignment.
    ("topic routing",                             "wiki/regin/topic-routing", "X"),
    ("regin route command",                       "topic-router",             "X"),
    ("topic-router workflow",                     "topic-router",             "X"),
    # Known rerank weakness: "I want to run" is action-flavored
    # (→ pattern per convention) but rerank picks the wiki by a
    # 0.24% margin on body density alone.
    ("I want to run a SKILL.md conceal ablation", "experiments",              "X"),
]


def _mrr(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


def _rank_of(slug: str, hits: list[dict]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if h.get("slug") == slug:
            return i
    return None


def _require_router_ready():
    try:
        from lib.skills import skill_router
        skill_router.ensure_deps()
    except Exception as exc:
        pytest.skip(f"SkillRouter deps not installed: {exc}")
    from lib.patterns import pattern_router
    cov = pattern_router.embedding_coverage()
    if cov["embedded"] < 5:
        pytest.skip(
            f"pattern_embeddings has only {cov['embedded']} rows — "
            f"run `regin pattern embed` first"
        )


def _score(hits_by_query: dict[str, list[dict]]) -> dict:
    """Compute top-1 hit rate, MRR@5, per-family breakdown, and a per-row table."""
    per_family: dict[str, list[tuple[str, str, int | None]]] = {}
    rows: list[tuple[str, str, str, int | None]] = []
    for query, expected, family in EVAL_SET:
        rank = _rank_of(expected, hits_by_query[query])
        per_family.setdefault(family, []).append((query, expected, rank))
        rows.append((family, query, expected, rank))
    out = {"rows": rows, "n": len(EVAL_SET)}
    out["top1"] = sum(1 for r in rows if r[3] == 1) / len(rows)
    out["mrr5"] = sum(_mrr(r[3]) for r in rows if (r[3] or 99) <= 5) / len(rows)
    out["families"] = {
        fam: {
            "n": len(items),
            "top1": sum(1 for _, _, r in items if r == 1) / len(items),
            "mrr5": sum(_mrr(r) for _, _, r in items if (r or 99) <= 5) / len(items),
        }
        for fam, items in per_family.items()
    }
    return out


def _print(label: str, scores: dict) -> None:
    print(f"\n── {label} ─────────────────────────────────")
    print(f"  N={scores['n']}  top-1={scores['top1']:.2f}  MRR@5={scores['mrr5']:.2f}")
    for fam, fs in sorted(scores["families"].items()):
        print(f"    [{fam}] n={fs['n']}  top-1={fs['top1']:.2f}  MRR@5={fs['mrr5']:.2f}")
    print("  per-query (rank, ∞ = miss):")
    for fam, query, expected, rank in scores["rows"]:
        marker = "✓" if rank == 1 else " "
        rstr = "∞" if rank is None else str(rank)
        print(f"   {marker} [{fam}] rank={rstr:>2}  {expected:30s}  ← {query}")


def test_eval_current_pipeline(capsys):
    """Prints retrieval-quality metrics for the current `pattern_router.route()`.

    Does NOT assert thresholds — it captures a baseline. Add an assertion
    once the hybrid refactor lands and the new floor is known.
    """
    _require_router_ready()
    from lib.patterns import pattern_router

    hits: "OrderedDict[str, list[dict]]" = OrderedDict()
    for query, _, _ in EVAL_SET:
        hits[query] = pattern_router.route(query, top_k=5, rerank=False)
    scores_retrieval = _score(hits)

    hits_rr: "OrderedDict[str, list[dict]]" = OrderedDict()
    for query, _, _ in EVAL_SET:
        hits_rr[query] = pattern_router.route(query, top_k=5, rerank=True)
    scores_rerank = _score(hits_rr)

    with capsys.disabled():
        _print("retrieval only (no rerank)", scores_retrieval)
        _print("retrieval + cross-encoder rerank", scores_rerank)
