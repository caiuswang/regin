"""Agentic deep-tier process judge — self-fetching.

The mechanical screen tier assesses the trajectory lexically: a Read is
"wasted" when its filename tokens overlap nothing downstream, an error is
"recovered" when a later span shares its command prefix. Cheap, but blind
to meaning — exploration that informed a decision without sharing tokens
reads as waste; an error fixed by an unrelated-looking edit reads as
ignored.

This module hands the judge the *method* instead: granted the read-only
trace commands, it runs `regin trace dump <id> --index`, fetches the spans
it needs, and decides P1 tool-use / P2 redundancy / P3 reliability on the
substance of what each span did. P4 cost stays mechanical — percentile and
cache-read share are data, not judgment — so this module computes it with
`assess_cost` and only the trajectory properties come from the judge.

regin stays authoritative on the gate (`build_process_grade` applies the
same rubric thresholds as the screen tier) and on anti-hallucination:
every cited span_id is validated against the recorded trace and dropped if
it doesn't exist, and reliability findings are intersected with the spans
that actually errored. Any judge/parse failure returns None so the caller
falls back to the mechanical tier.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.grader.evidence import EvidenceIndex
from lib.grader.models import APPROPRIATE, SUBOPTIMAL, WASTED, AxisGrade
from lib.grader.process import (
    _re_derivations, assess_cost, build_process_grade, correctness_context,
    process_bars,
)

log = get_activity_logger("grader")

_TOOL_USE_VERDICTS = {SUBOPTIMAL, WASTED}
# A response missing all of these isn't a process verdict — fall back.
_LEDGER_KEYS = ("tool_use", "reliability", "redundant_reads", "thrash_episodes")

_PROMPT = """<role>
You are a strict, independent PROCESS judge for AI coding-agent sessions.
You assess HOW the agent worked — was each tool call the right instrument
and was its output used; did it repeat work or thrash; did it recover from
the errors it hit — judging from what the session's own tool calls
recorded. You grade trajectory PROPERTIES, never a prescribed step order.
</role>

<session_id>{trace_id}</session_id>

<gather_evidence>
You have a shell. The recorded spans are the ONLY admissible evidence:
1. Run `{python} cli/regin.py trace dump {trace_id} --index` → JSON with
   `prompts`, `final_deliverable`, and a COMPACT `spans` catalog IN ORDER
   (span_id, tool, file_path, command, status, short preview).
2. For any span you need, run
   `{python} cli/regin.py trace span {trace_id} <span_id>` → its full
   recorded content. Fetch sparingly.
</gather_evidence>

<how_to_judge>
Cite the exact span_id from the catalog for every finding — never invent
one. Assess three properties (cost/efficiency is computed mechanically — do
NOT assess it):
1. TOOL-USE: flag a span WASTED when its output fed no later span AND no
   part of the final deliverable — pure dead-end exploration; or SUBOPTIMAL
   when a worse instrument was used (a `cat`/`grep`/`find` shell call where
   a Read/Grep/Glob tool existed). A read that INFORMED a later edit or the
   answer is appropriate — do not flag it. List only the flagged spans.
2. REDUNDANCY: `redundant_reads` = the same unchanged target read again
   with no edit between the reads; `thrash_episodes` = a run of same-shape
   failing commands with no edit between (a changed approach or a success
   ends a run).
3. RELIABILITY: for each span whose status is an error, decide recovered (a
   later span fixed it or worked around it) or ignored (nothing addressed
   it). An ignored error whose subject feeds a load-bearing claim in
   `final_deliverable` also goes in `ignored_feeding_claim`.
</how_to_judge>

<example>
If the catalog showed s1 Read util.py (then edited), s2 Read util.py again
(no change between), s3 Bash "pytest" status=error, s4 Edit util.py, you
would answer:
{"tool_use":[],
 "redundant_reads":[{"path":"util.py","spans":["s1","s2"],
   "reason":"re-read with no edit between"}],
 "thrash_episodes":[],
 "reliability":{"recovered":["s3"],"ignored":[],"ignored_feeding_claim":[]}}
s3 is recovered because s4 edits the file the failing run was about.
</example>

