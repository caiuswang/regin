"""Claim extraction — the bridge from a session's artifact to the ledger.

Turns the final deliverable text into typed, checkable claims. The two
error modes are asymmetric: a falsely-extracted non-claim is cheap (the
grounder finds trivial support or marks it out of scope), while a missed
claim silently passes — the exact failure the grader exists to prevent.
So extraction is tuned for recall and the grounding step filters
precision.

Two paths, composed:

* Heuristic (always available, the `screen` tier): sentence segmentation,
  checkability filter, keyword typing, referent extraction. Deterministic
  and LLM-free.
* LLM (the `deep` tier): a structured-output extraction pass plus a
  completeness-critic second pass ("list every verifiable assertion NOT
  already in the ledger"). Both validated by the verbatim-provenance
  guard: a claim whose `raw_text` is not a substring of the artifact is
  dropped — the extractor cannot invent a claim the agent never made.

Every ledger gets the synthetic load-bearing claim `c0` ("the session
accomplished <task>"), grounded later by the coverage checklist, so terse
code-only sessions still give the gate something to bite.
"""

from __future__ import annotations

import json
import re

from lib.activity_log import get_activity_logger
from lib.grader.evidence import EvidenceIndex
from lib.grader.models import Claim

log = get_activity_logger("grader")

# ── Checkability filter ──────────────────────────────────────────
# Hedges / plans / questions are rationale, not claims (§11.10). The
# filter is biased toward keeping (a missed claim silently passes): the
# list stays close to the survey's own examples, and the segmenter splits
# on `;`/clauses first so one hedged clause can't drop its assertive
# siblings.
_HEDGE_RE = re.compile(
    r"\b(might|probably|perhaps|possibly|"
    r"let me|i'll|i will|want me to|"
    r"next step|todo|plan(s|ned|ning)? to|going to)\b",
    re.IGNORECASE,
)

# ── Typing routing table (§13.3), checked strictest-first ────────
_DIAGNOSTIC_RE = re.compile(
    r"\b(because|caused by|root cause|due to|the (issue|problem|bug) (was|is)|"
    r"culprit|stems from)\b", re.IGNORECASE)
_RESULT_RE = re.compile(
    r"\b(pass(es|ed)?|fail(s|ed)?|green|succeed(s|ed)?|error(s|ed)?|"
    r"builds?|compil(es|ed)|works?( now)?|(is|are) fixed|now returns?|"
    r"exit (code )?0|all \d+ tests?|test suite|verified|reproduce[sd]?|"
    r"commit(ted|s)?|merged|pushed|staged|deployed)\b",
    re.IGNORECASE)
_EXTERNAL_RE = re.compile(
    r"\b(the (sdk|library|api|package|framework|docs?)|defaults? to|"
    r"upstream|documentation says|according to|v?\d+\.\d+(\.\d+)?)\b",
    re.IGNORECASE)

_FILE_RE = re.compile(
    r"[\w./~-]+\.(?:py|js|ts|vue|md|json|sql|yaml|yml|toml|sh|css|html|grit|txt)\b")
_HASH_RE = re.compile(r"`[0-9a-f]{7,40}`")
_BACKTICK_RE = re.compile(r"`([^`\n]{2,80})`")
_URL_RE = re.compile(r"https?://\S+")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+|;\s+")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_TABLE_CHROME_RE = re.compile(r"^\s*\||\|\s*$|^[\s|:-]+$")


def _segment(text: str) -> list[tuple[str, int]]:
    """Split the artifact into candidate clauses with their offsets."""
    out: list[tuple[str, int]] = []
    stripped = _CODE_FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)
    pos = 0
    for raw in _SENTENCE_SPLIT_RE.split(stripped):
        if raw is None:
            continue
        offset = stripped.find(raw, pos)
        pos = offset + len(raw) if offset >= 0 else pos
        clause = _BULLET_PREFIX_RE.sub("", raw).strip()
        clause = _TABLE_CHROME_RE.sub("", clause).strip()
        if clause and not _TABLE_CHROME_RE.fullmatch(clause):
            out.append((clause, max(offset, 0)))
    return out


