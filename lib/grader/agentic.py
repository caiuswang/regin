"""Agentic deep-tier correctness judge — self-fetching.

The screen tier and the older `grade_correctness(llm=…)` path pre-extract
claims and pre-ground them mechanically, then ask the judge to rubber-stamp.
This module instead hands the judge a *method* and lets it investigate the
session itself: granted the read-only trace commands (`resolve_judge` adds
them to `--allowedTools`), it runs `regin trace dump <id> --index` for a
compact catalog, fetches the full content of only the spans it needs with
`regin trace span <id> <span_id>`, extracts the load-bearing claims, grounds
each with an exact quote, and emits one structured verdict. Nothing is
embedded in the prompt — evidence size never inflates it, and the judge
reads selectively.

regin stays authoritative on what the agent can't be trusted with: the
**anti-paraphrase guard** (every GROUNDED/CONTRADICTED quote is re-checked
against the recorded span output — whitespace/punctuation-folded, then an
≥80% word-subset fallback — and downgraded to UNGROUNDED if it can't be
substantiated) and the **rubric gates** (applied by `build_axis_grade`). Any
judge/parse failure returns None so the caller falls back to the mechanical
tier.
"""

from __future__ import annotations

import re

from lib.activity_log import get_activity_logger
from lib.grader.correctness import (
    _aggregate_verdict, build_axis_grade, correctness_bars,
)
from lib.grader.dump import span_recorded_text
from lib.grader.evidence import EvidenceIndex
from lib.grader.extraction import synthetic_top_claim
from lib.grader.models import (
    CONTRADICTED, GROUNDED, STALE, UNGROUNDED,
    AxisGrade, Claim, ClaimVerdict, CoverageItem,
)

log = get_activity_logger("grader")

_VALID_CLAIM_VERDICTS = {GROUNDED, UNGROUNDED, STALE, CONTRADICTED}
_VALID_COVERAGE = {"COVERED", "PARTIAL", "MISSING"}
_GROUNDED_NEEDS_QUOTE = {GROUNDED, CONTRADICTED}
_MIN_QUOTE = 12

# Absence grounding: a claim that the agent did NOT do something is
# confirmed by the *absence* of a span performing that action — the trace
# captures every action, so there is no quote to copy. Guarded by a
# negation marker so the judge cannot ground a positive claim "by absence".
_NEGATION_RE = re.compile(
    r"\b(no|not|never|without|nothing|none|neither|nor|didn't|doesn't|"
    r"don't|wasn't|weren't|isn't|aren't|won't|hasn't|haven't|left out|"
    r"excluded?|omitt?ed|skipp?ed|untouched|unchanged)\b", re.IGNORECASE)


def _is_absence_grounded(item: dict, claim: Claim, verdict: str,
                         span_id: str | None) -> bool:
    """The judge grounded a negative claim on the absence of a span: honor
    it only for a GROUNDED, span-less verdict on a genuinely negative claim."""
    return (bool(item.get("by_absence")) and verdict == GROUNDED
            and span_id is None and bool(_NEGATION_RE.search(claim.raw_text)))

# Map the cited span's tool to the source_kind the source-quality pass
# expects, so an agentic verdict classifies like a mechanical one.
_TOOL_SOURCE_KIND = {
    "Read": "read", "Grep": "grep", "Glob": "grep", "Bash": "bash",
    "WebFetch": "webfetch", "WebSearch": "webfetch",
}

