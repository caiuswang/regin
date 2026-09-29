"""Characterization of `/api/patterns/<slug>/delete` (deployed dirs, tag
links, embeddings, skill undeploy) and `/api/patterns/import-dir`
(counts, audit gate, result shape)."""

from __future__ import annotations

import pytest
import sqlmodel as _sm

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDeployment, PatternDoc, Tag
from lib.orm.models.patterns import PatternEmbedding
from lib.patterns.pattern_importer import BatchImportEntry
from lib.settings import settings


def _auth():
    return {"Authorization": f"Bearer {create_token(1, 'editor-tester', 'editor')}"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))
    from lib import audit
    audits = []
    monkeypatch.setattr(audit, "log_action", lambda *a, **kw: audits.append(a))
    return {"root": tmp_path, "patterns": patterns, "audits": audits}


def _seed_full(slug):
    with SessionLocal() as session:
        doc = PatternDoc(slug=slug, title="P", file_path=f"patterns/{slug}/SKILL.md",
                         category="procedure", content_hash="0" * 64)
        session.add(doc)
        session.flush()
        tag = Tag(name="t1", category="concept")
        session.add(tag)
        session.flush()
        session.add(DocTag(doc_id=doc.id, tag_id=tag.id))
        session.add(PatternEmbedding(pattern_id=doc.id, content_hash="h",
                                     model_id="m", dim=1, vector=b"\0"))
        session.add(PatternDeployment(pattern_slug=slug, scope="user",
                                      deployed_path="/x"))
        session.commit()
        return doc.id


def _count(model, where):
    with SessionLocal() as session:
        return len(session.exec(_sm.select(model).where(where)).all())


@pytest.mark.parametrize("skill_id,undeploy_raises", [
    (None, False), ("skill-x", False), ("skill-x", True),
])
def test_delete_pattern_full_cleanup(flask_client, tmp_db, env, monkeypatch,
                                     skill_id, undeploy_raises):
    slug = "doomed"
    (env["patterns"] / slug).mkdir()
    (env["patterns"] / slug / "SKILL.md").write_text("body")
    deployed = env["root"] / "deployed"
    deployed.mkdir()
    doc_id = _seed_full(slug)

    from lib.patterns import pattern_deployments
    from lib.skills import skill_registry, skill_sync
    monkeypatch.setattr(pattern_deployments, "list_deployments",
                        lambda pattern_slug=None: [
                            {"deployed_path": str(deployed)},
                            {"deployed_path": str(env["root"] / "missing")},
                            {"deployed_path": None}])
    monkeypatch.setattr(skill_registry, "skill_id_for_procedure", lambda s: skill_id)
    undeployed = []

    def _undeploy(sid):
        undeployed.append(sid)
        if undeploy_raises:
            raise RuntimeError("boom")
    monkeypatch.setattr(skill_sync, "undeploy", _undeploy)

    resp = flask_client.post(f"/api/patterns/{slug}/delete", headers=_auth())
    assert (resp.status_code, resp.get_json()) == (200, {
        "ok": True,
        "msg": f'Deleted pattern "{slug}" + 1 deployed skill(s)',
        "removed_deployments": [str(deployed)],
    })
    remaining = [
        _count(PatternDoc, PatternDoc.slug == slug),
        _count(DocTag, DocTag.doc_id == doc_id),
        _count(PatternEmbedding, PatternEmbedding.pattern_id == doc_id),
        _count(PatternDeployment, PatternDeployment.pattern_slug == slug),
        int(deployed.exists()), int((env["patterns"] / slug).exists()),
    ]
    assert remaining == [0] * 6
    assert undeployed == ([skill_id] if skill_id else [])
    assert env["audits"][-1][2:] == ("delete_pattern", f"patterns/{slug}")


def _entry(name, status):
    return BatchImportEntry(name=name, status=status, slug=f"s-{name}",
                            title=f"T {name}", file_count=2, error=None,
                            grit_rules=["r"], grit_languages=["py"],
                            enabled_languages=["py"])


@pytest.fixture
def batch(monkeypatch):
    from lib.patterns import pattern_importer
    state = {"results": [], "calls": []}

    def fake(root_dir, *, on_conflict="skip", dry_run=False, only=None, progress=None):
        state["calls"].append((root_dir, on_conflict, dry_run, only))
        if state.get("raise"):
            raise pattern_importer.ImportError_("bad dir")
        return state["results"]
    monkeypatch.setattr(pattern_importer, "batch_import_skill_directory", fake)
    return state


def _post(client, body):
    resp = client.post("/api/patterns/import-dir", json=body, headers=_auth())
    return resp.status_code, resp.get_json()


def test_import_dir_missing_path(flask_client, tmp_db, env, batch):
    assert _post(flask_client, {"path": "  "}) == (
        400, {"ok": False, "msg": "missing 'path'"})


def test_import_dir_not_a_directory(flask_client, tmp_db, env, batch):
    target = str(env["root"] / "nope")
    assert _post(flask_client, {"path": target}) == (
        400, {"ok": False, "msg": f"not a directory: {target}"})


def test_import_dir_importer_error(flask_client, tmp_db, env, batch):
    batch["raise"] = True
    assert _post(flask_client, {"path": str(env["root"])}) == (
        400, {"ok": False, "msg": "bad dir"})


@pytest.mark.parametrize("statuses,dry_run,selected,audit", [
    (["imported", "skipped", "imported"], False, None, "all"),
    (["overwritten"], False, ["a"], 1),
    (["renamed", "failed"], False, [], 0),
    (["skipped", "failed"], False, None, None),
    (["imported"], True, None, None),
])
def test_import_dir_counts_and_audit(flask_client, tmp_db, env, batch,
                                     statuses, dry_run, selected, audit):
    batch["results"] = [_entry(f"n{i}", s) for i, s in enumerate(statuses)]
    body = {"path": str(env["root"]), "on_conflict": " Rename ", "dry_run": dry_run}
    if selected is not None:
        body["selected"] = selected
    status, got = _post(flask_client, body)
    counts = {}
    for s in statuses:
        counts[s] = counts.get(s, 0) + 1
    assert (status, got) == (200, {
        "ok": True, "path": str(env["root"]), "on_conflict": "rename",
        "dry_run": dry_run, "counts": counts,
        "results": [{"name": f"n{i}", "status": s, "slug": f"s-n{i}",
                     "title": f"T n{i}", "file_count": 2, "error": None,
                     "grit_rules": ["r"], "grit_languages": ["py"],
                     "enabled_languages": ["py"]}
                    for i, s in enumerate(statuses)],
    })
    expected_audits = [] if audit is None else [(
        1, "editor-tester", "import_dir", f"path:{env['root']}",
        {"on_conflict": "rename", "counts": counts, "selected": audit})]
    assert env["audits"] == expected_audits
