"""Unit tests for web.blueprints.experiments JSON API.

Covers list/detail/create/edit/activate/deactivate/delete flows
against tmp_db. skill_sync.push is stubbed to keep tests isolated
from the real deploy pipeline.
"""

from __future__ import annotations

import pytest

from lib import experiments
from lib.auth import create_token


def _editor_auth():
    token = create_token(1, "editor-tester", "editor")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def stub_deploy(monkeypatch):
    """Keep activate/deactivate/edit from hitting real deploy pipeline."""
    from lib.skills import skill_sync, skill_registry
    monkeypatch.setattr(skill_registry, "skill_id_for_procedure",
                        lambda _slug: None)
    monkeypatch.setattr(skill_sync, "push",
                        lambda *a, **kw: "pushed stub")


# ── GET /api/experiments ─────────────────────────────────────

def test_api_experiments_empty(flask_client, tmp_db):
    resp = flask_client.get("/api/experiments")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 0
    assert body["grouped"] == []


def test_api_experiments_groups_by_pattern(flask_client, tmp_db):
    experiments.create("alpha-slug", "exp-a", ["Disciplines"])
    experiments.create("alpha-slug", "exp-b", ["Anti-Patterns"])
    experiments.create("beta-slug", "exp-c", ["Disciplines"])

    resp = flask_client.get("/api/experiments")
    body = resp.get_json()
    assert body["total"] == 3
    grouped = dict(body["grouped"])
    assert len(grouped["alpha-slug"]) == 2
    assert len(grouped["beta-slug"]) == 1


# ── GET /api/experiments/<id> ───────────────────────────────

def test_api_experiment_detail_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.get("/api/experiments/999")
    assert resp.status_code == 404


def test_api_experiment_detail_no_rules_returns_zero_rollup(
        flask_client, tmp_db, monkeypatch):
    from web.blueprints import experiments as exp_bp
    monkeypatch.setattr(exp_bp, "rules_for_guide",
                        lambda _slug: [])
    eid = experiments.create("p", "exp", ["Disciplines"])

    resp = flask_client.get(f"/api/experiments/{eid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["exp"]["name"] == "exp"
    assert body["baseline"] == {"sessions": 0, "checks": 0, "fired": 0,
                                  "rate": None}
    assert body["experiment"] == {"sessions": 0, "checks": 0, "fired": 0,
                                    "rate": None}
    assert body["per_rule"] == []


def test_api_experiment_detail_populates_per_rule(
        flask_client, tmp_db, monkeypatch):
    from web.blueprints import experiments as exp_bp
    monkeypatch.setattr(exp_bp, "rules_for_guide",
                        lambda _slug: [{"id": "rule-a"}, {"id": "rule-b"}])
    eid = experiments.create("p", "exp", ["Disciplines"])

    resp = flask_client.get(f"/api/experiments/{eid}")
    body = resp.get_json()
    rule_ids = {r["rule_id"] for r in body["per_rule"]}
    assert rule_ids == {"rule-a", "rule-b"}
    # No RuleTrigger rows → all counts zero.
    for r in body["per_rule"]:
        assert r["baseline_checks"] == 0
        assert r["experiment_checks"] == 0


# ── POST /api/experiments (create) ──────────────────────────

def test_create_requires_auth(anon_client, tmp_db):
    resp = anon_client.post("/api/experiments",
                               json={"pattern_slug": "p",
                                     "name": "e",
                                     "sections": ["x"]})
    assert resp.status_code == 401


def test_create_requires_name_and_sections(flask_client, tmp_db):
    resp = flask_client.post("/api/experiments",
                               json={"pattern_slug": "p"},
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is False
    assert "required" in body["msg"]


def test_create_success(flask_client, tmp_db):
    resp = flask_client.post(
        "/api/experiments",
        json={"pattern_slug": "p", "name": "new-exp",
              "sections": ["Disciplines"]},
        headers=_editor_auth(),
    )
    body = resp.get_json()
    assert body["ok"] is True
    assert "new-exp" in body["msg"]

    # Persisted.
    rows = experiments.list_all()
    assert any(r["name"] == "new-exp" for r in rows)


# ── POST /api/experiments/<id>/edit ─────────────────────────

def test_edit_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.post("/api/experiments/999/edit",
                               json={"name": "x",
                                     "sections": ["y"]})
    assert resp.status_code == 404


def test_edit_missing_fields(flask_client, tmp_db):
    eid = experiments.create("p", "e", ["a"])
    resp = flask_client.post(f"/api/experiments/{eid}/edit",
                               json={})
    body = resp.get_json()
    assert body["ok"] is False


def test_edit_inactive_experiment_no_deploy(
        flask_client, tmp_db, stub_deploy):
    eid = experiments.create("p", "e", ["a"])
    resp = flask_client.post(
        f"/api/experiments/{eid}/edit",
        json={"name": "renamed", "sections": ["b"]},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    # Not active → deploy_msg empty → "(idle)".
    assert "idle" in body["msg"]


def test_edit_active_experiment_triggers_redeploy(
        flask_client, tmp_db, monkeypatch):
    eid = experiments.create("slug-x", "e", ["a"])
    experiments.activate(eid)

    # Force the deploy branch to fire.
    from lib.skills import skill_registry, skill_sync
    monkeypatch.setattr(skill_registry, "skill_id_for_procedure",
                        lambda slug: "slug-x")
    calls = []
    monkeypatch.setattr(
        skill_sync, "push",
        lambda sid, force=False: calls.append(sid) or "pushed x",
    )

    resp = flask_client.post(
        f"/api/experiments/{eid}/edit",
        json={"name": "renamed", "sections": ["b"]},
    )
    body = resp.get_json()
    assert body["ok"] is True
    assert calls == ["slug-x"]
    assert "pushed x" in body["msg"]


# ── POST /api/experiments/<id>/activate ─────────────────────

def test_activate_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.post("/api/experiments/999/activate")
    assert resp.status_code == 404


def test_activate_success(flask_client, tmp_db, stub_deploy):
    eid = experiments.create("p", "e", ["a"])
    resp = flask_client.post(f"/api/experiments/{eid}/activate")
    assert resp.status_code == 200
    assert experiments.get(eid)["active"] == 1


# ── POST /api/experiments/<id>/deactivate ───────────────────

def test_deactivate_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.post("/api/experiments/999/deactivate")
    assert resp.status_code == 404


def test_deactivate_success(flask_client, tmp_db, stub_deploy):
    eid = experiments.create("p", "e", ["a"])
    experiments.activate(eid)
    resp = flask_client.post(f"/api/experiments/{eid}/deactivate")
    assert resp.status_code == 200
    assert experiments.get(eid)["active"] == 0


# ── POST /api/experiments/<id>/delete ───────────────────────

def test_delete_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.post("/api/experiments/999/delete")
    assert resp.status_code == 404


def test_delete_success(flask_client, tmp_db):
    eid = experiments.create("p", "gonna-go", ["a"])
    resp = flask_client.post(f"/api/experiments/{eid}/delete")
    body = resp.get_json()
    assert body["ok"] is True
    assert "gonna-go" in body["msg"]
    assert experiments.get(eid) is None
