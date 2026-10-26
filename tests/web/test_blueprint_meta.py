"""Unit tests for web.blueprints.meta JSON API.

Covers /api/status, /api/doctor, /api/repos/<name>.
All endpoints are unauthenticated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.orm import SessionLocal
from lib.orm.models import Branch, PatternDeployment, PatternDoc, Repo


@pytest.fixture
def stubbed_deps(monkeypatch):
    from web.blueprints import meta as meta_bp
    monkeypatch.setattr(meta_bp, "run_checks", lambda: {"ok": True})


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
    assert {"id", "name", "global_dir", "project_subpath"} <= set(body.get("skill_paths", {}).keys())

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


# ── Retired Dashboard (F182) ─────────────────────────────────

def test_api_dashboard_is_gone(flask_client, tmp_db):
    assert flask_client.get("/api/dashboard").status_code == 404


def test_no_dashboard_references():
    root = Path(__file__).resolve().parents[2]
    needle = re.compile(r"""DashboardView|name:\s*['"]dashboard['"]|api/dashboard""")
    docs_site = root / "tests" / "docs"
    this_file = Path(__file__).resolve()
    hits = []
    for base in ("frontend/src", "web", "tests"):
        for path in (root / base).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".vue", ".ts", ".html"}:
                continue
            if path.resolve() == this_file or docs_site in path.parents:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits += [f"{path.relative_to(root)}:{n}" for n, line in enumerate(text.splitlines(), 1)
                     if needle.search(line)]
    assert hits == []


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
