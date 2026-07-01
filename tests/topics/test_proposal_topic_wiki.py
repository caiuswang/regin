"""Per-topic wiki: the drafting agent emits a wiki per topic, it round-trips
through the ORM, and legacy runs backfill from the combined document."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import select

from lib.orm.engine import SessionLocal, get_connection
from lib.orm.models.proposals import ProposalRevision, ProposalRevisionTopic
from lib.topics.proposal_external import (
    _combined_proposal_wiki,
    _normalise_agent_payload,
    _topic_wiki_section,
)
from lib.topics.proposal_orm.serializers import _proposed_topic_kwargs, _topic_to_dict
from lib.topics.wiki_backfill import backfill_topic_wiki, ensure_topic_wiki_columns


# ── source-side: per-topic wiki -> combined ──────────────────────────

def test_topic_wiki_section_heads_with_label_when_body_has_none():
    assert _topic_wiki_section({"id": "a", "label": "Alpha", "wiki": "Body."}) == "## Alpha\n\nBody."


def test_topic_wiki_section_keeps_own_h2_heading():
    section = _topic_wiki_section({"id": "a", "label": "Alpha", "wiki": "## Own\n\nBody."})
    assert section == "## Own\n\nBody."


def test_topic_wiki_section_none_when_empty():
    assert _topic_wiki_section({"id": "a", "label": "Alpha", "wiki": "  "}) is None


def test_combined_wiki_stitches_overview_and_sections():
    payload = {"overview": "Intro here."}
    topics = [
        {"id": "a", "label": "Alpha", "wiki": "Alpha body."},
        {"id": "b", "label": "Beta", "wiki": "Beta body."},
    ]
    combined = _combined_proposal_wiki(payload, topics)
    assert combined.startswith("Intro here.")
    assert "## Alpha\n\nAlpha body." in combined
    assert "## Beta\n\nBeta body." in combined


def test_topic_wiki_section_demotes_h1_so_combined_keeps_boundaries():
    # An h1-headed per-topic body is demoted to h2 so the combined doc still
    # has `## ` section boundaries (keeps wiki_intro extraction correct).
    section = _topic_wiki_section({"id": "a", "label": "Alpha", "wiki": "# Alpha\n\nBody."})
    assert section == "## Alpha\n\nBody."


def test_combined_wiki_from_h1_bodies_has_section_headings():
    payload = {"overview": "Intro."}
    topics = [
        {"id": "a", "label": "Alpha", "wiki": "# Alpha\n\nA body."},
        {"id": "b", "label": "Beta", "wiki": "# Beta\n\nB body."},
    ]
    combined = _combined_proposal_wiki(payload, topics)
    assert "## Alpha" in combined and "## Beta" in combined
    assert "# Alpha" not in combined.replace("## Alpha", "")  # no stray h1


def test_combined_wiki_falls_back_to_legacy_top_level():
    # No per-topic wiki -> use the old single top-level string.
    payload = {"wiki": "Legacy combined doc."}
    topics = [{"id": "a", "label": "Alpha"}]
    assert _combined_proposal_wiki(payload, topics) == "Legacy combined doc."


def test_normalise_payload_carries_per_topic_wiki_and_builds_combined():
    payload = {
        "version": 1,
        "topics": [
            {"id": "a", "label": "Alpha", "intent": "x", "status": "active", "wiki": "Alpha body."},
            {"id": "b", "label": "Beta", "intent": "y", "status": "active", "wiki": "Beta body."},
        ],
        "overview": "Shared intro.",
    }
    proposal, combined = _normalise_agent_payload(Path("/tmp/repo"), payload)
    assert [t["wiki"] for t in proposal["topics"]] == ["Alpha body.", "Beta body."]
    assert "Shared intro." in combined and "## Beta" in combined


# ── ORM round-trip ───────────────────────────────────────────────────

def test_proposed_topic_kwargs_persists_wiki():
    kwargs = _proposed_topic_kwargs({"id": "a", "label": "Alpha", "wiki": "Body."})
    assert kwargs["wiki_md"] == "Body."


def test_topic_to_dict_surfaces_wiki():
    topic = ProposalRevisionTopic(revision_id=1, topic_id="a", label="Alpha", wiki_md="Body.")
    assert _topic_to_dict(topic)["wiki"] == "Body."


# ── backfill of legacy runs ──────────────────────────────────────────

def _seed_run_parents() -> None:
    """A proposal_revisions row FKs to proposal_runs -> repos, so seed those."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO repos (id, name, path) VALUES (1, 'x', '/tmp/x')")
        conn.execute(
            "INSERT INTO proposal_runs (id, repo_id, provider, scope, state, "
            "started_at, updated_at) VALUES "
            "('run1', 1, 'p', 'all', 'completed', 't', 't')")
        conn.commit()
    finally:
        conn.close()


def _seed_legacy_revision() -> int:
    _seed_run_parents()
    combined = (
        "# Overview\n\nShared intro.\n\n"
        "## Alpha\n\nAlpha body.\n\n"
        "## Beta\n\nBeta body.\n"
    )
    with SessionLocal() as s:
        rev = ProposalRevision(
            run_id="run1", revision_number=1, wiki_md=combined,
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        s.add(rev)
        s.flush()
        rev_id = rev.id
        s.add(ProposalRevisionTopic(revision_id=rev_id, topic_id="alpha", label="Alpha", wiki_md=""))
        s.add(ProposalRevisionTopic(revision_id=rev_id, topic_id="beta", label="Beta", wiki_md=""))
        s.commit()
    return rev_id


def test_ensure_columns_idempotent_on_current_schema():
    # tmp_db already has the column, so nothing to add.
    assert ensure_topic_wiki_columns() == []


def test_backfill_splits_combined_into_distinct_topic_bodies():
    rev_id = _seed_legacy_revision()
    result = backfill_topic_wiki()
    assert result["filled"] == 2
    with SessionLocal() as s:
        topics = list(s.exec(select(ProposalRevisionTopic).where(
            ProposalRevisionTopic.revision_id == rev_id)))
        bodies = {t.topic_id: t.wiki_md for t in topics}
    assert "Alpha body." in bodies["alpha"]
    assert "Beta body." in bodies["beta"]
    assert bodies["alpha"] != bodies["beta"]


def test_backfill_is_idempotent():
    _seed_legacy_revision()
    first = backfill_topic_wiki()["filled"]
    second = backfill_topic_wiki()["filled"]
    assert first == 2
    assert second == 0  # already populated — nothing re-filled