_PROMPT = """<role>
You are a strict, independent correctness judge for AI coding-agent
sessions. You decide whether the load-bearing claims in a session's final
deliverable are backed by what the session's own tool calls recorded —
never by the agent's restatement or your own priors.
</role>

<session_id>{trace_id}</session_id>

<gather_evidence>
You have a shell. The session's recorded spans are the ONLY admissible
evidence — read them yourself, fetching only what you need:
1. Run `{python} cli/regin.py trace dump {trace_id} --index` → JSON with
   `prompts` (the user's words), `final_deliverable` (the artifact you
   grade), `commit_messages`, and a COMPACT `spans` catalog (span_id,
   tool, file_path, command, status, short preview).
2. For any span you need to verify a claim, run
   `{python} cli/regin.py trace span {trace_id} <span_id>` → that span's
   full recorded content. Fetch sparingly — only the spans a claim needs.
</gather_evidence>

<how_to_judge>
1. Pull the load-bearing, checkable claims from `final_deliverable` (skip
   hedges/plans/narration; decompose compound sentences). Type each:
   state (code is/does X) | result (a run produced Y) | external (a
   library/API behaves Z) | diagnostic (X caused Y).
2. For each claim, read the span(s) that would show it and decide:
   GROUNDED (a span shows it) | CONTRADICTED (a span disproves it, e.g. a
   failing run) | STALE (a span showed it but a later span changed that
   target and nothing re-established it; a new test added after a passing
   run does NOT stale it) | UNGROUNDED (no span shows it).
   NEGATIVE/ABSENCE claims are special: when the agent asserts it did NOT
   do something ("nothing pushed", "left AGENTS.md out", "did not touch
   X"), the recorded spans capture EVERY action the agent took — so the
   ABSENCE of any span performing that action CONFIRMS the claim. Mark it
   GROUNDED with `"span_id": null`, `"quote": ""`, and `"by_absence": true`
   (no quote is possible or required). If a span DID perform the
   disclaimed action, that CONTRADICTS the claim instead.
   Ground a claim on its SUBSTANTIVE action, not on a peripheral attribute
   the trace never records. When a span shows the action (a commit command
   ran, an edit landed, a test executed) but an incidental detail is not
   independently recorded — which branch a quiet `git commit` landed on, a
   hash the command did not echo, a timestamp — that missing detail does
   NOT make the claim UNGROUNDED: quote the action span and mark it
   GROUNDED. Go UNGROUNDED only when the action ITSELF has no span (e.g.
   "committed three commits" with no commit span), and CONTRADICTED only
   when a span disproves the action.
3. For GROUNDED/CONTRADICTED, set `quote` to a snippet copied EXACTLY —
   character for character, punctuation and spacing included — from that
   span's recorded content. Do not retype from memory, normalize `-`/`−`,
   or paraphrase; if you can't copy an exact snippet, it isn't GROUNDED.
4. Derive a 3–8 item checklist from the USER PROMPTS ALONE (include
   implied duties: root cause found, fix applied, verification green) and
   mark each COVERED | PARTIAL | MISSING.
</how_to_judge>

<example>
If `trace span` returned {"span_id":"e7a1","tool":"Edit","diff":"@@\\n+def
clamp(x):\\n+    return max(0, x)"} and a Bash span showed "5 passed in
0.12s", and the deliverable said "Added clamp() to util.py; all tests pass;
also tuned the cache; nothing pushed", you would answer:
{"claims":[
 {"id":"c1","text":"Added clamp() to util.py","type":"state",
   "load_bearing":true,"verdict":"GROUNDED","span_id":"e7a1",
   "quote":"+def clamp(x):","reason":"edit diff adds the function"},
 {"id":"c2","text":"all tests pass","type":"result","load_bearing":true,
   "verdict":"GROUNDED","span_id":"<the bash span_id>",
   "quote":"5 passed in 0.12s","reason":"pytest run is green"},
 {"id":"c3","text":"tuned the cache","type":"state","load_bearing":false,
   "verdict":"UNGROUNDED","span_id":null,"quote":"",
   "reason":"no span shows any cache change"},
 {"id":"c4","text":"nothing pushed","type":"state","load_bearing":false,
   "verdict":"GROUNDED","span_id":null,"quote":"","by_absence":true,
   "reason":"no span runs git push; the trace captures every action"}],
 "coverage":[{"item":"add clamp() to util.py","verdict":"COVERED",
   "reason":"c1 grounded"},
  {"item":"tests pass after the change","verdict":"COVERED",
   "reason":"c2 grounded"}]}
Note c1's quote is copied verbatim from the span, not rephrased.
</example>

<output_format>
After gathering evidence, respond with ONLY one JSON object in exactly the
shape of the example above — no prose before or after.
</output_format>"""


def _build_prompt(trace_id: str, python: str,
                  enabled_aspects: list[str] | None = None) -> str:
    from lib.grader.prompts import judge_system_prompt
    return judge_system_prompt(
        "correctness", _PROMPT,
        substitutions={"{trace_id}": trace_id, "{python}": python},
        enabled_aspects=enabled_aspects,
    )


def _parse_object(answer: str) -> dict | None:
    from lib.grader.judge_io import extract_json_object
    return extract_json_object(answer)


# ── anti-paraphrase quote guard ──────────────────────────────────

# Unicode punctuation a model renders/normalizes away from the recorded
# bytes (minus sign U+2212 for ascii hyphen, smart quotes, dashes, arrows),
# folded on BOTH sides before the verbatim check.
_PUNCT_FOLD = {
    0x2212: "-", 0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-",
    0x2014: "-", 0x2015: "-",
    0x2018: "'", 0x2019: "'", 0x201c: '"', 0x201d: '"',
    0x2190: "<-", 0x2192: "->", 0x2194: "<->", 0x21d2: "=>", 0x00a0: " ",
}

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_QUOTE_TOKEN_SUBSET = 0.8
_MIN_QUOTE_TOKENS = 3


def _norm(text: str) -> str:
    """Fold whitespace and unicode punctuation lookalikes, then collapse
    whitespace — so a true quote isn't rejected over a newline, a diff
    line's leading `+    `, or a `−`/`↔` rendering. A word-level paraphrase
    still differs, since only punctuation is folded."""
    return " ".join(text.translate(_PUNCT_FOLD).split())


def _word_tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text) if len(t) >= 2}


def _quote_grounds(quote: str, recorded: str) -> bool:
    """The quote anchors the claim when it appears verbatim
    (whitespace/punctuation-folded) OR — since an LLM rarely copies a span
    exactly — when ≥80% of its words are present in the span. Word-subset
    tolerates reordering/a dropped word but rejects a fabricated quote."""
    if _norm(quote) in _norm(recorded):
        return True
    qtokens = _word_tokens(quote)
    if len(qtokens) < _MIN_QUOTE_TOKENS:
        return False
    return len(qtokens & _word_tokens(recorded)) / len(qtokens) >= _QUOTE_TOKEN_SUBSET


