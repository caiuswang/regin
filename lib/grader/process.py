"""Process / efficiency axis — outcome-anchored but trajectory-aware.

Grades *properties* of the trajectory, never a prescribed step sequence:

* P1 tool-use appropriateness — each call was the right instrument and its
  output was actually used ("used" = the output fed a later span or the
  final text, approximated lexically; exploration that informed a decision
  is not waste — only unused output is).
* P2 redundancy — repeated reads of an unchanged target, consecutive
  same-shape failing commands with no intervening edit (thrash), repeated
  identical searches.
* P3 reliability — errors recovered vs ignored; an ignored error feeding a
  load-bearing claim caps the axis at `acceptable`.
* P4 cost-proportionality — absolute cost is meaningless, so spend is
  judged against the per-task-class percentile of other captured sessions
  and against coverage value; the cache-read-share sub-check flags an
  unmanaged context window. High spend on a *correct* session is
  proportionate, not wasteful — cost is judged against the outcome, never
  alone (which is why this axis is conditioned on the correctness verdict
  but never fused with it).
"""

from __future__ import annotations

import re

from lib.grader import rubric as rubric_mod
from lib.grader.evidence import (
    EvidenceIndex, MUTATING_TOOLS, ToolEvent, content_tokens,
)
from lib.grader.models import (
    APPROPRIATE, ELEVATED, PROPORTIONATE, RUNAWAY, SUBOPTIMAL, WASTED,
    AxisGrade,
)

EFFICIENT = "efficient"
ACCEPTABLE = "acceptable"
WASTEFUL = "wasteful"

_SHELL_DUPE_RE = re.compile(r"^\s*(cat|head|tail|grep|rg|find)\b")


# ── P1: tool-use appropriateness ─────────────────────────────────

def _event_ref_tokens(event: ToolEvent) -> set[str]:
    """The cheap reference tokens an event contributes downstream."""
    return content_tokens(" ".join((
        event.file_path, event.command[:500],
        str(event.attrs.get("diff") or "")[:2000],
        str(event.attrs.get("pattern") or ""))))


def _downstream_token_suffixes(events: list[ToolEvent],
                               final_text: str) -> list[set[str]]:
    """suffix[i] = tokens visible strictly after event i — one reverse
    pass instead of rebuilding the haystack per event (O(n), not O(n²))."""
    final_tokens = content_tokens(final_text)
    suffixes: list[set[str]] = [set()] * len(events)
    acc = set(final_tokens)
    for i in range(len(events) - 1, -1, -1):
        suffixes[i] = acc
        acc = acc | _event_ref_tokens(events[i])
    return suffixes


def _target_tokens(event: ToolEvent) -> set[str]:
    target = event.file_path.rsplit("/", 1)[-1] if event.file_path else ""
    pattern = str(event.attrs.get("pattern") or "")
    return content_tokens(f"{target} {pattern}")


def _p1_verdict(event: ToolEvent, downstream: set[str]) -> tuple[str, str]:
    if event.tool == "Bash" and _SHELL_DUPE_RE.match(event.command):
        return SUBOPTIMAL, (f"`{event.command[:60]}` — a dedicated "
                            "Read/Grep/Glob tool existed")
    if event.tool in ("Read", "Grep", "Glob"):
        targets = _target_tokens(event)
        if targets and not (targets & downstream):
            return WASTED, (f"output of {event.tool} "
                            f"{event.file_path or event.attrs.get('pattern', '')} "
                            "fed no later span or final claim")
    return APPROPRIATE, ""


def assess_tool_use(evidence: EvidenceIndex) -> dict:
    counts = {APPROPRIATE: 0, SUBOPTIMAL: 0, WASTED: 0}
    findings = []
    suffixes = _downstream_token_suffixes(evidence.events,
                                          evidence.final_text)
    for event in evidence.events:
        verdict, reason = _p1_verdict(event, suffixes[event.index])
        counts[verdict] += 1
        if reason:
            findings.append({"span_id": event.span_id, "verdict": verdict,
                             "reason": reason})
    return {"counts": counts, "findings": findings,
            "total": len(evidence.events)}


