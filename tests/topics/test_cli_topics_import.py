"""CLI tests for `regin topics import` — the post-pull sync command.

Covers the multi-user-via-git use case: each user keeps their own
local SQLite, but the approved split graph + `.regin/topics/wiki/*.md`
travel through git, and `regin topics import` makes a teammate's
approved state routable locally.
"""

from __future__ import annotations

import json

from sqlmodel import select
from typer.testing import CliRunner

from cli.commands import topics as topics_cmd
from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo
from lib.topics.core import topic_dir, write_split_graph


runner = CliRunner()


def _seed_repo(path) -> Repo:
    with SessionLocal() as s:
        repo = Repo(name=path.name, path=str(path), default_branch="main", is_active=1)
        s.add(repo)
        s.commit()
        s.refresh(repo)
        return repo


def _write_topic_graph(repo_dir, topics: dict) -> None:
    write_split_graph(repo_dir, {
        "version": 1, "repo": repo_dir.name,
        "updated_at": "2026-01-01T00:00:00Z", "topics": topics,
    })


def _write_wiki(repo_dir, topic_id: str, body: str) -> None:
    wiki_dir = topic_dir(repo_dir) / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / f"{topic_id}.md").write_text(body)


def _latest_snapshot(repo_id: int):
    with SessionLocal() as s:
        return s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()


_TOPIC = {
    "alpha": {
        "label": "Alpha", "intent": "a", "status": "active",
        "aliases": [], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    },
}


def test_import_errors_when_repo_unregistered(fake_git_repo):
    """User wrote a topic graph but never ran `add-repo` — surface the
    config error instead of silently no-opping."""
    _write_topic_graph(fake_git_repo, _TOPIC)

    result = runner.invoke(topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo)])

    assert result.exit_code == 1
    assert "Repo not registered" in result.stdout


def test_import_no_topic_file_exits_zero(fake_git_repo):
    """Repo registered but the graph absent (fresh clone, hook ran
    before bootstrap). Graceful no-op so the post-merge hook never
    breaks `git pull`."""
    _seed_repo(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo)])
    assert result.exit_code == 0
    assert "nothing to import" in result.stdout


def test_import_legacy_only_repo_prints_retired_hint(fake_git_repo):
    """A legacy-only repo must surface the retired-layout hint (even with
    --quiet, which the post-merge hook passes) instead of the silent
    'nothing to import' no-op."""
    import json

    _seed_repo(fake_git_repo)
    legacy = fake_git_repo / ".regin" / "topics" / "topic.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"version": 1, "repo": "demo", "topics": {}}))
    result = runner.invoke(
        topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo), "--quiet"])
    assert result.exit_code == 0
    assert "Legacy single-file layout retired" in result.stdout


def test_import_seeds_when_no_prior_snapshot(fake_git_repo):
    """First-time import for a repo that has disk content but no
    snapshot row yet — drives through the Phase 0 auto-seed path,
    which ingests wikis."""
    repo = _seed_repo(fake_git_repo)
    _write_topic_graph(fake_git_repo, _TOPIC)
    _write_wiki(fake_git_repo, "alpha", "# Alpha\n\nBody.\n")

    result = runner.invoke(topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo)])

    assert result.exit_code == 0
    assert "Seeded snapshot" in result.stdout
    snap = _latest_snapshot(repo.id)
    assert snap is not None
    assert snap.reason == "auto_seed"
    wiki_pages = json.loads(snap.wiki_pages_json)
    assert wiki_pages.get("alpha") == "# Alpha\n\nBody.\n"


def test_import_idempotent_when_in_sync(fake_git_repo):
    """Second import is a no-op — same on-disk state must not produce
    a new snapshot row. This is what makes the post-merge hook safe
    to fire on every `git switch`."""
    repo = _seed_repo(fake_git_repo)
    _write_topic_graph(fake_git_repo, _TOPIC)
    _write_wiki(fake_git_repo, "alpha", "# Alpha\n")

    runner.invoke(topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo)])
    first_snap = _latest_snapshot(repo.id)

    result = runner.invoke(topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo)])

    assert result.exit_code == 0
    assert "Already in sync" in result.stdout
    second_snap = _latest_snapshot(repo.id)
    assert second_snap.id == first_snap.id


