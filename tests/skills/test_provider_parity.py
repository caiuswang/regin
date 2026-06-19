"""End-to-end verification of pattern push/unpush + provider features.

Drives the REAL Flask endpoints across Claude/Kimi provider switches against
isolated temp dirs. Run via pytest; deleted after verification.
"""

from __future__ import annotations

import os
import pytest

from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.settings import settings, ProviderConfig


def _seed_pattern(patterns_dir, slug):
    d = patterns_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f'---\ntitle: "{slug.title()}"\nprocedure: {slug}\n---\n'
        f'## Disciplines\n- be good\n\n## Anti-Patterns\n- be bad\n'
    )
    (d / "references").mkdir(exist_ok=True)
    (d / "references" / "note.md").write_text("ref\n")
    return d


@pytest.fixture
def harness(tmp_path, monkeypatch):
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    claude_global = tmp_path / "claude_global"
    claude_global.mkdir()
    kimi_global = tmp_path / "kimi_global"
    kimi_global.mkdir()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "skills_dir", str(claude_global))
    monkeypatch.setattr(settings, "active_provider", "claude")
    # Kimi global override → temp dir so global deploy never touches ~/.
    monkeypatch.setattr(settings, "providers", {
        "kimi": ProviderConfig(skills_dir=str(kimi_global)),
    })

    _seed_pattern(patterns, "demo-skill")

    with SessionLocal() as s:
        repo = Repo(name="myrepo", path=str(repo_dir), is_active=1)
        s.add(repo)
        s.commit()
        repo_id = repo.id

    return {
        "patterns": patterns, "claude_global": claude_global,
        "kimi_global": kimi_global, "repo_dir": repo_dir,
        "repo_id": repo_id, "monkeypatch": monkeypatch, "tmp": tmp_path,
    }


def _set_providers(mp, mapping):
    mp.setattr(settings, "providers", mapping)


def test_claude_project_push_and_unpush(flask_client, harness):
    mp, rid, repo = harness["monkeypatch"], harness["repo_id"], harness["repo_dir"]
    mp.setattr(settings, "active_provider", "claude")
    _set_providers(mp, {})

    r = flask_client.post("/api/skills/demo-skill/push-to-project",
                          json={"project_id": rid})
    body = r.get_json()
    assert body["ok"], body
    landed = repo / ".claude" / "skills" / "demo-skill" / "SKILL.md"
    assert landed.is_file(), "claude project push must land in .claude/skills"
    assert (repo / ".claude" / "skills" / "demo-skill" / "references" / "note.md").is_file()

    # Deployments: one tracked claude row, NO phantom untracked.
    dep = flask_client.get("/api/skills/demo-skill/deployments").get_json()
    proj = [d for d in dep["deployments"] if d["scope"] == "project"]
    assert len(proj) == 1, proj
    assert proj[0]["provider"] == "claude"
    assert proj[0]["tracked"] is True
    assert not any(d.get("tracked") is False for d in dep["deployments"]), "no phantom untracked"

    # Unpush.
    r = flask_client.delete(f"/api/skills/demo-skill/project-deployment/{rid}")
    assert r.get_json()["ok"], r.get_json()
    assert not landed.exists(), "files must be removed on unpush"
    dep2 = flask_client.get("/api/skills/demo-skill/deployments").get_json()
    assert not [d for d in dep2["deployments"] if d["scope"] == "project"], "row gone"


def test_kimi_project_push_and_unpush(flask_client, harness):
    mp, rid, repo = harness["monkeypatch"], harness["repo_id"], harness["repo_dir"]
    mp.setattr(settings, "active_provider", "kimi")
    _set_providers(mp, {"kimi": ProviderConfig(skills_dir=str(harness["kimi_global"]),
                                               enabled=True)})

    r = flask_client.post("/api/skills/demo-skill/push-to-project",
                          json={"project_id": rid})
    body = r.get_json()
    assert body["ok"], body
    landed = repo / ".kimi-code" / "skills" / "demo-skill" / "SKILL.md"
    assert landed.is_file(), "kimi project push must land in .kimi-code/skills"

    dep = flask_client.get("/api/skills/demo-skill/deployments").get_json()
    proj = [d for d in dep["deployments"] if d["scope"] == "project"]
    assert len(proj) == 1, proj
    assert proj[0]["provider"] == "kimi"
    assert not any(d.get("tracked") is False for d in dep["deployments"])

    r = flask_client.delete(f"/api/skills/demo-skill/project-deployment/{rid}")
    assert r.get_json()["ok"]
    assert not landed.exists()


def test_both_providers_push_lands_in_both(flask_client, harness):
    mp, rid, repo = harness["monkeypatch"], harness["repo_id"], harness["repo_dir"]
    mp.setattr(settings, "active_provider", "claude")
    _set_providers(mp, {"kimi": ProviderConfig(skills_dir=str(harness["kimi_global"]),
                                               enabled=True)})

    r = flask_client.post("/api/skills/demo-skill/push-to-project",
                          json={"project_id": rid})
    body = r.get_json()
    assert body["ok"], body
    claude_landed = repo / ".claude" / "skills" / "demo-skill" / "SKILL.md"
    kimi_landed = repo / ".kimi-code" / "skills" / "demo-skill" / "SKILL.md"
    assert claude_landed.is_file() and kimi_landed.is_file(), "must land in BOTH"
    providers_in_resp = {p["provider"] for p in body["per_provider"]}
    assert providers_in_resp == {"claude", "kimi"}, providers_in_resp

    dep = flask_client.get("/api/skills/demo-skill/deployments").get_json()
    proj = sorted(d["provider"] for d in dep["deployments"] if d["scope"] == "project")
    assert proj == ["claude", "kimi"], proj
    assert not any(d.get("tracked") is False for d in dep["deployments"])

    # Unpush with no provider arg removes BOTH.
    r = flask_client.delete(f"/api/skills/demo-skill/project-deployment/{rid}")
    assert r.get_json()["ok"]
    assert not claude_landed.exists() and not kimi_landed.exists(), "both removed"


