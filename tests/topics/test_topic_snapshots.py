"""Snapshot orchestration: latest, list, prune, restore."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot
from lib.topics.apply import apply_diff
from lib.topics.diff import diff_against_graph
from lib.topics.snapshots import (
    latest_snapshot,
    list_snapshots,
    prune_snapshots,
    resolve_or_create_repo,
    restore_preview,
    restore_snapshot,
)


@pytest.fixture
def fresh_repo(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    return resolve_or_create_repo(str(fake_git_repo))


def _add_snapshot(repo_id: int, *, is_latest: int = 0, pinned: int = 0, reason: str = "manual"):
    """Insert a snapshot directly — bypasses apply_diff for prune/list tests
    that don't care about graph content.

    Caller is responsible for keeping the single-is_latest invariant.
    """
    with SessionLocal() as s:
        snap = GraphSnapshot(
            repo_id=repo_id,
            taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            reason=reason,
            graph_json=json.dumps({"version": 1, "repo": "demo", "topics": {}}),
            wiki_pages_json="{}",
            diff_summary_json="{}",
            pinned=pinned,
            is_latest=is_latest,
        )
        s.add(snap)
        s.commit()
        s.refresh(snap)
        return snap.id


def test_latest_snapshot_none_when_empty(fresh_repo):
    assert latest_snapshot(fresh_repo.id) is None


def test_latest_snapshot_returns_is_latest_row(fresh_repo, fake_git_repo):
    base = {"version": 1, "repo": "demo", "topics": {}}
    new_topic = {"id": "x", "label": "X", "intent": "x", "status": "active",
                 "aliases": [], "refs": [], "edges": [],
                 "commands": [], "include_globs": [], "exclude_globs": []}
    diff = diff_against_graph(new_topic, base, strategy="create")
    result = apply_diff(fresh_repo.id, diff, reason="accept")

    snap = latest_snapshot(fresh_repo.id)
    assert snap is not None
    assert snap.id == result.snapshot_id
    assert snap.is_latest == 1


def test_list_snapshots_newest_first(fresh_repo):
    ids = []
    # Manually add three non-latest snapshots, then promote the last one.
    ids.append(_add_snapshot(fresh_repo.id))
    ids.append(_add_snapshot(fresh_repo.id))
    ids.append(_add_snapshot(fresh_repo.id, is_latest=1))
    rows = list_snapshots(fresh_repo.id)
    # Newest first => the latest-promoted id is at the head.
    assert [r.id for r in rows] == list(reversed(ids))


def test_prune_keeps_latest_and_pinned(fresh_repo):
    """Latest stays. Pinned stays. The N newest non-latest stay. Rest go."""
    # 1 pinned
    pinned_id = _add_snapshot(fresh_repo.id, pinned=1)
    # 5 unpinned
    history_ids = [_add_snapshot(fresh_repo.id) for _ in range(5)]
    # latest at the head
    latest_id = _add_snapshot(fresh_repo.id, is_latest=1)

    n_deleted = prune_snapshots(fresh_repo.id, keep=2)
    # Keep the 2 newest non-latest non-pinned => deletes 3.
    assert n_deleted == 3

    surviving = {r.id for r in list_snapshots(fresh_repo.id, limit=100)}
    # Latest survives.
    assert latest_id in surviving
    # Pinned survives.
    assert pinned_id in surviving
    # The 2 newest history ids survive; the 3 oldest are gone.
    assert history_ids[-2] in surviving
    assert history_ids[-1] in surviving
    assert history_ids[0] not in surviving


def test_prune_noop_below_threshold(fresh_repo):
    _add_snapshot(fresh_repo.id, is_latest=1)
    for _ in range(3):
        _add_snapshot(fresh_repo.id)
    # 3 non-latest, keep=10 => nothing deleted
    assert prune_snapshots(fresh_repo.id, keep=10) == 0


def test_restore_clones_and_flips(fresh_repo, fake_git_repo):
    """Restore creates a NEW row marked is_latest=1, with the prior
    flipped to 0. The cloned graph_json matches the source."""
    base = {"version": 1, "repo": "demo", "topics": {}}
    one = {"id": "one", "label": "1", "intent": "1", "status": "active",
           "aliases": ["one-alias"], "refs": [], "edges": [],
           "commands": [], "include_globs": [], "exclude_globs": []}
    r1 = apply_diff(fresh_repo.id, diff_against_graph(one, base, strategy="create"),
                    reason="accept")

    after = _current_graph(fake_git_repo)
    two = {"id": "two", "label": "2", "intent": "2", "status": "active",
           "aliases": ["two-alias"], "refs": [], "edges": [],
           "commands": [], "include_globs": [], "exclude_globs": []}
    r2 = apply_diff(fresh_repo.id, diff_against_graph(two, after, strategy="create"),
                    reason="accept")

    # Restore back to r1's state.
    restored = restore_snapshot(r1.snapshot_id)
    assert restored.is_latest == 1
    assert restored.reason == "undo"
    # graph_json mirrors r1's
    assert json.loads(restored.graph_json) == json.loads(r1.snapshot.graph_json)

    # Original r1 stays as it was (history). r2 flipped to not-latest.
    with SessionLocal() as s:
        latest_count = len(list(s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == fresh_repo.id)
            .where(GraphSnapshot.is_latest == 1)
        )))
    assert latest_count == 1


def test_apply_diff_prunes_snapshot_history(fresh_repo, fake_git_repo, monkeypatch):
    """apply_diff prunes inline so `graph_snapshots` stays bounded.

    Without this wire-up, each accept/merge/replace appends forever.
    """
    # Patch the settings object `apply.py` actually reads. `reload_settings`
    # rebinds `lib.settings.settings` to a fresh instance, but apply.py
    # captured its reference at import time (`from lib.settings import
    # settings`), so an earlier test's reload would otherwise leave our
    # monkeypatch targeting the wrong object and the prune at its default.
    import lib.topics.apply as _apply
    monkeypatch.setattr(_apply.settings, "topic_snapshot_keep", 2)

    for i in range(5):
        graph = {"version": 1, "repo": "demo", "topics": {}}
        topic = {"id": f"t{i}", "label": f"T{i}", "intent": str(i), "status": "active",
                 "aliases": [], "refs": [], "edges": [],
                 "commands": [], "include_globs": [], "exclude_globs": []}
        apply_diff(fresh_repo.id, diff_against_graph(topic, graph, strategy="create"),
                   reason="accept")

    rows = list_snapshots(fresh_repo.id, limit=100)
    # 1 is_latest + at most keep (2) non-latest unpinned = 3 total
    assert len(rows) == 3
    assert sum(1 for r in rows if r.is_latest) == 1


def test_apply_diff_prune_disabled_when_keep_is_zero(fresh_repo, fake_git_repo, monkeypatch):
    from lib.settings import settings as _settings
    monkeypatch.setattr(_settings, "topic_snapshot_keep", 0)

    for i in range(4):
        graph = {"version": 1, "repo": "demo", "topics": {}}
        topic = {"id": f"k{i}", "label": f"K{i}", "intent": str(i), "status": "active",
                 "aliases": [], "refs": [], "edges": [],
                 "commands": [], "include_globs": [], "exclude_globs": []}
        apply_diff(fresh_repo.id, diff_against_graph(topic, graph, strategy="create"),
                   reason="accept")

    rows = list_snapshots(fresh_repo.id, limit=100)
    # All 4 applies survived because pruning is disabled.
    assert len(rows) == 4


def _t(tid: str, **overrides):
    """Build a minimal topic dict for restore_preview tests."""
    base = {"id": tid, "label": tid.upper(), "intent": tid, "status": "active",
            "aliases": [], "refs": [], "edges": [],
            "commands": [], "include_globs": [], "exclude_globs": []}
    base.update(overrides)
    return base


def _current_graph(fake_git_repo):
    # apply writes the local overlay, not base topic.json; the effective
    # current graph is the snapshot-first authoritative read (which merges
    # base + overlay). Before the first apply there's no snapshot/base, so
    # fall back to an empty graph.
    from lib.topics import TopicGraphError
    from lib.topics.graph_io import load_authoritative_graph
    try:
        return load_authoritative_graph(str(fake_git_repo))
    except TopicGraphError:
        return {"version": 1, "repo": "demo", "topics": {}}


def test_restore_preview_no_change_when_target_is_latest(fresh_repo, fake_git_repo):
    r1 = apply_diff(fresh_repo.id,
                    diff_against_graph(_t("x"), _current_graph(fake_git_repo), strategy="create"),
                    reason="accept")
    preview = restore_preview(r1.snapshot_id)
    assert preview["is_latest"] is True
    assert preview["no_change"] is True
    assert preview["topic_deltas"] == []


def test_restore_preview_would_remove_topics_added_since(fresh_repo, fake_git_repo):
    """A topic that exists in current but NOT in the source snapshot
    is flagged would_remove — restoring drops it."""
    r1 = apply_diff(fresh_repo.id,
                    diff_against_graph(_t("alpha"), _current_graph(fake_git_repo), strategy="create"),
                    reason="accept")
    apply_diff(fresh_repo.id,
               diff_against_graph(_t("beta"), _current_graph(fake_git_repo), strategy="create"),
               reason="accept")

    preview = restore_preview(r1.snapshot_id)
    assert preview["no_change"] is False
    kinds = {d["topic_id"]: d["kind"] for d in preview["topic_deltas"]}
    assert kinds == {"beta": "would_remove"}


def test_restore_preview_would_add_back_topics_deleted_since(fresh_repo, fake_git_repo):
    """A topic that exists in source but NOT in current is flagged
    would_add_back — restoring brings it back."""
    apply_diff(fresh_repo.id,
               diff_against_graph(_t("alpha"), _current_graph(fake_git_repo), strategy="create"),
               reason="accept")
    r2 = apply_diff(fresh_repo.id,
                    diff_against_graph(_t("beta"), _current_graph(fake_git_repo), strategy="create"),
                    reason="accept")
    # Now restore back to the very first (pre-anything) state by going
    # back via r2 then rolling further: actually easier — just snapshot
    # r2 (has alpha+beta), roll the current state forward another step,
    # then preview r2 against the new current. The new current still
    # has alpha+beta, no add_back. So instead: roll back current to
    # the state BEFORE r2 by restore_snapshot to r2's parent.
    # Simpler path: pin r2, restore to an earlier id, then preview r2.
    # Find r1's id from list_snapshots.
    rows = list_snapshots(fresh_repo.id, limit=100)
    r1_id = next(r.id for r in rows if not r.is_latest and r.id < r2.snapshot_id)
    restore_snapshot(r1_id)  # current latest now matches the alpha-only state

    preview = restore_preview(r2.snapshot_id)
    kinds = {d["topic_id"]: d["kind"] for d in preview["topic_deltas"]}
    assert kinds == {"beta": "would_add_back"}


def test_restore_preview_would_revert_with_alias_changes(fresh_repo, fake_git_repo):
    """A topic that exists in both but differs is flagged would_revert,
    and the per-topic alias/ref/edge diff is filled in."""
    r1 = apply_diff(fresh_repo.id,
                    diff_against_graph(_t("alpha"), _current_graph(fake_git_repo), strategy="create"),
                    reason="accept")
    # Merge an alias into alpha. compute_topic_delta uses the approved
    # graph view, so the merged result has aliases=["a-extra"].
    apply_diff(fresh_repo.id,
               diff_against_graph(_t("alpha", aliases=["a-extra"]),
                                  _current_graph(fake_git_repo),
                                  strategy="merge", target_topic_id="alpha"),
               reason="merge")

    preview = restore_preview(r1.snapshot_id)
    assert preview["no_change"] is False
    [delta] = preview["topic_deltas"]
    assert delta["topic_id"] == "alpha"
    assert delta["kind"] == "would_revert"
    # Restoring to r1 means the alias added later should be REMOVED.
    assert "a-extra" in delta["alias_removes"]


def test_restore_preview_endpoint_returns_ok(fresh_repo, fake_git_repo, flask_client):
    apply_diff(fresh_repo.id,
               diff_against_graph(_t("x"), _current_graph(fake_git_repo), strategy="create"),
               reason="accept")
    apply_diff(fresh_repo.id,
               diff_against_graph(_t("y"), _current_graph(fake_git_repo), strategy="create"),
               reason="accept")
    # Pick the oldest non-latest snapshot — the one with only "x". The
    # background wiki indexer can re-seed an auto-snapshot if it detects
    # disk drift, so latest_id is not necessarily r2.snapshot_id.
    rows = list_snapshots(fresh_repo.id, limit=100)
    older = next(r for r in rows if not r.is_latest)

    res = flask_client.get(f"/api/repos/{fresh_repo.name}/topics/snapshots/{older.id}/restore-preview")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    preview = body["preview"]
    assert preview["snapshot_id"] == older.id
    assert preview["latest_id"] is not None
    assert preview["is_latest"] is False
    # Restoring to the older snapshot would remove y.
    kinds = {d["topic_id"]: d["kind"] for d in preview["topic_deltas"]}
    assert kinds == {"y": "would_remove"}


def test_restore_preview_endpoint_404_on_missing(fresh_repo, flask_client):
    res = flask_client.get(f"/api/repos/{fresh_repo.name}/topics/snapshots/9999/restore-preview")
    assert res.status_code == 404


def test_partial_unique_index_blocks_two_latest(fresh_repo):
    """Defense-in-depth: the partial unique index throws if two rows
    have is_latest=1 simultaneously for the same repo. apply_diff's
    flush-before-insert dance is what avoids this in production."""
    from sqlalchemy.exc import IntegrityError
    _add_snapshot(fresh_repo.id, is_latest=1)
    with pytest.raises(IntegrityError):
        _add_snapshot(fresh_repo.id, is_latest=1)
