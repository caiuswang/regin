"""Unit tests for web.blueprints.rules JSON API.

Covers /api/rules, /api/rules/<id>, /api/rules/<id>/delete + update,
/api/triggers (keyset), /api/triggers/reset, /api/rule-triggers
(ingest), /api/pattern-scripts.
"""

from __future__ import annotations

import pytest

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models import RuleTrigger
from lib.rule_engines.base import Rule
from lib.settings import settings


def _editor_auth():
    token = create_token(1, "editor-tester", "editor")
    return {"Authorization": f"Bearer {token}"}


def _admin_auth():
    token = create_token(1, "admin-tester", "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake_rules(monkeypatch):
    """Replace load_rules_index at both blueprint + lib bindings.

    The list/detail endpoints use the direct import
    (`from lib.rules.grit_rule_index import load_rules_index`) while
    delete/update call through the module namespace
    (`grit_rule_index.load_rules_index()`), so both need stubbing.
    """
    from web.blueprints import rules as rules_bp
    from lib.rules import grit_rule_index as gri
    rules_data = [
        {"id": "alpha_rule", "layer": "entity",
         "triggers": ["*Entity.java"], "severity": "error",
         "guide": "entity-pattern", "summary": "Entity summary",
         "source_file": ".grit/patterns/java/alpha.grit"},
        {"id": "beta_rule", "layer": "controller",
         "triggers": ["*Controller.java"], "severity": "warn",
         "guide": "rest-controller", "summary": "Controller summary",
         "source_file": ".grit/patterns/java/beta.grit"},
    ]
    payload = {
        "version": 1, "rules": rules_data,
        "by_guide": {
            "entity-pattern": ["alpha_rule"],
            "rest-controller": ["beta_rule"],
        },
    }
    monkeypatch.setattr(rules_bp, "load_rules_index", lambda: payload)
    monkeypatch.setattr(gri, "load_rules_index", lambda: payload)
    monkeypatch.setattr(
        rules_bp.rule_engines,
        "all_engines",
        lambda: [type("GritStub", (), {"id": "grit", "kind": "grit", "language_ids": ("java",), "grit_dir": "/tmp/.grit"})()],
    )
    return rules_data


# ── GET /api/rules ───────────────────────────────────────────

def test_api_rules_groups_by_guide_default(flask_client, fake_rules):
    resp = flask_client.get("/api/rules")
    body = resp.get_json()
    assert body["total"] == 2
    assert body["group_by"] == "guide"
    assert isinstance(body["engines"], list)
    groups = dict(body["grouped"])
    assert "entity-pattern" in groups
    assert "rest-controller" in groups
    assert groups["entity-pattern"][0]["engine"] == "grit"
    assert groups["entity-pattern"][0]["engine_kind"] == "grit"
    assert groups["entity-pattern"][0]["capabilities"]["can_edit_source"] is True


def test_api_rules_groups_by_layer(flask_client, fake_rules):
    resp = flask_client.get("/api/rules?by=layer")
    body = resp.get_json()
    assert body["group_by"] == "layer"
    groups = dict(body["grouped"])
    assert "entity" in groups
    assert "controller" in groups


def test_api_rules_empty_returns_zero_total(flask_client, monkeypatch):
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(rules_bp, "load_rules_index",
                        lambda: {"rules": []})
    monkeypatch.setattr(rules_bp.rule_engines, "all_engines", lambda: [])
    body = flask_client.get("/api/rules").get_json()
    assert body["total"] == 0


def test_api_rules_includes_bundle_engine_rules(flask_client, monkeypatch):
    from web.blueprints import rules as rules_bp

    class _BundleStub:
        id = "frontend-style-convention"
        kind = "bundle"
        language_ids = ("vue", "css")

        def parse_rules(self):
            return [
                Rule(
                    id="icon_button_requires_label",
                    engine="frontend-style-convention",
                    summary="Icon buttons need an accessible name",
                    severity="error",
                    triggers=("src/**/*.vue",),
                    source_file="accessibility.yaml",
                    metadata={
                        "checker": "icon_button_accessible_name",
                        "category": "accessibility",
                    },
                )
            ]

    monkeypatch.setattr(rules_bp, "load_rules_index", lambda: {"rules": []})
    monkeypatch.setattr(
        rules_bp.rule_engines,
        "all_engines",
        lambda: [_BundleStub()],
    )

    body = flask_client.get("/api/rules").get_json()
    assert body["total"] == 1
    groups = dict(body["grouped"])
    # No explicit `guide` in metadata → falls back to engine.id (the pattern slug).
    assert "frontend-style-convention" in groups
    rule = groups["frontend-style-convention"][0]
    assert rule["id"] == "icon_button_requires_label"
    assert rule["engine"] == "frontend-style-convention"
    assert rule["engine_kind"] == "bundle"
    assert rule["layer"] == "accessibility"


# ── GET /api/rules/<rule_id> ────────────────────────────────

def test_api_rule_detail_unknown_returns_404(flask_client, fake_rules):
    resp = flask_client.get("/api/rules/nonexistent")
    assert resp.status_code == 404


def test_api_rule_detail_returns_rule(flask_client, fake_rules):
    resp = flask_client.get("/api/rules/alpha_rule")
    body = resp.get_json()
    assert body["rule"]["id"] == "alpha_rule"
    assert body["rule"]["guide"] == "entity-pattern"
    assert body["rule"]["engine"] == "grit"
    assert body["rule"]["engine_kind"] == "grit"
    assert body["engine"]["id"] == "grit"
    assert "grit apply" in body["engine"]["invocation_hint"]
    assert body["ui"]["source_label"] == "Rule source (grit)"
    # No real source file on disk → source_snippet is None.
    assert body["source_snippet"] is None


def test_api_rule_detail_extracts_source_snippet(
    flask_client, fake_rules, tmp_path, monkeypatch
):
    """When the .grit file exists, the `pattern <id>(...)` block is sliced out.

    Characterizes the brace-matching extraction: the snippet starts at the
    blank-line boundary before `pattern` and ends just past the balanced
    closing brace, so the preamble and trailing content are excluded.
    """
    base = tmp_path / "base"
    grit_file = base / ".grit/patterns/java/alpha.grit"
    grit_file.parent.mkdir(parents=True)
    grit_file.write_text(
        "language java\n"
        "\n"
        "pattern alpha_rule() {\n"
        "  `class $name { $body }` where { $name <: contains `Entity` }\n"
        "}\n"
        "\n"
        "pattern unrelated() { `x` }\n"
    )
    # api_rule_detail derives source_path from dirname(patterns_dir).
    monkeypatch.setattr(settings, "patterns_dir", base / "patterns")

    resp = flask_client.get("/api/rules/alpha_rule")
    assert resp.status_code == 200
    snippet = resp.get_json()["source_snippet"]
    assert snippet is not None
    assert snippet.startswith("pattern alpha_rule() {")
    assert snippet.rstrip().endswith("}")
    # The balanced-brace scan stops at the first pattern's closing brace.
    assert "pattern unrelated()" not in snippet
    assert "language java" not in snippet


def test_api_rule_detail_returns_bundle_rule(flask_client, monkeypatch):
    from web.blueprints import rules as rules_bp

    class _BundleStub:
        id = "frontend-style-convention"
        kind = "bundle"
        language_ids = ("vue",)

        def parse_rules(self):
            return [
                Rule(
                    id="focus_visible_styling_coverage",
                    engine="frontend-style-convention",
                    summary="Interactive elements need focus-visible styling",
                    severity="warn",
                    triggers=("src/**/*.vue",),
                    source_file="accessibility.yaml",
                    metadata={
                        "checker": "interactive_requires_focus_visible",
                        "category": "accessibility",
                    },
                )
            ]

    monkeypatch.setattr(rules_bp, "load_rules_index", lambda: {"rules": []})
    monkeypatch.setattr(
        rules_bp.rule_engines,
        "all_engines",
        lambda: [_BundleStub()],
    )

    resp = flask_client.get("/api/rules/focus_visible_styling_coverage")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["rule"]["id"] == "focus_visible_styling_coverage"
    # No explicit `guide` → falls back to engine.id (the pattern slug).
    assert body["rule"]["guide"] == "frontend-style-convention"
    assert body["rule"]["engine"] == "frontend-style-convention"
    assert body["rule"]["engine_kind"] == "bundle"
    assert body["engine"]["id"] == "frontend-style-convention"
    assert "regin rules run --engine frontend-style-convention" in body["engine"]["invocation_hint"]


# ── POST /api/rules/<rule_id>/delete ────────────────────────

def test_delete_rule_requires_auth(anon_client, fake_rules):
    resp = anon_client.post("/api/rules/alpha_rule/delete")
    assert resp.status_code == 401


def test_delete_rule_unknown_returns_404(flask_client, fake_rules):
    resp = flask_client.post("/api/rules/unknown/delete",
                               headers=_editor_auth())
    assert resp.status_code == 404


def test_delete_rule_failure_reported(flask_client, fake_rules, monkeypatch):
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(rules_bp.grit_rule_index, "delete_rule",
                        lambda _id: False)
    resp = flask_client.post("/api/rules/alpha_rule/delete",
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is False


def test_delete_rule_success(flask_client, fake_rules, monkeypatch):
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(rules_bp.grit_rule_index, "delete_rule",
                        lambda _id: True)
    monkeypatch.setattr(rules_bp.grit_rule_index, "regenerate",
                        lambda write_guides=False: {"rules": 0})
    monkeypatch.setattr(rules_bp, "deploy_rules_index_skill",
                        lambda _p: "/tmp/skill")
    monkeypatch.setattr(rules_bp.audit, "log_action",
                        lambda *a, **kw: None)
    resp = flask_client.post("/api/rules/alpha_rule/delete",
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True


# ── POST /api/rules/<rule_id>/update ────────────────────────

def test_update_rule_requires_auth(anon_client, fake_rules):
    resp = anon_client.post("/api/rules/alpha_rule/update",
                               json={"severity": "info"})
    assert resp.status_code == 401


def test_update_rule_unknown_returns_404(flask_client, fake_rules):
    resp = flask_client.post("/api/rules/nope/update",
                               json={"severity": "info"},
                               headers=_editor_auth())
    assert resp.status_code == 404


def test_update_rule_empty_payload_rejected(flask_client, fake_rules):
    resp = flask_client.post("/api/rules/alpha_rule/update",
                               json={},
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is False
    assert "No fields" in body["msg"]


def test_update_rule_success_path(flask_client, fake_rules, monkeypatch):
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(rules_bp.grit_rule_index, "update_rule",
                        lambda _id, _updates: True)
    monkeypatch.setattr(rules_bp.grit_rule_index, "regenerate",
                        lambda write_guides=False: {"rules": 0})
    monkeypatch.setattr(rules_bp, "deploy_rules_index_skill",
                        lambda _p: "/tmp/skill")
    monkeypatch.setattr(rules_bp.audit, "log_action",
                        lambda *a, **kw: None)
    resp = flask_client.post(
        "/api/rules/alpha_rule/update",
        json={"severity": "warn", "extra_rejected_field": "x"},
        headers=_editor_auth(),
    )
    body = resp.get_json()
    assert body["ok"] is True


def test_update_rule_failure_reported(
        flask_client, fake_rules, monkeypatch):
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(rules_bp.grit_rule_index, "update_rule",
                        lambda _id, _updates: False)
    resp = flask_client.post(
        "/api/rules/alpha_rule/update",
        json={"severity": "warn"},
        headers=_editor_auth(),
    )
    body = resp.get_json()
    assert body["ok"] is False


# ── GET /api/triggers ───────────────────────────────────────

def test_api_triggers_empty(flask_client, tmp_db):
    resp = flask_client.get("/api/triggers")
    body = resp.get_json()
    assert body["items"] == []
    assert body["pagination"]["strategy"] == "cursor"
    assert body["stats"] == []
    assert body["sessions"] == []


def test_api_triggers_populated(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="r1", file_path="src/X.java", repo="svc",
            match_count=2, triggered=1, severity="error",
            guide="g", summary="s", source="hook", session_id="sess-a",
            checked_at="2026-04-22 10:00:00",
        ))
        session.add(RuleTrigger(
            rule_id="r1", file_path="src/Y.java", repo="svc",
            match_count=0, triggered=0, severity="error",
            guide="g", summary="s", source="hook", session_id="sess-a",
            checked_at="2026-04-22 10:01:00",
        ))
        session.commit()

    resp = flask_client.get("/api/triggers")
    body = resp.get_json()
    assert len(body["items"]) == 2
    # stats populated on first page (no cursor).
    rule_totals = {r["rule_id"]: r for r in body["stats"]}
    assert rule_totals["r1"]["total"] == 2
    assert rule_totals["r1"]["fired"] == 1


def test_api_triggers_filter_by_rule(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="alpha", file_path="a.java",
            match_count=1, triggered=1,
            checked_at="2026-04-22 10:00:00",
        ))
        session.add(RuleTrigger(
            rule_id="beta", file_path="b.java",
            match_count=1, triggered=1,
            checked_at="2026-04-22 10:01:00",
        ))
        session.commit()

    resp = flask_client.get("/api/triggers?rule=alpha")
    body = resp.get_json()
    assert {r["rule_id"] for r in body["items"]} == {"alpha"}
    assert body["rule_filter"] == "alpha"


def test_api_triggers_only_triggered_filter(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="r", file_path="a.java",
            match_count=0, triggered=0,
            checked_at="2026-04-22 10:00:00",
        ))
        session.add(RuleTrigger(
            rule_id="r", file_path="b.java",
            match_count=2, triggered=1,
            checked_at="2026-04-22 10:01:00",
        ))
        session.commit()

    resp = flask_client.get("/api/triggers?triggered=1")
    body = resp.get_json()
    assert len(body["items"]) == 1
    assert body["items"][0]["triggered"] == 1


# ── POST /api/triggers/reset ────────────────────────────────

def test_reset_triggers_deletes_all(flask_client, tmp_db):
    with SessionLocal() as session:
        for i in range(3):
            session.add(RuleTrigger(
                rule_id=f"r{i}", file_path=f"f{i}.java",
                match_count=1, triggered=1,
                checked_at="2026-04-22 10:00:00",
            ))
        session.commit()

    resp = flask_client.post("/api/triggers/reset", json={},
                              headers=_admin_auth())
    body = resp.get_json()
    assert body["ok"] is True
    assert "3 row" in body["msg"]


def test_reset_triggers_filter_by_rule(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="keep", file_path="k.java",
            match_count=1, triggered=1,
            checked_at="2026-04-22 10:00:00",
        ))
        session.add(RuleTrigger(
            rule_id="drop", file_path="d.java",
            match_count=1, triggered=1,
            checked_at="2026-04-22 10:01:00",
        ))
        session.commit()

    resp = flask_client.post("/api/triggers/reset", json={"rule": "drop"},
                              headers=_admin_auth())
    assert resp.get_json()["ok"] is True

    with SessionLocal() as session:
        from sqlmodel import select as _sel
        remaining = session.exec(_sel(RuleTrigger)).all()
        assert {r.rule_id for r in remaining} == {"keep"}