# ── P2: redundancy / thrash ──────────────────────────────────────

def _read_range(event: ToolEvent) -> tuple:
    # The slice a Read actually covered. `num_lines` is load-bearing: a read
    # of the first 100 lines and a read of the whole file both start at line
    # 1, so without the length they collapse to one key and a chunked walk
    # through a large file is miscounted as a re-read. `offset`/`limit` are
    # the input params (not always persisted) — kept so a span that does
    # carry them still distinguishes.
    return (event.attrs.get("start_line"), event.attrs.get("num_lines"),
            event.attrs.get("offset"), event.attrs.get("limit"))


def _redundant_reads(evidence: EvidenceIndex) -> list[dict]:
    episodes = []
    for path, reads in evidence.reads.items():
        ordered = sorted(reads, key=lambda e: e.index)
        for prev, nxt in zip(ordered, ordered[1:]):
            if _read_range(prev) != _read_range(nxt):
                continue   # chunked reads of one large file aren't redundant
            mutated_between = any(
                prev.index < m.index < nxt.index
                for m in evidence.mutations.get(path, []))
            if not mutated_between:
                episodes.append({"path": path, "spans":
                                 [prev.span_id, nxt.span_id]})
    return episodes


def _same_failure_shape(event: ToolEvent, run: list[ToolEvent]) -> bool:
    if event.tool != "Bash" or not event.is_error:
        return False
    return (not run
            or event.command.split()[:1] == run[-1].command.split()[:1])


def _flush_thrash_run(run: list[ToolEvent], k: int,
                      episodes: list[dict]) -> None:
    if len(run) >= k:
        episodes.append({"command": run[0].command[:80],
                         "spans": [e.span_id for e in run]})


def _thrash_episodes(evidence: EvidenceIndex, k: int) -> list[dict]:
    """Runs of ≥k same-shape failing Bash spans with no intervening edit.
    Reads/searches between retries don't break a run — only an edit (a
    changed approach) or a different/succeeding command does."""
    episodes: list[dict] = []
    run: list[ToolEvent] = []
    for event in evidence.events:
        if _same_failure_shape(event, run):
            run.append(event)
            continue
        if event.tool not in MUTATING_TOOLS and event.tool != "Bash":
            continue
        _flush_thrash_run(run, k, episodes)
        run = [event] if (event.tool == "Bash" and event.is_error) else []
    _flush_thrash_run(run, k, episodes)
    return episodes


def _re_derivations(evidence: EvidenceIndex) -> list[dict]:
    seen: dict[str, str] = {}
    episodes = []
    for search in evidence.searches:
        pattern = str(search.attrs.get("pattern") or "")
        if not pattern:
            continue
        if pattern in seen:
            episodes.append({"pattern": pattern[:80],
                             "spans": [seen[pattern], search.span_id]})
        seen[pattern] = search.span_id
    return episodes


def assess_redundancy(evidence: EvidenceIndex, thrash_k: int) -> dict:
    return {
        "redundant_reads": _redundant_reads(evidence),
        "thrash_episodes": _thrash_episodes(evidence, thrash_k),
        "re_derivations": _re_derivations(evidence),
    }


# ── P3: reliability ──────────────────────────────────────────────

def _mutation_addresses_error(error: ToolEvent, event: ToolEvent) -> bool:
    """A later edit only counts as recovery when it touches something the
    error was about — not any unrelated mutation."""
    if event.tool not in MUTATING_TOOLS:
        return False
    error_tokens = content_tokens(error.command[:500] + " " + error.file_path
                                  + " " + error.stderr[:500])
    return len(error_tokens & _event_ref_tokens(event)) >= 1


