"""Criterion G — groundedness: every claim is backed by a span that
actually supports it.

The bar varies by claim type (the grounding map in the rubric):

* `state`      → a Read whose recorded content shows it (the cited symbol,
                 or — lacking a symbol — content overlapping the claim),
                 or an Edit whose diff shows the asserted change.
* `result`     → a Bash span whose command matches and whose status (and
                 stdout) confirms; a positive claim with no run is
                 UNGROUNDED, a failed run is CONTRADICTED.
* `external`   → a WebFetch/WebSearch span proving the source was
                 consulted; the deep tier additionally asks the judge to
                 re-fetch and verify the source supports the claim.
* `diagnostic` → both a cause span and an effect/repro span.

The regin-specific STALE rule: evidence exists but a *later* span mutated
the cited target after the evidence was captured (ordered by timeline
position). For result claims the comparison excludes doc-ish mutations —
editing a README after the test run doesn't invalidate the run.

Anti-gaming: never accept the agent's paraphrase of a tool result — only
the recorded span output grounds a claim, and the deep-tier LLM rescue
must return a verbatim quote from a span's *output* (not the excerpt
header) or its answer is discarded. Matching deliberately considers
`raw_text` alongside `normalized_text` as a recall aid — the verdict
still rests on span output, never on the agent's restatement.
"""

from __future__ import annotations

import json
import re

from lib.activity_log import get_activity_logger
from lib.grader.evidence import EvidenceIndex, ToolEvent, content_tokens
from lib.grader.models import (
    CONTRADICTED, GROUNDED, STALE, UNGROUNDED, Claim, ClaimVerdict,
)

log = get_activity_logger("grader")

_NEGATIVE_RE = re.compile(
    r"\b(fail(s|ed|ing)?|error(s|ed)?|broken|crash(es|ed)?|red)\b",
    re.IGNORECASE)
_POSITIVE_RE = re.compile(
    r"\b(pass(es|ed)?|green|succeed(s|ed)?|works?|(is|are|now) fixed|"
    r"resolved|no longer)\b", re.IGNORECASE)
_NEGATION_GUARD_RE = re.compile(
    r"\b(no|zero|0|without|fixed the|resolved the)\s+\w{0,12}\s?"
    r"(fail|error|crash)", re.IGNORECASE)
# stdout that itself reports failures contradicts a positive claim even
# when the process exited 0 ("12 passed, 2 failed" under a lenient runner)
_STDOUT_FAIL_RE = re.compile(r"\b[1-9]\d*\s+(failed|errors)\b", re.IGNORECASE)
_CHANGE_RE = re.compile(
    r"\b(added|removed|changed|updated|renamed|introduced|deleted|moved|"
    r"extracted|replaced|implemented|created|wrote|fixed)\b", re.IGNORECASE)
_TEST_CMD_RE = re.compile(
    r"\b(pytest|vitest|jest|playwright|unittest|tox|go test|cargo test|"
    r"npm (run )?test|rspec)\b", re.IGNORECASE)
_TEST_CLAIM_RE = re.compile(r"\b(test|suite|spec)s?\b", re.IGNORECASE)
_BUILD_CMD_RE = re.compile(
    r"\b(build|compile|tsc|vite|webpack|make|gradle)\b", re.IGNORECASE)
_BUILD_CLAIM_RE = re.compile(r"\b(build|compil)", re.IGNORECASE)


def claim_is_negative(text: str) -> bool:
    """True when the claim asserts a failure ("the build fails"). Mixed
    polarity ("tests failed initially, now pass") reads as positive: the
    trailing outcome is what the claim asserts."""
    if _NEGATION_GUARD_RE.search(text):
        return False
    if _POSITIVE_RE.search(text):
        return False
    return bool(_NEGATIVE_RE.search(text))


def _claim_text(claim: Claim) -> str:
    return f"{claim.normalized_text} {claim.raw_text}"


# ── state claims ─────────────────────────────────────────────────

def _read_supports(event: ToolEvent, symbol: str | None,
                   claim_tokens: set[str]) -> bool:
    """A Read grounds a state claim only when its recorded content shows
    the cited symbol — or, lacking one, overlaps the claim's content.
    Mere existence of a read is not lines_present."""
    if symbol:
        return symbol.lower() in event.content.lower()
    return len(claim_tokens & event.match_tokens()) >= 2


