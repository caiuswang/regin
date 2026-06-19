"""Criterion C — coverage: every key fact / sub-task a correct answer must
include is addressed.

The unit is a required-items checklist derived from the *user's task
alone*, fixed before the session body is read — the agent cannot define
coverage down by simply not mentioning what it skipped. Each item must be
present *and* grounded (coverage piggybacks on groundedness): an item
matched only by ungrounded claims or bare trace activity is PARTIAL, not
COVERED.

Items that demand verification ("suite green") are checked mechanically
against the timeline: a successful matching run must postdate the last
file mutation.
"""

from __future__ import annotations

import json
import re

from lib.grader.evidence import EvidenceIndex, content_tokens
from lib.grader.grounding import _matching_bash_events  # shared matcher
from lib.grader.models import (
    COVERED, GROUNDED, MISSING, PARTIAL, Claim, ClaimVerdict, CoverageItem,
)

_SLASH_PREFIX_RE = re.compile(r"^/\w[\w-]*\s*")
_SPLIT_RE = re.compile(r"\n+|;|\.\s+|,?\s+(?:and then|then)\s+|\s+and\s+")
# A short clause that merely confirms / greenlights ("yes, apply the fix")
# is not a deliverable — the obligation it confirms is already an implied
# item. Drop it so it can't become an un-satisfiable required item.
_CONFIRM_RE = re.compile(r"^(yes|yeah|yep|ok|okay|sure)\b", re.IGNORECASE)
_FIX_RE = re.compile(r"\b(fix|bug|broken|fails?|error|crash)\b", re.IGNORECASE)
_TEST_RE = re.compile(r"\b(test|spec|regression)\b", re.IGNORECASE)
_BUILD_RE = re.compile(
    r"\b(implement|build|add|create|write|refactor|migrate)\b", re.IGNORECASE)

_VERIFY_ITEM = "verification run is green after the changes"
_ROOT_CAUSE_ITEM = "root cause identified with evidence"
_TEST_ITEM = "test added or updated and actually exercised"
_APPLY_ITEM = "changes applied to the codebase"


def _is_confirmation(clause: str) -> bool:
    """A short go-ahead ("yes, apply the fix") — not a required item."""
    return bool(_CONFIRM_RE.match(clause)) and len(clause.split()) <= 5


def _implied_items(task: str) -> list[str]:
    items: list[str] = []
    if _FIX_RE.search(task):
        items.append(_ROOT_CAUSE_ITEM)
    if _BUILD_RE.search(task) or _FIX_RE.search(task):
        items.append(_APPLY_ITEM)
        items.append(_VERIFY_ITEM)
    if _TEST_RE.search(task):
        items.append(_TEST_ITEM)
    return items


def derive_checklist_heuristic(task_text: str) -> list[str]:
    """Split the task into explicit sub-items, then add what a correct
    answer implies (root cause, applied change, green verification)."""
    task = _SLASH_PREFIX_RE.sub("", (task_text or "").strip())
    explicit = [part.strip() for part in _SPLIT_RE.split(task)
                if part and len(part.split()) >= 3
                and not _is_confirmation(part.strip())]
    items = explicit[:6] + _implied_items(task)
    # de-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out or [task or "complete the requested task"]


_CHECKLIST_PROMPT = """You write grading checklists for an automated session
grader. Given ONLY the user's task below (you have NOT seen what the agent
did), produce the required-items checklist a correct completion must satisfy.
Be more specific than the task; include implied obligations (e.g. "fix the
login 500 and add a regression test" implies the root cause is identified,
the fix is applied, the regression test exercises the bug, and the full
suite is green). 3-8 items. Respond ONLY with a JSON array of strings.

THE TASK:
"""


def derive_checklist(task_text: str, llm=None) -> list[str]:
    """The checklist is fixed from the task alone, before grading."""
    if llm is not None:
        answer = llm.complete(_CHECKLIST_PROMPT + (task_text or ""),
                              max_tokens=1024)
        items = _parse_string_array(answer or "")
        if items:
            return items[:8]
    return derive_checklist_heuristic(task_text)


def _parse_string_array(answer: str) -> list[str]:
    start, end = answer.find("["), answer.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(answer[start:end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x).strip() for x in parsed if str(x).strip()]


# ── assessment ───────────────────────────────────────────────────

def _verify_item_verdict(evidence: EvidenceIndex) -> CoverageItem:
    """Mechanical check: a successful test/build run after the last edit."""
    fake = Claim(id="_verify", raw_text="tests pass build green",
                 normalized_text="the test suite passes", type="result")
    runs = _matching_bash_events(fake, evidence)
    good = [e for e in runs if not e.is_error]
    # Same staleness notion as result-claim grounding: a doc edit or a
    # newly-added test after the run doesn't make the verification stale.
    if good and max(e.index for e in good) > evidence.last_code_mutation_index():
        return CoverageItem(_VERIFY_ITEM, COVERED,
                            "successful run postdates the last edit")
    if good:
        return CoverageItem(_VERIFY_ITEM, PARTIAL,
                            "last successful run predates later edits")
    return CoverageItem(_VERIFY_ITEM, MISSING,
                        "no successful verification run recorded")


def _evidence_addresses(item_tokens: set[str],
                        evidence: EvidenceIndex) -> bool:
    for events in evidence.mutations.values():
        for event in events:
            hay = event.file_path + " " + str(event.attrs.get("diff") or "")[:4000]
            if len(item_tokens & content_tokens(hay)) >= 2:
                return True
    return any(len(item_tokens & content_tokens(e.command)) >= 2
               for e in evidence.bash)