def _recovered(error: ToolEvent, evidence: EvidenceIndex) -> bool:
    for event in evidence.events:
        if event.index <= error.index or event.is_error:
            continue
        same_file = error.file_path and event.file_path == error.file_path
        same_cmd = (error.command and event.command
                    and event.command.split()[:1] == error.command.split()[:1])
        if same_file or same_cmd or _mutation_addresses_error(error, event):
            return True
    return False


def assess_reliability(evidence: EvidenceIndex,
                       claim_texts: list[str]) -> dict:
    errored = [e for e in evidence.events if e.is_error]
    recovered, ignored, feeding = [], [], []
    claim_tokens = content_tokens(" ".join(claim_texts))
    for error in errored:
        if _recovered(error, evidence):
            recovered.append(error.span_id)
            continue
        ignored.append(error.span_id)
        overlap = content_tokens(error.command + " " + error.file_path)
        if len(overlap & claim_tokens) >= 2:
            feeding.append(error.span_id)
    return {"errored": len(errored), "recovered": len(recovered),
            "ignored": len(ignored), "ignored_spans": ignored,
            "ignored_feeding_claim": feeding}


# ── P4: cost proportionality ─────────────────────────────────────

def _task_class(session: dict) -> str:
    """Prompt-count buckets as the task-class proxy — a deliberate
    simplification (task *type* isn't recorded); the bucket keeps the
    percentile comparison from judging a 30-prompt session against
    one-shot runs."""
    prompts = int(session.get("prompts") or 0)
    if prompts <= 2:
        return "single-shot"
    return "interactive" if prompts <= 10 else "long-interactive"


def _class_bounds(task_class: str) -> tuple[int, int]:
    return {"single-shot": (0, 2), "interactive": (3, 10)}.get(
        task_class, (11, 10**9))


# A percentile against a handful of sessions is noise that could gate the
# whole axis to wasteful; below this sample size we abstain.
_MIN_PERCENTILE_SAMPLE = 20


def cost_percentile(trace_id: str, cost: float, task_class: str) -> float | None:
    """Mid-rank fraction of comparable sessions costing less than this
    one (strictly-below averaged with at-or-below, so a cohort of equal
    costs reads as the 50th percentile, not the 100th). Raw aggregate by
    design: a COUNT/SUM over `sessions` is the kind of read the ORM layer
    doesn't express better."""
    from lib.orm.engine import get_connection
    lo, hi = _class_bounds(task_class)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN cost_usd < ? THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN cost_usd <= ? THEN 1 ELSE 0 END) "
            "FROM sessions WHERE cost_usd > 0 AND is_test = 0 "
            "AND trace_id != ? AND prompts BETWEEN ? AND ?",
            (cost, cost, trace_id, lo, hi)).fetchone()
    finally:
        conn.close()
    total, strictly, at_or = (row or (0, 0, 0))
    if not total or total < _MIN_PERCENTILE_SAMPLE:
        return None
    return ((strictly or 0) + (at_or or 0)) / (2 * total)


def _cache_read_share(session: dict) -> float | None:
    cache_read = float(session.get("cache_read_tokens") or 0)
    denom = (float(session.get("input_tokens") or 0) + cache_read
             + float(session.get("cache_creation_tokens") or 0))
    return (cache_read / denom) if denom > 0 else None


def _cost_verdict(cost: float, pctile: float | None, bars: dict,
                  correctness_verdict: str | None) -> str:
    budget = bars.get("cost_budget_usd")
    if budget and cost > 2 * budget:
        return RUNAWAY
    if budget and cost > budget:
        return ELEVATED
    if pctile is not None and pctile >= bars["runaway_percentile"]:
        return RUNAWAY
    if pctile is not None and pctile >= bars["elevated_percentile"]:
        # accuracy gains carry disproportionate cost: high spend on a
        # correct session is proportionate, not wasteful (§12.5 caveat)
        return (PROPORTIONATE if correctness_verdict == "satisfied"
                else ELEVATED)
    return PROPORTIONATE