def _content_line_ref(event: ToolEvent, symbol: str | None) -> str:
    """`file:line` for the supporting lines inside the recorded content."""
    path = event.file_path
    if not symbol:
        return path
    for lineno, line in enumerate(event.content.splitlines(), start=1):
        if symbol.lower() in line.lower():
            offset = int(event.attrs.get("start_line") or 1)
            return f"{path}:{offset + lineno - 1}"
    return path


def _change_supports(claim: Claim,
                     evidence: EvidenceIndex) -> ToolEvent | None:
    """The latest mutation to the cited file whose session diffs evidence
    the claim — the cited symbol must appear in the union of those diffs
    (or, lacking a symbol, the file was mutated at all). The *latest*
    mutation is returned: it reflects the file's post-edit state, so a
    claim describing a change grounds on the edit that produced it rather
    than on a read that predates the edit (and would only show the
    pre-change state)."""
    path = claim.referents.get("file") or ""
    muts = evidence.mutations_of(path) if path else []
    if not muts:
        return None
    symbol = (claim.referents.get("symbol") or "").lower()
    if symbol:
        union = " ".join(e.diff[:8000] for e in muts).lower()
        if symbol not in union:
            return None
    return muts[-1]


def _grep_evidence(symbol: str | None,
                   evidence: EvidenceIndex) -> ToolEvent | None:
    if not symbol:
        return None
    sym = symbol.lower()
    for search in evidence.searches:
        if sym in str(search.attrs.get("pattern", "")).lower():
            return search
    return None


def _supporting_reads(claim: Claim,
                      evidence: EvidenceIndex) -> list[ToolEvent]:
    path = claim.referents.get("file") or ""
    reads = evidence.reads_of(path) if path else [
        e for events in evidence.reads.values() for e in events]
    claim_tokens = content_tokens(_claim_text(claim))
    symbol = claim.referents.get("symbol")
    return [e for e in sorted(reads, key=lambda e: e.index)
            if _read_supports(e, symbol, claim_tokens)]


def _find_state_evidence(claim: Claim, evidence: EvidenceIndex
                         ) -> tuple[ToolEvent | None, str]:
    """Best (event, kind) backing a state claim; kind ∈ edit|read|grep.

    A state claim about a file the session mutated grounds on the edit
    that produced the state whenever that edit evidences the claim —
    preferred over a read when the read predates the edit (and so reads as
    STALE) or when there is no read at all (a file created by Write was
    never read). When a fresh read and a change span both stand, an
    explicit change-verb claim still cites the edit; otherwise the read,
    which already reflects the post-edit content, grounds it."""
    edit = _change_supports(claim, evidence)
    reads = _supporting_reads(claim, evidence)
    read = reads[-1] if reads else None
    read_stale = bool(read and evidence.mutations_after(read.file_path,
                                                        read.index))
    if edit is not None and (read is None or read_stale
                             or _CHANGE_RE.search(claim.raw_text)):
        return edit, "edit"
    if read is not None:
        return read, "read"
    if edit is not None:
        return edit, "edit"
    grep = _grep_evidence(claim.referents.get("symbol"), evidence)
    return (grep, "grep") if grep is not None else (None, "")


def _stale_check(event: ToolEvent, kind: str,
                 evidence: EvidenceIndex) -> ClaimVerdict | None:
    """Read- and edit-grounded evidence goes stale when a later span
    mutated the same path."""
    if kind not in ("read", "edit") or not event.file_path:
        return None
    later = evidence.mutations_after(event.file_path, event.index)
    if not later:
        return None
    return ClaimVerdict(
        "", STALE, event.span_id, event.file_path,
        f"{kind} predates a later edit of {event.file_path} "
        f"(span {later[0].span_id})", kind)


