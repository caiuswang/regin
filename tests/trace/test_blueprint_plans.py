"""Unit tests for web.blueprints.plans JSON API.

Covers plan file browsing (list / detail / mentions) and plan-mode
session lifecycle ingest/list.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import PlanSession, SkillRead


@pytest.fixture
def isolated_plans_dir(tmp_path, monkeypatch):
    """Make lib.plans see only `tmp_path` as a single fake provider's
    plans directory, so list_plans/get_plan read from there in isolation."""
    from lib.trace import plans as plans_mod
    monkeypatch.setattr(
        plans_mod, "_plans_dirs",
        lambda: iter([("test", str(tmp_path))]),
    )
    return tmp_path


# ── GET /api/plans ───────────────────────────────────────────

def test_api_plans_empty_dir(flask_client, isolated_plans_dir):
    resp = flask_client.get("/api/plans")
    assert resp.status_code == 200
    assert resp.get_json() == {"plans": []}


def test_api_plans_lists_md_files_sorted_newest_first(
        flask_client, isolated_plans_dir):
    import time
    (isolated_plans_dir / "old.md").write_text("# Old plan\n")
    time.sleep(0.02)  # ensure mtime ordering
    (isolated_plans_dir / "new.md").write_text("# New plan\n")
    # Non-md file ignored.
    (isolated_plans_dir / "notes.txt").write_text("ignored")

    resp = flask_client.get("/api/plans")
    body = resp.get_json()
    filenames = [p["filename"] for p in body["plans"]]
    assert filenames == ["new.md", "old.md"]
    assert body["plans"][0]["title"] == "New plan"


# ── GET /api/plans/<filename> ────────────────────────────────

def test_api_plan_detail_found(flask_client, isolated_plans_dir):
    (isolated_plans_dir / "foo.md").write_text(
        "# Foo Plan\n\nBody text\n"
    )
    resp = flask_client.get("/api/plans/foo.md")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["filename"] == "foo.md"
    assert body["title"] == "Foo Plan"
    assert "Body text" in body["content"]


def test_api_plan_detail_missing_returns_404(
        flask_client, isolated_plans_dir):
    resp = flask_client.get("/api/plans/nope.md")
    assert resp.status_code == 404


def test_api_plan_detail_rejects_path_traversal(
        flask_client, isolated_plans_dir):
    resp = flask_client.get("/api/plans/..%2Fetc%2Fpasswd")
    # get_plan returns None on `..` in name → 404.
    assert resp.status_code == 404


# ── GET /api/plans/<filename>/mentions ───────────────────────

def test_api_plan_mentions_empty_when_plan_has_no_authoring_session(
        flask_client, isolated_plans_dir, tmp_db):
    """No PlanSession row for this filename → no attribution → []. The
    endpoint no longer guesses from plan text."""
    (isolated_plans_dir / "quiet.md").write_text(
        "# Quiet\n\nNothing interesting here.\n"
    )
    resp = flask_client.get("/api/plans/quiet.md/mentions")
    assert resp.status_code == 200
    assert resp.get_json() == {"skills": []}


def test_api_plan_mentions_returns_skills_read_by_authoring_session(
        flask_client, isolated_plans_dir, tmp_db):
    """Session-based attribution: skills the authoring session
    actually read (or invoked) come back, regardless of whether the
    plan text mentions them. This is the reliable channel — substring
    matching is gone."""
    (isolated_plans_dir / "refs.md").write_text("# Refs\n\nNo names here.\n")

    with SessionLocal() as session:
        session.add(PlanSession(
            session_id="sess-A", plan_filename="refs.md",
            started_at="2026-04-22 10:00:00",
        ))
        # alpha-skill read by the authoring session — should appear.
        session.add(SkillRead(
            skill_id="alpha-skill", session_id="sess-A",
            file_path="~/.claude/skills/alpha-skill/SKILL.md",
            read_at="2026-04-22 10:30:00",
        ))
        # beta-skill read by a DIFFERENT session — must be excluded.
        session.add(SkillRead(
            skill_id="beta-skill", session_id="sess-other",
            file_path="~/.claude/skills/beta-skill/SKILL.md",
            read_at="2026-04-22 11:00:00",
        ))
        session.commit()

    resp = flask_client.get("/api/plans/refs.md/mentions")
    assert resp.status_code == 200
    body = resp.get_json()
    ids = {s["skill_id"] for s in body["skills"]}
    assert ids == {"alpha-skill"}
    assert body["skills"][0]["last_read_at"] == "2026-04-22 10:30:00"


def test_api_plan_mentions_collapses_multiple_reads_to_latest(
        flask_client, isolated_plans_dir, tmp_db):
    """The same skill read N times by the same session aggregates to one
    row carrying the most recent read_at."""
    (isolated_plans_dir / "multi.md").write_text("# Multi\n")
    with SessionLocal() as session:
        session.add(PlanSession(
            session_id="sess-M", plan_filename="multi.md",
            started_at="2026-04-22 10:00:00",
        ))
        for ts in ("2026-04-22 10:01:00", "2026-04-22 10:05:00",
                   "2026-04-22 10:03:00"):
            session.add(SkillRead(
                skill_id="gamma-skill", session_id="sess-M",
                file_path="~/.claude/skills/gamma-skill/SKILL.md",
                read_at=ts,
            ))
        session.commit()

    body = flask_client.get("/api/plans/multi.md/mentions").get_json()
    assert len(body["skills"]) == 1
    assert body["skills"][0]["last_read_at"] == "2026-04-22 10:05:00"


def test_api_plan_mentions_unions_skills_across_authoring_sessions(
        flask_client, isolated_plans_dir, tmp_db):
    """A plan edited by multiple sessions returns the union of skills
    each session read. The plan_sessions table is the contract; the
    endpoint joins on whichever sessions wrote/edited the plan."""
    (isolated_plans_dir / "shared.md").write_text("# Shared\n")
    with SessionLocal() as session:
        session.add(PlanSession(
            session_id="sess-X", plan_filename="shared.md",
            started_at="2026-04-22 10:00:00",
        ))
        session.add(PlanSession(
            session_id="sess-Y", plan_filename="shared.md",
            started_at="2026-04-22 12:00:00",
        ))
        session.add(SkillRead(
            skill_id="alpha", session_id="sess-X",
            file_path="~/.claude/skills/alpha/SKILL.md",
            read_at="2026-04-22 10:30:00",
        ))
        session.add(SkillRead(
            skill_id="beta", session_id="sess-Y",
            file_path="~/.claude/skills/beta/SKILL.md",
            read_at="2026-04-22 12:30:00",
        ))
        session.commit()

    body = flask_client.get("/api/plans/shared.md/mentions").get_json()
    ids = {s["skill_id"] for s in body["skills"]}
    assert ids == {"alpha", "beta"}


def test_api_plan_mentions_ignores_skill_reads_with_found_zero(
        flask_client, isolated_plans_dir, tmp_db):
    """`skill_reads.found = 0` rows record a Read of a file that
    *looked* like a skill SKILL.md path but didn't match a registered
    skill. They shouldn't be attributed."""
    (isolated_plans_dir / "f.md").write_text("# F\n")
    with SessionLocal() as session:
        session.add(PlanSession(
            session_id="sess-F", plan_filename="f.md",
            started_at="2026-04-22 10:00:00",
        ))
        session.add(SkillRead(
            skill_id="ghost-skill", session_id="sess-F",
            file_path="~/.claude/skills/ghost-skill/SKILL.md",
            read_at="2026-04-22 10:30:00", found=0,
        ))
        session.commit()

    body = flask_client.get("/api/plans/f.md/mentions").get_json()
    assert body["skills"] == []