def _is_checkable(clause: str) -> bool:
    if len(clause.split()) < 3 or clause.endswith("?"):
        return False
    if _HEDGE_RE.search(clause):
        return False
    return True


def _classify(clause: str) -> str | None:
    """Type a clause, strictest type first; None → not a claim."""
    if _DIAGNOSTIC_RE.search(clause):
        return "diagnostic"
    if _RESULT_RE.search(clause) or _HASH_RE.search(clause):
        # a cited commit hash asserts that a commit landed — a result
        return "result"
    if _EXTERNAL_RE.search(clause) or _URL_RE.search(clause):
        return "external"
    if _FILE_RE.search(clause) or _BACKTICK_RE.search(clause):
        return "state"
    return None


def _referents(clause: str) -> dict:
    files = _FILE_RE.findall(clause)
    file_match = _FILE_RE.search(clause)
    symbols = [s for s in _BACKTICK_RE.findall(clause)
               if not _FILE_RE.fullmatch(s)]
    url = _URL_RE.search(clause)
    return {
        "file": file_match.group(0) if file_match else None,
        "symbol": symbols[0] if symbols else None,
        "command": None,
        "url": url.group(0).rstrip(".,)") if url else None,
        "_extra_files": files[1:] if len(files) > 1 else [],
    }


def _dedup_key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def synthetic_top_claim(task_text: str) -> Claim:
    """`c0` — always present, grounded by the coverage checklist."""
    task = (task_text or "the user's task").strip()[:300]
    return Claim(
        id="c0",
        raw_text=task,
        normalized_text=f"the session accomplished: {task}",
        type="aggregate",
        provenance={"surface": "task"},
        load_bearing=True,
    )


def extract_claims_heuristic(evidence: EvidenceIndex) -> list[Claim]:
    """Deterministic, LLM-free extraction from the artifact (final
    deliverable text + agent-authored commit messages)."""
    claims: list[Claim] = [synthetic_top_claim(evidence.task_text)]
    seen: set[str] = set()
    for clause, offset in _segment(evidence.artifact_text()):
        if not _is_checkable(clause):
            continue
        ctype = _classify(clause)
        if ctype is None:
            continue
        key = _dedup_key(clause)
        if key in seen:
            continue
        seen.add(key)
        claims.append(Claim(
            id=f"c{len(claims)}",
            raw_text=clause,
            normalized_text=clause,
            type=ctype,
            referents=_referents(clause),
            provenance={"surface": "final_summary", "offset": offset},
            load_bearing=True,
            parent_sentence=clause,
            extraction_confidence=0.7,
        ))
    return claims


# ── LLM path (deep tier) ─────────────────────────────────────────

_EXTRACT_PROMPT = """You are a claim extractor for an automated session grader.
Below is the final summary an AI coding agent wrote after a work session.
Extract every CHECKABLE assertion into a JSON array. Rules:
- Over-extract rather than omit: a missed claim silently passes grading.
- Skip hedges, plans, questions, and narration ("let me", "I'll", "might").
- Decompose compound sentences into atomic claims, one checkable fact each.
- raw_text MUST be a verbatim substring of the summary (copy exactly).
- normalized_text restates the claim self-contained (resolve "it"/"the file"
  to concrete names).
- type is one of: state (code/repo behavior), result (a command/test/build
  outcome), external (library/API/doc behavior), diagnostic (X caused Y).
  When ambiguous pick the type demanding MORE evidence
  (diagnostic > result > external > state).
- referents: {"file": str|null, "symbol": str|null, "command": str|null,
  "url": str|null}
- load_bearing: true when the deliverable's correctness depends on it.
Respond with ONLY the JSON array, e.g.:
[{"raw_text": "...", "normalized_text": "...", "type": "result",
  "referents": {"file": null, "symbol": null, "command": "pytest",
  "url": null}, "load_bearing": true}]

THE SUMMARY:
"""

