"""Revision lookups + restore / downgrade revision appends.

Each ProposalRun has a chain of ProposalRevision rows (one per
generate / regenerate / restore / downgrade). The latest revision is
the one the UI renders by default; older revisions are reachable via
`/revisions/<id>`. Topic snapshots for a revision live in
ProposalRevisionTopic.

Legacy `ProposalTopic` rows predate revision persistence — they're
kept around so backfilled proposals still render, and
`_ensure_legacy_revision` lazily promotes them to a `kind="system_migrated"`
revision on first read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from lib.orm.models import (
    ProposalFeedbackThread,
    ProposalRevision,
    ProposalRevisionTopic,
    ProposalRun,
    ProposalTopic,
)
from lib.topics.core import topic_dir, TopicGraphError

from ._common import _utc_now
from .serializers import _proposed_topic_kwargs, _topic_to_dict


# ───────────────────────────── lookups ─────────────────────────────


def _latest_revision(session: Session, proposal_id: str) -> ProposalRevision | None:
    revision = session.exec(
        select(ProposalRevision)
        .where(ProposalRevision.run_id == proposal_id)
        .where(ProposalRevision.is_latest == 1)
        .order_by(ProposalRevision.revision_number.desc(), ProposalRevision.id.desc())
    ).first()
    if revision is not None:
        return revision
    return session.exec(
        select(ProposalRevision)
        .where(ProposalRevision.run_id == proposal_id)
        .order_by(ProposalRevision.revision_number.desc(), ProposalRevision.id.desc())
    ).first()


def _revision_rows(session: Session, proposal_id: str) -> list[ProposalRevision]:
    return list(session.exec(
        select(ProposalRevision)
        .where(ProposalRevision.run_id == proposal_id)
        .order_by(ProposalRevision.revision_number.desc(), ProposalRevision.id.desc())
    ))


def _revision_topics(session: Session, revision_id: int) -> list[ProposalRevisionTopic]:
    return list(session.exec(
        select(ProposalRevisionTopic).where(ProposalRevisionTopic.revision_id == revision_id)
    ))


def _revision_number_map(session: Session, proposal_id: str) -> dict[int, int]:
    return {
        revision.id: revision.revision_number
        for revision in _revision_rows(session, proposal_id)
        if revision.id is not None
    }


def _copy_legacy_topics_to_revision(
    session: Session,
    revision_id: int,
    legacy_topics: list[ProposalTopic],
) -> None:
    for topic in legacy_topics:
        session.add(ProposalRevisionTopic(
            revision_id=revision_id,
            **_proposed_topic_kwargs(_topic_to_dict(topic)),
        ))


def _ensure_legacy_revision(
    session: Session,
    repo_path: str | Path,
    run: ProposalRun,
) -> ProposalRevision | None:
    """Lazy-promote legacy `proposal_topics` rows to a `system_migrated`
    revision so reads that expect revision shape work uniformly."""
    revision = _latest_revision(session, run.id)
    if revision is not None:
        return revision
    legacy_topics = list(session.exec(
        select(ProposalTopic).where(ProposalTopic.run_id == run.id)
    ))
    if not legacy_topics:
        return None
    timestamp = run.completed_at or run.started_at or _utc_now()
    wiki_path = topic_dir(repo_path) / "proposals" / run.id / "wiki.md"
    revision = ProposalRevision(
        run_id=run.id,
        revision_number=1,
        parent_revision_id=None,
        kind="system_migrated",
        wiki_md=wiki_path.read_text() if wiki_path.exists() else "",
        is_latest=1,
        created_at=timestamp,
        updated_at=timestamp,
        metadata_json=json.dumps({"backfilled_from_legacy": True}),
    )
    session.add(revision)
    session.flush()
    if revision.id is None:
        return None
    _copy_legacy_topics_to_revision(session, revision.id, legacy_topics)
    for thread in session.exec(
        select(ProposalFeedbackThread).where(ProposalFeedbackThread.run_id == run.id)
    ):
        if thread.revision_id is None:
            thread.revision_id = revision.id
            session.add(thread)
    session.commit()
    session.refresh(revision)
    return revision


def _purge_legacy_proposal_topics(s: Session, proposal_id: str) -> None:
    for old in s.exec(select(ProposalTopic).where(ProposalTopic.run_id == proposal_id)):
        s.delete(old)
    s.flush()


def _reset_run_status_to_pending(s: Session, run: ProposalRun, now: str) -> None:
    run_meta = json.loads(run.metadata_json or "{}")
    run_meta["proposal_status"] = "pending_review"
    run.metadata_json = json.dumps(run_meta)
    run.updated_at = now
    s.add(run)


# ────────────────────────── restore-to-revision ─────────────────────


_RESTORED_TOPIC_MARKERS = (
    "review_status", "accepted_topic", "accepted_at",
    "merged_topic", "merged_at", "ignored_at", "replaced_existing",
)


def _strip_review_markers(topic: dict[str, Any]) -> dict[str, Any]:
    for field in _RESTORED_TOPIC_MARKERS:
        topic.pop(field, None)
    return topic


def _resolve_restore_targets(
    s: Session,
    repo_path: str | Path,
    proposal_id: str,
    source_revision_id: int,
) -> Optional[tuple[ProposalRun, ProposalRevision, ProposalRevision]]:
    """Validate inputs and return (run, source_revision, latest_revision).

    Returns None if any lookup is missing / cross-repo. Raises
    TopicGraphError if the source revision is already the latest.
    """
    from ._common import _repo_for_path

    run = s.get(ProposalRun, proposal_id)
    if run is None:
        return None
    repo = _repo_for_path(s, repo_path)
    if repo is None or run.repo_id != repo.id:
        return None
    source = s.get(ProposalRevision, source_revision_id)
    if source is None or source.run_id != proposal_id:
        return None
    latest = _latest_revision(s, proposal_id)
    if latest is None or latest.id == source.id:
        raise TopicGraphError(
            f"revision {source_revision_id} is already the latest; nothing to restore"
        )
    return run, source, latest


def _build_restored_revision_metadata(
    source: ProposalRevision,
    run: ProposalRun,
) -> dict[str, Any]:
    source_meta = json.loads(source.metadata_json or "{}")
    return {
        "provider": source_meta.get("provider") or run.provider,
        "scope": source_meta.get("scope") or run.scope,
        "status": "pending_review",
        "restored_from_revision_id": source.id,
        "restored_from_revision_number": source.revision_number,
    }


def _append_restored_revision(
    s: Session,
    proposal_id: str,
    source: ProposalRevision,
    latest: ProposalRevision,
    metadata: dict[str, Any],
    now: str,
) -> ProposalRevision:
    latest.is_latest = 0
    latest.updated_at = now
    s.add(latest)
    s.flush()
    new_revision = ProposalRevision(
        run_id=proposal_id,
        revision_number=(latest.revision_number or 0) + 1,
        parent_revision_id=latest.id,
        kind="restored",
        wiki_md=source.wiki_md or "",
        is_latest=1,
        created_at=now,
        updated_at=now,
        metadata_json=json.dumps(metadata),
    )
    s.add(new_revision)
    s.flush()
    return new_revision


def _copy_topics_for_restore(s: Session, source_revision_id: int, target_revision_id: int) -> None:
    for source_topic in _revision_topics(s, source_revision_id):
        payload = _strip_review_markers(_topic_to_dict(source_topic))
        s.add(ProposalRevisionTopic(
            revision_id=target_revision_id,
            **_proposed_topic_kwargs(payload),
        ))


def orm_restore_proposal_to_revision(
    repo_path: str | Path,
    proposal_id: str,
    source_revision_id: int,
) -> Optional[dict[str, Any]]:
    """Append a new revision whose content is copied from `source_revision_id`.

    Used by the "Restore this revision" UI: takes a historical revision
    (e.g. r1 of a run regenerated into r2) and makes a fresh revision
    (r3) carrying r1's wiki + topic snapshots. The new revision is
    flagged as latest with `kind='restored'`, and `metadata_json`
    records the source as `restored_from_revision_id` /
    `restored_from_revision_number`.

    Topic accept/merge/ignore markers from the source are stripped on
    the copy — the apply path checks `topic.review_status`, and stale
    markers from a long-ago accept would suppress the Accept button.

    Also resets `run.metadata.proposal_status` to `pending_review` so
    the Mark ready → Apply flow works on the new revision.

    Returns the proposal dict for the new latest revision, None if the
    run or source revision is missing / belongs to a different repo.
    Raises TopicGraphError if the source revision is already the latest
    (nothing to restore).
    """
    from lib.orm import SessionLocal
    from .serializers import _run_to_proposal_dict

    with SessionLocal() as s:
        resolved = _resolve_restore_targets(s, repo_path, proposal_id, source_revision_id)
        if resolved is None:
            return None
        run, source, latest = resolved
        now = _utc_now()
        metadata = _build_restored_revision_metadata(source, run)
        new_revision = _append_restored_revision(s, proposal_id, source, latest, metadata, now)
        _copy_topics_for_restore(s, source_revision_id, new_revision.id)
        _reset_run_status_to_pending(s, run, now)
        _purge_legacy_proposal_topics(s, proposal_id)
        s.commit()
        topics = _revision_topics(s, new_revision.id)
        revisions = _revision_rows(s, proposal_id)
        return _run_to_proposal_dict(run, topics, revision=new_revision, revisions=revisions)


# ────────────────────────── downgrade-to-revision ───────────────────


def _build_downgrade_revision_metadata(
    source: ProposalRevision,
    run: ProposalRun,
    downgraded_topic_id: str,
    downgraded_at: str,
) -> dict[str, Any]:
    source_meta = json.loads(source.metadata_json or "{}")
    return {
        "provider": source_meta.get("provider") or run.provider,
        "scope": source_meta.get("scope") or run.scope,
        "status": "changes_requested",
        "downgrade_origin": True,
        "downgraded_from_topic_id": downgraded_topic_id,
        "downgraded_at": downgraded_at,
    }


def _topics_for_downgrade_revision(
    s: Session,
    latest_revision: ProposalRevision,
    downgraded_topic_id: str,
    downgraded_topic_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the new revision's topic list: every existing topic from
    the latest revision preserved (review_status kept so re-applied
    topics stay marked as such), with the downgraded entry replaced by
    `downgraded_topic_payload`. The downgraded entry resurfaces as a
    fresh draft regardless of whether it was even present in the latest
    revision — its origin was the approved graph, not this revision.
    """
    out: list[dict[str, Any]] = []
    saw_downgraded = False
    for existing in _revision_topics(s, latest_revision.id):
        topic = _topic_to_dict(existing)
        if topic.get("id") == downgraded_topic_id:
            out.append(dict(downgraded_topic_payload))
            saw_downgraded = True
        else:
            out.append(topic)
    if not saw_downgraded:
        out.append(dict(downgraded_topic_payload))
    return out


