"""System-agnostic scorer for the memory A/B harness.

Reads one or both result dumps (results/regin.json, results/hindsight.json),
computes the four dimensions, and writes results/scorecard.md. Works with a
single system present (regin alone) and produces a side-by-side comparison
when both are. Before scoring two systems it asserts they ingested the same
corpus ids — the fairness gate — and refuses to compare if they diverge.

    .venv/bin/python -m tests.memory_ab.scorer
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tests.memory_ab import spec

SYSTEMS = ("regin", "hindsight")


@dataclass
class Metrics:
    system: str
    mode: str
    recall_at_1: float
    recall_at_3: float
    mrr: float
    lure_beats_target: float
    neg_leak: float
    capture_ok: float
    roundtrip: float
    ingest_ms: float
    lifecycle_pass: bool | None
    scored_probes: int


def _hit_rank(hits: list[dict], expect_ids: list[str]) -> int | None:
    """1-based rank of the first hit whose corpus_id is expected."""
    for hit in hits:
        if hit.get("corpus_id") in expect_ids:
            return hit["rank"]
    return None


def _best_rank(hits: list[dict], ids: list[str]) -> int | None:
    """Best (lowest) rank among hits matching any of `ids`."""
    ranks = [h["rank"] for h in hits if h.get("corpus_id") in ids]
    return min(ranks) if ranks else None


def _expected_ranks(queries: list[dict]) -> list[int | None]:
    """First-hit rank per probe that expects something (None on a miss)."""
    return [_hit_rank(q["hits"], q["expect_ids"])
            for q in queries if q.get("expect_ids")]


def _recall_metrics(queries: list[dict]) -> tuple[float, float, float, int]:
    """recall@1, recall@3, MRR over probes that expect a hit."""
    ranks = _expected_ranks(queries)
    n = len(ranks)
    if n == 0:
        return 0.0, 0.0, 0.0, 0
    r1 = sum(1 for r in ranks if r == 1) / n
    r3 = sum(1 for r in ranks if r and r <= 3) / n
    mrr = sum(1.0 / r for r in ranks if r) / n
    return r1, r3, mrr, n


def _lure_beats_target_rate(queries: list[dict]) -> float:
    """Discrimination probes (expect AND must_not): fraction where the lure
    outranks the right answer — corpus-size-independent, unlike top-k FP."""
    disc = [q for q in queries
            if q.get("expect_ids") and q.get("must_not_ids")]
    if not disc:
        return 0.0
    bad = 0
    for q in disc:
        target = _best_rank(q["hits"], q["expect_ids"])
        lure = _best_rank(q["hits"], q["must_not_ids"])
        if lure is not None and (target is None or lure < target):
            bad += 1
    return bad / len(disc)


def _neg_leak_rate(queries: list[dict]) -> float:
    """Pure-negative probes (no expected answer): fraction whose rank-1 hit
    is a listed lure — calibration on unanswerable queries."""
    negs = [q for q in queries
            if not q.get("expect_ids") and q.get("must_not_ids")]
    if not negs:
        return 0.0
    bad = 0
    for q in negs:
        top = q["hits"][0]["corpus_id"] if q["hits"] else None
        if top in q["must_not_ids"]:
            bad += 1
    return bad / len(negs)


def _capture_rate(ingest: list[dict]) -> float:
    if not ingest:
        return 0.0
    return sum(1 for r in ingest if r.get("ok")) / len(ingest)


def _ingest_latency(ingest: list[dict]) -> float:
    """Mean per-fact write latency (ms) — captures the LLM-extraction cost
    of the write path vs a verbatim store."""
    if not ingest:
        return 0.0
    return sum(r.get("latency_ms", 0.0) for r in ingest) / len(ingest)


def _roundtrip_rate(queries: list[dict], top_k: int) -> float:
    """Fraction of single-fact probes whose own fact returns within top_k —
    the capture round-trip signal (written then immediately recallable)."""
    singles = [q for q in queries if len(q.get("expect_ids", [])) == 1]
    if not singles:
        return 0.0
    ok = 0
    for q in singles:
        rank = _hit_rank(q["hits"][:top_k], q["expect_ids"])
        ok += 1 if rank else 0
    return ok / len(singles)


def _lifecycle_pass(life: dict | None) -> bool | None:
    if not life:
        return None
    return bool(life.get("old_retired") and life.get("new_surfaced")
                and not life.get("old_surfaced"))


def score(dump: dict) -> Metrics:
    top_k = dump.get("top_k", 5)
    queries = dump.get("queries", [])
    r1, r3, mrr, n = _recall_metrics(queries)
    return Metrics(
        system=dump["system"], mode=dump.get("mode", "?"),
        recall_at_1=r1, recall_at_3=r3, mrr=mrr,
        lure_beats_target=_lure_beats_target_rate(queries),
        neg_leak=_neg_leak_rate(queries),
        capture_ok=_capture_rate(dump.get("ingest", [])),
        roundtrip=_roundtrip_rate(queries, top_k),
        ingest_ms=_ingest_latency(dump.get("ingest", [])),
        lifecycle_pass=_lifecycle_pass(dump.get("lifecycle")),
        scored_probes=n)


def _fairness_ok(dumps: list[dict]) -> tuple[bool, str]:
    sets = {d["system"]: set(d.get("corpus_ids", [])) for d in dumps}
    ids = list(sets.values())
    if len(ids) < 2:
        return True, "single system — fairness gate not applicable"
    if ids[0] == ids[1]:
        return True, f"both systems ingested {len(ids[0])} identical corpus ids"
    missing = ids[0].symmetric_difference(ids[1])
    return False, f"corpus mismatch — diverging ids: {sorted(missing)}"


def _fmt_pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def _fmt_life(v: bool | None) -> str:
    return "n/a" if v is None else ("PASS" if v else "FAIL")


_ROWS = (
    ("recall@1", lambda m: _fmt_pct(m.recall_at_1), "higher"),
    ("recall@3", lambda m: _fmt_pct(m.recall_at_3), "higher"),
    ("MRR", lambda m: f"{m.mrr:6.3f}", "higher"),
    ("lure beats target", lambda m: _fmt_pct(m.lure_beats_target), "lower"),
    ("negative rank-1 leak", lambda m: _fmt_pct(m.neg_leak), "lower"),
    ("capture ok", lambda m: _fmt_pct(m.capture_ok), "higher"),
    ("round-trip", lambda m: _fmt_pct(m.roundtrip), "higher"),
    ("ingest latency ms/fact", lambda m: f"{m.ingest_ms:8.1f}", "lower"),
    ("lifecycle", lambda m: _fmt_life(m.lifecycle_pass), "PASS"),
)


def render(metrics: list[Metrics], fairness: str) -> str:
    cols = [m.system for m in metrics]
    lines = ["# Memory A/B Scorecard", "",
             f"_Fairness: {fairness}_", ""]
    head = "| dimension | better | " + " | ".join(cols) + " |"
    sep = "|---|---|" + "---|" * len(cols)
    lines += [head, sep]
    for label, fn, better in _ROWS:
        cells = " | ".join(fn(m) for m in metrics)
        lines.append(f"| {label} | {better} | {cells} |")
    lines += ["", "_Modes: " +
              ", ".join(f"{m.system}={m.mode}" for m in metrics) +
              f"; scored probes={metrics[0].scored_probes}_", ""]
    lines.append(_recommendation(metrics))
    return "\n".join(lines) + "\n"


def _recommendation(metrics: list[Metrics]) -> str:
    if len(metrics) < 2:
        m = metrics[0]
        return (f"## Note\n\nOnly **{m.system}** present — run the other "
                "adapter, then re-score for the head-to-head.")
    a, b = metrics[0], metrics[1]
    quality_tied = (abs(a.recall_at_3 - b.recall_at_3) < 1e-9
                    and abs(a.lure_beats_target - b.lure_beats_target) < 1e-9)
    fast = a if a.ingest_ms <= b.ingest_ms else b
    slow = b if fast is a else a
    head = ("Recall quality is a **tie** on this corpus (recall@1/@3, MRR, "
            "lure-discrimination all equal)" if quality_tied
            else f"**{(a if a.recall_at_3 >= b.recall_at_3 else b).system}** "
            "leads on recall@3")
    return (f"## Recommendation\n\n{head}, so the separators are the other "
            f"axes. **{fast.system}** writes ~{slow.ingest_ms / max(fast.ingest_ms, 1e-9):.0f}× "
            f"faster ({fast.ingest_ms:.1f} vs {slow.ingest_ms:.1f} ms/fact) "
            "because it stores verbatim with no extraction LLM in the write "
            f"path; **{slow.system}** paraphrases on ingest and carries that "
            "provider dependency (a billing/availability risk). On negative "
            "calibration, lower rank-1 leak is better. Net: keep the faster, "
            "verbatim store as source-of-truth and export to the other only "
            "where its cross-tool reach is needed. Confirm with the live "
            "per-prompt overhead pass (step 5).")


def _overhead_section() -> str:
    """Append the step-5 per-prompt injection overhead if it's been run."""
    path = spec.RESULTS_DIR / "overhead.json"
    if not path.exists():
        return ""
    o = json.loads(path.read_text())
    lines = ["", "## Per-prompt injection overhead (step 5)", "",
             f"_Tokens of the injected block per prompt; tokenizer "
             f"{o['tokenizer']}._", "",
             "| regime | regin | hindsight | ratio |", "|---|---|---|---|"]
    for regime in ("controlled", "production"):
        r, h = o[regime]["regin"], o[regime]["hindsight"]
        ratio = h["mean_tokens"] / max(r["mean_tokens"], 1e-9)
        lines.append(
            f"| {regime} | {r['mean_tokens']:.0f} tok ({r['mean_entries']} ent) "
            f"| {h['mean_tokens']:.0f} tok ({h['mean_entries']} ent) "
            f"| {ratio:.1f}× |")
    lines.append("")
    return "\n".join(lines)


