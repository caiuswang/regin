"""Grading orchestration — the two-tier cost strategy.

Every session can be graded by the cheap mechanical `screen` tier (no
LLM: span evidence only). The expensive `deep` tier adds a self-fetching
agentic judge on BOTH axes — correctness (claim extraction, grounding,
coverage) and process (P1–P3 trajectory assessment; P4 cost stays
mechanical). `tier='auto'` screens first and escalates only
borderline/failing sessions — reserving the agentic grader for the
sessions where it earns its cost. Each axis resolves its own judge, so a
deep run drives two independent investigations.

Grades are triage, not truth: the verdicts surface suspicious sessions
and explain why; they are persisted with their tier/judge/rubric
provenance so a human spot-check loop can calibrate them.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.grader import store
from lib.grader.correctness import SATISFIED, grade_correctness
from lib.grader.evidence import build_evidence
from lib.grader.models import AxisGrade
from lib.grader.process import EFFICIENT, grade_process
from lib.settings import settings

log = get_activity_logger("grader")

AXES = ("correctness", "process")
TIERS = ("screen", "deep", "auto")


class GradingError(ValueError):
    """Raised when a session cannot be graded (no trace data)."""


def _resolve_judge(llm, tier: str, provider: str | None = None):
    """Explicit llm object wins; otherwise resolve by tier. `provider` names a
    judge agent (key in `topic_proposal_external_agents`) to use for this run,
    overriding `settings.grader.external_agent`."""
    if llm is not None and llm != "auto":
        return llm
    if tier == "screen":
        return None
    from lib.grader.adapters import resolve_judge
    return resolve_judge(agent_id=provider)


_PASS_VERDICTS = {"correctness": SATISFIED, "process": EFFICIENT}


def _pass_verdict(axis: str) -> str:
    """The verdict that counts as a pass for `axis`. Gradeable aspects reuse
    the correctness vocabulary, so their pass value is SATISFIED."""
    return _PASS_VERDICTS.get(axis, SATISFIED)


def _mechanical_grades(evidence, axes: tuple[str, ...]
                       ) -> dict[str, AxisGrade]:
    """The cheap, LLM-free screen pass for the requested axes (correctness
    first so process can condition on it). Aspects have no mechanical form."""
    out: dict[str, AxisGrade] = {}
    if "correctness" in axes:
        out["correctness"] = grade_correctness(
            evidence, llm=None, max_claims=settings.grader.deep_max_claims)
    if "process" in axes:
        out["process"] = grade_process(evidence,
                                       correctness=out.get("correctness"))
    return out


def _resolve_aspect_defs(aspect_keys: list[str]
                         ) -> list[tuple[str, str, str]]:
    """(key, label, description) for each configured, non-builtin aspect the
    run asked to grade. Builtin aspects (the grounded axes) are excluded —
    they are graded as axes, not generic aspects."""
    by_key = {a.key: a for a in (settings.grader.aspects or [])}
    defs: list[tuple[str, str, str]] = []
    for key in aspect_keys:
        aspect = by_key.get(key)
        if aspect is not None and not getattr(aspect, "builtin", False):
            defs.append((aspect.key, aspect.label, aspect.description or ""))
    return defs


def _invalid_aspects(aspect_keys: list[str]) -> list[str]:
    """Aspect keys that are unknown or are a builtin axis (not gradeable as
    an aspect) — the caller 400s/raises on a non-empty result."""
    by_key = {a.key: a for a in (settings.grader.aspects or [])}
    return [k for k in aspect_keys
            if by_key.get(k) is None or getattr(by_key[k], "builtin", False)]


def _any_nonpass(grades: dict[str, AxisGrade]) -> bool:
    return any(g.verdict != _pass_verdict(a) for a, g in grades.items())


def _deep_combined(evidence, judge, axes: tuple[str, ...],
                   aspect_keys: list[str],
                   fallback: dict[str, AxisGrade] | None
                   ) -> dict[str, AxisGrade]:
    """Run ONE combined judge over the requested axes + aspects, then fill
    any axis the judge couldn't grade from the mechanical fallback. Aspects
    are LLM-only, so an aspect the judge omits is simply absent."""
    from lib.grader.combined_agentic import grade_combined
    aspect_defs = _resolve_aspect_defs(aspect_keys)
    deep = grade_combined(evidence, judge, evidence.trace_id, axes, aspect_defs)
    fb = fallback if fallback is not None else _mechanical_grades(evidence, axes)
    out: dict[str, AxisGrade] = {}
    for axis in axes:
        out[axis] = deep.get(axis) or fb.get(axis)
    for key, _label, _desc in aspect_defs:
        if deep.get(key) is not None:
            out[key] = deep[key]
    return out


def _run_grades(evidence, axes: tuple[str, ...], tier: str, llm,
                provider: str | None, aspect_keys: list[str]
                ) -> dict[str, AxisGrade]:
    """Resolve the requested dimensions for one session. screen → mechanical
    only (aspects skipped); deep → one combined judge; auto → mechanical
    screen, escalating to the combined judge when an axis is non-pass or any
    aspect was requested (aspects are deep-only)."""
    judge = _resolve_judge(llm, tier, provider)
    if judge is None or tier == "screen":
        if aspect_keys:
            log.write("aspects_skipped_no_judge", trace_id=evidence.trace_id,
                      tier=tier, aspects=aspect_keys)
        return _mechanical_grades(evidence, axes)
    if tier == "deep":
        return _deep_combined(evidence, judge, axes, aspect_keys, None)
    base = _mechanical_grades(evidence, axes)
    if settings.grader.auto_escalate and (_any_nonpass(base) or aspect_keys):
        log.write("grade_escalated", trace_id=evidence.trace_id,
                  screen_verdicts={a: g.verdict for a, g in base.items()})
        return _deep_combined(evidence, judge, axes, aspect_keys, base)
    return base


def _validate_request(axes: tuple[str, ...], tier: str,
                      aspects: list[str]) -> None:
    if not settings.grader.enabled:
        raise GradingError("the session grader is disabled "
                           "(settings.grader.enabled)")
    if tier not in TIERS:
        raise GradingError(f"unknown tier {tier!r}; expected one of {TIERS}")
    unknown = [a for a in axes if a not in AXES]
    if unknown:
        raise GradingError(f"unknown axes {unknown}; expected from {AXES}")
    bad = _invalid_aspects(aspects)
    if bad:
        raise GradingError(f"unknown/non-gradeable aspects {bad}")
    if not axes and not aspects:
        raise GradingError("select at least one axis or aspect to grade")


def grade_session(trace_id: str, *, axes: tuple[str, ...] = AXES,
                  tier: str = "auto", llm=None, persist: bool = True,
                  is_test: int = 0, provider: str | None = None,
                  distill: bool | None = None,
                  aspects: list[str] | None = None) -> dict:
    """Grade one captured session on the requested axes.

    Returns `{"trace_id", "grades": {axis: AxisGrade-dict}}`. The process
    axis is conditioned on the correctness verdict when both are graded,
    so correctness always runs first. `provider` overrides the configured
    judge agent (`settings.grader.external_agent`) for this run.

    `aspects` is a per-run list of reviewer-aspect keys to grade as their own
    dimensions (each gets a `satisfied`/`needs_revision`/`fail` verdict from
    the combined deep judge). Aspects are LLM-only, so the screen tier skips
    them. `distill` decides whether a flagged session is fed to the distiller
    this run: explicit True/False wins; None falls back to
    `settings.grader.distill_on_fail`.
    """
    aspects = list(aspects or [])
    _validate_request(axes, tier, aspects)
    evidence = build_evidence(trace_id)
    if not evidence.events and not evidence.final_text:
        raise GradingError(f"no trace data recorded for session {trace_id}")

    grades = _run_grades(evidence, axes, tier, llm, provider, aspects)

    if persist:
        _persist_grades(trace_id, grades, is_test, evidence)
        _maybe_distill_on_fail(trace_id, grades, evidence, is_test, distill)
        _maybe_score_injection_usefulness(trace_id, evidence, is_test)
        _maybe_apply_injection_relevance(trace_id, grades, evidence, is_test)
        _maybe_notify_grade(trace_id, grades, is_test)
    log.write("session_graded", trace_id=trace_id,
              verdicts={a: g.verdict for a, g in grades.items()})
    return {"trace_id": trace_id,
            "grades": {a: g.to_dict() for a, g in grades.items()}}


def _has_failure(grades: dict[str, AxisGrade]) -> bool:
    """True when any graded dimension missed its pass verdict — the trigger
    for distilling the session into preventive lessons."""
    return any(g.verdict != _pass_verdict(axis)
               for axis, g in grades.items())


def _maybe_distill_on_fail(trace_id: str, grades: dict[str, AxisGrade],
                           evidence, is_test: int,
                           distill: bool | None = None) -> None:
    """Slice 2 of the grade→memory loop: when a persisted, non-test grade
    flags any axis, feed the grader's findings to the distiller so they
    become recallable lessons. Lazy, fully guarded, and best-effort — a
    distill failure must never fail the grade.

    `distill` is the per-run decision: explicit True/False wins; None falls
    back to `settings.grader.distill_on_fail`."""
    want_distill = (settings.grader.distill_on_fail if distill is None
                    else distill)
    is_test = is_test or int(evidence.session.get("is_test") or 0)
    if is_test or not want_distill or not _has_failure(grades):
        return
    _run_distill(trace_id, grades)


def _run_distill(trace_id: str, grades: dict[str, AxisGrade]) -> None:
    """Feed the grader's per-axis findings to the distiller. Best-effort —
    a distill failure must never fail the grade."""
    try:
        import lib.memory as memory
        if not memory.enabled():
            return
        from lib.memory.adapters import resolve_distiller
        from lib.memory.distill import distill_session
        payload = {axis: g.to_dict() for axis, g in grades.items()}
        result = distill_session(
            memory.get_store(), trace_id, llm=resolve_distiller(),
            grade=payload,
            importance_bonus=settings.grader.distill_importance_bonus)
        if result.skipped_already_distilled:
            log.read("grade_distill_skipped_already_distilled",
                     trace_id=trace_id)
            return
        log.write("grade_distilled", trace_id=trace_id,
                  proposed=result.proposed, approved=result.approved,
                  dropped=result.dropped)
    except Exception:  # noqa: BLE001 — feed-forward must not break grading
        log.error("grade_distill_failed", trace_id=trace_id, exc_info=True)


def _maybe_score_injection_usefulness(trace_id: str, evidence,
                                      is_test: int) -> None:
    """Close the inject→usefulness loop on a real grading run: score
    whether the memories auto-injected into this session engaged its work
    (deterministic referent overlap — no LLM, no failure trigger needed).
    Same gating as distill (persisted, non-test, memory enabled) and
    best-effort — a feedback failure must never fail the grade."""
    is_test = is_test or int(evidence.session.get("is_test") or 0)
    if is_test or not settings.agent_memory.feedback_on_grade:
        return
    try:
        import lib.memory as memory
        if not memory.enabled():
            return
        from lib.memory.feedback import score_injection_usefulness
        result = score_injection_usefulness(trace_id, memory.get_store())
        log.write("grade_injection_scored", trace_id=trace_id,
                  engaged=result.engaged, ignored=result.ignored,
                  no_referents=result.no_referents)
    except Exception:  # noqa: BLE001 — feedback must not break grading
        log.error("grade_injection_feedback_failed", trace_id=trace_id,
                  exc_info=True)


def _maybe_apply_injection_relevance(trace_id: str,
                                     grades: dict[str, AxisGrade],
                                     evidence, is_test: int) -> None:
    """Close the topic-routing loop: stamp the `InjectedRelated` aspect's
    verdict onto this session's `topic_injections` so the recall hook can
    learn to withhold a recurringly-irrelevant route. The outcome signal the
    deterministic engagement proxy can't produce. Same gating as the other
    grade-time feedback (persisted, non-test, memory enabled, feature on) and
    best-effort — a feedback failure must never fail the grade."""
    cfg = settings.agent_memory
    grade = grades.get(cfg.topic_relevance_aspect)
    if grade is None or not cfg.topic_relevance_feedback:
        return
    is_test = is_test or int(evidence.session.get("is_test") or 0)
    if is_test:
        return
    try:
        import lib.memory as memory
        if not memory.enabled():
            return
        store = memory.get_store()
        stamped = store.apply_topic_relevance(trace_id, grade.verdict)
        log.write("topic_relevance_applied", trace_id=trace_id,
                  aspect=cfg.topic_relevance_aspect, verdict=grade.verdict,
                  rows=stamped)
        if cfg.topic_relevance_notify:
            _notify_topic_proposals(store)
    except Exception:  # noqa: BLE001 — feedback must not break grading
        log.error("topic_relevance_feedback_failed", trace_id=trace_id,
                  exc_info=True)


def _notify_topic_proposals(store) -> None:
    """Push any topic now over the fail-rate bar (status 'proposed') to the
    agent inbox so the human gate isn't invisible. Already best-effort behind
    its caller's guard."""
    from lib.grader.topic_notify import notify_proposals
    proposed = [r for r in store.topic_relevance_summary()
                if r["status"] == "proposed"]
    notify_proposals(proposed)


def _maybe_notify_grade(trace_id: str, grades: dict[str, AxisGrade],
                        is_test: int) -> None:
    """Emit a `grade.finished` inbox event deep-linked to the graded
    session's trace view. Skipped for test grades; off unless the operator
    enables the kind. `events.emit` is best-effort and swallows failures."""
    if is_test:
        return
    from lib.agent_messages import events
    verdicts = ", ".join(f"{a}: {g.verdict}" for a, g in grades.items())
    events.emit(
        "grade.finished", trace_id=trace_id, title="Session grade finished",
        body=f"Grading completed for this session — {verdicts}.",
        key=f"grade-finished:{trace_id}",
        links=[{"label": "View session trace",
                "href": events.session_url(trace_id)}])


def _persist_grades(trace_id: str, grades: dict[str, AxisGrade],
                    is_test: int, evidence) -> None:
    # grades of test-capture sessions inherit the session's flag so the
    # include_tests=False default keeps excluding them
    is_test = is_test or int(evidence.session.get("is_test") or 0)
    for grade in grades.values():
        store.save_grade(trace_id, grade, is_test=is_test)


__all__ = ["grade_session", "GradingError", "AXES", "TIERS"]
