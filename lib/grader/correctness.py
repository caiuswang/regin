"""Correctness axis — orchestrates the groundedness / coverage /
source-quality triad and applies the gates.

Gate order (gates fire before ratios):

1. a CONTRADICTED load-bearing claim ⇒ FAIL — the answer depends on
   something the trace disproves;
2. a CONTRADICTED incidental claim, any MISSING required item, any
   not-GROUNDED claim, or a PROXY/UNVERIFIED source behind a load-bearing
   claim ⇒ at most needs_revision;
3. otherwise the graded remainder: groundedness must be perfect, coverage
   must clear its ratio bar. Ratios are reported regardless, for trending.

The two axes are never fused: this module knows nothing about cost.
"""

from __future__ import annotations

from lib.grader import rubric as rubric_mod
from lib.grader.coverage import assess_coverage, derive_checklist
from lib.grader.evidence import EvidenceIndex
from lib.grader.extraction import extract_claims
from lib.grader.grounding import ground_claims
from lib.grader.models import (
    AUTHORITATIVE, CONTRADICTED, COVERED, GROUNDED, MISSING, PARTIAL,
    PROXY, STALE, UNGROUNDED, UNVERIFIED,
    AxisGrade, Claim, ClaimVerdict, CoverageItem, SourceVerdict,
)
from lib.grader.source_quality import assess_sources

SATISFIED = "satisfied"
NEEDS_REVISION = "needs_revision"
FAIL = "fail"


def _aggregate_verdict(coverage: list[CoverageItem],
                       pass_ratio: float) -> ClaimVerdict:
    """`c0` is grounded by the checklist, not a single span."""
    covered = sum(1 for c in coverage if c.verdict == COVERED)
    total = len(coverage) or 1
    missing = [c.item for c in coverage if c.verdict == MISSING]
    if not missing and covered / total >= pass_ratio:
        return ClaimVerdict("c0", GROUNDED, None, "coverage checklist",
                            f"{covered}/{total} required items covered",
                            "checklist")
    return ClaimVerdict("c0", UNGROUNDED, None, "coverage checklist",
                        f"{covered}/{total} covered; missing: "
                        + ("; ".join(missing[:3]) or "ratio below bar"),
                        "checklist")


def _contradiction_gate(claims: list[Claim],
                        verdicts: dict[str, ClaimVerdict]) -> str | None:
    by_id = {c.id: c for c in claims}
    contradicted = [v for v in verdicts.values() if v.verdict == CONTRADICTED]
    if not contradicted:
        return None
    load_bearing = any(by_id[v.claim_id].load_bearing for v in contradicted
                       if v.claim_id in by_id)
    return FAIL if load_bearing else NEEDS_REVISION


def _groundedness_below_bar(claims: list[Claim],
                            verdicts: dict[str, ClaimVerdict],
                            bar: float) -> bool:
    """Perfect-groundedness is required of LOAD-BEARING claims only. An
    incidental over-extracted claim that merely lacks evidence — e.g. a
    closing reassurance like "nothing pushed", a *non-action* no positive
    span can confirm — must not sink the axis (extraction over-extracts by
    design; the docstring calls a falsely-extracted non-claim "cheap"). A
    CONTRADICTED incidental is still caught by the contradiction gate."""
    by_id = {c.id: c for c in claims}
    gated = [v for v in verdicts.values()
             if (c := by_id.get(v.claim_id)) is None or c.load_bearing]
    grounded = sum(1 for v in gated if v.verdict == GROUNDED)
    return bool(gated) and grounded / len(gated) < bar


def _has_revision_smell(claims: list[Claim],
                        verdicts: dict[str, ClaimVerdict],
                        coverage: list[CoverageItem], bars: dict) -> bool:
    if any(c.verdict == MISSING for c in coverage):
        return True
    if _groundedness_below_bar(claims, verdicts, bars["groundedness_ratio"]):
        return True
    covered = sum(1 for c in coverage if c.verdict == COVERED)
    return bool(coverage) and covered / len(coverage) < bars["coverage_ratio"]


def _unverified_load_bearing(claims: list[Claim],
                             sources: list[SourceVerdict]) -> bool:
    by_id = {c.id: c for c in claims}
    return any(s.verdict == UNVERIFIED and by_id.get(s.claim_id) is not None
               and by_id[s.claim_id].load_bearing for s in sources)


def _source_smell(claims: list[Claim], sources: list[SourceVerdict],
                  bars: dict) -> bool:
    """Any proxy-only claim caps the axis (§11.4); an UNVERIFIED source on
    a load-bearing claim is a hard miss; widespread proxying fails the
    source-quality ratio bar."""
    if any(s.verdict == PROXY for s in sources):
        return True
    if _unverified_load_bearing(claims, sources):
        return True
    authoritative = sum(1 for s in sources if s.verdict == AUTHORITATIVE)
    return bool(sources) and authoritative / len(sources) < bars["source_ratio"]


def _decide_verdict(claims: list[Claim], verdicts: dict[str, ClaimVerdict],
                    coverage: list[CoverageItem],
                    sources: list[SourceVerdict], bars: dict) -> str:
    gated = _contradiction_gate(claims, verdicts)
    if gated is not None:
        return gated
    if _has_revision_smell(claims, verdicts, coverage, bars):
        return NEEDS_REVISION
    if _source_smell(claims, sources, bars):
        return NEEDS_REVISION
    return SATISFIED