# ── POST /api/rule-triggers (ingest) ────────────────────────

def test_ingest_rule_trigger_invalid_json(flask_client, tmp_db):
    resp = flask_client.post("/api/rule-triggers",
                               data="not-json",
                               content_type="application/json")
    assert resp.status_code == 400


def test_ingest_rule_trigger_single_event(flask_client, tmp_db):
    resp = flask_client.post("/api/rule-triggers", json={
        "rule_id": "r1", "file_path": "a.java", "match_count": 2,
    })
    body = resp.get_json()
    assert body["ok"] is True
    assert body["ingested"] == 1

    with SessionLocal() as session:
        from sqlmodel import select as _sel
        rows = session.exec(_sel(RuleTrigger)).all()
        assert rows[0].triggered == 1  # match_count>0 → triggered


def test_ingest_rule_trigger_batch(flask_client, tmp_db):
    resp = flask_client.post("/api/rule-triggers", json=[
        {"rule_id": "r1", "file_path": "a.java", "match_count": 0},
        {"rule_id": "r2", "file_path": "b.java", "match_count": 5},
    ])
    assert resp.get_json()["ingested"] == 2


def test_ingest_rule_trigger_missing_rule_id_rejected(flask_client, tmp_db):
    resp = flask_client.post("/api/rule-triggers", json={
        "file_path": "a.java", "match_count": 1,
    })
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["ingested"] == 0
    assert body["errors"][0]["reason"].startswith("missing or blank rule_id")