<output_format>
After gathering evidence, respond with ONLY one JSON object in exactly the
shape of the example above — no prose before or after.
</output_format>"""


def _build_prompt(trace_id: str, python: str,
                  enabled_aspects: list[str] | None = None) -> str:
    from lib.grader.prompts import judge_system_prompt
    return judge_system_prompt(
        "process", _PROMPT,
        substitutions={"{trace_id}": trace_id, "{python}": python},
        enabled_aspects=enabled_aspects,
    )


def _parse_object(answer: str) -> dict | None:
    from lib.grader.judge_io import extract_json_object
    return extract_json_object(answer)


def _valid_spans(spans, known: set[str]) -> list[str]:
    return [s for s in (str(x) for x in (spans or [])) if s in known]


# ── P1–P3 assembly from the judge's findings ─────────────────────

def _tool_use_from(items, total: int, known: set[str]) -> dict:
    counts = {APPROPRIATE: 0, SUBOPTIMAL: 0, WASTED: 0}
    findings = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("span_id") or "")
        verdict = str(it.get("verdict") or "").upper()
        if sid not in known or verdict not in _TOOL_USE_VERDICTS:
            continue
        counts[verdict] += 1
        findings.append({"span_id": sid, "verdict": verdict,
                         "reason": str(it.get("reason") or "")[:200]})
    counts[APPROPRIATE] = max(total - counts[SUBOPTIMAL] - counts[WASTED], 0)
    return {"counts": counts, "findings": findings, "total": total}


def _episodes_from(items, known: set[str], label_key: str) -> list[dict]:
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        spans = _valid_spans(it.get("spans"), known)
        if len(spans) < 2:
            continue          # an episode needs ≥2 real spans to be one
        out.append({label_key: str(it.get(label_key) or "")[:80],
                    "spans": spans})
    return out


def _reliability_from(obj, evidence: EvidenceIndex) -> dict:
    errored = {e.span_id for e in evidence.events if e.is_error}
    obj = obj if isinstance(obj, dict) else {}
    recovered = [s for s in _valid_spans(obj.get("recovered"), errored)]
    ignored = [s for s in _valid_spans(obj.get("ignored"), errored)]
    feeding = [s for s in _valid_spans(obj.get("ignored_feeding_claim"),
                                       set(ignored))]
    return {"errored": len(errored), "recovered": len(recovered),
            "ignored": len(ignored), "ignored_spans": ignored,
            "ignored_feeding_claim": feeding}


def _redundancy_from(parsed: dict, evidence: EvidenceIndex,
                     known: set[str]) -> dict:
    return {
        "redundant_reads": _episodes_from(parsed.get("redundant_reads"),
                                          known, "path"),
        "thrash_episodes": _episodes_from(parsed.get("thrash_episodes"),
                                          known, "command"),
        # re-derivations are a deterministic fact (identical search pattern
        # run twice) — keep them mechanical rather than ask the judge.
        "re_derivations": _re_derivations(evidence),
    }


def grade_process_agentic(evidence: EvidenceIndex, judge, trace_id: str,
                          correctness: AxisGrade | None = None,
                          python: str = ".venv/bin/python",
                          enabled_aspects: list[str] | None = None
                          ) -> AxisGrade | None:
    """Drive the self-fetching agentic process judge over one session.
    Returns None on any judge/parse failure so the caller can fall back to
    the mechanical tier. `enabled_aspects` is a per-run aspect-key whitelist
    for the judge prompt (None = the configured defaults)."""
    if judge is None:
        return None
    answer = judge.complete(_build_prompt(trace_id, python, enabled_aspects),
                            max_tokens=4096)
    parsed = _parse_object(answer or "")
    if parsed is None or not any(k in parsed for k in _LEDGER_KEYS):
        log.error("agentic_process_unparseable", trace_id=trace_id)
        return None
    known = {e.span_id for e in evidence.events}
    tool_use = _tool_use_from(parsed.get("tool_use"), len(evidence.events), known)
    redundancy = _redundancy_from(parsed, evidence, known)
    reliability = _reliability_from(parsed.get("reliability"), evidence)
    bars = process_bars()
    _, covered, correctness_verdict = correctness_context(correctness)
    cost = assess_cost(evidence, bars, covered, correctness_verdict)
    label = str(getattr(judge, "judge_id", None) or "judge")
    log.write("agentic_process_graded", trace_id=trace_id, judge=label,
              wasted=tool_use["counts"][WASTED],
              suboptimal=tool_use["counts"][SUBOPTIMAL],
              ignored=reliability["ignored"])
    return build_process_grade(tool_use, redundancy, reliability, cost, bars,
                               tier="deep", judge=label)


__all__ = ["grade_process_agentic"]
