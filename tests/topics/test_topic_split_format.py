"""Split per-topic graph layout: the only layout. Reader/writer semantics,
`_meta.json` sidecar, the legacy-retirement guard, and bootstrap's git
integration (gitignore re-include block, pre-commit staging)."""

import json
import subprocess

import pytest

from lib.topics.core import (
    TopicGraphError,
    bootstrap,
    load_graph,
    save_graph,
    topic_meta_path,
    topic_path,
    topic_split_dir,
    write_graph_to_disk,
    write_split_graph,
)
from lib.topics.graph_io import _graph_hash
from lib.topics.scan import SPLIT_GITIGNORE_LINES, patch_gitignore


def _topic(label: str) -> dict:
    return {
        "label": label, "aliases": [], "intent": f"{label} intent",
        "status": "active",
        "refs": [{"path": "README.md", "role": "implementation"}],
        "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
    }


def _graph(topics: dict, repo: str = "fixture-repo") -> dict:
    return {"version": 1, "repo": repo,
            "updated_at": "2026-01-01T00:00:00Z", "topics": topics}


def _write_legacy(repo_path, graph: dict) -> None:
    path = topic_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n")


def _write_split_by_hand(repo_path, graph: dict) -> None:
    split_dir = topic_split_dir(repo_path)
    split_dir.mkdir(parents=True, exist_ok=True)
    meta = {k: v for k, v in graph.items() if k != "topics"}
    topic_meta_path(repo_path).write_text(json.dumps(meta) + "\n")
    for tid, body in graph["topics"].items():
        (split_dir / f"{tid}.json").write_text(json.dumps(body) + "\n")


_WIKI_STYLE_GITIGNORE = """\
.regin/*
!.regin/topics/
.regin/topics/*
!.regin/topics/wiki/
.regin/topics/wiki/*
!.regin/topics/wiki/*.md
"""


# ── read side ─────────────────────────────────────────────────


def test_load_graph_split_only(tmp_path):
    graph = _graph({"alpha": _topic("Alpha"), "beta": _topic("Beta")})
    _write_split_by_hand(tmp_path, graph)
    assert load_graph(tmp_path) == graph


def test_load_graph_legacy_only_raises_retired(tmp_path):
    _write_legacy(tmp_path, _graph({"alpha": _topic("Alpha")}))
    with pytest.raises(TopicGraphError, match="legacy single-file layout retired"):
        load_graph(tmp_path)


def test_split_wins_when_stray_legacy_present(tmp_path):
    _write_legacy(tmp_path, _graph({"legacy-only": _topic("Legacy")}))
    _write_split_by_hand(tmp_path, _graph({"split-only": _topic("Split")}))
    loaded = load_graph(tmp_path)
    assert set(loaded["topics"]) == {"split-only"}


def test_load_graph_missing_everywhere_raises(tmp_path):
    with pytest.raises(TopicGraphError, match="missing topic graph"):
        load_graph(tmp_path)


def test_missing_meta_sidecar_synthesizes_defaults(tmp_path):
    split_dir = topic_split_dir(tmp_path)
    split_dir.mkdir(parents=True)
    (split_dir / "alpha.json").write_text(json.dumps(_topic("Alpha")))
    loaded = load_graph(tmp_path)
    assert loaded["version"] == 1
    assert loaded["repo"] == tmp_path.resolve().name
    assert loaded["updated_at"]
    assert set(loaded["topics"]) == {"alpha"}


def test_meta_sidecar_round_trip(tmp_path):
    graph = _graph({"alpha": _topic("Alpha")}, repo="custom-name")
    write_split_graph(tmp_path, graph)
    loaded = load_graph(tmp_path)
    assert loaded["repo"] == "custom-name"
    assert loaded["version"] == 1
    assert loaded["updated_at"] == "2026-01-01T00:00:00Z"


# ── write side ────────────────────────────────────────────────


def test_write_graph_on_empty_repo_writes_split(tmp_path):
    target = write_graph_to_disk(tmp_path, _graph({"alpha": _topic("Alpha")}))
    assert target == topic_split_dir(tmp_path)
    assert (target / "alpha.json").exists()
    assert topic_meta_path(tmp_path).exists()
    assert not topic_path(tmp_path).exists()


def test_write_never_touches_stray_legacy_file(tmp_path):
    _write_legacy(tmp_path, _graph({"legacy-only": _topic("Legacy")}))
    before = topic_path(tmp_path).read_text()
    write_graph_to_disk(tmp_path, _graph({"alpha": _topic("Alpha")}))
    assert (topic_split_dir(tmp_path) / "alpha.json").exists()
    assert topic_path(tmp_path).read_text() == before


def test_save_on_split_repo_updates_and_deletes_per_topic_files(tmp_path):
    _write_split_by_hand(
        tmp_path, _graph({"alpha": _topic("Alpha"), "beta": _topic("Beta")}))
    graph = load_graph(tmp_path)
    del graph["topics"]["beta"]
    graph["topics"]["gamma"] = _topic("Gamma")
    save_graph(tmp_path, graph)

    split_dir = topic_split_dir(tmp_path)
    assert (split_dir / "alpha.json").exists()
    assert (split_dir / "gamma.json").exists()
    assert not (split_dir / "beta.json").exists()
    assert not topic_path(tmp_path).exists()
    assert json.loads((split_dir / "gamma.json").read_text())["label"] == "Gamma"


def test_graph_hash_stable_across_writer_and_hand_written(tmp_path):
    graph = _graph({"alpha": _topic("Alpha"), "beta": _topic("Beta")})
    hand_repo = tmp_path / "hand"
    writer_repo = tmp_path / "writer"
    _write_split_by_hand(hand_repo, graph)
    write_split_graph(writer_repo, graph)
    assert (_graph_hash(load_graph(hand_repo))
            == _graph_hash(load_graph(writer_repo))
            == _graph_hash(graph))


