"""Step 6 — cross-system dedup: same-fact double-surfacing.

When both regin's `<recalled_experience>` and Hindsight's `<hindsight_memories>`
are active in one session, a fact known to both gets injected twice — redundant
tokens for zero added signal (the "double-injection tax"). This measures it on
the shared corpus: for each probe, reconstruct the *distinct corpus facts* each
system actually injects, intersect them, and price the overlap.

    .venv/bin/python -m tests.memory_ab.dedup

Controlled regime only (same 14-fact corpus in both) — overlap is only
meaningful when both stores hold the same knowledge. Writes results/dedup.json.
"""

from __future__ import annotations

import json
import statistics

import lib.memory as memory
from hook_manager.handlers import memory_recall as mr
from lib.settings import settings
from tests.memory_ab import overhead
from tests.memory_ab import spec
from tests.memory_ab.adapters import hindsight as hs
from tests.memory_ab.adapters import regin as rg


def _regin_ids(query: str) -> list[str]:
    """Distinct corpus ids regin would actually inject for this prompt."""
    cfg = settings.agent_memory
    hits = memory.recall(query, top_k=cfg.inject_top_k, mode="auto",
                         include_tests=True, reinforce=False)
    if not hits:
        return []
    block = mr._build_block(hits, cfg.inject_max_chars)
    n = block.count("\n- ")          # entries that fit the char budget
    ids = [spec.resolve_corpus_id(h.memory.get("body")) for h in hits[:n]]
    return [i for i in ids if i]


def _hs_ids(query: str) -> list[str]:
    """Distinct corpus ids Hindsight would inject (its fact-units map back to
    corpus facts via document_id; multiple units can share one fact)."""
    ids = [hs._resolve(r) for r in overhead._hs_recall(hs.BANK, query)]
    return [i for i in ids if i]


def _row(probe: dict, corpus_tokens: dict[str, int]) -> dict:
    r = set(_regin_ids(probe["query"]))
    h = set(_hs_ids(probe["query"]))
    overlap = sorted(r & h)
    return {
        "probe_id": probe["id"],
        "regin_ids": sorted(r), "hindsight_ids": sorted(h),
        "overlap": overlap,
        "redundant_tokens": sum(corpus_tokens[c] for c in overlap),
    }


def _aggregate(rows: list[dict]) -> dict:
    overlaps = [len(r["overlap"]) for r in rows]
    redundant = [r["redundant_tokens"] for r in rows]
    covered = [len(r["overlap"]) / len(r["regin_ids"])
               for r in rows if r["regin_ids"]]
    return {
        "prompts": len(rows),
        "mean_overlap_facts": round(statistics.mean(overlaps), 2),
        "pct_prompts_with_overlap":
            round(100 * sum(1 for o in overlaps if o) / len(rows), 1),
        "mean_redundant_tokens": round(statistics.mean(redundant), 1),
        "regin_facts_also_in_hindsight_pct":
            round(100 * statistics.mean(covered), 1) if covered else 0.0,
    }


def run() -> dict:
    enc = overhead._enc()
    probes = spec.load_probes()
    corpus = spec.load_corpus()
    corpus_tokens = {e["id"]: len(enc.encode(e["body"])) for e in corpus}
    db = spec.RESULTS_DIR / ".tmp" / "regin_dedup.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    rg._cleanup_db(db)
    rg._isolate_store(db)
    rg._ingest(corpus)
    hs._clear()
    hs._ingest(corpus)
    rows = [_row(p, corpus_tokens) for p in probes]
    hs._clear()
    rg.dispose_memory_engine()
    rg._cleanup_db(db)
    out = {"summary": _aggregate(rows), "rows": rows}
    spec.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (spec.RESULTS_DIR / "dedup.json").write_text(json.dumps(out, indent=2))
    return out


def main() -> None:
    out = run()
    s = out["summary"]
    print("cross-system dedup (both systems active, same corpus):")
    print(f"  mean overlapping facts/prompt : {s['mean_overlap_facts']}")
    print(f"  prompts with >=1 overlap      : {s['pct_prompts_with_overlap']}%")
    print(f"  redundant tokens/prompt       : {s['mean_redundant_tokens']}")
    print(f"  regin facts also in hindsight : "
          f"{s['regin_facts_also_in_hindsight_pct']}%")
    print(f"\nwrote {spec.RESULTS_DIR / 'dedup.json'}")


if __name__ == "__main__":
    main()
