"""Unit tests for web.blueprints.patterns JSON API.

Covers listing, detail, create/content/tags/delete, skillhub-status.
More peripheral endpoints (import/promote/source-update/rules
enable/disable) remain to be exercised in a follow-up.
"""

from __future__ import annotations

import pytest

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag
from lib.rule_engines.base import Rule
from lib.settings import settings


def _editor_auth():
    token = create_token(1, "editor-tester", "editor")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated_patterns(tmp_path, monkeypatch):
    """Redirect PATTERNS_DIR + PROJECT_ROOT into tmp_path."""
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    monkeypatch.setattr(settings, "patterns_dir",
                        str(patterns))
    monkeypatch.setattr(settings, "project_root",
                        str(tmp_path))

    # Stub the skill-sync + registry side so tests don't hit the real
    # ~/.claude/skills tree.
    from lib.skills import skill_registry, skill_sync
    monkeypatch.setattr(skill_registry, "skill_id_for_procedure",
                        lambda _slug: None)
    monkeypatch.setattr(skill_sync, "state", lambda _id, **kw: "in_sync")

    # No-op audit so we don't need users in the DB.
    from lib import audit
    monkeypatch.setattr(audit, "log_action", lambda *a, **kw: None)

    return {"patterns": patterns, "root": tmp_path}


def _seed_pattern_doc(slug, title="P", tags=None, description=None):
    with SessionLocal() as session:
        doc = PatternDoc(
            slug=slug, title=title,
            file_path=f"patterns/{slug}/SKILL.md",
            category="procedure", content_hash="0" * 64,
            description=description,
        )
        session.add(doc)
        session.flush()
        doc_id = doc.id
        if tags:
            for name in tags:
                t = session.exec(
                    __import__("sqlmodel").select(Tag).where(Tag.name == name)
                ).first()
                if t is None:
                    t = Tag(name=name, category="concept")
                    session.add(t)
                    session.flush()
                session.add(DocTag(doc_id=doc_id, tag_id=t.id))
        session.commit()
    return doc_id


# ── GET /api/patterns ────────────────────────────────────────

def test_api_patterns_empty(flask_client, tmp_db, isolated_patterns):
    resp = flask_client.get("/api/patterns")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["docs"] == []


def test_api_patterns_lists_docs_with_tags(
        flask_client, tmp_db, isolated_patterns):
    _seed_pattern_doc("alpha", tags=["tag-one"])
    _seed_pattern_doc("beta")
    resp = flask_client.get("/api/patterns")
    body = resp.get_json()
    slugs = {d["slug"] for d in body["docs"]}
    assert slugs == {"alpha", "beta"}
    alpha = next(d for d in body["docs"] if d["slug"] == "alpha")
    assert alpha["tag_names"] == "tag-one"


def test_api_patterns_filter_by_tag(
        flask_client, tmp_db, isolated_patterns):
    _seed_pattern_doc("with-t", tags=["the-tag"])
    _seed_pattern_doc("no-t")
    resp = flask_client.get("/api/patterns?tag=the-tag")
    slugs = {d["slug"] for d in resp.get_json()["docs"]}
    assert slugs == {"with-t"}


def test_api_patterns_filter_by_category(
        flask_client, tmp_db, isolated_patterns):
    _seed_pattern_doc("a")
    # Add one doc in another category.
    with SessionLocal() as session:
        session.add(PatternDoc(
            slug="topic-b", title="T",
            file_path="patterns/topic-b/SKILL.md",
            category="topic", content_hash="0" * 64,
        ))
        session.commit()
    resp = flask_client.get("/api/patterns?category=topic")
    slugs = {d["slug"] for d in resp.get_json()["docs"]}
    assert slugs == {"topic-b"}


# ── GET /api/patterns/<slug> ─────────────────────────────────