def test_api_plan_mentions_substring_text_match_no_longer_applies(
        flask_client, isolated_plans_dir, tmp_db):
    """Old heuristic: any skill id appearing in the plan text was
    flagged. New behaviour: text content is ignored — only session
    attribution counts. Without a PlanSession row, even a literal
    `init` mention returns []."""
    (isolated_plans_dir / "mention.md").write_text(
        "# Plan\n\nReferences /init and /topic-router and /register-pattern.\n"
    )
    body = flask_client.get("/api/plans/mention.md/mentions").get_json()
    assert body["skills"] == []


def test_api_plan_mentions_missing_plan_returns_404(
        flask_client, isolated_plans_dir):
    resp = flask_client.get("/api/plans/nope.md/mentions")
    assert resp.status_code == 404


# ── POST /api/plan-sessions ──────────────────────────────────

def test_ingest_plan_session_enter_inserts_row(flask_client, tmp_db):
    resp = flask_client.post("/api/plan-sessions", json={
        "event": "enter",
        "session_id": "sess-a",
        "plan_filename": "foo.md",
        "started_at": "2026-04-22 10:00:00",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["skipped_duplicate"] is False

    with SessionLocal() as session:
        rows = session.exec(
            select(PlanSession).where(PlanSession.session_id == "sess-a")
        ).all()
        assert len(rows) == 1


def test_ingest_plan_session_enter_is_idempotent(flask_client, tmp_db):
    payload = {
        "event": "enter",
        "session_id": "sess-b",
        "plan_filename": "foo.md",
        "started_at": "2026-04-22 10:00:00",
    }
    flask_client.post("/api/plan-sessions", json=payload)
    resp = flask_client.post("/api/plan-sessions", json=payload)
    assert resp.get_json()["skipped_duplicate"] is True

    with SessionLocal() as session:
        rows = session.exec(
            select(PlanSession).where(PlanSession.session_id == "sess-b")
        ).all()
        assert len(rows) == 1  # no dupe


def test_ingest_plan_session_enter_dedupes_on_session_plan_pair(
        flask_client, tmp_db):
    """Multiple edits to the same plan within one session collapse to a
    single PlanSession row — dedup is on (session_id, plan_filename),
    not on the exact (session_id, plan_filename, started_at) triple.
    Required so plan_trace's per-edit `enter` posts don't pile up rows.
    """
    flask_client.post("/api/plan-sessions", json={
        "event": "enter",
        "session_id": "sess-e",
        "plan_filename": "p.md",
        "started_at": "2026-04-22 10:00:00",
    })
    resp = flask_client.post("/api/plan-sessions", json={
        "event": "enter",
        "session_id": "sess-e",
        "plan_filename": "p.md",
        "started_at": "2026-04-22 10:05:00",  # different timestamp
    })
    assert resp.get_json()["skipped_duplicate"] is True

    with SessionLocal() as session:
        rows = session.exec(
            select(PlanSession).where(PlanSession.session_id == "sess-e")
        ).all()
        assert len(rows) == 1
        assert rows[0].started_at == "2026-04-22 10:00:00"  # first wins


def test_ingest_plan_session_draft_completed_updates_active_row(
        flask_client, tmp_db):
    flask_client.post("/api/plan-sessions", json={
        "event": "enter",
        "session_id": "sess-c",
        "plan_filename": "p.md",
        "started_at": "2026-04-22 10:00:00",
    })

    resp = flask_client.post("/api/plan-sessions", json={
        "event": "draft_completed",
        "session_id": "sess-c",
        "draft_completed_at": "2026-04-22 10:30:00",
        "review_started_at": "2026-04-22 10:35:00",
    })
    assert resp.status_code == 200

    with SessionLocal() as session:
        row = session.exec(
            select(PlanSession).where(PlanSession.session_id == "sess-c")
        ).first()
        assert row.draft_completed_at == "2026-04-22 10:30:00"
        assert row.review_started_at == "2026-04-22 10:35:00"


def test_ingest_plan_session_exit_closes_active_row(
        flask_client, tmp_db):
    flask_client.post("/api/plan-sessions", json={
        "event": "enter",
        "session_id": "sess-d",
        "plan_filename": "p.md",
        "started_at": "2026-04-22 10:00:00",
    })
    resp = flask_client.post("/api/plan-sessions", json={
        "event": "exit",
        "session_id": "sess-d",
        "ended_at": "2026-04-22 11:00:00",
    })
    assert resp.status_code == 200

    with SessionLocal() as session:
        row = session.exec(
            select(PlanSession).where(PlanSession.session_id == "sess-d")
        ).first()
        assert row.ended_at == "2026-04-22 11:00:00"


# ── GET /api/plan-sessions ───────────────────────────────────

def test_list_plan_sessions_returns_keyset_envelope(
        flask_client, tmp_db):
    with SessionLocal() as session:
        for i in range(3):
            session.add(PlanSession(
                session_id=f"s{i}", plan_filename="p.md",
                started_at=f"2026-04-22 10:0{i}:00",
            ))
        session.commit()

    resp = flask_client.get("/api/plan-sessions?size=50")
    assert resp.status_code == 200
    body = resp.get_json()
    # Envelope shape: items + paging fields from keyset_page_stmt.
    assert "items" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 3
    # Newest first.
    assert body["items"][0]["started_at"] >= body["items"][-1]["started_at"]


def test_list_plan_sessions_filters_by_plan(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(PlanSession(
            session_id="s1", plan_filename="alpha.md",
            started_at="2026-04-22 10:00:00",
        ))
        session.add(PlanSession(
            session_id="s2", plan_filename="beta.md",
            started_at="2026-04-22 10:01:00",
        ))
        session.commit()

    resp = flask_client.get("/api/plan-sessions?plan=alpha.md")
    body = resp.get_json()
    filenames = {row["plan_filename"] for row in body["items"]}
    assert filenames == {"alpha.md"}


def test_list_plan_sessions_active_only_filter(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(PlanSession(
            session_id="open", plan_filename="p.md",
            started_at="2026-04-22 10:00:00", ended_at=None,
        ))
        session.add(PlanSession(
            session_id="closed", plan_filename="p.md",
            started_at="2026-04-22 10:01:00",
            ended_at="2026-04-22 10:02:00",
        ))
        session.commit()

    resp = flask_client.get("/api/plan-sessions?active=1")
    body = resp.get_json()
    assert body["only_active"] is True
    assert {r["session_id"] for r in body["items"]} == {"open"}
