"""Diff-scoped topic-wiki freshness audit (`lib/topics/wiki_debt`).

A topic with refs but no `wiki/<id>.md` is `missing`; a topic whose digested
ref changed (and which already has a wiki) is `drifted`; a healthy topic is
omitted. `--changed-since` narrows the audit to topics owning a file changed
between a git ref and HEAD.
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import DEVNULL, check_call

from lib.topics.core import topic_path
from lib.topics.proposals import load_proposal
from lib.topics.ref_digest import capture_ref_digests
from lib.topics.snapshots import resolve_or_create_repo
from lib.topics.wiki import wiki_dir
from lib.topics.wiki_debt import emit_wiki_debt_proposals, wiki_debt


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _write_graph(repo: Path, topics: dict) -> None:
    p = topic_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "repo": repo.name, "topics": topics}))


def _write_wiki(repo: Path, topic_id: str) -> None:
    wd = wiki_dir(repo)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / f"{topic_id}.md").write_text(f"# {topic_id}\n\nnarrative\n")


def _commit_all(repo: Path, message: str) -> None:
    check_call(["git", "-C", str(repo), "add", "."], stdout=DEVNULL, stderr=DEVNULL)
    check_call(["git", "-C", str(repo), "commit", "-q", "-m", message],
               stdout=DEVNULL, stderr=DEVNULL)


def test_topic_without_wiki_is_missing(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    resolve_or_create_repo(str(repo))

    rows = wiki_debt(repo)
    assert rows == [{"topic_id": "t1", "status": "missing",
                     "drifted_paths": [], "changed_refs": ["a.py"]}]


def test_topic_with_wiki_and_no_drift_is_healthy(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    resolve_or_create_repo(str(repo))
    capture_ref_digests(repo, "t1")
    _write_wiki(repo, "t1")

    assert wiki_debt(repo) == []


def test_drifted_ref_with_wiki_is_drifted(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    resolve_or_create_repo(str(repo))
    capture_ref_digests(repo, "t1")
    _write_wiki(repo, "t1")
    (repo / "a.py").write_text("MUTATED\n")  # hash now differs from digest

    rows = wiki_debt(repo)
    assert rows == [{"topic_id": "t1", "status": "drifted",
                     "drifted_paths": ["a.py"], "changed_refs": ["a.py"]}]


def test_changed_since_scopes_to_diff(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("a\n")
    (repo / "b.py").write_text("b\n")
    _write_graph(repo, {
        "t_a": _topic([{"path": "a.py"}]),
        "t_b": _topic([{"path": "b.py"}]),
    })
    resolve_or_create_repo(str(repo))
    _commit_all(repo, "add a and b")          # both topics have no wiki here

    (repo / "a.py").write_text("a changed\n")  # only a.py moves
    _commit_all(repo, "touch a")

    rows = wiki_debt(repo, changed_since="HEAD~1")
    # b.py was untouched by the last commit → t_b is out of scope
    assert [r["topic_id"] for r in rows] == ["t_a"]
    assert rows[0]["status"] == "missing"


# ── emit: stub proposals for drifted, report-only for missing ──


def test_emit_creates_stub_proposal_for_drifted(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    resolve_or_create_repo(str(repo))
    capture_ref_digests(repo, "t1")
    _write_wiki(repo, "t1")
    (repo / "a.py").write_text("MUTATED\n")  # drift

    rows = emit_wiki_debt_proposals(repo)
    assert len(rows) == 1
    pid = rows[0]["proposal_id"]
    assert pid == "content-drift-t1"            # deterministic id

    proposal = load_proposal(repo, pid)         # actually persisted
    assert proposal["status"] == "pending_review"
    assert proposal["metadata"]["drifted_paths"] == ["a.py"]


def test_emit_leaves_missing_report_only(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    resolve_or_create_repo(str(repo))
    # no digest, no wiki → status "missing"; no agent-free draft path

    rows = emit_wiki_debt_proposals(repo)
    assert rows[0]["status"] == "missing"
    assert rows[0]["proposal_id"] is None       # not emitted


def test_emit_is_idempotent(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    resolve_or_create_repo(str(repo))
    capture_ref_digests(repo, "t1")
    _write_wiki(repo, "t1")
    (repo / "a.py").write_text("MUTATED\n")

    first = emit_wiki_debt_proposals(repo)[0]["proposal_id"]
    second = emit_wiki_debt_proposals(repo)[0]["proposal_id"]
    assert first == second == "content-drift-t1"  # UPSERT, no stacking
