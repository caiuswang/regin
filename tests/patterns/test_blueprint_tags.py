"""Unit tests for web.blueprints.tags JSON API.

Covers list, detail, rename, and delete endpoints. Uses flask_client
(wired to tmp_db via conftest) so no real users or DB data are touched.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag


def _editor_auth() -> dict:
    """Bearer header for an editor — rename/delete are @require_editor."""
    token = create_token(1, "editor-tester", "editor")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def seeded_tags(tmp_db):
    """Seed a couple of tags + one pattern with linked tags."""
    with SessionLocal() as session:
        t1 = Tag(name="t-one", category="layer", description="first")
        t2 = Tag(name="t-two", category="concept", description=None)
        t3 = Tag(name="t-solo", category="concept", description=None)
        session.add_all([t1, t2, t3])
        session.flush()

        doc = PatternDoc(
            slug="p1", title="Pattern 1",
            file_path="p1/SKILL.md", category="procedure",
            content_hash="0" * 64,
        )
        session.add(doc)
        session.flush()
        session.add(DocTag(doc_id=doc.id, tag_id=t1.id))
        session.commit()
    return {"linked_tag": "t-one", "lone_tag": "t-solo",
            "pattern_slug": "p1"}


# ── GET /api/tags ────────────────────────────────────────────

def test_api_tags_list_returns_all(flask_client, seeded_tags):
    resp = flask_client.get("/api/tags")
    assert resp.status_code == 200
    payload = resp.get_json()
    names = {t["name"] for t in payload}
    assert {"t-one", "t-two", "t-solo"} <= names

    t_one = next(t for t in payload if t["name"] == "t-one")
    assert t_one["doc_count"] == 1
    t_two = next(t for t in payload if t["name"] == "t-two")
    assert t_two["doc_count"] == 0


# ── GET /api/tags/<name> ─────────────────────────────────────

def test_api_tag_detail_with_patterns(flask_client, seeded_tags):
    resp = flask_client.get(f"/api/tags/{seeded_tags['linked_tag']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tag"]["name"] == "t-one"
    assert body["tag"]["category"] == "layer"
    assert len(body["docs"]) == 1
    assert body["docs"][0]["slug"] == seeded_tags["pattern_slug"]


def test_api_tag_detail_no_patterns(flask_client, seeded_tags):
    resp = flask_client.get(f"/api/tags/{seeded_tags['lone_tag']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tag"]["name"] == "t-solo"
    assert body["docs"] == []


def test_api_tag_detail_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.get("/api/tags/nonexistent")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


# ── POST /api/tags/<name>/rename ─────────────────────────────

def test_api_tag_rename_success(flask_client, seeded_tags):
    resp = flask_client.post(
        f"/api/tags/{seeded_tags['lone_tag']}/rename",
        json={"name": "renamed-tag"},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    assert resp.get_json() == {
        "ok": True, "msg": "Renamed", "new_name": "renamed-tag",
    }

    # Persisted.
    with SessionLocal() as session:
        row = session.exec(
            select(Tag).where(Tag.name == "renamed-tag")
        ).first()
        assert row is not None


def test_api_tag_rename_empty_name_fails(flask_client, seeded_tags):
    resp = flask_client.post(
        f"/api/tags/{seeded_tags['lone_tag']}/rename",
        json={"name": "   "},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False
    assert "required" in body["msg"].lower()


def test_api_tag_rename_unknown_source(flask_client, tmp_db):
    resp = flask_client.post(
        "/api/tags/does-not-exist/rename",
        json={"name": "something"},
        headers=_editor_auth(),
    )
    assert resp.status_code == 404


def test_api_tag_rename_collision_rejected(flask_client, seeded_tags):
    resp = flask_client.post(
        f"/api/tags/{seeded_tags['lone_tag']}/rename",
        json={"name": "t-one"},  # already exists
        headers=_editor_auth(),
    )
    body = resp.get_json()
    assert body["ok"] is False
    assert "already exists" in body["msg"]


# ── POST /api/tags/<name>/delete ─────────────────────────────

def test_api_tag_delete_unused(flask_client, seeded_tags):
    resp = flask_client.post(f"/api/tags/{seeded_tags['lone_tag']}/delete",
                             headers=_editor_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True

    # Removed from DB.
    with SessionLocal() as session:
        row = session.exec(
            select(Tag).where(Tag.name == seeded_tags["lone_tag"])
        ).first()
        assert row is None


def test_api_tag_delete_in_use_rejected(flask_client, seeded_tags):
    resp = flask_client.post(f"/api/tags/{seeded_tags['linked_tag']}/delete",
                             headers=_editor_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False
    assert "associated patterns" in body["msg"]

    # Tag still in DB.
    with SessionLocal() as session:
        row = session.exec(
            select(Tag).where(Tag.name == seeded_tags["linked_tag"])
        ).first()
        assert row is not None


def test_api_tag_delete_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.post("/api/tags/nope/delete", headers=_editor_auth())
    assert resp.status_code == 404
