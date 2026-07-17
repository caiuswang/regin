"""graph_io helpers: load_authoritative_graph + reconcile_if_drifted.

These bridge the Phase A→D source-of-truth flip. The helper falls back
to the on-disk graph when no snapshot exists; once a snapshot lives
in `graph_snapshots`, it wins.
"""

from __future__ import annotations

import json

import pytest

from lib.topics.apply import apply_diff
from lib.topics.core import load_graph, topic_dir, write_split_graph
from lib.topics.diff import diff_against_graph
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.snapshots import resolve_or_create_repo


@pytest.fixture
def repo_dir(fake_git_repo):
    """A bootstrapped repo dir with no Repo row and no snapshot — the
    test then chooses which to seed."""
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    base = {
        "version": 1,
        "repo": "demo",
        "updated_at": "2026-01-01T00:00:00Z",
        "topics": {
            "disk-only": {
                "label": "D", "intent": "d", "status": "active",
                "aliases": [], "refs": [], "edges": [],
                "commands": [], "include_globs": [], "exclude_globs": [],
            },
        },
    }
    write_split_graph(fake_git_repo, base)
    return fake_git_repo


def test_no_repo_row_falls_back_to_disk(repo_dir):
    graph = load_authoritative_graph(str(repo_dir))
    assert "disk-only" in graph["topics"]


def test_repo_row_without_snapshot_auto_seeds(repo_dir):
    """Phase E3: outcome 2 (Repo row, no snapshot) now auto-seeds a
    snapshot from the on-disk graph rather than falling back forever."""
    from lib.orm import SessionLocal
    from lib.orm.models import GraphSnapshot
    from sqlmodel import select

    repo = resolve_or_create_repo(str(repo_dir))
    graph = load_authoritative_graph(str(repo_dir))
    assert "disk-only" in graph["topics"]

    # A new snapshot was seeded.
    with SessionLocal() as s:
        snaps = list(s.exec(
            select(GraphSnapshot).where(GraphSnapshot.repo_id == repo.id)
        ))
    assert len(snaps) == 1
    assert snaps[0].reason == "auto_seed"
    assert snaps[0].is_latest == 1

    # Subsequent calls don't re-seed.
    load_authoritative_graph(str(repo_dir))
    with SessionLocal() as s:
        snaps2 = list(s.exec(
            select(GraphSnapshot).where(GraphSnapshot.repo_id == repo.id)
        ))
    assert len(snaps2) == 1


def test_snapshot_wins_over_identical_disk(repo_dir):
    """When disk matches the snapshot, the snapshot's content is what
    callers see. Phase E3's drift-detect only kicks in when disk and
    snapshot have diverged — for the matching case, the snapshot is
    the authoritative source.
    """
    repo = resolve_or_create_repo(str(repo_dir))
    base_graph = load_graph(repo_dir)
    apply_diff(
        repo.id,
        diff_against_graph(
            {"id": "snap-only", "label": "S", "intent": "s", "status": "active",
             "aliases": [], "refs": [], "edges": []},
            base_graph, strategy="create",
        ),
        reason="accept",
    )
    # Disk and snapshot are in sync after apply_diff.
    graph = load_authoritative_graph(str(repo_dir))
    assert "snap-only" in graph["topics"]


def test_auto_seed_ingests_wikis_from_disk(repo_dir):
    """Outcome-2 auto-seed must populate `wiki_pages_json` from the
    sibling `wiki/<tid>.md` files so a rollback to this snapshot
    restores wiki bodies. Previously hardcoded to `{}`, which made
    rollback silently lose wikis for any repo whose first snapshot
    arrived via auto-seed (e.g. a fresh `git pull`).
    """
    from lib.orm import SessionLocal
    from lib.orm.models import GraphSnapshot
    from sqlmodel import select

    wiki_dir = topic_dir(repo_dir) / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "disk-only.md").write_text("# Disk-only topic\n\nBody.\n")
    (wiki_dir / "orphan.md").write_text("stale — topic gone from graph\n")

    repo = resolve_or_create_repo(str(repo_dir))
    load_authoritative_graph(str(repo_dir))

    with SessionLocal() as s:
        snap = s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo.id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()
    assert snap is not None
    wiki_pages = json.loads(snap.wiki_pages_json)
    assert wiki_pages.get("disk-only") == "# Disk-only topic\n\nBody.\n"
    assert "orphan" not in wiki_pages, "stale wiki must not be carried into snapshot"