def _ground_state(claim: Claim, evidence: EvidenceIndex) -> ClaimVerdict:
    event, kind = _find_state_evidence(claim, evidence)
    if event is None:
        return ClaimVerdict(claim.id, UNGROUNDED, reason=(
            "no Read/Grep/Edit span shows the cited code"))
    stale = _stale_check(event, kind, evidence)
    if stale is not None:
        stale.claim_id = claim.id
        return stale
    if kind == "read":
        ref = _content_line_ref(event, claim.referents.get("symbol"))
    else:
        ref = event.file_path or str(event.attrs.get("pattern", ""))
    return ClaimVerdict(claim.id, GROUNDED, event.span_id, ref,
                        f"{kind} span shows the cited code/change", kind)


# ── result claims ────────────────────────────────────────────────

_HEX_HASH_RE = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")


def _stemmed(tokens: set[str]) -> set[str]:
    """Light suffix-stripping so 'committed' matches 'commit'."""
    out = set()
    for token in tokens:
        for suffix in ("ed", "es", "s", "ing"):
            if len(token) > 4 and token.endswith(suffix):
                token = token[:-len(suffix)]
                break
        out.add(token)
    return out


def _bash_by_command(command: str | None,
                     bash: list[ToolEvent]) -> list[ToolEvent]:
    if not command:
        return []
    return [e for e in bash if command in e.command]


def _bash_by_hash(text: str, bash: list[ToolEvent]) -> list[ToolEvent]:
    hashes = set(_HEX_HASH_RE.findall(text.lower()))
    if not hashes:
        return []
    return [e for e in bash
            if any(h in e.stdout.lower() for h in hashes)]


def _bash_by_intent(claim_text: str, bash: list[ToolEvent]):
    if _TEST_CLAIM_RE.search(claim_text):
        hits = [e for e in bash if _TEST_CMD_RE.search(e.command)]
        if hits:
            return hits
    if _BUILD_CLAIM_RE.search(claim_text):
        hits = [e for e in bash if _BUILD_CMD_RE.search(e.command)]
        if hits:
            return hits
    return []


def _bash_by_token(text: str, bash: list[ToolEvent]) -> list[ToolEvent]:
    tokens = _stemmed(content_tokens(text))
    return [e for e in bash
            if len(tokens & _stemmed(e.match_tokens())) >= 2]


def _matching_bash_events(claim: Claim,
                          evidence: EvidenceIndex) -> list[ToolEvent]:
    """Every run consistent with the claim, across all match strategies.

    The strategies are unioned rather than short-circuited so a claim
    binds to the *latest* consistent run (`_ground_result` takes the max
    index), not the first one a single strategy happens to find: a result
    that cites a session/commit hash echoed by an early exploration must
    not bind to that stale run when a later run actually verifies it."""
    text = _claim_text(claim)
    seen: set[str] = set()
    out: list[ToolEvent] = []
    for hits in (
            _bash_by_command(claim.referents.get("command"), evidence.bash),
            _bash_by_hash(text, evidence.bash),
            _bash_by_intent(text, evidence.bash),
            _bash_by_token(text, evidence.bash)):
        for event in hits:
            if event.span_id not in seen:
                seen.add(event.span_id)
                out.append(event)
    return out


def _ground_result(claim: Claim, evidence: EvidenceIndex) -> ClaimVerdict:
    events = _matching_bash_events(claim, evidence)
    if not events:
        return ClaimVerdict(claim.id, UNGROUNDED, reason=(
            "no Bash span runs anything matching this result claim"))
    latest = max(events, key=lambda e: e.index)
    negative = claim_is_negative(claim.raw_text)
    succeeded = not latest.is_error
    if succeeded and not negative and _STDOUT_FAIL_RE.search(latest.stdout):
        return ClaimVerdict(
            claim.id, CONTRADICTED, latest.span_id, latest.command,
            "the run's own output reports failures: "
            f"{_STDOUT_FAIL_RE.search(latest.stdout).group(0)!r}", "bash")
    if succeeded != (not negative):
        excerpt = (latest.stderr or latest.stdout)[:120]
        return ClaimVerdict(
            claim.id, CONTRADICTED, latest.span_id, latest.command,
            f"recorded run contradicts the claim: {excerpt!r}", "bash")
    if not negative and evidence.last_code_mutation_index() > latest.index:
        return ClaimVerdict(
            claim.id, STALE, latest.span_id, latest.command,
            "last matching run predates later code edits — re-run to "
            "claim this", "bash")
    return ClaimVerdict(claim.id, GROUNDED, latest.span_id, latest.command,
                        "command ran with matching outcome", "bash")