def _quote_verifies(verdict: str, span_id: str | None, quote: str,
                    evidence: EvidenceIndex) -> bool:
    if verdict not in _GROUNDED_NEEDS_QUOTE:
        return True
    if not span_id or len(quote) < _MIN_QUOTE:
        return False
    return _quote_grounds(quote, span_recorded_text(evidence, span_id))


def _source_kind_for(span_id: str | None, evidence: EvidenceIndex) -> str:
    if not span_id:
        return "judge"
    for event in evidence.events:
        if event.span_id == span_id:
            return _TOOL_SOURCE_KIND.get(event.tool, "judge")
    return "judge"


# ── ledger assembly ──────────────────────────────────────────────

def _claim_from(item: dict, idx: int) -> Claim:
    text = str(item.get("text") or "").strip()
    return Claim(
        id=f"c{idx}", raw_text=text, normalized_text=text,
        type=str(item.get("type") or "state"),
        referents={"file": None, "symbol": None, "command": None, "url": None},
        provenance={"surface": "judge"},
        load_bearing=bool(item.get("load_bearing", True)),
        extraction_confidence=0.9)


def _verdict_fields(item: dict) -> tuple[str, str | None, str, str]:
    raw = str(item.get("verdict") or "").upper()
    verdict = raw if raw in _VALID_CLAIM_VERDICTS else UNGROUNDED
    span_id = item.get("span_id") or None
    quote = str(item.get("quote") or "")
    reason = str(item.get("reason") or "")[:200]
    return verdict, span_id, quote, reason


def _verdict_from(item: dict, claim: Claim,
                  evidence: EvidenceIndex) -> ClaimVerdict:
    """The judge's per-claim verdict, downgraded to UNGROUNDED when a
    GROUNDED/CONTRADICTED quote can't be substantiated against the span."""
    verdict, span_id, quote, reason = _verdict_fields(item)
    if _is_absence_grounded(item, claim, verdict, span_id):
        return ClaimVerdict(
            claim.id, GROUNDED, None, "grounded by absence",
            reason or "no recorded span performs the disclaimed action; the "
            "trace captures every action", "judge")
    if not _quote_verifies(verdict, span_id, quote, evidence):
        return ClaimVerdict(
            claim.id, UNGROUNDED, span_id, quote[:80],
            "judge quote not substantiated in the cited span — downgraded",
            "judge")
    return ClaimVerdict(claim.id, verdict, span_id, quote[:80] or reason,
                        reason or "judge-assessed",
                        _source_kind_for(span_id, evidence))


def _coverage_from(items: list) -> list[CoverageItem]:
    out: list[CoverageItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict") or "MISSING").upper()
        if verdict not in _VALID_COVERAGE:
            verdict = "MISSING"
        out.append(CoverageItem(str(item.get("item") or "")[:200], verdict,
                                str(item.get("reason") or "")[:200]))
    return out


def _ledger_from(parsed: dict, evidence: EvidenceIndex
                 ) -> tuple[list[Claim], dict[str, ClaimVerdict],
                            list[CoverageItem]]:
    claims: list[Claim] = [synthetic_top_claim(evidence.task_text)]
    verdicts: dict[str, ClaimVerdict] = {}
    raw = parsed.get("claims") if isinstance(parsed.get("claims"), list) else []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        claim = _claim_from(item, i)
        claims.append(claim)
        verdicts[claim.id] = _verdict_from(item, claim, evidence)
    return claims, verdicts, _coverage_from(parsed.get("coverage") or [])


def grade_correctness_agentic(evidence: EvidenceIndex, judge, trace_id: str,
                              python: str = ".venv/bin/python",
                              enabled_aspects: list[str] | None = None
                              ) -> AxisGrade | None:
    """Drive the self-fetching agentic judge over one session. Returns None
    on any judge/parse failure so the caller can fall back to the mechanical
    tier. `enabled_aspects` is a per-run aspect-key whitelist for the judge
    prompt (None = the configured `settings.grader.aspects` defaults)."""
    if judge is None:
        return None
    answer = judge.complete(_build_prompt(trace_id, python, enabled_aspects),
                            max_tokens=4096)
    parsed = _parse_object(answer or "")
    if parsed is None:
        log.error("agentic_judge_unparseable", trace_id=trace_id)
        return None
    claims, verdicts, coverage = _ledger_from(parsed, evidence)
    if not coverage and len(claims) <= 1:
        log.error("agentic_judge_empty_ledger", trace_id=trace_id)
        return None
    bars = correctness_bars()
    verdicts["c0"] = _aggregate_verdict(coverage, bars["aggregate_ratio"])
    label = str(getattr(judge, "judge_id", None) or "judge")
    counts: dict[str, int] = {}
    for v in verdicts.values():
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    log.write("agentic_graded", trace_id=trace_id, judge=label, **counts)
    return build_axis_grade(claims, verdicts, coverage, bars,
                            tier="deep", judge=label)


__all__ = ["grade_correctness_agentic"]
