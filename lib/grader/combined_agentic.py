"""Combined deep-tier judge — one self-fetching investigation, many verdicts.

Replaces the per-axis judge subprocesses (one for correctness, one for
process, one per aspect) with a SINGLE judge run: it dumps the index once,
fetches the spans it needs, and emits one JSON with a section per requested
dimension. regin parses each section with the SAME builders/gates the
standalone judges use (`lib/grader/{agentic,process_agentic,correctness,
process}.py`), so the rubric rigor — the verbatim-quote guard, span-id
validation, gate thresholds — is preserved; only the prompt is unified.

One subprocess = one captured session = lower cost and no scattered
`<role>` judge sessions. Each requested dimension is parsed independently;
a dimension the judge omits or mangles is left out of the returned dict so
the caller can fall back to the mechanical tier for that one axis.

Aspects are reviewer-defined dimensions (key + description) graded
holistically: the judge returns a `satisfied`/`needs_revision`/`fail`
verdict with cited span evidence, validated against the recorded trace.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.grader.correctness import (
    FAIL, NEEDS_REVISION, SATISFIED, _aggregate_verdict, build_axis_grade,
    correctness_bars,
)
from lib.grader.evidence import EvidenceIndex
from lib.grader.judge_io import extract_json_object
from lib.grader.models import AxisGrade
from lib.grader.process import (
    assess_cost, build_process_grade, correctness_context, process_bars,
)

log = get_activity_logger("grader")

_ASPECT_VERDICTS = {SATISFIED, NEEDS_REVISION, FAIL}

# ── prompt fragments (faithful to the standalone judges; the shared
# parsers/gates remain the source of rigor) ──────────────────────────

_ROLE = """<role>
You are a strict, independent judge for AI coding-agent sessions. You grade
ONLY the dimensions requested below, each on its own, deciding every verdict
from what the session's own recorded tool calls show — never from the
agent's restatement or your own priors.
</role>

<session_id>{trace_id}</session_id>

<gather_evidence>
You have a shell. The session's recorded spans are the ONLY admissible
evidence — read them yourself, fetching only what you need:
1. Run `{python} cli/regin.py trace dump {trace_id} --index` → JSON with
   `prompts` (the user's words), `final_deliverable` (the artifact you
   grade), `commit_messages`, and a COMPACT `spans` catalog IN ORDER
   (span_id, tool, file_path, command, status, short preview).
2. For any span you need, run
   `{python} cli/regin.py trace span {trace_id} <span_id>` → its full
   recorded content. Fetch sparingly — only the spans a verdict needs.