def _scoreboard(verdicts: dict[str, ClaimVerdict],
                coverage: list[CoverageItem],
                sources: list[SourceVerdict]) -> dict:
    grounded = sum(1 for v in verdicts.values() if v.verdict == GROUNDED)
    covered = sum(1 for c in coverage if c.verdict == COVERED)
    authoritative = sum(1 for s in sources if s.verdict == AUTHORITATIVE)
    return {
        "groundedness": {"grounded": grounded, "total": len(verdicts)},
        "coverage": {"covered": covered, "total": len(coverage)},
        "source_quality": {"authoritative": authoritative,
                           "total": len(sources)},
    }


def _claim_bullets(claims: list[Claim],
                   verdicts: dict[str, ClaimVerdict]) -> list[str]:
    by_id = {c.id: c for c in claims}
    bullets = []
    for verdict in verdicts.values():
        if verdict.verdict == GROUNDED:
            continue
        claim = by_id.get(verdict.claim_id)
        ctype = claim.type if claim else "?"
        bullets.append(f"- [{verdict.claim_id}] {ctype} — "
                       f"{verdict.verdict}: {verdict.reason}.")
    return bullets


def _coverage_bullets(coverage: list[CoverageItem]) -> list[str]:
    return [f"- [item: {c.item}] — {c.verdict}: {c.reason}."
            for c in coverage if c.verdict in (PARTIAL, MISSING)]


def _source_bullets(sources: list[SourceVerdict]) -> list[str]:
    return [f"- [{s.claim_id}] {s.source} — {s.verdict}: {s.reason}."
            for s in sources if s.verdict != AUTHORITATIVE]


def render_report(verdict: str, scoreboard: dict, claims: list[Claim],
                  verdicts: dict[str, ClaimVerdict],
                  coverage: list[CoverageItem],
                  sources: list[SourceVerdict]) -> str:
    g, c, s = (scoreboard["groundedness"], scoreboard["coverage"],
               scoreboard["source_quality"])
    lines = [f"Groundedness {g['grounded']}/{g['total']}. "
             f"Coverage {c['covered']}/{c['total']}. "
             f"Source-quality {s['authoritative']}/{s['total']}.  "
             f"Verdict: {verdict}"]
    lines += _claim_bullets(claims, verdicts)
    lines += _coverage_bullets(coverage)
    lines += _source_bullets(sources)
    return "\n".join(lines)


def correctness_bars() -> dict:
    """The rubric's numeric pass-bars, named for the gate code. Shared by
    the mechanical and agentic correctness paths so both apply the same
    policy."""
    rubric = rubric_mod.correctness_rubric()
    return {
        "coverage_ratio": rubric["criteria"]["coverage"]["pass_ratio"],
        "aggregate_ratio": rubric["criteria"]["coverage"]["pass_ratio"],
        "groundedness_ratio": rubric["criteria"]["groundedness"]["pass_ratio"],
        "source_ratio": rubric["criteria"]["source_quality"]["pass_ratio"],
    }


def build_axis_grade(claims: list[Claim], verdicts: dict[str, ClaimVerdict],
                     coverage: list[CoverageItem], bars: dict, *,
                     tier: str, judge: str = "mechanical") -> AxisGrade:
    """Apply the rubric gates to a ready ledger and assemble the grade.

    This is the policy layer — source classification, the gate order, the
    scoreboard and report. It does not know *how* the claims were grounded
    (mechanical matching vs. an agentic judge), only their verdicts, so
    both paths share one authoritative rubric application. `c0` must
    already be in `verdicts`."""
    sources = assess_sources(claims, verdicts)
    verdict = _decide_verdict(claims, verdicts, coverage, sources, bars)
    scoreboard = _scoreboard(verdicts, coverage, sources)
    report = render_report(verdict, scoreboard, claims, verdicts,
                           coverage, sources)
    detail = {
        "claims": [c.to_dict() for c in claims],
        "verdicts": {k: v.to_dict() for k, v in verdicts.items()},
        "checklist": [c.to_dict() for c in coverage],
        "sources": [s.to_dict() for s in sources],
    }
    return AxisGrade(axis="correctness", verdict=verdict, tier=tier,
                     scoreboard=scoreboard, report=report, detail=detail,
                     rubric_version=rubric_mod.RUBRIC_VERSION, judge=judge)


def grade_correctness(evidence: EvidenceIndex, llm=None,
                      max_claims: int = 40) -> AxisGrade:
    """Run the full mechanical correctness triad over one session's
    evidence (with `llm`, claim extraction / checklist / grounding get
    LLM-assisted passes; the agentic judge is a separate path —
    `lib.grader.agentic`)."""
    bars = correctness_bars()
    # The checklist is fixed from the user's words alone, before the
    # session body is read (anti-gaming) — so derive it before extraction.
    checklist = derive_checklist(evidence.full_task_text(), llm)
    claims = extract_claims(evidence, llm, max_claims=max_claims)
    verdicts = ground_claims(claims, evidence, llm)
    coverage = assess_coverage(checklist, claims, verdicts, evidence)
    verdicts["c0"] = _aggregate_verdict(coverage, bars["aggregate_ratio"])
    return build_axis_grade(claims, verdicts, coverage, bars,
                            tier="deep" if llm is not None else "screen")


__all__ = ["grade_correctness", "build_axis_grade", "correctness_bars",
           "render_report", "_aggregate_verdict",
           "SATISFIED", "NEEDS_REVISION", "FAIL"]