# ── external claims ──────────────────────────────────────────────

def _ground_external(claim: Claim, evidence: EvidenceIndex) -> ClaimVerdict:
    url = claim.referents.get("url") or ""
    tokens = content_tokens(_claim_text(claim))
    for fetch in evidence.fetches:
        fetched = str(fetch.attrs.get("url") or fetch.attrs.get("query") or "")
        if (url and url in fetched) or (
                not url and len(tokens & content_tokens(fetched)) >= 2):
            return ClaimVerdict(
                claim.id, GROUNDED, fetch.span_id, fetched,
                "source was consulted via fetch/search span", "webfetch")
    return ClaimVerdict(claim.id, UNGROUNDED, reason=(
        "no WebFetch/WebSearch span proves the external source was consulted"))


_EXTERNAL_SUPPORT_PROMPT = """You are a grounding judge with tools. An AI
agent made an external claim, citing a source the trace shows it consulted.
Re-fetch the source yourself and verify it actually SUPPORTS the claim —
require a quote that matches the claim's substance, not mere topical
overlap. Respond ONLY with JSON:
{"supports": true|false, "quote": "<the supporting sentence, or empty>"}

CLAIM: {claim}
SOURCE: {source}
"""


def _verify_external_support(claim: Claim, verdict: ClaimVerdict,
                             llm) -> ClaimVerdict:
    """Deep tier: consultation alone isn't support — ask the judge to
    re-fetch and check (the cookbook's QUOTE_MATCH → SUPPORTS_CLAIM)."""
    prompt = (_EXTERNAL_SUPPORT_PROMPT
              .replace("{claim}", claim.normalized_text)
              .replace("{source}", verdict.evidence_ref or ""))
    parsed = _parse_json_object(llm.complete(prompt, max_tokens=512) or "")
    if parsed is None:
        return verdict
    if parsed.get("supports") is True:
        verdict.reason = ("consulted and judge-verified against the source: "
                          f"{str(parsed.get('quote') or '')[:80]!r}")
        return verdict
    return ClaimVerdict(
        claim.id, UNGROUNDED, verdict.evidence_span_id, verdict.evidence_ref,
        "source was consulted but the judge could not verify it supports "
        "the claim", "webfetch")


# ── diagnostic claims ────────────────────────────────────────────

def _ground_diagnostic(claim: Claim, evidence: EvidenceIndex) -> ClaimVerdict:
    cause, kind = _find_state_evidence(claim, evidence)
    effects = _matching_bash_events(claim, evidence)
    if cause is not None and effects:
        effect = max(effects, key=lambda e: e.index)
        return ClaimVerdict(
            claim.id, GROUNDED, cause.span_id,
            f"{cause.file_path or kind} + {effect.command}",
            f"cause span {cause.span_id} and effect span {effect.span_id} "
            "both present", kind or "bash")
    missing = []
    if cause is None:
        missing.append("no span shows the cited cause")
    if not effects:
        missing.append("no repro/result span shows the effect")
    return ClaimVerdict(claim.id, UNGROUNDED, reason="; ".join(missing))


_GROUNDERS = {
    "state": _ground_state,
    "result": _ground_result,
    "external": _ground_external,
    "diagnostic": _ground_diagnostic,
}


# ── deep-tier LLM rescue ─────────────────────────────────────────

_RESCUE_PROMPT = """You are a strict grounding judge for a session grader.
A claim from an AI agent's final summary could not be mechanically matched
to trace evidence. Below are candidate evidence spans (recorded tool
outputs). Decide whether any span genuinely supports or contradicts the
claim. Rules:
- The quote must be copied VERBATIM from one excerpt below. Never accept
  the agent's own restatement of a tool result as evidence.
- When in doubt answer NONE.
Respond ONLY with JSON:
{"verdict": "SUPPORTS"|"CONTRADICTS"|"NONE", "span_id": "...", "quote": "..."}

CLAIM: {claim}

CANDIDATE SPANS:
{candidates}
"""