_ITEM_FILE_RE = re.compile(r"[\w./~-]+\.[A-Za-z][A-Za-z0-9]{0,5}\b")


def _named_artifact_edited(item: str, evidence: EvidenceIndex) -> str | None:
    """When the item names a concrete file (`also fix Agent.schema.json`)
    that the session mutated, the edit is direct grounded evidence the
    deliverable was produced — even if the final summary never restates
    it. Returns the matched basename, or None."""
    named = {m.group(0).rsplit("/", 1)[-1].lower()
             for m in _ITEM_FILE_RE.finditer(item)}
    if not named:
        return None
    for path in evidence.mutations:
        base = path.rsplit("/", 1)[-1].lower()
        if base in named:
            return base
    return None


def _overlapping_claims(item_tokens: set[str],
                        claims: list[Claim]) -> list[Claim]:
    return [c for c in claims if c.type != "aggregate"
            and len(item_tokens & content_tokens(
                c.normalized_text + " " + c.raw_text)) >= 2]


def _item_verdict(item: str, claims: list[Claim],
                  verdicts: dict[str, ClaimVerdict],
                  evidence: EvidenceIndex) -> CoverageItem:
    tokens = content_tokens(item)
    overlapping = _overlapping_claims(tokens, claims)
    grounded = [c for c in overlapping
                if verdicts.get(c.id) and verdicts[c.id].verdict == GROUNDED]
    if grounded:
        return CoverageItem(item, COVERED,
                            f"grounded claim [{grounded[0].id}] addresses it")
    edited = _named_artifact_edited(item, evidence)
    if edited is not None:
        return CoverageItem(item, COVERED,
                            f"trace shows the named artifact {edited} was edited")
    if overlapping:
        return CoverageItem(
            item, PARTIAL,
            f"claimed ([{overlapping[0].id}]) but the claim is not grounded")
    if _evidence_addresses(tokens, evidence):
        return CoverageItem(item, PARTIAL,
                            "trace activity touches it but nothing claims it")
    return CoverageItem(item, MISSING, "not addressed")


def _investigated_before_fix(evidence: EvidenceIndex) -> bool:
    """True when code was read or searched before the first mutation — the
    trace shape of locating a cause before changing it."""
    if not evidence.mutations:
        return False
    first_edit = min(e.index for evs in evidence.mutations.values()
                     for e in evs)
    reads = [e for evs in evidence.reads.values() for e in evs]
    return (any(e.index < first_edit for e in reads)
            or any(s.index < first_edit for s in evidence.searches))


def _first_grounded(claims: list[Claim], verdicts: dict[str, ClaimVerdict],
                    ctype: str) -> Claim | None:
    for claim in claims:
        verdict = verdicts.get(claim.id)
        if (claim.type == ctype and verdict is not None
                and verdict.verdict == GROUNDED):
            return claim
    return None


def _root_cause_item_verdict(claims: list[Claim],
                             verdicts: dict[str, ClaimVerdict],
                             evidence: EvidenceIndex) -> CoverageItem:
    """A fix's root cause counts as "identified with evidence" when a
    grounded diagnostic claim states it — or, lacking an explicit
    diagnostic, when the agent made a grounded claim about the changed
    code AND the trace shows that code was read/searched before the fix
    landed. Token-overlap against the claims is the last resort: an
    implied item phrased as prose ("root cause identified") rarely shares
    two literal tokens with a real diagnostic, so it must not gate on
    that alone."""
    diagnostic = _first_grounded(claims, verdicts, "diagnostic")
    if diagnostic is not None:
        return CoverageItem(_ROOT_CAUSE_ITEM, COVERED,
                            f"grounded diagnostic claim [{diagnostic.id}]")
    change = _first_grounded(claims, verdicts, "state")
    if change is not None and _investigated_before_fix(evidence):
        return CoverageItem(
            _ROOT_CAUSE_ITEM, COVERED,
            f"code investigated before the grounded fix [{change.id}]")
    return _item_verdict(_ROOT_CAUSE_ITEM, claims, verdicts, evidence)


def _apply_item_verdict(evidence: EvidenceIndex) -> CoverageItem:
    """Mechanical check: file mutations were recorded this session."""
    if evidence.mutations:
        paths = sorted(evidence.mutations)[:3]
        return CoverageItem(_APPLY_ITEM, COVERED,
                            f"edits recorded on {', '.join(paths)}")
    return CoverageItem(_APPLY_ITEM, MISSING,
                        "no file-mutating span recorded")


def assess_coverage(checklist: list[str], claims: list[Claim],
                    verdicts: dict[str, ClaimVerdict],
                    evidence: EvidenceIndex) -> list[CoverageItem]:
    mechanical = {_VERIFY_ITEM: _verify_item_verdict,
                  _APPLY_ITEM: _apply_item_verdict}
    out: list[CoverageItem] = []
    for item in checklist:
        checker = mechanical.get(item)
        if item == _ROOT_CAUSE_ITEM:
            out.append(_root_cause_item_verdict(claims, verdicts, evidence))
        elif checker is not None:
            out.append(checker(evidence))
        else:
            out.append(_item_verdict(item, claims, verdicts, evidence))
    return out


__all__ = ["derive_checklist", "derive_checklist_heuristic",
           "assess_coverage"]
