"""One-time backfill: give legacy proposals per-topic wiki bodies.

New proposal runs draft a per-topic ``wiki`` at the source, so each topic row
carries its own page. Runs drafted *before* per-topic wiki existed have a
single combined ``wiki_md`` on the revision and empty ``wiki_md`` on every
topic row — so the review UI and apply path would show the whole combined
document under every topic. This backfill splits that combined document into
per-topic sections (best-effort, by ``## `` heading) and writes them onto the
topic rows, so old runs also render distinct content.

The heading split is deliberately confined to this one-time migration of
*existing* data — it is NOT on the live drafting/serve path, where per-topic
wiki is authored directly. Unmatched topics are left empty (the UI falls back
to the combined wiki), never mis-assigned.

Also ensures the ``wiki_md`` columns exist on databases created before the
column was added to ``db/schema.sql`` (there is no auto-migration runner —
``regin init`` bakes ``schema.sql``).
"""

from __future__ import annotations

from sqlmodel import select

from lib.orm.engine import SessionLocal, get_connection
from lib.orm.models.proposals import ProposalRevision, ProposalRevisionTopic
from lib.topics.wiki_sections import assign_wiki_sections

_TABLES = ("proposal_topics", "proposal_revision_topics")


def ensure_topic_wiki_columns() -> list[str]:
    """Idempotently add the ``wiki_md`` column to the proposal-topic tables on
    databases that predate it. Returns the tables actually altered."""
    added: list[str] = []
    conn = get_connection()
    try:
        for table in _TABLES:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "wiki_md" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN wiki_md TEXT NOT NULL DEFAULT ''"
                )
                added.append(table)
        conn.commit()
    finally:
        conn.close()
    return added


def _backfill_revision(session, revision: ProposalRevision) -> int:
    """Populate empty per-topic wiki bodies for one revision from its combined
    wiki. Returns the number of topic rows filled."""
    if not (revision.wiki_md or "").strip():
        return 0
    topics = list(session.exec(
        select(ProposalRevisionTopic).where(
            ProposalRevisionTopic.revision_id == revision.id)
    ))
    pending = [t for t in topics if not (t.wiki_md or "").strip()]
    if not pending:
        return 0
    assigned = assign_wiki_sections(
        revision.wiki_md,
        [{"id": t.topic_id, "label": t.label} for t in pending],
    )
    filled = 0
    for topic in pending:
        section = assigned.get(topic.topic_id)
        if section:
            topic.wiki_md = section
            session.add(topic)
            filled += 1
    return filled


def backfill_topic_wiki() -> dict[str, int]:
    """Split legacy combined wikis into per-topic bodies. Idempotent: topics
    that already have a body (new runs, or a prior backfill) are skipped."""
    ensure_topic_wiki_columns()
    filled = 0
    revisions_touched = 0
    with SessionLocal() as session:
        for revision in list(session.exec(select(ProposalRevision))):
            revision_filled = _backfill_revision(session, revision)
            if revision_filled:
                revisions_touched += 1
                filled += revision_filled
        session.commit()
    return {"revisions": revisions_touched, "filled": filled}
