"""LLM-written review notes for proposal runs.

After a proposal run drafts (or regenerates) topics, an agentic reviewer
assesses the draft and writes a single ``review_note`` feedback thread carrying
a structured recommendation — ``REGENERATE`` / ``ACCEPT`` / ``DISMISS`` — plus
its reasons. The note rides the *existing* feedback-thread machinery, so it

* renders in the review sidebar like any human comment, and
* is carried into the next regenerate's drafting prompt by
  ``format_review_feedback_for_prompt`` (which only replays *open* threads).

Generation is **agentic**: the reviewer is the configured external agent
(``resolve_proposal_reviewer`` → ``ExternalAgentLLM`` with read-only repo
tools), so it verifies the draft against the current refs itself rather than
judging a pre-baked evidence pack. When no agent is configured, ``complete``
returns ``None`` and we write no note.

``maybe_generate_review_note`` is the gated, best-effort trigger wired into the
run-completion paths — a no-op unless ``topic_evolution.auto_review_notes`` is
set. ``generate_review_note`` is the manual, ungated entry point (explicit user
action via the CLI / web endpoint).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("topics")

# Allowed recommendations, in the order we scan for them. ACCEPT is the
# neutral fallback when the reviewer doesn't emit a recognisable token.
RECOMMENDATIONS: tuple[str, ...] = ("REGENERATE", "DISMISS", "ACCEPT")
_DEFAULT_RECOMMENDATION = "ACCEPT"
_RECOMMENDATION_RE = re.compile(
    r"RECOMMENDATION\s*[:=]\s*(REGENERATE|ACCEPT|DISMISS)", re.IGNORECASE
)


def _open_feedback_lines(threads: list[dict[str, Any]]) -> str:
    """One bullet per still-open human thread, so the reviewer doesn't
    re-raise issues the user already flagged (and can note if a draft
    addressed them)."""
    lines: list[str] = []
    for thread in threads:
        if thread.get("resolution_state") != "open":
            continue
        comments = thread.get("comments") or []
        body = comments[0].get("body") if comments else thread.get("body")
        if body and str(body).strip():
            lines.append(f"- {str(body).strip()}")
    return "\n".join(lines)


def _topic_lines(proposal: dict[str, Any]) -> str:
    """One bullet per drafted topic (`- <id>: <intent>` + its ref paths) for the
    ``<draft_topics>`` block. Kept as a discrete builder so it can be passed as
    the ``topic_lines`` variable into the editable review skeleton."""
    lines: list[str] = []
    for topic in proposal.get("topics") or []:
        tid = topic.get("id", "?")
        refs = ", ".join(
            r.get("path", "") for r in topic.get("refs", []) if isinstance(r, dict)
        )
        lines.append(f"- {tid}: {topic.get('intent', '')}\n  refs: {refs}")
    return "\n".join(lines)


def _feedback_block(open_feedback: str) -> str:
    """The `<prior_open_feedback>` block, or "" when there is no open feedback."""
    if not open_feedback:
        return ""
    return f"<prior_open_feedback>\n{open_feedback}\n</prior_open_feedback>\n\n"


def _build_prompt(proposal: dict[str, Any], open_feedback: str) -> str:
    """Build the reviewer's task prompt.

    The body is the editable ``topic-proposal-review`` surface
    (``lib/prompts/surfaces/review.py``); this function only assembles the
    runtime context it interpolates. A broken user edit degrades to the built-in
    default inside ``render_surface`` — the prompt is never left unbuildable.
    """
    from lib.prompts import render_surface
    from lib.prompts.surfaces.review import SURFACE_ID

    context = {
        "topic_lines": _topic_lines(proposal),
        "feedback_block": _feedback_block(open_feedback),
    }
    return render_surface(SURFACE_ID, context)


def _parse_recommendation(answer: str) -> str:
    """Pull the recommendation token from the reviewer's text. Prefer the
    explicit ``RECOMMENDATION:`` line; else first token seen; else the neutral
    default. Never raises — a malformed answer still yields a usable note."""
    match = _RECOMMENDATION_RE.search(answer or "")
    if match:
        return match.group(1).upper()
    upper = (answer or "").upper()
    for token in RECOMMENDATIONS:
        if token in upper:
            return token
    return _DEFAULT_RECOMMENDATION


def generate_review_note(
    repo_path: str | Path, proposal_id: str, *, llm: Any = None,
) -> Optional[dict[str, Any]]:
    """Generate one review note for a proposal run and persist it as a
    ``review_note`` feedback thread (``created_by='agent'``). Returns the
    thread dict, or ``None`` when the proposal has no drafted topics or no
    external agent is configured (``complete`` → ``None``). Manual/ungated:
    callers decide whether to gate on ``auto_review_notes``."""
    from lib.topics.proposals import (
        create_proposal_feedback_thread,
        list_proposal_feedback_threads,
        load_proposal,
    )

    proposal = load_proposal(repo_path, proposal_id)
    if not proposal.get("topics"):
        return None

    open_feedback = _open_feedback_lines(
        list_proposal_feedback_threads(repo_path, proposal_id)
    )
    if llm is None:
        from lib.memory.adapters import resolve_proposal_reviewer
        llm = resolve_proposal_reviewer()
    answer = llm.complete(
        _build_prompt(proposal, open_feedback), max_tokens=1024, cwd=repo_path,
    )
    if not answer or not str(answer).strip():
        log.write("proposal_review_note_skipped", proposal_id=proposal_id,
                  reason="no_llm_output", repo_path=str(repo_path))
        return None

    recommendation = _parse_recommendation(str(answer))
    body = (
        f"**Automated review — recommendation: {recommendation}**\n\n"
        f"{str(answer).strip()}"
    )
    thread = create_proposal_feedback_thread(
        repo_path, proposal_id,
        proposal_topic_id=None,
        kind="review_note",
        anchor_kind="general",
        body=body,
        created_by="agent",
        # Structured copy of the recommendation so the UI badge reads a field
        # instead of regexing the LLM's prose body.
        metadata={"recommendation": recommendation},
    )
    log.write("proposal_review_note_created", proposal_id=proposal_id,
              recommendation=recommendation, repo_path=str(repo_path))
    return thread


def maybe_generate_review_note(repo_path: str | Path, proposal_id: str) -> bool:
    """Gated, best-effort trigger for the run-completion paths. A no-op
    (returns ``False``) unless ``auto_review_notes`` is set; never raises into
    the run thread — a review-note failure must not fail the proposal run."""
    if not settings.topic_evolution.auto_review_notes:
        return False
    try:
        return generate_review_note(repo_path, proposal_id) is not None
    except Exception:  # noqa: BLE001 - a review note must never break the run
        log.error("proposal_review_note_failed", proposal_id=proposal_id,
                  exc_info=True)
        return False


__all__ = ["generate_review_note", "maybe_generate_review_note",
           "RECOMMENDATIONS"]