def assess_cost(evidence: EvidenceIndex, bars: dict,
                covered_items: int | None,
                correctness_verdict: str | None) -> dict:
    session = evidence.session
    cost = float(session.get("cost_usd") or 0.0)
    task_class = _task_class(session)
    pctile = cost_percentile(evidence.trace_id, cost, task_class) if cost else None
    share = _cache_read_share(session)
    verdict = _cost_verdict(cost, pctile, bars, correctness_verdict)
    return {
        "cost_usd": cost, "task_class": task_class, "percentile": pctile,
        "cache_read_share": share,
        "cache_bloat": bool(share is not None
                            and share >= bars["cache_read_share_flag"]),
        "cost_per_covered_item": (round(cost / covered_items, 4)
                                  if covered_items else None),
        "verdict": verdict,
    }


# ── verdict + report ─────────────────────────────────────────────

def _decide_process_verdict(tool_use: dict, redundancy: dict,
                            reliability: dict, cost: dict,
                            bars: dict) -> str:
    if cost["verdict"] == RUNAWAY:
        return WASTEFUL
    total = max(tool_use["total"], 1)
    waste_share = (tool_use["counts"][SUBOPTIMAL]
                   + tool_use["counts"][WASTED]) / total
    if waste_share > bars["wasteful_waste_share"]:
        return WASTEFUL
    capped = bool(reliability["ignored_feeding_claim"])
    episodes = (len(redundancy["redundant_reads"])
                + len(redundancy["thrash_episodes"]))
    smells = (capped or waste_share > bars["max_waste_share"]
              or episodes > bars["max_redundant_episodes"]
              or cost["verdict"] == ELEVATED or cost["cache_bloat"])
    return ACCEPTABLE if smells else EFFICIENT


def _cost_line(cost: dict) -> str:
    pct = (f"{round(cost['percentile'] * 100)}th pctile"
           if cost["percentile"] is not None else "no cost data")
    share = (f"; cache-read share {round(cost['cache_read_share'] * 100)}%"
             if cost["cache_read_share"] is not None else "")
    per_item = (f"; cost/covered-item ${cost['cost_per_covered_item']}"
                if cost["cost_per_covered_item"] is not None else "")
    return (f"Cost: {pct} for \"{cost['task_class']}\"{per_item}{share}")


def _process_bullets(tool_use: dict, redundancy: dict,
                     reliability: dict, cost: dict) -> list[str]:
    bullets = [f"- [{f['span_id']}] {f['verdict']}: {f['reason']}."
               for f in tool_use["findings"][:10]]
    for ep in redundancy["redundant_reads"][:5]:
        bullets.append(f"- [{','.join(ep['spans'])}] redundant read: "
                       f"{ep['path']} read twice with no change between.")
    for ep in redundancy["thrash_episodes"][:5]:
        bullets.append(f"- [{','.join(ep['spans'][:4])}] thrash: repeated "
                       f"failures of `{ep['command']}` with no edit between.")
    for span in reliability["ignored_feeding_claim"][:5]:
        bullets.append(f"- [{span}] ignored error feeds a claim — the "
                       "correctness axis checks the claim independently.")
    if cost["cache_bloat"]:
        bullets.append("- cost: cache-read share suggests the context "
                       "window was never compacted mid-session.")
    return bullets


def render_process_report(verdict: str, tool_use: dict, redundancy: dict,
                          reliability: dict, cost: dict) -> str:
    counts = tool_use["counts"]
    lines = [
        f"Tool-use: {counts[APPROPRIATE]} appropriate / "
        f"{counts[SUBOPTIMAL]} suboptimal / {counts[WASTED]} wasted "
        f"(of {tool_use['total']} spans)",
        f"Redundancy: {len(redundancy['redundant_reads'])} redundant reads, "
        f"{len(redundancy['thrash_episodes'])} thrash episodes",
        f"Reliability: {reliability['errored']} errored / "
        f"{reliability['recovered']} recovered / "
        f"{reliability['ignored']} ignored",
        _cost_line(cost),
        f"Verdict: {verdict}",
    ]
    return "\n".join(lines + _process_bullets(tool_use, redundancy,
                                              reliability, cost))


