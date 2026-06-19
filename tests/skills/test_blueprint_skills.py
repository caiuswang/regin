"""Unit tests for web.blueprints.skills JSON API.

Covers listing, detail, require_known_skill guard, pull/push/undeploy,
project-deployment lifecycle, repo list endpoint, and auto-skill
regeneration. Heavy underlying modules (skill_sync, skill_deployer,
grit_rule_index) are monkeypatched at the blueprint binding level so
tests don't touch the user's real skill tree.
"""

from __future__ import annotations

import pytest

from lib.auth import create_token, register_user
from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.settings import settings


def _editor_auth():
    token = create_token(1, "editor-tester", "editor")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect PATTERNS_DIR + SKILLS_DIR so registry walks stay local."""
    patterns = tmp_path / "patterns"
    skills = tmp_path / "skills"
    patterns.mkdir()
    skills.mkdir()
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "skills_dir", str(skills))
    # Some callers import SKILLS_DIR via lib.skills.skill_deployer too.
    monkeypatch.setattr(settings, "skills_dir", str(skills))
    return {"patterns": patterns, "skills": skills}


def _seed_pattern(patterns_dir, slug, body="body"):
    d = patterns_dir / slug
    d.mkdir()
    (d / "SKILL.md").write_text(
        f'---\ntitle: "{slug.title()}"\nprocedure: {slug}\n---\n{body}'
    )


# ── GET /api/skills ─────────────────────────────────────────

def test_api_skills_returns_rows_and_totals(
        flask_client, tmp_db, isolated_dirs, monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "example-pat")

    # Stub skill_sync.list_states to return a deterministic row.
    from lib.skills import skill_sync
    monkeypatch.setattr(
        skill_sync, "list_states",
        lambda: iter([
            ("example-pat", "pattern",
             str(isolated_dirs["patterns"] / "example-pat"),
             str(isolated_dirs["skills"] / "example-pat"),
             skill_sync.STATE_SOURCE_ONLY),
        ]),
    )
    resp = flask_client.get("/api/skills")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 1
    assert body["drift_count"] == 1  # not in_sync → drift
    assert body["rows"][0]["id"] == "example-pat"
    assert body["rows"][0]["href"] == "/patterns/example-pat"
    assert body["by_type"]["pattern"][0]["id"] == "example-pat"
    assert body["provider"]["id"] == "claude"
    assert body["provider"]["project_subpath"] == ".claude/skills"


# ── require_known_skill guard ───────────────────────────────

def test_unknown_skill_returns_404(flask_client, tmp_db, isolated_dirs):
    resp = flask_client.get("/api/skills/does-not-exist")
    assert resp.status_code == 404


def test_known_skill_pattern_redirects_to_patterns(
        flask_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "my-pat")
    resp = flask_client.get("/api/skills/my-pat")
    assert resp.status_code == 200
    assert resp.get_json()["redirect"] == "/patterns/my-pat"


def test_known_auto_skill_returns_detail(
        configured_grit_engine, flask_client, tmp_db, isolated_dirs, monkeypatch):
    from lib.skills import skill_sync
    monkeypatch.setattr(skill_sync, "state",
                        lambda _id: skill_sync.STATE_IN_SYNC)

    # Pre-deploy a content.md so the body_md preview path gets hit.
    auto_dir = isolated_dirs["skills"] / "grit-rules"
    auto_dir.mkdir()
    (auto_dir / "SKILL.md").write_text("---\nname: grit-rules\n---\n")
    (auto_dir / "content.md").write_text("## Rules\n\n- r1\n")

    resp = flask_client.get("/api/skills/grit-rules")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["skill_id"] == "grit-rules"
    assert body["entry"]["type"] == "auto"
    assert "## Rules" in body["body_md"]
    assert body["provider"]["id"] == "claude"
    assert body["provider"]["project_subpath"] == ".claude/skills"


# ── POST /api/skills/<id>/pull ──────────────────────────────

def test_pull_requires_auth(anon_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = anon_client.post("/api/skills/p/pull")
    assert resp.status_code == 401


def test_pull_success_result(flask_client, tmp_db, isolated_dirs,
                                monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    from lib.skills import skill_sync
    monkeypatch.setattr(skill_sync, "pull",
                        lambda _id: "pulled pattern p -> /src")
    resp = flask_client.post("/api/skills/p/pull", headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True
    assert "pulled" in body["msg"]


def test_pull_refused_is_not_ok(flask_client, tmp_db, isolated_dirs,
                                   monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    from lib.skills import skill_sync
    monkeypatch.setattr(skill_sync, "pull",
                        lambda _id: "refused: auto-generated")
    resp = flask_client.post("/api/skills/p/pull", headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is False


# ── POST /api/skills/<id>/push ──────────────────────────────

def test_push_confirm_force_response(flask_client, tmp_db, isolated_dirs,
                                        monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    from lib.skills import skill_sync
    monkeypatch.setattr(
        skill_sync, "push",
        lambda _id, force=False: "confirm-force: drifted",
    )
    resp = flask_client.post("/api/skills/p/push", json={"force": False},
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is False
    assert body["confirm_force"] is True


def test_push_success_records_audit(flask_client, tmp_db, isolated_dirs,
                                       monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    register_user("editor-tester", "E", "pw12")

    from lib.skills import skill_sync
    monkeypatch.setattr(
        skill_sync, "push",
        lambda _id, force=False: "pushed pattern p -> /x",
    )
    # No-op the deployment and audit writes so tests don't require
    # additional schema.
    from lib import audit
    from lib.patterns import pattern_deployments
    monkeypatch.setattr(pattern_deployments, "record_deployment",
                        lambda *a, **kw: None)
    monkeypatch.setattr(audit, "log_action", lambda *a, **kw: None)

    resp = flask_client.post("/api/skills/p/push", json={},
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True


# ── POST /api/skills/<id>/push-to-project ───────────────────

def test_push_to_project_rejects_auto_skill(configured_grit_engine, flask_client, tmp_db, isolated_dirs):
    resp = flask_client.post(
        "/api/skills/grit-rules/push-to-project",
        json={"project_id": 1},
        headers=_editor_auth(),
    )
    body = resp.get_json()
    assert body["ok"] is False
    assert "auto skill" in body["msg"]


def test_push_to_project_requires_project_id(
        flask_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = flask_client.post(
        "/api/skills/p/push-to-project",
        json={},
        headers=_editor_auth(),
    )
    assert resp.status_code == 400


def test_push_to_project_unknown_project_404(
        flask_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = flask_client.post(
        "/api/skills/p/push-to-project",
        json={"project_id": 999},
        headers=_editor_auth(),
    )
    assert resp.status_code == 404


def test_push_to_project_success(flask_client, tmp_db, isolated_dirs,
                                    tmp_path, monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    with SessionLocal() as session:
        repo = Repo(name="proj", path=str(tmp_path / "proj"),
                     default_branch="main", is_active=1)
        session.add(repo)
        session.commit()
        rid = repo.id

    from lib import audit
    from lib.patterns import pattern_deployments
    from lib.skills import skill_sync
    monkeypatch.setattr(
        skill_sync, "push",
        lambda _id, force=False, target_dir=None: "pushed pattern p -> /x",
    )
    monkeypatch.setattr(pattern_deployments, "record_deployment",
                        lambda *a, **kw: None)
    monkeypatch.setattr(audit, "log_action", lambda *a, **kw: None)

    resp = flask_client.post(
        "/api/skills/p/push-to-project",
        json={"project_id": rid},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True


# ── GET /api/skills/<id>/deployments ────────────────────────

def test_skills_deployments_list_tags_tracked(flask_client, tmp_db,
                                              isolated_dirs, monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    from lib.patterns import pattern_deployments
    monkeypatch.setattr(
        pattern_deployments, "list_deployments",
        lambda pattern_slug=None: [
            {"scope": "project", "project_id": 1, "project_name": "x"}],
    )
    monkeypatch.setattr(pattern_deployments,
                        "untracked_project_deployments", lambda slug: [])
    body = flask_client.get("/api/skills/p/deployments").get_json()
    assert body["deployments"] == [
        {"scope": "project", "project_id": 1, "project_name": "x",
         "tracked": True}]


def test_skills_deployments_merges_untracked(flask_client, tmp_db,
                                             isolated_dirs, monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    from lib.patterns import pattern_deployments
    monkeypatch.setattr(pattern_deployments, "list_deployments",
                        lambda pattern_slug=None: [])
    untracked = {"scope": "project", "project_id": 7, "project_name": "proj",
                 "tracked": False, "id": "untracked:7"}
    monkeypatch.setattr(pattern_deployments,
                        "untracked_project_deployments",
                        lambda slug: [untracked])
    body = flask_client.get("/api/skills/p/deployments").get_json()
    assert body["deployments"] == [untracked]


# ── POST /api/skills/<id>/backfill-deployment ───────────────

def test_backfill_requires_editor(anon_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = anon_client.post("/api/skills/p/backfill-deployment",
                            json={"project_id": 1})
    assert resp.status_code == 401


def test_backfill_requires_project_id(flask_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = flask_client.post("/api/skills/p/backfill-deployment",
                             json={}, headers=_editor_auth())
    assert resp.status_code == 400


def test_backfill_unknown_project_404(flask_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = flask_client.post("/api/skills/p/backfill-deployment",
                             json={"project_id": 99999},
                             headers=_editor_auth())
    assert resp.status_code == 404


def test_backfill_rejects_when_not_on_disk(
        flask_client, tmp_db, isolated_dirs, tmp_path):
    _seed_pattern(isolated_dirs["patterns"], "p")
    with SessionLocal() as session:
        repo = Repo(name="proj3", path=str(tmp_path / "proj3"),
                     default_branch="main", is_active=1)
        session.add(repo)
        session.commit()
        rid = repo.id

    resp = flask_client.post("/api/skills/p/backfill-deployment",
                             json={"project_id": rid},
                             headers=_editor_auth())
    assert resp.status_code == 400
    assert "not deployed" in resp.get_json()["msg"]


def test_backfill_records_when_on_disk(
        flask_client, tmp_db, isolated_dirs, tmp_path, monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    repo_path = tmp_path / "proj4"
    with SessionLocal() as session:
        repo = Repo(name="proj4", path=str(repo_path),
                     default_branch="main", is_active=1)
        session.add(repo)
        session.commit()
        rid = repo.id

    # Place the skill dir on disk at the active provider's project subpath.
    from lib.providers import get_active_provider
    skill_dir = repo_path.joinpath(*get_active_provider().project_skills_subpath(), "p")
    skill_dir.mkdir(parents=True)

    from lib import audit
    monkeypatch.setattr(audit, "log_action", lambda *a, **kw: None)

    resp = flask_client.post("/api/skills/p/backfill-deployment",
                             json={"project_id": rid},
                             headers=_editor_auth())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    from lib.patterns import pattern_deployments
    rows = pattern_deployments.list_deployments(pattern_slug="p")
    assert any(r["scope"] == "project" and r["project_id"] == rid
               for r in rows)


# ── DELETE /api/skills/<id>/project-deployment/<project_id> ──

def test_remove_project_deployment_unknown_project(
        flask_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = flask_client.delete(
        "/api/skills/p/project-deployment/99999",
        headers=_editor_auth(),
    )
    assert resp.status_code == 404


def test_remove_project_deployment_success(
        flask_client, tmp_db, isolated_dirs, tmp_path, monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    with SessionLocal() as session:
        repo = Repo(name="proj2", path=str(tmp_path / "proj2"),
                     default_branch="main", is_active=1)
        session.add(repo)
        session.commit()
        rid = repo.id

    from web.blueprints import skills as skills_bp
    monkeypatch.setattr(skills_bp, "undeploy_skill",
                        lambda _id, target_dir=None: True)
    from lib import audit
    from lib.patterns import pattern_deployments
    monkeypatch.setattr(pattern_deployments, "remove_deployment",
                        lambda *a, **kw: True)
    monkeypatch.setattr(audit, "log_action", lambda *a, **kw: None)

    resp = flask_client.delete(
        f"/api/skills/p/project-deployment/{rid}",
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "proj2" in body["msg"]


# ── GET /api/repos ──────────────────────────────────────────

# GET /api/repos covered by tests/test_blueprint_repos.py — the
# legacy skills-side endpoint was folded into the new repos blueprint.


# ── GET /api/pattern-deployments ────────────────────────────

def test_pattern_deployments_returns_list(
        flask_client, tmp_db, monkeypatch):
    from lib.patterns import pattern_deployments
    monkeypatch.setattr(pattern_deployments, "list_deployments",
                        lambda: [{"x": 1}])
    resp = flask_client.get("/api/pattern-deployments")
    body = resp.get_json()
    assert body == {"deployments": [{"x": 1}]}


# ── POST /api/skills/<id>/undeploy ──────────────────────────

def test_undeploy_requires_editor(anon_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = anon_client.post("/api/skills/p/undeploy")
    assert resp.status_code == 401


def test_undeploy_success(flask_client, tmp_db, isolated_dirs,
                             monkeypatch):
    _seed_pattern(isolated_dirs["patterns"], "p")
    from lib.skills import skill_sync
    monkeypatch.setattr(
        skill_sync, "undeploy",
        lambda _id, target_dir=None, provider_id=None, disable_linked_rules=True: "removed p",
    )
    resp = flask_client.post("/api/skills/p/undeploy",
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True
    assert "removed" in body["msg"]


# ── POST /api/skills/<id>/regenerate ────────────────────────

def test_regenerate_rejects_pattern_skill(
        flask_client, tmp_db, isolated_dirs):
    _seed_pattern(isolated_dirs["patterns"], "p")
    resp = flask_client.post("/api/skills/p/regenerate")
    body = resp.get_json()
    assert body["ok"] is False
    assert "only auto" in body["msg"]


def test_regenerate_auto_skill_runs_pipeline(configured_grit_engine, flask_client, tmp_db, isolated_dirs, monkeypatch):
    # Stub the expensive regenerate + deploy chain.
    from lib.rules import grit_rule_index
    from web.blueprints import skills as skills_bp
    monkeypatch.setattr(
        grit_rule_index, "regenerate",
        lambda write_guides=True: {"rules": 5, "rules_md": "/tmp/RULES.md"},
    )
    monkeypatch.setattr(
        skills_bp, "deploy_rules_index_skill",
        lambda _path: "/tmp/skill",
    )
    resp = flask_client.post("/api/skills/grit-rules/regenerate")
    body = resp.get_json()
    assert body["ok"] is True
    assert "5 rules" in body["msg"]