def test_ingest_rule_trigger_missing_file_path_rejected(
        flask_client, tmp_db):
    resp = flask_client.post("/api/rule-triggers", json={
        "rule_id": "r1", "match_count": 1,
    })
    assert resp.status_code == 400


def test_ingest_rule_trigger_all_or_nothing(flask_client, tmp_db):
    """One bad event aborts the whole batch — zero rows written."""
    resp = flask_client.post("/api/rule-triggers", json=[
        {"rule_id": "r1", "file_path": "a.java", "match_count": 1},
        {"rule_id": "", "file_path": "b.java", "match_count": 1},
    ])
    assert resp.status_code == 400

    with SessionLocal() as session:
        from sqlmodel import select as _sel
        rows = session.exec(_sel(RuleTrigger)).all()
        assert rows == []


# ── GET /api/pattern-scripts ────────────────────────────────

def test_pattern_scripts_missing_dir_returns_empty(
        flask_client, tmp_db, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(settings, "patterns_dir",
                        str(tmp_path / "nope"))
    resp = flask_client.get("/api/pattern-scripts")
    body = resp.get_json()
    assert body == {"patterns": [], "total_scripts": 0}


# ── GET /api/applicable-rules ───────────────────────────────

def test_applicable_rules_requires_repo_param(flask_client):
    resp = flask_client.get("/api/applicable-rules")
    assert resp.status_code == 400
    assert "repo" in resp.get_json()["error"]


def test_applicable_rules_unknown_repo_returns_404(
        flask_client, tmp_path):
    resp = flask_client.get(
        f"/api/applicable-rules?repo={tmp_path / 'nope'}",
    )
    assert resp.status_code == 404


def test_applicable_rules_matches_filename_trigger(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    """A rule with a *.java glob trigger matches a repo with .java files."""
    from web.blueprints import rules as rules_bp
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "UserEntity.java").write_text("class UserEntity {}")

    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "match_entity", "triggers": ["*Entity.java"],
            "layer": "entity", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(f"/api/applicable-rules?repo={tmp_path}")
    body = resp.get_json()
    assert body == [{"id": "match_entity", "applicable": True}]


def test_applicable_rules_skips_rule_with_no_file_match(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "plain.txt").write_text("no java files here")

    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "no_match", "triggers": ["*Entity.java"],
            "layer": "entity", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(f"/api/applicable-rules?repo={tmp_path}")
    assert resp.get_json() == []


def test_applicable_rules_skips_disabled(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp
    (tmp_path / "UserEntity.java").write_text("x")

    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "disabled_rule", "triggers": ["*Entity.java"],
            "layer": "entity", "severity": "error", "disabled": True,
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(f"/api/applicable-rules?repo={tmp_path}")
    assert resp.get_json() == []


# ── GET /api/applicable-files ───────────────────────────────

def test_applicable_files_requires_params(flask_client):
    resp = flask_client.get("/api/applicable-files")
    assert resp.status_code == 400


def test_applicable_files_repo_missing_404(flask_client, tmp_path):
    resp = flask_client.get(
        f"/api/applicable-files?rule=r&repo={tmp_path / 'nope'}",
    )
    assert resp.status_code == 404


def test_applicable_files_unknown_rule_404(
        flask_client, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(rules_bp, "load_rules_index",
                        lambda: {"rules": []})
    resp = flask_client.get(
        f"/api/applicable-files?rule=nope&repo={tmp_path}",
    )
    assert resp.status_code == 404


def test_applicable_files_filename_glob_match(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp
    (tmp_path / "UserEntity.java").write_text("class X {}")
    (tmp_path / "UserController.java").write_text("class Y {}")

    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "entity_rule", "triggers": ["*Entity.java"],
            "layer": "entity", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(
        f"/api/applicable-files?rule=entity_rule&repo={tmp_path}",
    )
    body = resp.get_json()
    assert "UserEntity.java" in body
    assert "UserController.java" not in body


def test_applicable_files_content_trigger_match(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp
    (tmp_path / "Svc.java").write_text(
        "import x;\n@Service\npublic class Svc {}"
    )
    (tmp_path / "Other.java").write_text(
        "public class Other {}"
    )

    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "svc_rule", "triggers": ["@Service"],
            "layer": "service", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(
        f"/api/applicable-files?rule=svc_rule&repo={tmp_path}",
    )
    body = resp.get_json()
    assert "Svc.java" in body
    assert "Other.java" not in body


def test_applicable_files_skips_hidden_and_build_dirs(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp
    # Skipped dirs: .hidden, target, build, node_modules.
    for skip in (".hidden", "target", "build", "node_modules"):
        d = tmp_path / skip
        d.mkdir()
        (d / "Skipped.java").write_text("x")

    (tmp_path / "KeepMe.java").write_text("x")

    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "r", "triggers": ["*.java"],
            "layer": "x", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(
        f"/api/applicable-files?rule=r&repo={tmp_path}",
    )
    body = resp.get_json()
    assert "KeepMe.java" in body
    assert all("target" not in p and "build" not in p for p in body)


def test_pattern_scripts_lists_patterns_with_scripts(
        flask_client, tmp_db, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp

    patterns = tmp_path / "patterns"
    patterns.mkdir()
    with_scripts = patterns / "with-scripts"
    with_scripts.mkdir()
    (with_scripts / "SKILL.md").write_text(
        '---\ntitle: "Has Scripts"\n---\nbody'
    )
    (with_scripts / "scripts").mkdir()
    (with_scripts / "scripts" / "run.sh").write_text("#!/bin/sh\n")

    # Pattern without scripts — should be skipped.
    no_scripts = patterns / "no-scripts"
    no_scripts.mkdir()
    (no_scripts / "SKILL.md").write_text("body")

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(rules_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [])

    resp = flask_client.get("/api/pattern-scripts")
    body = resp.get_json()
    assert body["total_scripts"] == 1
    assert len(body["patterns"]) == 1
    entry = body["patterns"][0]
    assert entry["slug"] == "with-scripts"
    assert entry["title"] == "Has Scripts"
    assert entry["own_scripts"][0]["name"] == "run.sh"
    assert entry["own_scripts"][0]["language"] == "shell"
    assert entry["has_grit_rules"] is False


# ── pattern-scripts edge cases ──────────────────────────────────

def test_pattern_scripts_skips_hidden_and_underscore_dirs(
        flask_client, tmp_db, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp

    patterns = tmp_path / "patterns"
    patterns.mkdir()
    for skip_name in (".hidden", "_private"):
        d = patterns / skip_name
        d.mkdir()
        (d / "scripts").mkdir()
        (d / "scripts" / "x.sh").write_text("#!/bin/sh\n")

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(rules_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [])
    body = flask_client.get("/api/pattern-scripts").get_json()
    assert body["patterns"] == []
    assert body["total_scripts"] == 0


def test_pattern_scripts_falls_back_to_slug_when_skill_md_unreadable(
        flask_client, tmp_db, monkeypatch, tmp_path):
    """If SKILL.md read raises OSError, title defaults to the slug."""
    from web.blueprints import rules as rules_bp
    import builtins as _builtins

    patterns = tmp_path / "patterns"
    patterns.mkdir()
    pat = patterns / "busted-skill"
    pat.mkdir()
    (pat / "SKILL.md").write_text('---\ntitle: "X"\n---\nbody')
    (pat / "scripts").mkdir()
    (pat / "scripts" / "x.sh").write_text("#!/bin/sh\n")

    real_open = _builtins.open

    def _open(path, *a, **kw):
        if str(path).endswith("SKILL.md"):
            raise OSError("cannot read")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(_builtins, "open", _open)
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(rules_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [])

    body = flask_client.get("/api/pattern-scripts").get_json()
    entry = body["patterns"][0]
    assert entry["title"] == "busted-skill"


def test_pattern_scripts_skips_empty_scripts_dir(
        flask_client, tmp_db, monkeypatch, tmp_path):
    from web.blueprints import rules as rules_bp

    patterns = tmp_path / "patterns"
    patterns.mkdir()
    pat = patterns / "empty"
    pat.mkdir()
    (pat / "scripts").mkdir()  # exists but has no files

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(rules_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [])
    body = flask_client.get("/api/pattern-scripts").get_json()
    assert body["patterns"] == []


# ── rule detail source_snippet ──────────────────────────────────

def test_api_rule_detail_returns_source_snippet(
        flask_client, monkeypatch, tmp_path):
    """When `source_file` exists on disk, api returns the extracted block."""
    from web.blueprints import rules as rules_bp

    # PATTERNS_DIR is the *parent* of source_file resolution: the
    # blueprint does `os.path.join(os.path.dirname(PATTERNS_DIR), source_file)`.
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    grit_file = tmp_path / "rules.grit"
    grit_file.write_text(
        "helper text\n\n"
        "pattern alpha_rule($name) {\n"
        "  `class $name { }`\n"
        "}\n"
    )

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "alpha_rule", "layer": "entity",
            "triggers": ["*Entity.java"], "severity": "error",
            "guide": "g", "summary": "s",
            "source_file": "rules.grit",
        }]},
    )

    body = flask_client.get("/api/rules/alpha_rule").get_json()
    assert body["rule"]["id"] == "alpha_rule"
    snippet = body["source_snippet"]
    assert snippet is not None
    assert snippet.startswith("pattern alpha_rule(")
    assert snippet.rstrip().endswith("}")


# ── applicable-rules edge cases ─────────────────────────────────

def test_applicable_rules_skips_rule_with_no_triggers(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    """A rule with an empty triggers list contributes nothing."""
    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "no_trigs", "triggers": [],
            "layer": "x", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(f"/api/applicable-rules?repo={tmp_path}")
    assert resp.get_json() == []


def test_applicable_rules_content_trigger_matches(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    """Rules with content-only triggers (no glob) take the grep path."""
    from web.blueprints import rules as rules_bp
    (tmp_path / "Svc.java").write_text(
        "import x;\n@Service\nclass Svc {}"
    )
    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "svc_content", "triggers": ["@Service"],
            "layer": "service", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    body = flask_client.get(
        f"/api/applicable-rules?repo={tmp_path}",
    ).get_json()
    assert body == [{"id": "svc_content", "applicable": True}]


def test_applicable_rules_content_trigger_no_match(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    """Content trigger with no match → rule is skipped."""
    from web.blueprints import rules as rules_bp
    (tmp_path / "Svc.java").write_text("class Svc {}")
    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "no_svc", "triggers": ["@Service"],
            "layer": "service", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    body = flask_client.get(
        f"/api/applicable-rules?repo={tmp_path}",
    ).get_json()
    assert body == []


def test_applicable_rules_subprocess_timeout_is_swallowed(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    """subprocess.TimeoutExpired during find → treated as no match, no crash."""
    from web.blueprints import rules as rules_bp

    def _boom(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="find", timeout=5)

    import subprocess
    monkeypatch.setattr(rules_bp.subprocess, "run", _boom)
    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "r", "triggers": ["*Entity.java"],
            "layer": "x", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    resp = flask_client.get(f"/api/applicable-rules?repo={tmp_path}")
    assert resp.status_code == 200
    assert resp.get_json() == []


# ── applicable-files edge cases ─────────────────────────────────

def test_applicable_files_content_trigger_word_boundary(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    """Non-@ triggers use word-boundary regex, not substring."""
    from web.blueprints import rules as rules_bp
    (tmp_path / "Hit.java").write_text(
        "public class Hit { transactional(); }"
    )
    (tmp_path / "Miss.java").write_text(
        "public class Miss { nontransactionalXxx(); }"
    )

    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "tx_rule", "triggers": ["transactional"],
            "layer": "x", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    body = flask_client.get(
        f"/api/applicable-files?rule=tx_rule&repo={tmp_path}",
    ).get_json()
    assert "Hit.java" in body
    assert "Miss.java" not in body


def test_applicable_files_unreadable_file_is_skipped(configured_grit_engine, flask_client, monkeypatch, tmp_path):
    """OSError opening a file must not abort the walk."""
    from web.blueprints import rules as rules_bp
    import builtins as _builtins
    (tmp_path / "Good.java").write_text("@Service\nclass X {}")
    (tmp_path / "Bad.java").write_text("@Service\nclass Y {}")

    real_open = _builtins.open

    def _open(path, *a, **kw):
        if str(path).endswith("Bad.java"):
            raise OSError("permission denied")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(_builtins, "open", _open)
    monkeypatch.setattr(
        rules_bp, "load_rules_index",
        lambda: {"rules": [{
            "id": "svc", "triggers": ["@Service"],
            "layer": "service", "severity": "error",
            "guide": "g", "summary": "s", "source_file": "x",
        }]},
    )
    body = flask_client.get(
        f"/api/applicable-files?rule=svc&repo={tmp_path}",
    ).get_json()
    # Only the readable file should surface.
    assert "Good.java" in body
    assert "Bad.java" not in body


# ── triggers filters & root stripping ───────────────────────────

def test_api_triggers_strips_registered_repo_path(
        flask_client, tmp_db):
    """file_path under a registered Repo.path gets its prefix stripped
    for display. Reads straight from the repos table so removing a repo
    via /repos takes effect immediately."""
    from lib.orm.models import Repo

    with SessionLocal() as session:
        session.add(Repo(name="svc", path="/repos/svc", default_branch="main"))
        session.add(RuleTrigger(
            rule_id="r", file_path="/repos/svc/src/X.java",
            match_count=1, triggered=1,
            checked_at="2026-04-22 10:00:00",
        ))
        session.commit()

    body = flask_client.get("/api/triggers").get_json()
    assert body["items"][0]["file_path"] == "src/X.java"


def test_api_triggers_filter_by_session(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="r", file_path="a.java",
            match_count=1, triggered=1, session_id="sess-keep",
            checked_at="2026-04-22 10:00:00",
        ))
        session.add(RuleTrigger(
            rule_id="r", file_path="b.java",
            match_count=1, triggered=1, session_id="sess-drop",
            checked_at="2026-04-22 10:01:00",
        ))
        session.commit()
    body = flask_client.get(
        "/api/triggers?session=sess-keep",
    ).get_json()
    assert {r["session_id"] for r in body["items"]} == {"sess-keep"}
    assert body["session_filter"] == "sess-keep"


def test_reset_triggers_filter_by_session(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="r", file_path="a.java",
            match_count=1, triggered=1, session_id="keep",
            checked_at="2026-04-22 10:00:00",
        ))
        session.add(RuleTrigger(
            rule_id="r", file_path="b.java",
            match_count=1, triggered=1, session_id="drop",
            checked_at="2026-04-22 10:01:00",
        ))
        session.commit()

    resp = flask_client.post("/api/triggers/reset",
                               json={"session": "drop"},
                               headers=_admin_auth())
    assert resp.get_json()["ok"] is True
    with SessionLocal() as session:
        from sqlmodel import select as _sel
        remaining = session.exec(_sel(RuleTrigger)).all()
        assert {r.session_id for r in remaining} == {"keep"}


# ── ingest: non-dict event ──────────────────────────────────────

def test_ingest_rule_trigger_non_dict_event_rejected(flask_client, tmp_db):
    """A non-dict element in a batch should fail validation with 400."""
    resp = flask_client.post("/api/rule-triggers",
                               json=["not-a-dict"])
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["errors"][0]["reason"] == "event must be an object"