_MIN_RESCUE_QUOTE = 15


def _event_body(event: ToolEvent) -> str:
    return (event.content or event.stdout or event.stderr
            or event.diff)[:800]


def _event_excerpt(event: ToolEvent) -> str:
    return (f"[span {event.span_id} | {event.tool} {event.command[:80]}]\n"
            + _event_body(event))


def _rescue_candidates(claim: Claim, evidence: EvidenceIndex,
                       top_k: int = 3) -> list[ToolEvent]:
    tokens = content_tokens(_claim_text(claim))

    def score(event: ToolEvent) -> int:
        return len(tokens & event.match_tokens())

    ranked = sorted(evidence.events, key=score, reverse=True)
    return [e for e in ranked[:top_k] if score(e) > 0]


def _parse_json_object(answer: str) -> dict | None:
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(answer[start:end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_rescue(answer: str, candidates: list[ToolEvent]) -> dict | None:
    parsed = _parse_json_object(answer)
    if parsed is None:
        return None
    span_id = str(parsed.get("span_id") or "")
    quote = str(parsed.get("quote") or "")
    event = next((e for e in candidates if e.span_id == span_id), None)
    # Anti-paraphrase guard: a substantial quote, found in the span's
    # recorded OUTPUT (not the excerpt header we fabricated).
    if (event is None or len(quote) < _MIN_RESCUE_QUOTE
            or quote not in _event_body(event)):
        return None
    parsed["_event"] = event
    return parsed


def _llm_rescue(claim: Claim, evidence: EvidenceIndex, llm) -> ClaimVerdict | None:
    candidates = _rescue_candidates(claim, evidence)
    if not candidates:
        return None
    prompt = _RESCUE_PROMPT.replace("{claim}", claim.normalized_text).replace(
        "{candidates}", "\n\n".join(_event_excerpt(e) for e in candidates))
    answer = llm.complete(prompt, max_tokens=512)
    parsed = _parse_rescue(answer or "", candidates)
    if parsed is None:
        return None
    event = parsed["_event"]
    verdict = str(parsed.get("verdict") or "NONE")
    mapping = {"SUPPORTS": GROUNDED, "CONTRADICTS": CONTRADICTED}
    if verdict not in mapping:
        return None
    return ClaimVerdict(
        claim.id, mapping[verdict], event.span_id,
        event.file_path or event.command,
        f"judge-verified against span output: {parsed.get('quote', '')[:80]!r}",
        "judge")


def _ground_one(claim: Claim, evidence: EvidenceIndex, llm) -> ClaimVerdict | None:
    grounder = _GROUNDERS.get(claim.type)
    if grounder is None:
        return None
    verdict = grounder(claim, evidence)
    if llm is None:
        return verdict
    # STALE is a soft non-grounding — the read predates a later edit, but
    # the claim may still hold against the file's *current* state. Like
    # UNGROUNDED, let the agentic judge re-verify (the rescue candidates
    # include the later edit spans); only keep STALE if it cannot.
    if verdict.verdict in (UNGROUNDED, STALE):
        rescued = _llm_rescue(claim, evidence, llm)
        if rescued is not None:
            return rescued
    elif claim.type == "external" and verdict.verdict == GROUNDED:
        return _verify_external_support(claim, verdict, llm)
    return verdict


def ground_claims(claims: list[Claim], evidence: EvidenceIndex,
                  llm=None) -> dict[str, ClaimVerdict]:
    """Ground every non-aggregate claim. With `llm`, mechanically
    UNGROUNDED claims get one judge-assisted rescue attempt and grounded
    external claims get a support-verification pass."""
    verdicts: dict[str, ClaimVerdict] = {}
    for claim in claims:
        verdict = _ground_one(claim, evidence, llm)
        if verdict is not None:
            verdicts[claim.id] = verdict
    counts: dict[str, int] = {}
    for v in verdicts.values():
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    log.read("claims_grounded", trace_id=evidence.trace_id, **counts)
    return verdicts


__all__ = ["ground_claims", "claim_is_negative"]
