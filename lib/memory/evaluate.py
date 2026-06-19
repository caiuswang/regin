"""Recall-quality eval harness for agent memory.

regin auto-injects recalled memories into prompts, but nothing measures
whether recall surfaces the *right* memory for a given task description.
This module makes inject quality measurable and regression-guarded: a
small JSONL case set drives the **true** recall path (`lib.memory.recall`
— FTS/dense fusion and quality weighting intact) and scores it with
standard retrieval metrics (hit@1, hit@k, MRR).

A case is `{"query", "expect_any", "note"?}`. It PASSES at k when any of
the top-k recalled memories' title+body contains any `expect_any`
substring (case-insensitive).

No LLM, no network: works against FTS-only stores (embedder optional).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lib.activity_log import get_activity_logger

log = get_activity_logger("memory")


@dataclass
class EvalCase:
    """One recall expectation. `expect_any` substrings identify the
    memory that *should* surface for `query`; a case passes when any of
    them appears in any top-k hit's title+body."""

    query: str
    expect_any: list[str]
    note: Optional[str] = None

    @classmethod
    def from_dict(cls, obj: dict) -> "EvalCase":
        query = (obj.get("query") or "").strip()
        if not query:
            raise ValueError("eval case missing non-empty 'query'")
        expect = obj.get("expect_any") or []
        if not isinstance(expect, list) or not expect:
            raise ValueError(f"eval case {query!r} needs a non-empty "
                             "'expect_any' list")
        return cls(query=query, expect_any=[str(s) for s in expect],
                   note=obj.get("note"))


@dataclass
class CaseVerdict:
    """Outcome of running one case: where (if anywhere) the expected
    memory surfaced in the top-k, plus the top result for context."""

    query: str
    passed: bool
    hit_rank: Optional[int]          # 1-based rank of first matching hit
    top_id: Optional[str]
    top_title: Optional[str]
    matched_title: Optional[str]     # title of the matching hit (if any)

    def to_dict(self) -> dict:
        return {
            "query": self.query, "passed": self.passed,
            "hit_rank": self.hit_rank, "top_id": self.top_id,
            "top_title": self.top_title, "matched_title": self.matched_title,
        }


@dataclass
class EvalReport:
    """Per-case verdicts plus aggregate retrieval metrics."""

    verdicts: list[CaseVerdict] = field(default_factory=list)
    top_k: int = 5

    @property
    def total(self) -> int:
        return len(self.verdicts)

    @property
    def passed(self) -> int:
        return sum(1 for v in self.verdicts if v.passed)

    @property
    def hit_at_1(self) -> float:
        if not self.verdicts:
            return 0.0
        wins = sum(1 for v in self.verdicts if v.hit_rank == 1)
        return wins / len(self.verdicts)

    @property
    def hit_at_k(self) -> float:
        if not self.verdicts:
            return 0.0
        return self.passed / len(self.verdicts)

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank: 1/rank of the first matching hit per
        case (0 when the case missed), averaged over all cases."""
        if not self.verdicts:
            return 0.0
        total = sum(1.0 / v.hit_rank for v in self.verdicts if v.hit_rank)
        return total / len(self.verdicts)

    def to_dict(self) -> dict:
        return {
            "top_k": self.top_k,
            "total": self.total,
            "passed": self.passed,
            "hit_at_1": self.hit_at_1,
            "hit_at_k": self.hit_at_k,
            "mrr": self.mrr,
            "cases": [v.to_dict() for v in self.verdicts],
        }


def _matches(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def _rank_case(hits: list,
               expect_any: list[str]) -> tuple[Optional[int], Optional[str]]:
    """Find the 1-based rank of the first hit whose title+body contains
    any expected substring. Returns (rank, matched_title) — rank is None
    on a miss."""
    for rank, hit in enumerate(hits, start=1):
        mem = hit.memory
        doc = f"{mem.get('title') or ''} {mem.get('body') or ''}"
        if _matches(doc, expect_any):
            return rank, mem.get("title")
    return None, None


def _evaluate_case(case: EvalCase, *, store, top_k: int, mode: str) -> CaseVerdict:
    hits = store.recall(case.query, top_k=top_k, mode=mode,
                        include_tests=True, reinforce=False)
    rank, matched_title = _rank_case(hits, case.expect_any)
    top = hits[0].memory if hits else None
    return CaseVerdict(
        query=case.query,
        passed=rank is not None,
        hit_rank=rank,
        top_id=top.get("id") if top else None,
        top_title=top.get("title") if top else None,
        matched_title=matched_title,
    )


def evaluate_recall(cases, *, store=None, top_k: int = 5,
                    mode: str = "auto") -> EvalReport:
    """Run each case through the true recall path and score it.

    `cases` is an iterable of `EvalCase` or plain dicts. `store` defaults
    to the process-wide `lib.memory` store. `mode='fts'` forces the
    lexical-only path (no model load); `'auto'` uses dense+rerank when the
    store's embedder can deliver them."""
    if store is None:
        import lib.memory as memory
        store = memory.get_store()
    norm = [c if isinstance(c, EvalCase) else EvalCase.from_dict(c)
            for c in cases]
    report = EvalReport(top_k=top_k)
    for case in norm:
        report.verdicts.append(
            _evaluate_case(case, store=store, top_k=top_k, mode=mode))
    log.read("recall_eval_run", cases=report.total, passed=report.passed,
             hit_at_k=report.hit_at_k, mode=mode)
    return report


def load_cases(path) -> list[EvalCase]:
    """Parse a JSONL case file (one object per line; blank lines skipped)."""
    import json
    from pathlib import Path
    cases: list[EvalCase] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(EvalCase.from_dict(json.loads(line)))
    return cases


__all__ = [
    "EvalCase", "CaseVerdict", "EvalReport",
    "evaluate_recall", "load_cases",
]