def test_api_pattern_detail_missing_returns_404(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.get("/api/patterns/nope")
    assert resp.status_code == 404


def test_api_pattern_detail_envelope(
        flask_client, tmp_db, isolated_patterns):
    slug = "demo-pat"
    # Write the SKILL.md on disk so detail succeeds.
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text(
        f'---\ntitle: "D"\nprocedure: {slug}\n---\n# D\n\nBody\n'
    )
    _seed_pattern_doc(slug, title="D")

    resp = flask_client.get(f"/api/patterns/{slug}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["doc"]["slug"] == slug
    assert body["procedure_id"] == slug
    assert "# D" in body["body_md"]
    assert body["enforcing_rules"] == []
    assert body["attached_rule_bundles"] == []
    assert body["experiments"] == []
    assert body["provider"]["id"] == "claude"
    assert body["provider"]["project_subpath"] == ".claude/skills"


def test_api_pattern_detail_includes_attached_bundle_engine(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    """A `kind: bundle` engine whose id == the pattern slug attaches its rules."""
    slug = "frontend-style-convention"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text(
        f'---\ntitle: "Frontend Style Convention"\nprocedure: {slug}\n---\n# Frontend\n'
    )
    _seed_pattern_doc(slug, title="Frontend Style Convention")

    class _BundleStub:
        id = slug
        kind = "bundle"

        def parse_rules(self):
            return [
                Rule(
                    id="icon_button_requires_label",
                    engine=slug,
                    summary="Icon buttons need an accessible name",
                    severity="error",
                    triggers=("src/**/*.vue",),
                    source_file="accessibility.yaml",
                    metadata={
                        "checker": "icon_button_accessible_name",
                        "category": "accessibility",
                        "check_kind": "template-ast",
                        "wcag_ref": "4.1.2",
                    },
                )
            ]

    monkeypatch.setattr(
        "web.blueprints.patterns.rule_engines.all_engines",
        lambda: [_BundleStub()],
    )

    resp = flask_client.get(f"/api/patterns/{slug}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["attached_rule_bundles"]) == 1
    bundle = body["attached_rule_bundles"][0]
    assert bundle["engine_id"] == slug
    assert bundle["engine_kind"] == "bundle"
    assert bundle["rules"][0]["id"] == "icon_button_requires_label"
    assert bundle["rules"][0]["checker"] == "icon_button_accessible_name"


def test_api_pattern_detail_includes_bundle_engine_matched_by_slug(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    """New BundleEngine where engine.id matches the pattern slug."""
    slug = "frontend-style-convention"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text(
        f'---\ntitle: "Frontend Style Convention"\nprocedure: {slug}\n---\n# Frontend\n'
    )
    _seed_pattern_doc(slug, title="Frontend Style Convention")

    class _ManifestStub:
        description = "Self-describing rule bundle for frontend style convention."

    class _BundleEngineStub:
        id = slug
        kind = "bundle"
        manifest = _ManifestStub()

        def parse_rules(self):
            return [
                Rule(
                    id="disallow_raw_hex",
                    engine=slug,
                    summary="No raw hex colours",
                    severity="warn",
                    triggers=("src/**/*.vue",),
                    source_file="accessibility.yaml",
                    metadata={
                        "checker": "disallow_raw_hex",
                        "category": "typography",
                    },
                )
            ]

    monkeypatch.setattr(
        "web.blueprints.patterns.rule_engines.all_engines",
        lambda: [_BundleEngineStub()],
    )

    resp = flask_client.get(f"/api/patterns/{slug}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["attached_rule_bundles"]) == 1
    bundle = body["attached_rule_bundles"][0]
    assert bundle["engine_id"] == slug
    assert bundle["engine_kind"] == "bundle"
    assert bundle["description"].startswith("Self-describing rule bundle")
    assert bundle["rules"][0]["id"] == "disallow_raw_hex"


def test_api_pattern_detail_bundle_engine_not_attached_to_other_pattern(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    """A bundle engine whose id doesn't match the pattern slug is ignored."""
    slug = "some-other-pattern"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text(
        f'---\ntitle: "Other"\nprocedure: {slug}\n---\n# Other\n'
    )
    _seed_pattern_doc(slug, title="Other")

    class _BundleEngineStub:
        id = "frontend-style-convention"
        kind = "bundle"
        manifest = None

        def parse_rules(self):
            return []

    monkeypatch.setattr(
        "web.blueprints.patterns.rule_engines.all_engines",
        lambda: [_BundleEngineStub()],
    )

    resp = flask_client.get(f"/api/patterns/{slug}")
    assert resp.status_code == 200
    assert resp.get_json()["attached_rule_bundles"] == []


# ── POST /api/patterns/create ────────────────────────────────

def test_create_pattern_requires_auth(anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post("/api/patterns/create",
                               json={"title": "x"})
    assert resp.status_code == 401


def test_create_pattern_requires_title(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/create",
                               json={},
                               headers=_editor_auth())
    assert resp.status_code == 400


def test_create_pattern_rejects_bad_slug(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/create",
                               json={"title": "Ok", "slug": "Bad Slug"},
                               headers=_editor_auth())
    assert resp.status_code == 400


def test_create_pattern_auto_derives_slug(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/create",
                               json={"title": "My Cool Pattern"},
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True
    assert body["slug"] == "my-cool-pattern"

    # File + DB row written.
    pd = isolated_patterns["patterns"] / "my-cool-pattern"
    assert (pd / "SKILL.md").exists()


def test_create_pattern_conflict_on_existing_dir(
        flask_client, tmp_db, isolated_patterns):
    # Pre-create the dir.
    (isolated_patterns["patterns"] / "taken").mkdir()
    resp = flask_client.post("/api/patterns/create",
                               json={"title": "X", "slug": "taken"},
                               headers=_editor_auth())
    assert resp.status_code == 409


def test_create_pattern_attaches_tags(
        flask_client, tmp_db, isolated_patterns):
    # Seed the tags first so the FK link finds them.
    with SessionLocal() as session:
        session.add(Tag(name="custom-a", category="concept"))
        session.commit()
    resp = flask_client.post(
        "/api/patterns/create",
        json={"title": "With Tags", "tags": ["custom-a"]},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    resp2 = flask_client.get("/api/patterns")
    docs = resp2.get_json()["docs"]
    assert any(d["tag_names"] == "custom-a" for d in docs)


# ── POST /api/patterns/<slug>/content ───────────────────────

def test_save_content_requires_auth(anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post("/api/patterns/x/content",
                               json={"body": "new"})
    assert resp.status_code == 401


def test_save_content_unknown_returns_404(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/nope/content",
                               json={"body": "new"},
                               headers=_editor_auth())
    assert resp.status_code == 404


def test_save_content_missing_disk_file_returns_404(
        flask_client, tmp_db, isolated_patterns):
    _seed_pattern_doc("ghost")
    # No SKILL.md on disk → 404.
    resp = flask_client.post("/api/patterns/ghost/content",
                               json={"body": "x"},
                               headers=_editor_auth())
    assert resp.status_code == 404


def test_save_content_success_updates_body(
        flask_client, tmp_db, isolated_patterns):
    slug = "save-me"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text(
        f'---\ntitle: "X"\nprocedure: {slug}\n---\nold body'
    )
    _seed_pattern_doc(slug)
    # Make the stored file_path match the isolated tmp layout.
    with SessionLocal() as session:
        import sqlmodel as _sm
        doc = session.exec(
            _sm.select(PatternDoc).where(PatternDoc.slug == slug)
        ).first()
        import os
        doc.file_path = os.path.relpath(str(pd / "SKILL.md"),
                                          str(isolated_patterns["root"]))
        session.add(doc)
        session.commit()

    resp = flask_client.post(
        f"/api/patterns/{slug}/content",
        json={"body": "\n\nnew body\n"},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    text = (pd / "SKILL.md").read_text()
    assert "new body" in text
    assert "old body" not in text


def test_save_content_resyncs_title_and_description_from_frontmatter(
        flask_client, tmp_db, isolated_patterns):
    """Manually-edited frontmatter title and description must propagate to
    PatternDoc on body save so the WebUI reflects the SKILL.md without a
    re-import."""
    slug = "resync-me"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text(
        f'---\ntitle: "Brand New Title"\n'
        f'description: "Brand new description"\n'
        f'procedure: {slug}\n---\nbody'
    )
    _seed_pattern_doc(slug, title="Stale Title")
    with SessionLocal() as session:
        import sqlmodel as _sm
        doc = session.exec(
            _sm.select(PatternDoc).where(PatternDoc.slug == slug)
        ).first()
        import os
        doc.file_path = os.path.relpath(str(pd / "SKILL.md"),
                                          str(isolated_patterns["root"]))
        session.add(doc)
        session.commit()

    resp = flask_client.post(
        f"/api/patterns/{slug}/content",
        json={"body": "body v2"},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    with SessionLocal() as session:
        import sqlmodel as _sm
        refreshed = session.exec(
            _sm.select(PatternDoc).where(PatternDoc.slug == slug)
        ).first()
        assert refreshed.title == "Brand New Title"
        assert refreshed.description == "Brand new description"


# ── POST /api/patterns/<slug>/tags ──────────────────────────

def test_update_tags_requires_auth(anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post("/api/patterns/p/tags", json={})
    assert resp.status_code == 401


def test_update_tags_unknown_pattern_returns_404(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/nope/tags", json={},
                               headers=_editor_auth())
    assert resp.status_code == 404


def test_update_tags_replaces_links(
        flask_client, tmp_db, isolated_patterns):
    _seed_pattern_doc("p", tags=["old-tag"])
    # Seed a new tag to swap to.
    with SessionLocal() as session:
        session.add(Tag(name="new-tag", category="concept"))
        session.commit()

    resp = flask_client.post(
        "/api/patterns/p/tags",
        json={"tags": ["new-tag"]},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200

    resp2 = flask_client.get("/api/patterns")
    p = next(d for d in resp2.get_json()["docs"] if d["slug"] == "p")
    assert p["tag_names"] == "new-tag"


def test_update_tags_creates_new_tag_from_new_tag_field(
        flask_client, tmp_db, isolated_patterns):
    _seed_pattern_doc("p")
    resp = flask_client.post(
        "/api/patterns/p/tags",
        json={"tags": [], "new_tag": "fresh-tag"},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    with SessionLocal() as session:
        import sqlmodel as _sm
        tag = session.exec(
            _sm.select(Tag).where(Tag.name == "fresh-tag")
        ).first()
        assert tag is not None


# ── POST /api/patterns/<slug>/delete ────────────────────────

def test_delete_pattern_requires_auth(
        anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post("/api/patterns/p/delete")
    assert resp.status_code == 401


def test_delete_pattern_unknown_returns_404(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/nope/delete",
                               headers=_editor_auth())
    assert resp.status_code == 404


def test_delete_pattern_removes_row_and_dir(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    slug = "delete-me"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text("body")
    _seed_pattern_doc(slug)

    from lib.patterns import pattern_deployments
    monkeypatch.setattr(pattern_deployments, "list_deployments",
                        lambda pattern_slug=None: [])

    resp = flask_client.post(f"/api/patterns/{slug}/delete",
                               headers=_editor_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True

    # Dir + row gone.
    assert not pd.exists()
    with SessionLocal() as session:
        import sqlmodel as _sm
        assert session.exec(
            _sm.select(PatternDoc).where(PatternDoc.slug == slug)
        ).first() is None


def test_delete_pattern_cleans_up_grit_rules(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    """Deleting a pattern must purge the grit rules it imported (guide ==
    slug) and redeploy the grit-rules skill — otherwise they linger on the
    Rules page."""
    slug = "with-rules"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text("body")
    _seed_pattern_doc(slug)

    from lib.patterns import pattern_deployments
    from web.blueprints.patterns import editing
    import lib.skills.skill_deployer as skill_deployer
    monkeypatch.setattr(pattern_deployments, "list_deployments",
                        lambda pattern_slug=None: [])

    removed_for: list = []
    monkeypatch.setattr(editing.grit_rule_index, "remove_guide_rules",
                        lambda s: removed_for.append(s) or {"removed": 1})
    deployed: list = []
    monkeypatch.setattr(skill_deployer, "deploy_rules_index_skill",
                        lambda path: deployed.append(path))

    resp = flask_client.post(f"/api/patterns/{slug}/delete",
                               headers=_editor_auth())
    assert resp.status_code == 200
    assert removed_for == [slug]      # cleanup ran for this slug
    assert len(deployed) == 1         # skill redeployed since a rule was removed


# ── GET /api/skillhub-status ────────────────────────────────

def test_skillhub_status_reports_availability(
        flask_client, tmp_db, monkeypatch):
    from lib.patterns import pattern_promoter
    monkeypatch.setattr(pattern_promoter, "is_available",
                        lambda: {"available": True, "url": "http://hub",
                                  "reason": None})
    resp = flask_client.get("/api/skillhub-status")
    body = resp.get_json()
    assert body["available"] is True
    assert body["url"] == "http://hub"


# ── POST /api/patterns/import ───────────────────────────────

def test_import_pattern_requires_auth(
        anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post("/api/patterns/import")
    assert resp.status_code == 401


def test_import_pattern_missing_file_field(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/import",
                               headers=_editor_auth())
    body = resp.get_json()
    assert resp.status_code == 400
    assert "missing" in body["msg"]


def test_import_pattern_success(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    from dataclasses import dataclass, field
    import io
    from lib.patterns import pattern_importer

    @dataclass
    class FakeResult:
        slug: str = "imported-x"
        title: str = "Imported X"
        pattern_dir: str = "/tmp/imported"
        shape: str = "zip"
        file_count: int = 3
        doc_id: int = 1
        grit_rules: list = field(default_factory=list)
        grit_languages: list = field(default_factory=list)
        enabled_languages: list = field(default_factory=list)

    monkeypatch.setattr(
        pattern_importer, "import_upload",
        lambda filename, data, force=False, target_slug=None: FakeResult(),
    )

    resp = flask_client.post(
        "/api/patterns/import",
        data={"file": (io.BytesIO(b"fake zip bytes"), "bundle.zip")},
        headers=_editor_auth(),
        content_type="multipart/form-data",
    )
    body = resp.get_json()
    assert body["ok"] is True
    assert body["slug"] == "imported-x"
    assert body["shape"] == "zip"
    assert body["file_count"] == 3


def test_import_pattern_import_error_returns_400(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    import io
    from lib.patterns import pattern_importer

    def boom(filename, data, force=False, target_slug=None):
        raise pattern_importer.ImportError_("malformed zip")

    monkeypatch.setattr(pattern_importer, "import_upload", boom)
    resp = flask_client.post(
        "/api/patterns/import",
        data={"file": (io.BytesIO(b"x"), "broken.zip")},
        headers=_editor_auth(),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "malformed" in resp.get_json()["msg"]


def test_import_pattern_conflict_returns_409(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    import io
    from lib.patterns import pattern_importer

    def boom(filename, data, force=False, target_slug=None):
        raise pattern_importer.ImportConflictError("already exists")

    monkeypatch.setattr(pattern_importer, "import_upload", boom)
    resp = flask_client.post(
        "/api/patterns/import",
        data={"file": (io.BytesIO(b"x"), "bundle.zip")},
        headers=_editor_auth(),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert "already exists" in body["msg"]
    assert body.get("conflict") is True


def test_import_pattern_force_query_param_passed_through(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    import io
    from lib.patterns import pattern_importer

    calls = []

    def spy(filename, data, force=False, target_slug=None):
        calls.append({"force": force, "target_slug": target_slug})
        raise pattern_importer.ImportConflictError("already exists")

    monkeypatch.setattr(pattern_importer, "import_upload", spy)
    resp = flask_client.post(
        "/api/patterns/import?force=true",
        data={"file": (io.BytesIO(b"x"), "bundle.zip")},
        headers=_editor_auth(),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 409
    assert calls[0]["force"] is True


# ── POST /api/patterns/<slug>/promote ───────────────────────

def test_promote_requires_auth(anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post("/api/patterns/p/promote")
    assert resp.status_code == 401


def test_promote_unknown_pattern_returns_404(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post("/api/patterns/nope/promote",
                               headers=_editor_auth())
    assert resp.status_code == 404


def test_promote_success(flask_client, tmp_db, isolated_patterns,
                            monkeypatch):
    slug = "promote-me"
    (isolated_patterns["patterns"] / slug).mkdir()
    (isolated_patterns["patterns"] / slug / "SKILL.md").write_text("body")

    from lib.patterns import pattern_promoter
    monkeypatch.setattr(
        pattern_promoter, "promote",
        lambda slug, version, skillhub_url, force: {
            "slug": slug, "version": version,
            "bundle_filename": f"{slug}-{version}.zip",
            "url": "http://hub", "response": {"ok": True},
        },
    )

    resp = flask_client.post(
        f"/api/patterns/{slug}/promote",
        json={"version": "2.0.0"},
        headers=_editor_auth(),
    )
    body = resp.get_json()
    assert body["ok"] is True
    assert body["bundle_filename"] == "promote-me-2.0.0.zip"
    assert body["version"] == "2.0.0"


def test_promote_error_returns_400(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    slug = "promote-err"
    (isolated_patterns["patterns"] / slug).mkdir()

    from lib.patterns import pattern_promoter

    def boom(*a, **kw):
        raise pattern_promoter.PromoteError("skillhub unreachable")

    monkeypatch.setattr(pattern_promoter, "promote", boom)
    resp = flask_client.post(
        f"/api/patterns/{slug}/promote",
        json={},
        headers=_editor_auth(),
    )
    assert resp.status_code == 400
    assert "skillhub" in resp.get_json()["error"]


# ── POST /api/patterns/<slug>/rules/enable + disable ────────

def test_rule_toggle_requires_auth(
        anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post("/api/patterns/p/rules/disable")
    assert resp.status_code == 401
    resp = anon_client.post("/api/patterns/p/rules/enable")
    assert resp.status_code == 401


def test_rule_toggle_no_linked_rules(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    from web.blueprints import patterns as patterns_bp
    monkeypatch.setattr(patterns_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [])
    resp = flask_client.post("/api/patterns/p/rules/disable",
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is False
    assert "no rules" in body["msg"]


def test_rule_disable_success(flask_client, tmp_db, isolated_patterns,
                                 monkeypatch):
    from web.blueprints import patterns as patterns_bp
    monkeypatch.setattr(patterns_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [{"id": "rule-a"}, {"id": "rule-b"}])
    monkeypatch.setattr(patterns_bp.grit_rule_index, "set_rules_disabled",
                        lambda ids, disabled: None)
    monkeypatch.setattr(patterns_bp.grit_rule_index, "regenerate",
                        lambda write_guides=False: None)
    monkeypatch.setattr(patterns_bp, "deploy_rules_index_skill",
                        lambda _p: None)

    resp = flask_client.post("/api/patterns/p/rules/disable",
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True
    assert "disabled 2 rule(s)" in body["msg"]


def test_rule_disable_specific_ids(flask_client, tmp_db, isolated_patterns,
                                     monkeypatch):
    from web.blueprints import patterns as patterns_bp
    captured = {}
    monkeypatch.setattr(patterns_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [{"id": "rule-a"}, {"id": "rule-b"}])
    def fake_set(ids, disabled):
        captured["ids"] = list(ids)
        captured["disabled"] = disabled
    monkeypatch.setattr(patterns_bp.grit_rule_index, "set_rules_disabled", fake_set)
    monkeypatch.setattr(patterns_bp.grit_rule_index, "regenerate",
                        lambda write_guides=False: None)
    monkeypatch.setattr(patterns_bp, "deploy_rules_index_skill",
                        lambda _p: None)

    resp = flask_client.post("/api/patterns/p/rules/disable",
                               json={"rule_ids": ["rule-b"]},
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True
    assert "disabled 1 rule(s)" in body["msg"]
    assert "rule-b" in body["msg"]
    assert captured == {"ids": ["rule-b"], "disabled": True}


def test_rule_disable_specific_ids_not_linked(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    from web.blueprints import patterns as patterns_bp
    monkeypatch.setattr(patterns_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [{"id": "rule-a"}])

    resp = flask_client.post("/api/patterns/p/rules/disable",
                               json={"rule_ids": ["nope"]},
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is False
    assert "no requested rules" in body["msg"]


def test_rule_enable_deploys_missing_skill(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    from web.blueprints import patterns as patterns_bp
    from lib.skills import skill_registry, skill_sync
    monkeypatch.setattr(patterns_bp.grit_rule_index, "rules_for_guide",
                        lambda _slug: [{"id": "r1"}])
    monkeypatch.setattr(patterns_bp.grit_rule_index, "set_rules_disabled",
                        lambda ids, disabled: None)
    monkeypatch.setattr(patterns_bp.grit_rule_index, "regenerate",
                        lambda write_guides=False: None)
    monkeypatch.setattr(patterns_bp, "deploy_rules_index_skill",
                        lambda _p: None)

    monkeypatch.setattr(skill_registry, "skill_id_for_procedure",
                        lambda _slug: "sk-id")
    monkeypatch.setattr(skill_registry, "deployed_exists",
                        lambda _sid: False)
    monkeypatch.setattr(skill_sync, "push",
                        lambda sid, force=False: "pushed sk-id -> /dep")

    resp = flask_client.post("/api/patterns/p/rules/enable",
                               headers=_editor_auth())
    body = resp.get_json()
    assert body["ok"] is True
    assert "enabled 1 rule" in body["msg"]
    assert "deployed skill" in body["msg"]


# ── description: create + detail + list + edit ───────────────

def test_create_pattern_writes_description_to_frontmatter(
        flask_client, tmp_db, isolated_patterns):
    """Regression: description must live in the YAML frontmatter, not the
    body, so the skill deployer can read it into the shim."""
    resp = flask_client.post(
        "/api/patterns/create",
        json={"title": "With Desc", "description": "Short summary."},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    slug = resp.get_json()["slug"]
    text = (isolated_patterns["patterns"] / slug / "SKILL.md").read_text()
    head, _, body = text.partition("\n---\n")
    assert 'description: "Short summary."' in head
    # Old behaviour: description was dumped into the body. It must not be.
    assert "Short summary." not in body


def test_create_pattern_collapses_multiline_description_in_frontmatter(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post(
        "/api/patterns/create",
        json={"title": "Multi", "description": "Line one.\nLine two.\n\nLine three."},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    slug = resp.get_json()["slug"]
    text = (isolated_patterns["patterns"] / slug / "SKILL.md").read_text()
    head = text.split("\n---\n", 1)[0]
    assert 'description: "Line one. Line two. Line three."' in head


def test_api_pattern_detail_returns_db_description(
        flask_client, tmp_db, isolated_patterns):
    """Detail endpoint serves description from the DB column (cheap read);
    the SKILL.md file is no longer parsed at request time."""
    slug = "with-desc"
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    (pd / "SKILL.md").write_text(
        f'---\ntitle: "X"\ndescription: "A short summary."\nprocedure: {slug}\n---\nBody'
    )
    _seed_pattern_doc(slug, description="A short summary.")
    resp = flask_client.get(f"/api/patterns/{slug}")
    assert resp.status_code == 200
    assert resp.get_json()["description"] == "A short summary."


def test_api_patterns_listing_omits_description(
        flask_client, tmp_db, isolated_patterns):
    """Description is intentionally omitted from the list payload so the
    endpoint stays cheap. Clients render it only via the detail endpoint."""
    _seed_pattern_doc("with-desc", description="Listed.")
    resp = flask_client.get("/api/patterns")
    docs = resp.get_json()["docs"]
    entry = next(d for d in docs if d["slug"] == "with-desc")
    assert "description" not in entry


def test_save_description_requires_auth(
        anon_client, tmp_db, isolated_patterns):
    resp = anon_client.post(
        "/api/patterns/x/description", json={"description": "Hi"},
    )
    assert resp.status_code == 401


def test_save_description_unknown_returns_404(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post(
        "/api/patterns/nope/description",
        json={"description": "Hi"},
        headers=_editor_auth(),
    )
    assert resp.status_code == 404


def _seed_pattern_on_disk(isolated_patterns, slug, frontmatter_lines):
    """Helper: write a SKILL.md and seed the DB row pointing at it."""
    pd = isolated_patterns["patterns"] / slug
    pd.mkdir()
    body = "\n".join(["---", *frontmatter_lines, "---", "Body"]) + "\n"
    (pd / "SKILL.md").write_text(body)
    _seed_pattern_doc(slug)
    import os
    with SessionLocal() as session:
        import sqlmodel as _sm
        doc = session.exec(
            _sm.select(PatternDoc).where(PatternDoc.slug == slug)
        ).first()
        doc.file_path = os.path.relpath(
            str(pd / "SKILL.md"), str(isolated_patterns["root"])
        )
        session.add(doc)
        session.commit()
    return pd / "SKILL.md"


def test_save_description_inserts_into_existing_frontmatter(
        flask_client, tmp_db, isolated_patterns):
    skill_md = _seed_pattern_on_disk(
        isolated_patterns, "ins",
        ['title: "T"', "procedure: ins", "manual: true"],
    )
    resp = flask_client.post(
        "/api/patterns/ins/description",
        json={"description": "Now described."},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    text = skill_md.read_text()
    assert 'description: "Now described."' in text
    # Order: description goes right after title.
    head = text.split("---", 2)[1]
    title_idx = head.index("title:")
    desc_idx = head.index("description:")
    proc_idx = head.index("procedure:")
    assert title_idx < desc_idx < proc_idx


def test_save_description_replaces_existing_block_scalar(
        flask_client, tmp_db, isolated_patterns):
    skill_md = _seed_pattern_on_disk(
        isolated_patterns, "rep",
        [
            'title: "T"',
            "description: 'old line one",
            "  old line two.'",
            "procedure: rep",
        ],
    )
    resp = flask_client.post(
        "/api/patterns/rep/description",
        json={"description": "fresh."},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    text = skill_md.read_text()
    assert 'description: "fresh."' in text
    assert "old line" not in text


def test_save_description_empty_removes_line(
        flask_client, tmp_db, isolated_patterns):
    skill_md = _seed_pattern_on_disk(
        isolated_patterns, "rm",
        ['title: "T"', 'description: "to be cleared"', "procedure: rm"],
    )
    resp = flask_client.post(
        "/api/patterns/rm/description",
        json={"description": "   "},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    text = skill_md.read_text()
    assert "description:" not in text
    # Other frontmatter survives.
    assert "procedure: rm" in text


def test_save_description_roundtrip_through_yaml(
        flask_client, tmp_db, isolated_patterns):
    """End-to-end: a description containing both `"` and a newline must
    round-trip through POST → GET and the file must remain valid YAML."""
    import yaml

    skill_md = _seed_pattern_on_disk(
        isolated_patterns, "rt", ['title: "T"', "procedure: rt"],
    )
    payload = 'Quote: "tricky"\nand a second line.'
    resp = flask_client.post(
        "/api/patterns/rt/description",
        json={"description": payload},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200

    # On-disk YAML still parses cleanly.
    text = skill_md.read_text()
    fm_text = text.split("---", 2)[1]
    parsed = yaml.safe_load(fm_text)
    assert parsed["description"] == 'Quote: "tricky" and a second line.'

    # Detail GET returns the same single-line value.
    resp2 = flask_client.get("/api/patterns/rt")
    assert resp2.get_json()["description"] == 'Quote: "tricky" and a second line.'


# ── POST /api/patterns/import-dir: selective `selected` field ─

def test_import_dir_selected_non_list_returns_400(
        flask_client, tmp_db, isolated_patterns):
    resp = flask_client.post(
        "/api/patterns/import-dir",
        json={"path": str(isolated_patterns["root"]), "selected": "not-a-list"},
        headers=_editor_auth(),
    )
    assert resp.status_code == 400
    assert "list of names" in resp.get_json()["msg"]


def test_import_dir_threads_selected_subset_to_importer(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    from lib.patterns import pattern_importer
    captured = {}

    def fake_batch(root_dir, *, on_conflict="skip", dry_run=False,
                   only=None, progress=None):
        captured["only"] = only
        return []

    monkeypatch.setattr(
        pattern_importer, "batch_import_skill_directory", fake_batch,
    )
    resp = flask_client.post(
        "/api/patterns/import-dir",
        json={"path": str(isolated_patterns["root"]),
              "selected": ["keep-me", "and-me"]},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    assert captured["only"] == ["keep-me", "and-me"]


def test_import_dir_omitted_selected_imports_all(
        flask_client, tmp_db, isolated_patterns, monkeypatch):
    """No `selected` key ⇒ only=None ⇒ historical import-everything."""
    from lib.patterns import pattern_importer
    captured = {"only": "sentinel"}

    def fake_batch(root_dir, *, on_conflict="skip", dry_run=False,
                   only=None, progress=None):
        captured["only"] = only
        return []

    monkeypatch.setattr(
        pattern_importer, "batch_import_skill_directory", fake_batch,
    )
    resp = flask_client.post(
        "/api/patterns/import-dir",
        json={"path": str(isolated_patterns["root"])},
        headers=_editor_auth(),
    )
    assert resp.status_code == 200
    assert captured["only"] is None