def test_untracked_detection_and_backfill(flask_client, harness):
    mp, rid, repo = harness["monkeypatch"], harness["repo_id"], harness["repo_dir"]
    mp.setattr(settings, "active_provider", "claude")
    _set_providers(mp, {})

    # Simulate a manual on-disk deploy with NO DB row.
    manual = repo / ".claude" / "skills" / "demo-skill"
    manual.mkdir(parents=True)
    (manual / "SKILL.md").write_text("manual\n")

    dep = flask_client.get("/api/skills/demo-skill/deployments").get_json()
    untracked = [d for d in dep["deployments"] if d.get("tracked") is False]
    assert len(untracked) == 1, untracked
    assert untracked[0]["provider"] == "claude"

    # Backfill records the row.
    r = flask_client.post("/api/skills/demo-skill/backfill-deployment",
                          json={"project_id": rid, "provider": "claude"})
    assert r.get_json()["ok"], r.get_json()
    dep2 = flask_client.get("/api/skills/demo-skill/deployments").get_json()
    proj = [d for d in dep2["deployments"] if d["scope"] == "project"]
    assert len(proj) == 1 and proj[0]["tracked"] is True
    assert not [d for d in dep2["deployments"] if d.get("tracked") is False], "no longer untracked"


def test_provider_settings_get_and_put(flask_client, harness):
    mp = harness["monkeypatch"]
    mp.setattr(settings, "active_provider", "claude")
    # Need experimental_providers so kimi is visible.
    mp.setattr(settings, "experimental_providers", True)

    g = flask_client.get("/api/settings/providers").get_json()
    ids = {p["id"] for p in g["providers"]}
    assert "claude" in ids and "kimi" in ids, ids
    assert any(h["name"] for h in g["handler_defaults"]), "handler defaults present"
    claude_row = next(p for p in g["providers"] if p["id"] == "claude")
    assert claude_row["active"] is True
    assert claude_row["enabled"] is True  # active is always enabled

    # PUT: enable kimi + a disabled handler + a priority override.
    handler = g["handler_defaults"][0]["name"]
    r = flask_client.put("/api/settings/providers", json={"providers": {
        "kimi": {
            "enabled": True,
            "disabled_handlers": [handler],
            "priority_overrides": {handler: 42},
        }
    }})
    assert r.get_json()["ok"], r.get_json()


def test_global_undeploy_across_providers(flask_client, harness):
    mp, repo = harness["monkeypatch"], harness["repo_dir"]
    mp.setattr(settings, "active_provider", "claude")
    _set_providers(mp, {"kimi": ProviderConfig(skills_dir=str(harness["kimi_global"]),
                                               enabled=True)})
    # A deployed skill dir present in BOTH global skills dirs.
    for base in (harness["claude_global"], harness["kimi_global"]):
        d = base / "demo-skill"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("deployed\n")

    r = flask_client.post("/api/skills/demo-skill/undeploy")
    body = r.get_json()
    assert body["ok"], body
    assert "claude" in body["msg"] and "kimi" in body["msg"], body["msg"]
    assert not (harness["claude_global"] / "demo-skill").exists(), "claude global removed"
    assert not (harness["kimi_global"] / "demo-skill").exists(), "kimi global removed"


def test_hook_config_per_provider_override(harness):
    """Settings-based per-provider handler overrides flow into the
    runner-facing read path (is_enabled / effective_priority), scoped to the
    right provider only. Isolated config paths keep it hermetic."""
    mp, tmp = harness["monkeypatch"], harness["tmp"]
    from hook_manager import registry as hreg, config as hcfg
    names = [h.name for h in hreg.REGISTRY]
    off, bumped = names[0], names[1]
    mp.setattr(settings, "providers", {
        "claude": ProviderConfig(
            hook_manager_config_path=str(tmp / "claude-hmc.json")),
        "kimi": ProviderConfig(
            enabled=True,
            hook_manager_config_path=str(tmp / "kimi-hmc.json"),
            disabled_handlers=[off],
            priority_overrides={bumped: 777}),
    })
    # Kimi sees the override...
    assert hcfg.is_enabled(off, "kimi") is False
    assert hcfg.effective_priority(bumped, 10, "kimi") == 777
    # ...Claude does not (different provider).
    assert hcfg.is_enabled(off, "claude") is True
    assert hcfg.effective_priority(bumped, 10, "claude") == 10


def test_provider_handler_config_merge(harness):
    """Per-provider handler overrides flow into hook_manager read_config."""
    mp = harness["monkeypatch"]
    handler_name = "trace"  # any registered handler; merge is generic
    from hook_manager import registry as hreg
    names = [h.name for h in hreg.REGISTRY]
    handler_name = names[0]
    mp.setattr(settings, "providers", {
        "kimi": ProviderConfig(
            enabled=True,
            disabled_handlers=[handler_name],
            priority_overrides={names[-1]: 999},
        ),
    })
    from lib.providers import provider_handler_config
    cfg = provider_handler_config("kimi")
    assert handler_name in cfg["disabled_handlers"]
    assert cfg["priority_overrides"][names[-1]] == 999
    # A provider with no config returns empty (claude here).
    empty = provider_handler_config("claude")
    assert empty == {"disabled_handlers": [], "priority_overrides": {}}