def test_import_writes_new_snapshot_on_drift(fake_git_repo):
    """A `git pull` that updates the graph must produce a new
    snapshot row tagged with the caller-supplied reason (e.g. git_pull
    from the hook)."""
    repo = _seed_repo(fake_git_repo)
    _write_topic_graph(fake_git_repo, _TOPIC)
    runner.invoke(topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo)])
    first_snap = _latest_snapshot(repo.id)

    # Simulate upstream adding a new topic.
    updated = dict(_TOPIC)
    updated["beta"] = {
        "label": "Beta", "intent": "b", "status": "active",
        "aliases": [], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    _write_topic_graph(fake_git_repo, updated)
    _write_wiki(fake_git_repo, "beta", "# Beta\n")

    result = runner.invoke(topics_cmd.topics_app, [
        "import", "--repo", str(fake_git_repo), "--reason", "git_pull",
    ])

    assert result.exit_code == 0
    assert "Imported snapshot" in result.stdout
    assert "reason=git_pull" in result.stdout
    new_snap = _latest_snapshot(repo.id)
    assert new_snap.id != first_snap.id
    assert new_snap.reason == "git_pull"
    new_wikis = json.loads(new_snap.wiki_pages_json)
    assert "beta" in new_wikis


def test_import_preserves_in_flight_downgrade_proposal(fake_git_repo):
    """Cross-state contract: a `downgrade(X)` proposal owns a private
    copy of X's data — an upstream `git pull` that re-introduces X (or
    introduces a modified X) must not mutate the proposal's stored copy.

    Pins the design: proposals are local-only, the split graph is the
    git-shipped surface. Downgrade routes through the local overlay and
    leaves a `deleted_topics` tombstone, so an upstream re-introduction
    of the same topic id stays MASKED locally until the proposal is
    resolved (re-apply clears the tombstone). New upstream topics with no
    tombstone come through normally. The import path writes a new
    GraphSnapshot but must never reach into proposal_topics rows.
    """
    from lib.orm import SessionLocal
    from lib.orm.models import ProposalRevision, ProposalRevisionTopic, ProposalRun
    from lib.topics import bootstrap
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.proposals import downgrade_topic_to_proposal
    from lib.topics.snapshots import resolve_or_create_repo

    bootstrap(fake_git_repo)
    resolve_or_create_repo(str(fake_git_repo))
    # Seed an approved graph containing topic `x` directly on disk +
    # let auto-seed create the initial snapshot.
    write_split_graph(fake_git_repo, {
        "version": 1, "repo": fake_git_repo.name,
        "updated_at": "2026-01-01T00:00:00Z",
        "topics": {
            "x": {
                "label": "Original X", "intent": "the original",
                "status": "active",
                "aliases": ["alpha"], "refs": [], "edges": [],
                "commands": [], "include_globs": [], "exclude_globs": [],
            },
        },
    })
    load_authoritative_graph(str(fake_git_repo))

    # Downgrade X to a proposal. P stores X's data; X removed from disk.
    downgrade_topic_to_proposal(fake_git_repo, "x")

    with SessionLocal() as s:
        runs = list(s.exec(select(ProposalRun).where(
            ProposalRun.provider == "approved-topic-downgrade")))
    assert len(runs) == 1, "downgrade should create one ProposalRun"
    run_id = runs[0].id
    # Topic content lives on the latest ProposalRevision's snapshot rows.
    with SessionLocal() as s:
        rev = s.exec(
            select(ProposalRevision)
            .where(ProposalRevision.run_id == run_id)
            .where(ProposalRevision.is_latest == 1)
        ).first()
        assert rev is not None, "downgrade should create a ProposalRevision"
        proposed = list(s.exec(select(ProposalRevisionTopic).where(
            ProposalRevisionTopic.revision_id == rev.id)))
    assert len(proposed) == 1
    snapshot_of_x_in_proposal = {
        "label": proposed[0].label,
        "intent": proposed[0].intent,
        "aliases_json": proposed[0].aliases_json,
    }

    # Simulate upstream pull: the graph arrives with X back, under a
    # different label, plus a brand-new topic y.
    write_split_graph(fake_git_repo, {
        "version": 1, "repo": fake_git_repo.name,
        "updated_at": "2026-01-02T00:00:00Z",
        "topics": {
            "x": {
                "label": "Upstream-overridden X", "intent": "different",
                "status": "active",
                "aliases": ["beta"], "refs": [], "edges": [],
                "commands": [], "include_globs": [], "exclude_globs": [],
            },
            "y": {
                "label": "New from upstream", "intent": "y",
                "status": "active",
                "aliases": [], "refs": [], "edges": [],
                "commands": [], "include_globs": [], "exclude_globs": [],
            },
        },
    })

    result = runner.invoke(topics_cmd.topics_app, [
        "import", "--repo", str(fake_git_repo), "--reason", "git_pull",
    ])
    assert result.exit_code == 0, result.stdout

    # The live graph reflects the upstream pull EXCEPT for x: the local
    # downgrade tombstone masks the re-introduced x until the proposal is
    # resolved. The brand-new y (no tombstone) comes through.
    live = load_authoritative_graph(str(fake_git_repo))
    assert "x" not in live["topics"], "local downgrade tombstone masks upstream x"
    assert "y" in live["topics"]

    # But the in-flight downgrade proposal still carries the ORIGINAL X.
    with SessionLocal() as s:
        proposed_after = list(s.exec(select(ProposalRevisionTopic).where(
            ProposalRevisionTopic.revision_id == rev.id)))
    assert len(proposed_after) == 1
    assert {
        "label": proposed_after[0].label,
        "intent": proposed_after[0].intent,
        "aliases_json": proposed_after[0].aliases_json,
    } == snapshot_of_x_in_proposal, "import must not mutate proposal_revision_topics rows"


def test_import_quiet_suppresses_in_sync_output(fake_git_repo):
    """--quiet makes the post-checkout hook silent when nothing
    changed (the common case on a branch flip)."""
    _seed_repo(fake_git_repo)
    _write_topic_graph(fake_git_repo, _TOPIC)
    runner.invoke(topics_cmd.topics_app, ["import", "--repo", str(fake_git_repo)])

    result = runner.invoke(topics_cmd.topics_app, [
        "import", "--repo", str(fake_git_repo), "--quiet",
    ])

    assert result.exit_code == 0
    assert result.stdout == ""
