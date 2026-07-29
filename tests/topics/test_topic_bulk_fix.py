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
from lib.topics.validation import BRANCH_OWNED_REF_CODE, audit_graph


def _seed_repo(path) -> str:
    with SessionLocal() as s:
        s.add(Repo(name="bulk-fix-repo", path=str(path), default_branch="main", is_active=1))
        s.commit()
    return "bulk-fix-repo"


# ── Composer unit tests ─────────────────────────────────────────────


def test_compose_fix_drops_dead_refs_only_for_selected_topic(fake_git_repo):
    """Every ref here is absent from the working tree *and* from every branch
    tip, so all of them are genuinely dead and all of them get stripped. The
    audit must run against a real repo: the branch lookup fails open, so on a
    bare directory git cannot rule out an owning branch and nothing is dead."""
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
    issues = audit_graph(graph, repo_path=fake_git_repo)

    fixes = compose_fix(graph, issues, codes_to_fix={"graph.dead_ref"})
    by_topic = {tid: (cleaned, before) for tid, cleaned, before in fixes}
    assert "alpha" in by_topic
    assert "beta" in by_topic
    alpha_cleaned, _ = by_topic["alpha"]
    assert alpha_cleaned["refs"] == []
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


# ── CAI-30: branch-owned anchors are not "dead" ─────────────────────


def _graph_with_refs(*paths):
    return {
        "version": 1, "repo": "demo", "topics": {
            "alpha": {
                "label": "A", "intent": "a", "status": "active",
                "aliases": [],
                "refs": [{"path": p, "role": "implementation"} for p in paths],
                "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
            },
        },
    }


def test_audit_separates_branch_owned_refs_from_dead_ones(
    fake_git_repo, branch_owned_ref,
):
    """Both are absent from this checkout, but only one is recoverable."""
    graph = _graph_with_refs(branch_owned_ref, "gone.py")

    issues = audit_graph(graph, repo_path=fake_git_repo)
    by_code = {i.code: i for i in issues if i.code in
               {"graph.dead_ref", BRANCH_OWNED_REF_CODE}}

    assert by_code[BRANCH_OWNED_REF_CODE].paths == (branch_owned_ref,)
    assert by_code["graph.dead_ref"].paths == ("gone.py",)
    assert "present on an unmerged branch" \
        in by_code[BRANCH_OWNED_REF_CODE].message


def test_compose_fix_refuses_to_strip_a_branch_owned_ref(
    fake_git_repo, branch_owned_ref,
):
    """The audit panel's one-click strip would otherwise delete an anchor whose
    file is alive on an unmerged branch, with no way to recover it (CAI-30)."""
    graph = _graph_with_refs(branch_owned_ref, "gone.py")
    issues = audit_graph(graph, repo_path=fake_git_repo)

    fixes = compose_fix(graph, issues, codes_to_fix=AUTO_FIXABLE_CODES)

    assert len(fixes) == 1
    _, cleaned, _ = fixes[0]
    assert [r["path"] for r in cleaned["refs"]] == [branch_owned_ref]


def test_branch_owned_ref_code_is_never_auto_fixable(
    fake_git_repo, branch_owned_ref,
):
    """Asking for it explicitly must not open a back door around the guard."""
    graph = _graph_with_refs(branch_owned_ref)
    issues = audit_graph(graph, repo_path=fake_git_repo)

    assert BRANCH_OWNED_REF_CODE not in AUTO_FIXABLE_CODES
    assert compose_fix(graph, issues, codes_to_fix={BRANCH_OWNED_REF_CODE}) == []


def test_audit_fix_endpoint_leaves_branch_owned_refs_alone(
    flask_client, fake_git_repo, branch_owned_ref,
):
    """End-to-end: the panel offers no fix for the code, and selecting every
    code it does offer still leaves the branch-owned anchor in the graph."""
    name = _seed_repo(fake_git_repo)
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = _graph_with_refs(
        branch_owned_ref, "gone.py")["topics"]["alpha"]
    write_split_graph(fake_git_repo, graph)

    audit = flask_client.get(f"/api/repos/{name}/topics/audit").get_json()
    assert BRANCH_OWNED_REF_CODE in audit["by_code"]
    assert BRANCH_OWNED_REF_CODE not in audit["auto_fixable_codes"]

    resp = flask_client.post(
        f"/api/repos/{name}/topics/audit/fix",
        json={"issue_codes": audit["auto_fixable_codes"]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    after = flask_client.get(f"/api/repos/{name}/topics/audit").get_json()
    assert "graph.dead_ref" not in after["by_code"]
    assert [i["paths"] for i in after["by_code"][BRANCH_OWNED_REF_CODE]] \
        == [[branch_owned_ref]]


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
