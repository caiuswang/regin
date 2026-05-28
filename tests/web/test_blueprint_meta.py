"""Unit tests for web.blueprints.meta JSON API.

Covers /api/status, /api/doctor, /api/dashboard, /api/repos/<name>.
All endpoints are unauthenticated.
"""

from __future__ import annotations

import pytest

from lib.orm import SessionLocal
from lib.orm.models import Branch, PatternDeployment, PatternDoc, Repo, Tag


@pytest.fixture
def stubbed_deps(monkeypatch):
    """Replace heavy external deps with no-op stubs at the blueprint binding."""
    from web.blueprints import meta as meta_bp
    from lib import settings as settings_mod
    monkeypatch.setattr(meta_bp, "_ss", type("X", (), {
        "list_states": staticmethod(lambda: iter([])),
    })())
    monkeypatch.setattr(meta_bp, "load_rules_index",
                        lambda: {"rules": []})
    monkeypatch.setattr(meta_bp, "run_checks", lambda: {"ok": True})
    monkeypatch.setattr(meta_bp, "search_patterns",
                        lambda q: [{"slug": "matched", "title": f"for {q}"}])
    # Block auto-discovery of bundles from the user's real patterns dir so
    # tests don't see engine-rule counts from the developer's installed
    # frontend-style-convention bundle.
    monkeypatch.setattr(settings_mod.settings, "bundle_autoload", False)
    # Likewise neutralise any non-grit rule engine the developer has
    # configured (e.g. radon for python) — its rules would otherwise leak
    # into the dashboard's rules.total.
    monkeypatch.setattr(meta_bp.rule_engines, "all_engines", lambda: [])


def _seed_repo(name: str = "r1", with_branch: bool = True) -> int:
    with SessionLocal() as session:
        repo = Repo(name=name, path=f"/{name}",
                     default_branch="main", is_active=1)
        session.add(repo)
        session.flush()
        rid = repo.id
        if with_branch:
            session.add(Branch(repo_id=rid, name="main", is_tracked=1))
        session.commit()
    return rid


# ── GET /api/status ──────────────────────────────────────────

def test_providers_returns_active_and_capabilities(flask_client, tmp_db):
    resp = flask_client.get("/api/providers")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body.get("active_provider"), str)
    providers = body.get("providers")
    assert isinstance(providers, list) and providers
    assert any(p.get("id") == body["active_provider"] for p in providers)
    for p in providers:
        assert {"id", "name", "active", "capabilities"} <= set(p.keys())

def test_status_empty_when_no_repos(flask_client, tmp_db):
    resp = flask_client.get("/api/status")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_status_returns_repo_branch_rows(flask_client, tmp_db):
    _seed_repo("active-repo")
    resp = flask_client.get("/api/status")
    body = resp.get_json()
    assert len(body) == 1
    row = body[0]
    assert row["name"] == "active-repo"
    assert row["branch"] == "main"
    assert row["patterns"] == 0


def test_status_skips_inactive_repos(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(Repo(name="inactive", path="/i",
                         default_branch="main", is_active=0))
        session.commit()
    resp = flask_client.get("/api/status")
    assert resp.get_json() == []


# ── GET /api/doctor ──────────────────────────────────────────

def test_doctor_returns_run_checks_output(
        flask_client, tmp_db, stubbed_deps):
    resp = flask_client.get("/api/doctor")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


# ── GET /api/dashboard ───────────────────────────────────────

def test_dashboard_returns_stats_envelope(
        flask_client, tmp_db, stubbed_deps):
    alpha_id = _seed_repo("alpha")

    with SessionLocal() as session:
        session.add(PatternDoc(
            slug="pat1", title="P1",
            file_path="pat1/SKILL.md", category="procedure",
            content_hash="0" * 64,
        ))
        session.add(PatternDeployment(
            pattern_slug="pat1", scope="project", project_id=alpha_id,
            deployed_path="/alpha/.claude/skills/pat1",
        ))
        session.add(Tag(name="extra-tag", category="concept"))
        session.commit()

    resp = flask_client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.get_json()

    # Required top-level keys.
    assert {"repos", "stats"} <= set(body.keys())

    # Repos list.
    assert len(body["repos"]) == 1
    assert body["repos"][0]["pattern_count"] == 1

    # Stats bundle shape.
    stats = body["stats"]
    assert stats["total_repos"] == 1
    assert stats["total_patterns"] == 1
    assert stats["skills"]["total"] == 0
    assert stats["rules"]["total"] == 0


# ── GET /api/repos/<name> ───────────────────────────────────

def test_repo_detail_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.get("/api/repos/unknown")
    assert resp.status_code == 404


def test_repo_detail_returns_envelope(flask_client, tmp_db):
    detail_id = _seed_repo("repo-detail")
    with SessionLocal() as session:
        session.add(PatternDoc(
            slug="p1", title="P1",
            file_path="p1/SKILL.md", category="procedure",
            content_hash="0" * 64,
        ))
        session.add(PatternDeployment(
            pattern_slug="p1", scope="project", project_id=detail_id,
            deployed_path="/repo-detail/.claude/skills/p1",
        ))
        session.commit()

    resp = flask_client.get("/api/repos/repo-detail")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["repo"]["name"] == "repo-detail"
    assert len(body["branches"]) == 1
    assert len(body["patterns"]) == 1
    assert body["patterns"][0]["slug"] == "p1"