def process_bars() -> dict:
    """The rubric's numeric process bars (plus `thrash_k`), named for the
    gate. Shared by the mechanical and agentic process paths so both apply
    the same policy."""
    crit = rubric_mod.process_rubric()["criteria"]
    return {
        "max_waste_share": crit["tool_use_appropriateness"]["max_waste_share"],
        "wasteful_waste_share":
            crit["tool_use_appropriateness"]["wasteful_waste_share"],
        "max_redundant_episodes": crit["redundancy"]["max_redundant_episodes"],
        "elevated_percentile": crit["cost_proportionality"]["elevated_percentile"],
        "runaway_percentile": crit["cost_proportionality"]["runaway_percentile"],
        "cache_read_share_flag": crit["cost_proportionality"]["cache_read_share_flag"],
        "cost_budget_usd": crit["cost_proportionality"]["cost_budget_usd"],
        "thrash_k": crit["redundancy"]["thrash_consecutive_failures"],
    }


def correctness_context(correctness: AxisGrade | None
                        ) -> tuple[list[str], int | None, str | None]:
    """The cross-axis inputs the process grade reads off a correctness
    grade: load-bearing claim texts (for the ignored-error gate), the
    covered-item count and the verdict (both condition the cost criterion).
    The axes check each other but are never fused."""
    if correctness is None:
        return [], None, None
    claim_texts = [c.get("normalized_text", "") for c in
                   correctness.detail.get("claims", []) if c.get("load_bearing")]
    covered = correctness.scoreboard.get("coverage", {}).get("covered")
    return claim_texts, covered, correctness.verdict


def build_process_grade(tool_use: dict, redundancy: dict, reliability: dict,
                        cost: dict, bars: dict, *, tier: str = "screen",
                        judge: str = "mechanical") -> AxisGrade:
    """Apply the process gate to ready P1–P4 assessments and assemble the
    grade. The policy layer — verdict, report, scoreboard — shared by the
    mechanical screen pass and the agentic deep judge, so both produce the
    identical persisted shape regardless of how P1–P3 were assessed."""
    verdict = _decide_process_verdict(tool_use, redundancy, reliability,
                                      cost, bars)
    report = render_process_report(verdict, tool_use, redundancy,
                                   reliability, cost)
    scoreboard = {
        "tool_use": tool_use["counts"] | {"total": tool_use["total"]},
        "redundancy": {k: len(v) for k, v in redundancy.items()},
        "reliability": {k: reliability[k]
                        for k in ("errored", "recovered", "ignored")},
        "cost": {k: cost[k] for k in ("cost_usd", "task_class", "percentile",
                                      "cache_read_share", "verdict")},
    }
    detail = {"tool_use": tool_use, "redundancy": redundancy,
              "reliability": reliability, "cost": cost}
    return AxisGrade(axis="process", verdict=verdict, tier=tier,
                     scoreboard=scoreboard, report=report, detail=detail,
                     rubric_version=rubric_mod.RUBRIC_VERSION, judge=judge)


def grade_process(evidence: EvidenceIndex,
                  correctness: AxisGrade | None = None) -> AxisGrade:
    """Run the four process criteria mechanically over one session's
    evidence (the `screen` tier; the agentic deep judge is a separate path —
    `lib.grader.process_agentic`)."""
    bars = process_bars()
    claim_texts, covered, correctness_verdict = correctness_context(correctness)
    tool_use = assess_tool_use(evidence)
    redundancy = assess_redundancy(evidence, bars["thrash_k"])
    reliability = assess_reliability(evidence, claim_texts)
    cost = assess_cost(evidence, bars, covered, correctness_verdict)
    return build_process_grade(tool_use, redundancy, reliability, cost, bars)


__all__ = ["grade_process", "build_process_grade", "process_bars",
           "correctness_context", "assess_cost", "EFFICIENT", "ACCEPTABLE",
           "WASTEFUL", "cost_percentile"]