# ── bootstrap + git integration ───────────────────────────────


def _ignored(repo, rel_path: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", rel_path],
        capture_output=True,
    ).returncode == 0


def test_bootstrap_writes_split_and_patches_gitignore(fake_git_repo):
    (fake_git_repo / ".gitignore").write_text(_WIKI_STYLE_GITIGNORE)
    paths = bootstrap(fake_git_repo, seeds=True)
    assert paths["topic"] == topic_split_dir(fake_git_repo)
    assert topic_meta_path(fake_git_repo).exists()
    assert not topic_path(fake_git_repo).exists()
    gitignore_lines = (fake_git_repo / ".gitignore").read_text().splitlines()
    assert [line for line in SPLIT_GITIGNORE_LINES
            if line not in gitignore_lines] == []
    assert not _ignored(fake_git_repo, ".regin/topics/topics/overview.json")
    assert not _ignored(fake_git_repo, ".regin/topics/topics/_meta.json")
    assert _ignored(fake_git_repo, ".regin/topics/topic.local.json")


def test_bootstrap_refuses_existing_split_without_force(tmp_path):
    _write_split_by_hand(tmp_path, _graph({"alpha": _topic("Alpha")}))
    with pytest.raises(TopicGraphError, match="already exists"):
        bootstrap(tmp_path)
    bootstrap(tmp_path, force=True)
    assert not (topic_split_dir(tmp_path) / "alpha.json").exists()


def test_hook_stages_split_dir_not_legacy_file(fake_git_repo):
    from lib.topics.scan import install_topic_hooks

    install_topic_hooks(fake_git_repo)
    hook_body = (fake_git_repo / ".git" / "hooks" / "pre-commit").read_text()
    assert 'git add -f "$ROOT/.regin/topics/topics"' in hook_body
    assert 'git add -f "$ROOT/.regin/topics/bundles"' in hook_body
    assert "topic.json" not in hook_body


def test_patch_gitignore_is_idempotent(tmp_path):
    (tmp_path / ".gitignore").write_text(_WIKI_STYLE_GITIGNORE)
    assert patch_gitignore(tmp_path) == "patched"
    once = (tmp_path / ".gitignore").read_text()
    assert patch_gitignore(tmp_path) == "already_patched"
    assert (tmp_path / ".gitignore").read_text() == once


def test_patch_gitignore_without_reinclude_block(tmp_path):
    (tmp_path / ".gitignore").write_text("*.pyc\n")
    assert patch_gitignore(tmp_path) == "no_block"
    assert (tmp_path / ".gitignore").read_text() == "*.pyc\n"


# ── existing single-writer flows on a split repo ──────────────


def test_promote_on_split_repo_writes_per_topic_file(tmp_path):
    from lib.topics.core import save_local_graph
    from lib.topics.scan import promote_topic

    _write_split_by_hand(tmp_path, _graph({"alpha": _topic("Alpha")}))
    save_local_graph(tmp_path, {"topics": {"beta": _topic("Beta")},
                                "deleted_topics": []})
    promote_topic(tmp_path, "beta")
    assert (topic_split_dir(tmp_path) / "beta.json").exists()
    assert not topic_path(tmp_path).exists()


def test_topic_signature_guard_covers_split_layout(tmp_path):
    from lib.topics.proposal_external import _read_topic_signature

    _write_split_by_hand(tmp_path, _graph({"alpha": _topic("Alpha")}))
    before = _read_topic_signature(tmp_path)
    assert before is not None

    # updated_at churn must not flip the fingerprint...
    meta_path = topic_split_dir(tmp_path) / "_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["updated_at"] = "2099-01-01T00:00:00Z"
    meta_path.write_text(json.dumps(meta))
    assert _read_topic_signature(tmp_path) == before

    # ...but a real topic mutation must.
    (topic_split_dir(tmp_path) / "beta.json").write_text(
        json.dumps(_topic("Beta")))
    assert _read_topic_signature(tmp_path) != before


def test_ensure_topic_graph_noops_on_split_repo(tmp_path):
    from web.blueprints.topics._helpers import _ensure_topic_graph

    _write_split_by_hand(tmp_path, _graph({"alpha": _topic("Alpha")}))
    _ensure_topic_graph(str(tmp_path))
    assert set(load_graph(tmp_path)["topics"]) == {"alpha"}
    assert not topic_path(tmp_path).exists()


def test_ensure_topic_graph_never_bootstraps_over_legacy_repo(tmp_path):
    """Auto-bootstrapping an empty split graph over a legacy-only repo would
    silently mask its graph (and the hook would propagate the loss)."""
    from web.blueprints.topics._helpers import _ensure_topic_graph

    _write_legacy(tmp_path, _graph({"alpha": _topic("Alpha")}))
    _ensure_topic_graph(str(tmp_path))
    assert not topic_split_dir(tmp_path).exists()
    with pytest.raises(TopicGraphError, match="legacy single-file layout retired"):
        load_graph(tmp_path)


def test_non_dict_meta_sidecar_raises_topic_graph_error(tmp_path):
    _write_split_by_hand(tmp_path, _graph({"alpha": _topic("Alpha")}))
    (topic_split_dir(tmp_path) / "_meta.json").write_text("[1]")
    with pytest.raises(TopicGraphError, match="meta sidecar"):
        load_graph(tmp_path)


def test_split_writer_rejects_sidecar_and_traversal_ids(tmp_path):
    for bad_id in ("_meta", "../escape", ".hidden"):
        with pytest.raises(TopicGraphError, match="invalid topic id"):
            write_split_graph(tmp_path, _graph({bad_id: _topic("Evil")}))