def test_sync_snapshot_from_disk_ingests_wikis(repo_dir):
    """`sync_snapshot_from_disk` (used by `scan` and the upcoming
    `regin topics import`) must also populate `wiki_pages_json` so
    cross-user / scan-driven snapshots preserve wiki bodies.
    """
    from lib.orm import SessionLocal
    from lib.orm.models import GraphSnapshot
    from lib.topics.graph_io import sync_snapshot_from_disk
    from sqlmodel import select

    # Bootstrap: register repo + create the first snapshot via apply_diff,
    # because sync_snapshot_from_disk is a no-op when no prior snapshot exists.
    repo = resolve_or_create_repo(str(repo_dir))
    base_graph = load_graph(repo_dir)
    apply_diff(
        repo.id,
        diff_against_graph(
            {"id": "snap-only", "label": "S", "intent": "s", "status": "active",
             "aliases": [], "refs": [], "edges": []},
            base_graph, strategy="create",
        ),
        reason="accept",
    )
    # Simulate a downstream git pull: a new wiki body lands on disk
    # for an existing topic that scan/import then snapshots.
    wiki_dir = topic_dir(repo_dir) / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "snap-only.md").write_text("# Snap-only\n\nFresh from upstream.\n")

    snap_id = sync_snapshot_from_disk(str(repo_dir), reason="git_pull")
    assert snap_id is not None

    with SessionLocal() as s:
        snap = s.exec(
            select(GraphSnapshot).where(GraphSnapshot.id == snap_id)
        ).first()
    assert snap is not None
    wiki_pages = json.loads(snap.wiki_pages_json)
    assert wiki_pages.get("snap-only") == "# Snap-only\n\nFresh from upstream.\n"


def test_check_graph_sync_unregistered_when_no_repo_row(repo_dir):
    from lib.topics.graph_io import check_graph_sync

    assert check_graph_sync(str(repo_dir)) == {"state": "unregistered"}


def test_check_graph_sync_no_snapshot_after_repo_registered(repo_dir):
    from lib.topics.graph_io import check_graph_sync

    repo = resolve_or_create_repo(str(repo_dir))
    result = check_graph_sync(str(repo_dir))
    assert result["state"] == "no_snapshot"
    assert result["repo_id"] == repo.id


def test_check_graph_sync_legacy_only_repo_is_legacy_unsupported(fake_git_repo):
    import json

    from lib.topics.graph_io import check_graph_sync

    repo = resolve_or_create_repo(str(fake_git_repo))
    legacy = fake_git_repo / ".regin" / "topics" / "topic.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"version": 1, "repo": "demo", "topics": {}}))
    result = check_graph_sync(str(fake_git_repo))
    assert result["state"] == "legacy_unsupported"
    assert result["repo_id"] == repo.id


def test_check_graph_sync_in_sync_after_apply(repo_dir):
    from lib.topics.graph_io import check_graph_sync

    repo = resolve_or_create_repo(str(repo_dir))
    base_graph = load_graph(repo_dir)
    apply_diff(
        repo.id,
        diff_against_graph(
            {"id": "x", "label": "X", "intent": "x", "status": "active",
             "aliases": [], "refs": [], "edges": []},
            base_graph, strategy="create",
        ),
        reason="accept",
    )
    result = check_graph_sync(str(repo_dir))
    assert result["state"] == "in_sync"


def test_check_graph_sync_disk_newer_after_external_write(repo_dir):
    """Simulates a `git pull` that updates the on-disk graph: doctor must
    flag the drift so the user knows to run `regin topics import`.

    Seeds via `load_authoritative_graph` (synchronous) instead of
    `apply_diff` to avoid racing with the `_bg_reindex` thread that
    `apply_diff` spawns — that thread also calls
    `load_authoritative_graph`, which would re-seed the snapshot
    from the drifted disk and erase the drift mid-test.
    """
    from lib.topics.graph_io import check_graph_sync, load_authoritative_graph

    resolve_or_create_repo(str(repo_dir))
    load_authoritative_graph(str(repo_dir))

    # Tamper with disk to simulate an upstream-pushed change.
    disk = load_graph(repo_dir)
    disk["topics"]["y"] = {
        "label": "Y", "intent": "y", "status": "active",
        "aliases": [], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    write_split_graph(repo_dir, disk)

    result = check_graph_sync(str(repo_dir))
    assert result["state"] == "disk_newer"


def test_disk_drift_re_seeds_snapshot(repo_dir):
    """Phase E3 drift-detect: a direct disk write outside apply_diff
    propagates to the snapshot on the next load. The merged disk
    (base graph + topic.local.json) stays the source of truth at
    quiescent states — anything that edits it outside the sanctioned
    writers (manual edits, CLI plumbing) is treated as an explicit
    override and the snapshot re-seeds to honour it.
    """
    from lib.topics.core import topic_local_path

    repo = resolve_or_create_repo(str(repo_dir))
    base_graph = load_graph(repo_dir)
    apply_diff(
        repo.id,
        diff_against_graph(
            {"id": "snap-only", "label": "S", "intent": "s", "status": "active",
             "aliases": [], "refs": [], "edges": []},
            base_graph, strategy="create",
        ),
        reason="accept",
    )
    # apply routes the topic to the local overlay; tamper with that file
    # directly (a manual edit outside apply) — drift-detect must pick it up.
    overlay = json.loads(topic_local_path(repo_dir).read_text())
    overlay["topics"].pop("snap-only", None)
    topic_local_path(repo_dir).write_text(json.dumps(overlay))

    graph = load_authoritative_graph(str(repo_dir))
    assert "snap-only" not in graph["topics"], "drift-detect should re-seed from merged disk"
