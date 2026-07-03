"""Push topic-suppression PROPOSALS to the agent inbox.

A topic the `InjectedRelated` grade pushes over the fail-rate bar becomes a
*proposal* (it keeps routing until a human signs off — see the topic-routing
feedback loop in `service.py`). Without a nudge the human would only find it
by opening the Memory panel, so each proposal lands as one inbox card.

All cards share a synthetic session id so the per-session keyed supersede
collapses a topic into a single durable card across every grading session;
a human decision resolves (dismisses) it. Best-effort throughout — a notify
failure must never affect the grade.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger

log = get_activity_logger("grader")

# Synthetic session the proposal cards live under, so the (session, key)
# supersede in `agent_messages` dedups one card per topic across all sessions.
_TRACE = "topic-routing-feedback"


def _key(topic_id: str) -> str:
    return f"topic-suppress-proposal:{topic_id}"


def notify_proposals(proposed: "list[dict]") -> int:
    """Emit one inbox card per proposed topic that doesn't already have a live
    (undismissed) one, so an open proposal isn't re-surfaced every grade.
    `proposed` are `topic_relevance_summary` rows with status == 'proposed'.
    Returns the number of cards pushed. Best-effort."""
    if not proposed:
        return 0
    pushed = 0
    try:
        from lib.agent_messages import events
        for row in proposed:
            topic_id = row["topic_id"]
            body = (
                f"Topic **{topic_id}** has been graded irrelevant "
                f"{row['fails']}/{row['scored']} times "
                f"(fail rate {row['fail_rate']:.0%}) and is now proposed for "
                f"suppression. It is **still routing** — approve suppression "
                f"or keep it routing in the Memory panel.")
            data = events.emit(
                "topic.suppress", trace_id=_TRACE, body=body,
                title=f"Suppress topic “{topic_id}”?", key=_key(topic_id),
                links=["/memory"], once=True)
            if data is not None:
                pushed += 1
    except Exception:  # noqa: BLE001 — notifying must never affect the grade
        log.error("topic_proposal_notify_failed", exc_info=True)
    if pushed:
        log.write("topic_proposals_notified", count=pushed)
    return pushed


def resolve_proposal(topic_id: str) -> None:
    """Dismiss a topic's live proposal card once a human has decided on it
    (suppress / allow / reset). Best-effort — never raises into the caller."""
    from lib.agent_messages import events
    events.resolve(_TRACE, _key(topic_id))


__all__ = ["notify_proposals", "resolve_proposal"]