_CRITIC_PROMPT = """You are a completeness critic for a claim ledger.
Given the agent's final summary and the claims already extracted, list every
verifiable assertion in the summary NOT already covered by the ledger.
Use the same JSON schema and the same rules (raw_text must be a verbatim
substring; over-extract rather than omit). Respond with ONLY a JSON array;
respond with [] if nothing is missing.

THE SUMMARY:
{artifact}

THE LEDGER SO FAR (normalized_text values):
{ledger}
"""


def _extract_json_array(answer: str) -> list:
    """Tolerant JSON-array extraction (handles markdown fences)."""
    if not answer:
        return []
    start, end = answer.find("["), answer.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(answer[start:end + 1])
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


_VALID_TYPES = {"state", "result", "external", "diagnostic"}


def _validated_raw_and_type(item: dict, artifact: str) -> tuple[str, str] | None:
    """Provenance-guard + type validation for one LLM-proposed claim."""
    if not isinstance(item, dict):
        return None
    raw = str(item.get("raw_text") or "").strip()
    # Verbatim-provenance guard: no quote from the artifact, no claim.
    if not raw or raw not in artifact:
        return None
    ctype = str(item.get("type") or "")
    if ctype not in _VALID_TYPES:
        ctype = _classify(raw) or "result"
    return raw, ctype


def _claim_from_llm(item: dict, artifact: str, idx: int) -> Claim | None:
    validated = _validated_raw_and_type(item, artifact)
    if validated is None:
        return None
    raw, ctype = validated
    referents = item.get("referents") or {}
    return Claim(
        id=f"c{idx}",
        raw_text=raw,
        normalized_text=str(item.get("normalized_text") or raw),
        type=ctype,
        referents={k: referents.get(k) for k in
                   ("file", "symbol", "command", "url")},
        provenance={"surface": "final_summary",
                    "offset": artifact.find(raw)},
        load_bearing=bool(item.get("load_bearing", True)),
        parent_sentence=raw,
        extraction_confidence=0.9,
    )


def _merge_claims(base: list[Claim], extra: list[Claim],
                  cap: int) -> list[Claim]:
    """Merge, dedup by normalized text, renumber, cap (load-bearing first)."""
    seen: set[str] = set()
    merged: list[Claim] = []
    ordered = base + extra
    keep = ([c for c in ordered if c.load_bearing]
            + [c for c in ordered if not c.load_bearing])
    for claim in keep:
        key = _dedup_key(claim.normalized_text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(claim)
        if len(merged) >= cap:
            break
    for i, claim in enumerate(merged):
        claim.id = f"c{i}"
    return merged


def extract_claims(evidence: EvidenceIndex, llm=None,
                   max_claims: int = 40) -> list[Claim]:
    """Build the ledger. With `llm`, run the LLM extractor plus the
    completeness critic and merge with the heuristic ledger; without,
    heuristic only."""
    heuristic = extract_claims_heuristic(evidence)
    artifact = evidence.artifact_text()
    if llm is None or not artifact:
        return _merge_claims(heuristic, [], max_claims)
    llm_claims: list[Claim] = []
    answer = llm.complete(_EXTRACT_PROMPT + artifact, max_tokens=4096)
    for item in _extract_json_array(answer or ""):
        claim = _claim_from_llm(item, artifact, len(llm_claims) + 1)
        if claim:
            llm_claims.append(claim)

    ledger_text = "\n".join(
        f"- {c.normalized_text}" for c in heuristic + llm_claims)
    critic_answer = llm.complete(
        _CRITIC_PROMPT.format(artifact=artifact, ledger=ledger_text),
        max_tokens=2048)
    for item in _extract_json_array(critic_answer or ""):
        claim = _claim_from_llm(item, artifact, len(llm_claims) + 1)
        if claim:
            llm_claims.append(claim)

    log.read("claims_extracted", trace_id=evidence.trace_id,
             heuristic=len(heuristic), llm=len(llm_claims))
    # `c0` (heuristic head) survives the merge; LLM claims take precedence
    # over their heuristic near-duplicates by coming first.
    head, tail = heuristic[:1], heuristic[1:]
    return _merge_claims(head + llm_claims, tail, max_claims)


__all__ = ["extract_claims", "extract_claims_heuristic",
           "synthetic_top_claim"]
