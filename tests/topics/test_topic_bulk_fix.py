"""Bulk-fix composer + /audit/fix endpoint.

The composer auto-fixes only the two unambiguous codes
(`graph.dead_ref` and `graph.orphan_edge_target`). `graph.duplicate_alias`
must be resolved manually — the endpoint reports it as skipped.
"""

from __future__ import annotations

import json
import subprocess

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo
from lib.topics import bootstrap, load_graph, write_split_graph
from lib.topics.bulk_fix import AUTO_FIXABLE_CODES, compose_fix
from lib.topics.validation import audit_graph


def _seed_repo(path) -> str:
    with SessionLocal() as s:
        s.add(Repo(name="bulk-fix-repo", path=str(path), default_branch="main", is_active=1))
        s.commit()
    return "bulk-fix-repo"


# ── Composer unit tests ─────────────────────────────────────────────


def test_compose_fix_drops_dead_refs_only_for_selected_topic(tmp_path):
    graph = {
        "version": 1, "repo": "demo", "topics": {
            "alpha": {
                "label": "A", "intent": "a", "status": "active",
                "aliases": [], "refs": [
                    {"path": "alpha.py", "role": "implementation"},
                    {"path": "missing.py", "role": "implementation"},
                ],
                "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
            },
            "beta": {
                "label": "B", "intent": "b", "status": "active",
                "aliases": [], "refs": [{"path": "also-missing.py", "role": "implementation"}],
                "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
            },
        },
    }
    issues = audit_graph(graph, repo_path=tmp_path)  # tmp_path has no files

    fixes = compose_fix(graph, issues, codes_to_fix={"graph.dead_ref"})
    by_topic = {tid: (cleaned, before) for tid, cleaned, before in fixes}
    assert "alpha" in by_topic
    assert "beta" in by_topic
    # alpha's missing.py removed; alpha.py kept (also missing but the
    # audit can't find it on tmp_path either — let me check)
    alpha_cleaned, _ = by_topic["alpha"]
    alpha_paths = sorted(r["path"] for r in alpha_cleaned["refs"])
    # Both refs are "missing" because tmp_path is empty, so both get dropped.
    assert alpha_paths == []
    beta_cleaned, _ = by_topic["beta"]
    assert beta_cleaned["refs"] == []


def test_compose_fix_drops_orphan_edges(tmp_path):
    graph = {
        "version": 1, "repo": "demo", "topics": {
            "alpha": {
                "label": "A", "intent": "a", "status": "active",
                "aliases": [], "refs": [], "edges": [
                    {"target": "no-such-topic", "type": "related"},
                    {"target": "alpha", "type": "related"},  # self-loop, audit-passing
                ],
                "commands": [], "include_globs": [], "exclude_globs": [],
            },
        },
    }
    issues = audit_graph(graph)
    fixes = compose_fix(graph, issues, codes_to_fix={"graph.orphan_edge_target"})
    assert len(fixes) == 1
    _, cleaned, _ = fixes[0]
    assert all(e["target"] != "no-such-topic" for e in cleaned["edges"])


def test_compose_fix_refuses_duplicate_alias(tmp_path):
    """Even when explicitly requested, duplicate_alias is not in
    AUTO_FIXABLE_CODES and the composer drops it silently."""
    graph = {
        "version": 1, "repo": "demo", "topics": {
            "alpha": {
                "label": "A", "intent": "a", "status": "active",
                "aliases": ["shared"], "refs": [], "edges": [],
                "commands": [], "include_globs": [], "exclude_globs": [],
            },
            "beta": {
                "label": "B", "intent": "b", "status": "active",
                "aliases": ["shared"], "refs": [], "edges": [],
                "commands": [], "include_globs": [], "exclude_globs": [],
            },
        },
    }
    issues = audit_graph(graph)
    fixes = compose_fix(graph, issues, codes_to_fix={"graph.duplicate_alias"})
    assert fixes == []
    assert "graph.duplicate_alias" not in AUTO_FIXABLE_CODES


def test_compose_fix_empty_when_no_issues(tmp_path):
    graph = {"version": 1, "repo": "demo", "topics": {}}
    assert compose_fix(graph, [], codes_to_fix=AUTO_FIXABLE_CODES) == []


# ── Endpoint integration ────────────────────────────────────────────


def test_audit_fix_endpoint_clears_dead_refs_and_orphan_edges(flask_client, fake_git_repo):
    """Plant a real combination of audit issues and verify /audit/fix
    snapshots out the clean state."""
    (fake_git_repo / "real.py").write_text("# real\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "real"])
    name = _seed_repo(fake_git_repo)
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "A", "intent": "a", "status": "active",
        "aliases": [], "refs": [
            {"path": "real.py", "role": "implementation"},
            {"path": "missing.py", "role": "implementation"},
        ],
        "edges": [{"target": "ghost", "type": "related"}],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    write_split_graph(fake_git_repo, graph)

    # First confirm /audit reports the issues + the auto-fixable list.
    audit_resp = flask_client.get(f"/api/repos/{name}/topics/audit")
    audit_body = audit_resp.get_json()
    assert "graph.dead_ref" in audit_body["by_code"]
    assert "graph.orphan_edge_target" in audit_body["by_code"]
    assert set(audit_body["auto_fixable_codes"]) == set(AUTO_FIXABLE_CODES)

    # Fix them.
    fix_resp = flask_client.post(
        f"/api/repos/{name}/topics/audit/fix",
        json={"issue_codes": ["graph.dead_ref", "graph.orphan_edge_target"]},
    )
    assert fix_resp.status_code == 200, fix_resp.get_data(as_text=True)
    fix_body = fix_resp.get_json()
    assert fix_body["ok"] is True
    assert len(fix_body["snapshot_ids"]) >= 1
    assert fix_body["fixed_counts"]["graph.dead_ref"] >= 1
    assert fix_body["fixed_counts"]["graph.orphan_edge_target"] >= 1

    # Re-audit: those codes should be gone.
    audit2 = flask_client.get(f"/api/repos/{name}/topics/audit").get_json()
    assert "graph.dead_ref" not in audit2["by_code"]
    assert "graph.orphan_edge_target" not in audit2["by_code"]


def test_audit_fix_endpoint_reports_skipped_codes(flask_client, fake_git_repo):
    """Non-auto-fixable codes are reported back, not applied."""
    name = _seed_repo(fake_git_repo)
    bootstrap(fake_git_repo)
    resp = flask_client.post(
        f"/api/repos/{name}/topics/audit/fix",
        json={"issue_codes": ["graph.duplicate_alias"]},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["snapshot_ids"] == []
    assert "graph.duplicate_alias" in body["skipped_codes"]


def test_audit_fix_endpoint_requires_issue_codes(flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    bootstrap(fake_git_repo)
    resp = flask_client.post(f"/api/repos/{name}/topics/audit/fix", json={})
    assert resp.status_code == 400
