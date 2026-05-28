"""Ablation: BM25 vs dense vs hybrid vs hybrid+rerank for pattern_router.

Reuses the EVAL_SET from tests/test_pattern_router_eval.py.
Runs each leg independently against the live regin.db and prints a
per-family comparison table.
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.patterns import pattern_router as pr
from lib.orm import SessionLocal
from tests.test_pattern_router_eval import EVAL_SET


def _rank_of(slug: str, slugs: list[str]) -> int | None:
    for i, s in enumerate(slugs, start=1):
        if s == slug:
            return i
    return None


def _mrr(rank: int | None) -> float:
    return 0.0 if rank is None or rank > 5 else 1.0 / rank


def _slugs_from_pids(pids: list[int]) -> list[str]:
    if not pids:
        return []
    with SessionLocal() as session:
        from sqlmodel import select
        from lib.orm.models import PatternDoc
        rows = session.exec(
            select(PatternDoc.id, PatternDoc.slug).where(PatternDoc.id.in_(pids))
        ).all()
    by_id = {pid: slug for pid, slug in rows}
    return [by_id[p] for p in pids if p in by_id]


def run_bm25(query: str, k: int = 5) -> list[str]:
    hits = pr._lexical_route(query, top_k=k)
    return [slug for slug, _ in hits]


def run_dense(query: str, k: int = 5) -> list[str]:
    pids = pr._dense_retrieve(query, retrieval_k=k, embed_model_id=pr.skill_router.EMBEDDING_MODEL_ID)
    return _slugs_from_pids(pids)


def run_hybrid(query: str, k: int = 5, rerank: bool = False) -> list[str]:
    hits = pr.route(query, top_k=k, rerank=rerank)
    return [h["slug"] for h in hits]


def score(results: dict[str, list[str]]) -> dict:
    per_family: dict[str, list[tuple[str, str, int | None]]] = {}
    rows = []
    for query, expected, family in EVAL_SET:
        rank = _rank_of(expected, results[query])
        per_family.setdefault(family, []).append((query, expected, rank))
        rows.append((family, query, expected, rank))
    out = {"n": len(EVAL_SET), "rows": rows}
    out["top1"] = sum(1 for r in rows if r[3] == 1) / len(rows)
    out["recall5"] = sum(1 for r in rows if (r[3] or 99) <= 5) / len(rows)
    out["mrr5"] = sum(_mrr(r[3]) for r in rows) / len(rows)
    out["families"] = {}
    for fam, items in per_family.items():
        out["families"][fam] = {
            "n": len(items),
            "top1": sum(1 for _, _, r in items if r == 1) / len(items),
            "recall5": sum(1 for _, _, r in items if (r or 99) <= 5) / len(items),
            "mrr5": sum(_mrr(r) for _, _, r in items) / len(items),
        }
    return out


def main():
    configs = OrderedDict([
        ("BM25-only", run_bm25),
        ("dense-only", run_dense),
        ("hybrid RRF (no rerank)", lambda q, k=5: run_hybrid(q, k, rerank=False)),
        ("hybrid + rerank", lambda q, k=5: run_hybrid(q, k, rerank=True)),
    ])

    all_scores = OrderedDict()
    per_query_ranks: dict[str, dict[str, int | None]] = {}
    for label, fn in configs.items():
        results = {}
        for query, _, _ in EVAL_SET:
            try:
                results[query] = fn(query, 5)
            except Exception as e:
                print(f"  !! {label} crashed on {query!r}: {e}")
                results[query] = []
        all_scores[label] = score(results)
        for query, expected, family in EVAL_SET:
            per_query_ranks.setdefault(query, {})[label] = _rank_of(expected, results[query])

    # ── Aggregate table ─────────────────────────────────────────
    n_total = next(iter(all_scores.values()))["n"]
    print(f"\n=== Aggregate (N={n_total}) ===")
    print(f"{'config':<24}  top-1   R@5    MRR@5")
    for label, s in all_scores.items():
        print(f"{label:<24}  {s['top1']:.2f}   {s['recall5']:.2f}   {s['mrr5']:.2f}")

    # ── Per-family ──────────────────────────────────────────────
    print("\n=== Per-family ===")
    print("  T/D/L = pattern title-paraphrase / description / lexical")
    print("  WT/WD/WL = wiki   title-paraphrase / description / lexical")
    fams = sorted({fam for _, _, fam in EVAL_SET})
    print(f"{'config':<24}  " + "  ".join(f"{f}.top1/R5" for f in fams))
    for label, s in all_scores.items():
        cells = []
        for fam in fams:
            fs = s["families"].get(fam, {})
            cells.append(f"{fs.get('top1', 0):.2f}/{fs.get('recall5', 0):.2f}")
        print(f"{label:<24}  " + "  ".join(f"  {c}   " for c in cells))

    # ── Per-query rank diff (where do they disagree?) ───────────
    print("\n=== Per-query ranks (∞ = miss / not in top-5) ===")
    print(f"{'fam':<3} {'expected':<30} {'BM25':>5} {'dense':>5} {'RRF':>5} {'+rr':>5}  query")
    for query, expected, family in EVAL_SET:
        rs = per_query_ranks[query]
        def fmt(r):
            return "∞" if r is None or r > 5 else str(r)
        print(
            f"{family:<3} {expected:<30} "
            f"{fmt(rs['BM25-only']):>5} {fmt(rs['dense-only']):>5} "
            f"{fmt(rs['hybrid RRF (no rerank)']):>5} {fmt(rs['hybrid + rerank']):>5}  "
            f"{query}"
        )


if __name__ == "__main__":
    main()