def _dedup_section() -> str:
    """Append the step-6 cross-system double-injection tax if measured."""
    path = spec.RESULTS_DIR / "dedup.json"
    if not path.exists():
        return ""
    s = json.loads(path.read_text())["summary"]
    return "\n".join([
        "", "## Cross-system dedup (step 6)", "",
        "_Both systems active on the same corpus — the worst case (fully "
        "shared knowledge). Production overlap is lower the less the two "
        "stores share._", "",
        f"- {s['regin_facts_also_in_hindsight_pct']:.0f}% of regin's injected "
        f"facts are **also** injected by Hindsight "
        f"({s['pct_prompts_with_overlap']:.0f}% of prompts have overlap)",
        f"- ~{s['mean_redundant_tokens']:.0f} redundant tokens/prompt "
        f"({s['mean_overlap_facts']} duplicated facts) — paid twice for the "
        "same knowledge when both run together", ""])


def main() -> None:
    dumps = [d for d in (spec.load_dump(s) for s in SYSTEMS) if d]
    if not dumps:
        print("no result dumps found — run an adapter first")
        return
    ok, fairness = _fairness_ok(dumps)
    if not ok:
        print(f"FAIRNESS GATE FAILED: {fairness}")
    metrics = [score(d) for d in dumps]
    card = render(metrics, fairness) + _overhead_section() + _dedup_section()
    out = spec.RESULTS_DIR / "scorecard.md"
    out.write_text(card)
    print(card)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
