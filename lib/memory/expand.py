"""Query expansion for recall — the alternative recall front-end.

The default recall path (the `UserPromptSubmit` hook) keys recall on the
*raw* prompt text. Interactive prompts are terse ("does those config are
configable in WebUI?") and lean on session context the hook can't see, so
a short prompt gives the dense/rerank stack little lexical surface to
match memories against.

This module is the other strategy: run the raw prompt through an LLM
(`LLMProvider`) that rewrites it into a short, keyword-rich description of
the underlying task — naming subsystems, concepts, and likely failure
modes — and recall on *that*. It is pure read-side policy, like
`lib.memory.intent`: nothing here writes memories or touches the store.

Every step degrades gracefully. No `LLMProvider`, an empty/failed
completion, or a degenerate expansion all fall back to the raw query, so
`expand_query` can never make recall worse than the direct path — only the
extra latency of the LLM round-trip is spent for nothing.
"""

from __future__ import annotations

from typing import Optional

from lib.activity_log import get_activity_logger
from lib.memory.ports import LLMProvider

log = get_activity_logger("memory")

# An expansion that is shorter than the original, or barely longer, added
# no lexical surface — treat it as a no-op and recall on the raw query.
_MIN_GROWTH_CHARS = 8
# Cap what we feed the embedder: a runaway expansion dilutes the query
# vector instead of sharpening it.
_MAX_EXPANSION_CHARS = 600

_INSTRUCTION = (
    "You rewrite a terse coding-session request into a short, keyword-rich "
    "search query for retrieving relevant past engineering lessons. Expand "
    "abbreviations, name the likely technical subsystems, concepts, and "
    "failure modes the request implies. Preserve the original intent; do "
    "not answer the request or invent specifics not implied by it. Output "
    "ONLY the expanded query as 1-2 sentences, no preamble or quoting."
)


def _build_prompt(query: str) -> str:
    return f"{_INSTRUCTION}\n\nRequest: {query}"


def _clean(raw: Optional[str]) -> str:
    """Strip an LLM completion down to a single recall query line."""
    text = (raw or "").strip()
    if not text:
        return ""
    # Some agents echo a leading label; drop one if present.
    for prefix in ("Expanded query:", "Query:", "Expanded:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    return text[:_MAX_EXPANSION_CHARS]


def expand_query(query: str, llm: Optional[LLMProvider]) -> str:
    """Return an expanded recall query, or the original `query` unchanged
    when expansion is unavailable or unhelpful.

    The contract is "never worse than raw": any failure mode — no LLM, a
    blank completion, or an expansion that didn't add lexical surface —
    returns the input verbatim.
    """
    base = (query or "").strip()
    if not base or llm is None:
        return base
    try:
        completion = llm.complete(_build_prompt(base), max_tokens=200)
    except Exception:
        log.error("query_expansion_failed", exc_info=True)
        return base
    expanded = _clean(completion)
    if len(expanded) < len(base) + _MIN_GROWTH_CHARS:
        return base
    # Keep the original terms alongside the expansion: the rewrite may drop
    # a rare identifier the raw prompt named, and FTS still wants it.
    return f"{base}. {expanded}"


__all__ = ["expand_query"]