</gather_evidence>"""

_CORRECTNESS_BLOCK = """<correctness>
Grade whether the load-bearing claims in `final_deliverable` are backed by
the spans. For each claim: GROUNDED (a span shows it) | CONTRADICTED (a span
disproves it) | STALE (a span showed it but a later span changed that target
and nothing re-established it) | UNGROUNDED (no span shows it). For a
NEGATIVE/ABSENCE claim ("nothing pushed", "did not touch X"), the trace
captures every action, so the ABSENCE of a span performing it CONFIRMS it:
mark GROUNDED with `"span_id": null`, `"quote": ""`, `"by_absence": true`.
For GROUNDED/CONTRADICTED set `quote` to a snippet copied EXACTLY (character
for character) from that span — never paraphrase; if you can't copy it
exactly, it isn't GROUNDED. Also derive a 3–8 item checklist from the USER
PROMPTS ALONE (include implied duties: root cause found, fix applied,
verification green) and mark each COVERED | PARTIAL | MISSING.
</correctness>"""

_PROCESS_BLOCK = """<process>
Grade HOW the agent worked (trajectory PROPERTIES, never a prescribed step
order; cost is computed mechanically — do NOT assess it). Cite the exact
span_id from the catalog for every finding. (1) TOOL-USE: flag a span WASTED
when its output fed no later span and no part of the deliverable, or
SUBOPTIMAL when a worse instrument was used (a cat/grep/find shell call where
a Read/Grep/Glob tool existed); list only flagged spans. (2) REDUNDANCY:
`redundant_reads` = the same unchanged target read again with no edit
between; `thrash_episodes` = a run of same-shape failing commands with no
edit between. (3) RELIABILITY: for each errored span decide recovered (a
later span fixed/worked around it) or ignored; an ignored error whose subject
feeds a load-bearing claim also goes in `ignored_feeding_claim`.
</process>"""


def _aspects_block(aspect_defs: list[tuple[str, str, str]]) -> str:
    lines = [
        "<aspects>",
        "Grade each reviewer-defined aspect below holistically. For each, "
        "return a `verdict` of \"satisfied\" (the aspect clearly holds), "
        "\"needs_revision\" (partial / some concerns), or \"fail\" (the "
        "aspect is materially violated), a one-line `summary`, and a "
        "`findings` list of {reason, span_id, quote} citing the spans that "
        "drive the verdict (span_id from the catalog; omit it only when the "
        "finding is about an absence).",
    ]
    for key, label, desc in aspect_defs:
        lines.append(f'- key "{key}" — {label}: {desc}'.rstrip())
    lines.append("</aspects>")
    return "\n".join(lines)


def _output_block(axes: tuple[str, ...],
                  aspect_defs: list[tuple[str, str, str]]) -> str:
    keys = []
    if "correctness" in axes:
        keys.append('"correctness": {"claims": [{"id","text","type",'
                    '"load_bearing","verdict","span_id","quote","reason",'
                    '"by_absence"}], "coverage": [{"item","verdict","reason"}]}')
    if "process" in axes:
        keys.append('"process": {"tool_use": [{"span_id","verdict","reason"}],'
                    ' "redundant_reads": [{"path","spans","reason"}],'
                    ' "thrash_episodes": [{"command","spans","reason"}],'
                    ' "reliability": {"recovered":[],"ignored":[],'
                    '"ignored_feeding_claim":[]}}')
    if aspect_defs:
        keys.append('"aspects": {"<key>": {"verdict","summary",'
                    '"findings":[{"reason","span_id","quote"}]}}')
    shape = ",\n ".join(keys)
    return ("<output_format>\nAfter gathering evidence, respond with ONLY one "
            "JSON object with exactly these top-level keys — no prose before "
            "or after:\n{" + shape + "}\n</output_format>")


def build_combined_prompt(trace_id: str, python: str, axes: tuple[str, ...],
                          aspect_defs: list[tuple[str, str, str]]) -> str:
    parts = [_ROLE.replace("{trace_id}", trace_id).replace("{python}", python)]
    if "correctness" in axes:
        parts.append(_CORRECTNESS_BLOCK)
    if "process" in axes:
        parts.append(_PROCESS_BLOCK)
    if aspect_defs:
        parts.append(_aspects_block(aspect_defs))
    parts.append(_output_block(axes, aspect_defs))
    return "\n\n".join(parts)


# ── per-dimension parsing (reuses the standalone builders) ───────────

def _correctness_grade(parsed: dict, evidence: EvidenceIndex,
                       label: str) -> AxisGrade | None:
    from lib.grader.agentic import _ledger_from
    section = parsed.get("correctness")
    if not isinstance(section, dict):
        return None
    claims, verdicts, coverage = _ledger_from(section, evidence)
    if not coverage and len(claims) <= 1:
        return None
    bars = correctness_bars()
    verdicts["c0"] = _aggregate_verdict(coverage, bars["aggregate_ratio"])
    return build_axis_grade(claims, verdicts, coverage, bars,
                            tier="deep", judge=label)


def _process_grade(parsed: dict, evidence: EvidenceIndex,
                   correctness: AxisGrade | None, label: str) -> AxisGrade | None:
    from lib.grader.process_agentic import (
        _LEDGER_KEYS, _redundancy_from, _reliability_from, _tool_use_from,
    )
    section = parsed.get("process")
    if not isinstance(section, dict) or not any(k in section for k in _LEDGER_KEYS):
        return None
    known = {e.span_id for e in evidence.events}
    tool_use = _tool_use_from(section.get("tool_use"), len(evidence.events), known)
    redundancy = _redundancy_from(section, evidence, known)
    reliability = _reliability_from(section.get("reliability"), evidence)
    bars = process_bars()
    _, covered, correctness_verdict = correctness_context(correctness)
    cost = assess_cost(evidence, bars, covered, correctness_verdict)
    return build_process_grade(tool_use, redundancy, reliability, cost, bars,
                               tier="deep", judge=label)


def _aspect_findings(raw, known: set[str]) -> list[dict]:
    out: list[dict] = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        sid = it.get("span_id") or None
        out.append({
            "reason": str(it.get("reason") or "")[:200],
            # drop a hallucinated span_id but keep the finding's reasoning
            "span_id": sid if sid in known else None,
            "quote": str(it.get("quote") or "")[:120],
        })
    return out


def _aspect_grade(key: str, label: str, desc: str, obj: dict,
                  known: set[str], judge: str) -> AxisGrade:
    raw = str(obj.get("verdict") or "").lower()
    verdict = raw if raw in _ASPECT_VERDICTS else NEEDS_REVISION
    summary = str(obj.get("summary") or "")[:300]
    findings = _aspect_findings(obj.get("findings"), known)
    lines = [f"{label}: {verdict}", summary] if summary else [f"{label}: {verdict}"]
    for f in findings:
        cite = f" [{f['span_id']}]" if f["span_id"] else ""
        lines.append(f"- {f['reason']}{cite}")
    return AxisGrade(
        axis=key, verdict=verdict, tier="deep",
        scoreboard={"aspect": {"findings": len(findings)}},
        report="\n".join(lines),
        detail={"aspect": {"key": key, "label": label, "description": desc},
                "summary": summary, "findings": findings},
        rubric_version="aspect-v1", judge=judge)


def _aspect_grades(parsed: dict, evidence: EvidenceIndex,
                   aspect_defs: list[tuple[str, str, str]],
                   judge: str) -> dict[str, AxisGrade]:
    section = parsed.get("aspects")
    if not isinstance(section, dict):
        return {}
    known = {e.span_id for e in evidence.events}
    out: dict[str, AxisGrade] = {}
    for key, label, desc in aspect_defs:
        obj = section.get(key)
        if isinstance(obj, dict):
            out[key] = _aspect_grade(key, label, desc, obj, known, judge)
    return out


def grade_combined(evidence: EvidenceIndex, judge, trace_id: str,
                   axes: tuple[str, ...],
                   aspect_defs: list[tuple[str, str, str]],
                   python: str = ".venv/bin/python") -> dict[str, AxisGrade]:
    """Run ONE combined judge over the requested axes + aspects. Returns a
    dict keyed by axis/aspect for every dimension the judge produced a usable
    verdict for; dimensions the judge omitted are absent so the caller can
    fall back to the mechanical tier. Returns {} on a total judge/parse
    failure."""
    if judge is None:
        return {}
    prompt = build_combined_prompt(trace_id, python, axes, aspect_defs)
    answer = judge.complete(prompt, max_tokens=6144)
    parsed = extract_json_object(answer or "")
    if parsed is None:
        log.error("combined_judge_unparseable", trace_id=trace_id)
        return {}
    label = str(getattr(judge, "judge_id", None) or "judge")
    grades: dict[str, AxisGrade] = {}
    if "correctness" in axes:
        corr = _correctness_grade(parsed, evidence, label)
        if corr is not None:
            grades["correctness"] = corr
    if "process" in axes:
        proc = _process_grade(parsed, evidence, grades.get("correctness"), label)
        if proc is not None:
            grades["process"] = proc
    grades.update(_aspect_grades(parsed, evidence, aspect_defs, label))
    log.write("combined_graded", trace_id=trace_id, judge=label,
              dimensions=sorted(grades))
    return grades


__all__ = ["grade_combined", "build_combined_prompt"]