def _apply_downgrade_run_metadata(
    run: ProposalRun,
    downgraded_topic_id: str,
    pruned_inbound_edges: Optional[dict[str, list[dict[str, Any]]]],
) -> None:
    """Override the "pending_review" set by _reset_run_status_to_pending —
    for a downgrade the more honest signal is "changes requested": the
    topic was applied, then the user explicitly asked to revisit it."""
    run_meta = json.loads(run.metadata_json or "{}")
    run_meta["proposal_status"] = "changes_requested"
    if pruned_inbound_edges:
        # Round-trip: apply path reads this back and patches the sibling
        # edges in before the topic re-lands.
        edges_bucket = run_meta.get("pruned_inbound_edges") or {}
        edges_bucket[downgraded_topic_id] = pruned_inbound_edges
        run_meta["pruned_inbound_edges"] = edges_bucket
    run.metadata_json = json.dumps(run_meta)


def orm_append_downgrade_revision(
    repo_path: str | Path,
    origin_run_id: str,
    downgraded_topic_id: str,
    downgraded_topic_payload: dict[str, Any],
    wiki_content: str,
    downgraded_at: str,
    *,
    pruned_inbound_edges: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> Optional[dict[str, Any]]:
    """Append a new revision (kind='downgraded') to `origin_run_id`,
    carrying every topic the prior latest revision had plus the fresh
    draft snapshot of the just-downgraded topic. Returns the proposal
    dict for the new latest revision, or None if origin missing.
    """
    from lib.orm import SessionLocal
    from ._common import _repo_for_path
    from .serializers import _run_to_proposal_dict

    with SessionLocal() as s:
        run = s.get(ProposalRun, origin_run_id)
        if run is None:
            return None
        repo = _repo_for_path(s, repo_path)
        if repo is None or run.repo_id != repo.id:
            return None
        latest = _latest_revision(s, origin_run_id)
        if latest is None:
            return None
        now = _utc_now()
        metadata = _build_downgrade_revision_metadata(
            latest, run, downgraded_topic_id, downgraded_at,
        )
        new_topics = _topics_for_downgrade_revision(
            s, latest, downgraded_topic_id, downgraded_topic_payload,
        )

        latest.is_latest = 0
        latest.updated_at = now
        s.add(latest)
        s.flush()

        new_revision = ProposalRevision(
            run_id=origin_run_id,
            revision_number=(latest.revision_number or 0) + 1,
            parent_revision_id=latest.id,
            kind="downgraded",
            wiki_md=wiki_content or "",
            is_latest=1,
            created_at=now,
            updated_at=now,
            metadata_json=json.dumps(metadata),
        )
        s.add(new_revision)
        s.flush()

        for topic in new_topics:
            s.add(ProposalRevisionTopic(
                revision_id=new_revision.id,
                **_proposed_topic_kwargs(topic),
            ))

        _reset_run_status_to_pending(s, run, now)
        _apply_downgrade_run_metadata(run, downgraded_topic_id, pruned_inbound_edges)
        s.add(run)

        _purge_legacy_proposal_topics(s, origin_run_id)
        s.commit()

        topics = _revision_topics(s, new_revision.id)
        revisions = _revision_rows(s, origin_run_id)
        return _run_to_proposal_dict(run, topics, revision=new_revision, revisions=revisions)


# ─────────────────────── read-only list / load endpoints ──────────────


def orm_load_proposal_revision(
    repo_path: str | Path,
    proposal_id: str,
    revision_id: int,
) -> Optional[dict[str, Any]]:
    from lib.orm import SessionLocal
    from ._common import _repo_for_path
    from .serializers import _run_to_proposal_dict

    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        if run is None:
            return None
        repo = _repo_for_path(s, repo_path)
        if repo is None or run.repo_id != repo.id:
            return None
        revision = s.get(ProposalRevision, revision_id)
        if revision is None or revision.run_id != proposal_id:
            return None
        topics = _revision_topics(s, revision_id)
        revisions = _revision_rows(s, proposal_id)
        return _run_to_proposal_dict(run, topics, revision=revision, revisions=revisions)


def orm_list_proposal_revisions(repo_path: str | Path, proposal_id: str) -> list[dict[str, Any]]:
    from lib.orm import SessionLocal
    from ._common import _repo_for_path
    from .serializers import _revision_to_dict

    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        repo = _repo_for_path(s, repo_path)
        if run is None or repo is None or run.repo_id != repo.id:
            return []
        revisions = _revision_rows(s, proposal_id)
        return [_revision_to_dict(revision) for revision in revisions]
