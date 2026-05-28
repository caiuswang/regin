"""Shared helpers + constants for the proposals package.

Small, dependency-free utilities used by load/save, topic-actions,
external-jobs, downgrade, and feedback sub-modules. Kept here so each
of those modules can import from _common without circular imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.activity_log import get_activity_logger as _get_activity_logger
from lib.prompt_templates import get_templates_by_slugs
from lib.topics import TopicGraphError, slugify, topic_dir


def _topics_log():
    return _get_activity_logger("topics")


VALID_PROPOSAL_REVIEW_STATES = {
    "draft",
    "pending_review",
    "changes_requested",
    "ready_to_apply",
    "partially_applied",
    "applied",
}

_REGENERATE_INFLIGHT_STATES = frozenset({"queued", "running"})

_REGENERATE_RESET_TOPIC_FIELDS = (
    "review_status", "accepted_topic", "accepted_at",
    "merged_topic", "merged_at", "ignored_at", "replaced_existing",
)


def _guard_regenerate_not_in_flight(repo_path: str | Path, proposal_id: str) -> None:
    from lib.topics.proposal_orm import orm_load_proposal_status
    status = orm_load_proposal_status(repo_path, proposal_id)
    if status and status.get("state") in _REGENERATE_INFLIGHT_STATES:
        raise TopicGraphError(
            f"regenerate already in flight for {proposal_id} (state={status['state']}); "
            "wait for it to finish or delete the run"
        )


def _reset_review_markers_for_regenerate(proposals: dict[str, Any]) -> None:
    """Clear per-topic accept/merge/ignore markers on a freshly regenerated draft.

    A regenerate replaces the topic content; any prior accept marker on a
    same-id topic is stale (the wiki / refs / intent no longer match what
    was accepted). Leaving review_status='accepted' on the new revision
    hid Edit/Apply/Ignore in the UI, stranding the user on a draft they
    couldn't act on.
    """
    for topic in proposals.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        for field in _REGENERATE_RESET_TOPIC_FIELDS:
            topic.pop(field, None)


def _recompute_proposal_status(proposal: dict[str, Any]) -> None:
    """Sync run-level status from topic review_status counts.

    Mirrors the rule the web /apply endpoint uses
    (web/blueprints/topics/apply.py:320-323) so CLI and web flows
    converge. Without this, library accept/merge/replace updated
    topic.review_status but left proposal.status stale.

    Only writes when topics have actually been reviewed — leaves
    draft / pending_review / changes_requested / ready_to_apply alone
    so a fresh or regenerated proposal isn't accidentally flipped to a
    review-driven status before any review happens.

    Parallel: lib/topics/proposal_orm.py:orm_unaccept_topic_across_proposals
    computes the same rule from ORM rows on the downgrade path. Keep
    these in sync if the rule changes.
    """
    topics = proposal.get("topics") or []
    if not topics:
        return
    reviewed = [t for t in topics if t.get("review_status")]
    if len(reviewed) == len(topics):
        proposal["status"] = "applied"
    elif reviewed:
        proposal["status"] = "partially_applied"


def _resolve_prompt_templates(slugs: list[str] | None) -> list[dict[str, Any]]:
    """Resolve template slugs into full dicts (with body) for prompt injection.

    Unknown slugs are silently dropped — a deleted custom template
    should not crash an in-flight or regenerated run.
    """
    if not slugs:
        return []
    return get_templates_by_slugs(slugs)


def _persist_per_topic_wiki(
    repo_path: str | Path,
    proposal_dir: Path,
    topic_id: str,
    label: str,
) -> Path | None:
    """Copy the proposal's full wiki.md to `.regin/topics/wiki/<id>.md`.

    Previously this function ran a heading-overlap heuristic to slice
    a per-topic section out of the agent's combined wiki.md. The
    heuristic was brittle (failed silently when no heading matched the
    label tokens) and lossy on re-accepts.

    Post-fix: the full proposal wiki becomes the per-topic file. For
    multi-topic proposals every accepted topic ends up with the same
    body — redundant but never lossy. The user can hand-edit later.

    `label` is kept in the signature so call sites don't need to change.
    """
    del label  # unused after the slicing drop
    wiki_path = proposal_dir / "wiki.md"
    if not wiki_path.exists():
        return None
    page_path = topic_dir(repo_path) / "wiki" / f"{slugify(topic_id)}.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(wiki_path.read_text())
    return page_path


def _find_proposed_topic(proposal: dict[str, Any], proposed_topic_id: str) -> dict[str, Any]:
    for topic in proposal.get("topics", []):
        if topic.get("id") == proposed_topic_id:
            return topic
    raise TopicGraphError(f"proposed topic not found: {proposed_topic_id}")


def proposal_review_state(proposal: dict[str, Any]) -> str:
    state = proposal.get("status") or proposal.get("metadata", {}).get("proposal_status") or "draft"
    if state not in VALID_PROPOSAL_REVIEW_STATES:
        return "draft"
    return state


def _reindex_wiki_after_graph_change(repo_path: str | Path) -> None:
    """Trigger the wiki PatternDoc reconcile in a background thread.

    Cold-start embedding-model load is ~10s — keep it off the user's
    response. Mirrors the same pattern in lib/topics/apply.py.
    """
    try:
        import threading
        from lib.orm import SessionLocal as _Session
        from lib.orm.models import Repo as _Repo
        from sqlmodel import select as _select
        from lib.patterns.wiki_indexer import index_wikis_best_effort
    except Exception:  # noqa: BLE001
        return
    with _Session() as s:
        repo_row = s.exec(_select(_Repo).where(_Repo.path == str(repo_path))).first()
    if repo_row is None:
        return
    threading.Thread(
        target=lambda: index_wikis_best_effort(repo_row),
        name=f"wiki-index-downgrade-{repo_row.id}",
        daemon=True,
    ).start()
