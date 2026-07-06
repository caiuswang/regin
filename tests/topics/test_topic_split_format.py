"""Split per-topic graph layout: dual-format reads, the layout-following
unified writer, `_meta.json` sidecar semantics, and `migrate-split`."""

import json
import subprocess

import pytest

from lib.topics.core import (
    TopicGraphError,
    load_graph,
    save_graph,
    topic_meta_path,
    topic_path,
    topic_split_dir,
    write_graph_to_disk,
    write_split_graph,
)
from lib.topics.graph_io import _graph_hash
from lib.topics.split_migrate import (
    SPLIT_GITIGNORE_LINES,
    migrate_to_split,
    patch_gitignore,
)


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
!.regin/topics/topic.json
!.regin/topics/wiki/
.regin/topics/wiki/*
!.regin/topics/wiki/*.md
"""


# ── dual-read ─────────────────────────────────────────────────


def test_load_graph_legacy_only(tmp_path):
    graph = _graph({"alpha": _topic("Alpha")})
    _write_legacy(tmp_path, graph)
    assert load_graph(tmp_path) == graph


def test_load_graph_split_only(tmp_path):
    graph = _graph({"alpha": _topic("Alpha"), "beta": _topic("Beta")})
    _write_split_by_hand(tmp_path, graph)
    assert load_graph(tmp_path) == graph


def test_split_wins_when_both_present(tmp_path):
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


# ── writer follows the disk layout ────────────────────────────


def test_save_on_legacy_repo_keeps_single_file(tmp_path):
    _write_legacy(tmp_path, _graph({"alpha": _topic("Alpha")}))
    graph = load_graph(tmp_path)
    graph["topics"]["beta"] = _topic("Beta")
    save_graph(tmp_path, graph)
    assert set(json.loads(topic_path(tmp_path).read_text())["topics"]) == {"alpha", "beta"}
    assert not topic_split_dir(tmp_path).exists()


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


def test_write_graph_on_empty_repo_writes_legacy(tmp_path):
    target = write_graph_to_disk(tmp_path, _graph({"alpha": _topic("Alpha")}))
    assert target == topic_path(tmp_path)
    assert target.exists()
    assert not topic_split_dir(tmp_path).exists()


def test_graph_hash_equal_across_layouts(tmp_path):
    graph = _graph({"alpha": _topic("Alpha"), "beta": _topic("Beta")})
    legacy_repo = tmp_path / "legacy"
    split_repo = tmp_path / "split"
    _write_legacy(legacy_repo, graph)
    write_split_graph(split_repo, graph)
    assert (_graph_hash(load_graph(legacy_repo))
            == _graph_hash(load_graph(split_repo))
            == _graph_hash(graph))


# ── migrate-split ─────────────────────────────────────────────


def _ignored(repo, rel_path: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", rel_path],
        capture_output=True,
    ).returncode == 0


@pytest.fixture
def migrated_repo(fake_git_repo):
    """`fake_git_repo` with a two-topic legacy graph migrated to split."""
    (fake_git_repo / ".gitignore").write_text(_WIKI_STYLE_GITIGNORE)
    _write_legacy(fake_git_repo,
                  _graph({"alpha": _topic("Alpha"), "beta": _topic("Beta")}))
    result = migrate_to_split(fake_git_repo)
    return fake_git_repo, result


def test_migrate_split_writes_files_and_removes_legacy(migrated_repo):
    repo, result = migrated_repo
    split_dir = topic_split_dir(repo)
    assert (split_dir / "alpha.json").exists()
    assert (split_dir / "beta.json").exists()
    assert topic_meta_path(repo).exists()
    assert not topic_path(repo).exists()
    assert result["topic_count"] == 2
    assert set(load_graph(repo)["topics"]) == {"alpha", "beta"}


def test_migrate_split_patches_gitignore(migrated_repo):
    repo, result = migrated_repo
    assert result["gitignore"] == "patched"
    gitignore_lines = (repo / ".gitignore").read_text().splitlines()
    missing = [line for line in SPLIT_GITIGNORE_LINES
               if line not in gitignore_lines]
    assert missing == []


def test_migrate_split_hook_stages_both_layouts(migrated_repo):
    repo, _ = migrated_repo
    hook_body = (repo / ".git" / "hooks" / "pre-commit").read_text()
    assert 'git add "$ROOT/.regin/topics/topics"' in hook_body
    assert 'git add "$ROOT/.regin/topics/topic.json"' in hook_body


def test_migrate_split_files_travel_via_git(migrated_repo):
    repo, _ = migrated_repo
    assert not _ignored(repo, ".regin/topics/topics/alpha.json")
    assert not _ignored(repo, ".regin/topics/topics/_meta.json")
    assert _ignored(repo, ".regin/topics/topic.local.json")


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


def test_migrate_refuses_without_legacy_file(tmp_path):
    with pytest.raises(TopicGraphError, match="nothing to migrate"):
        migrate_to_split(tmp_path)


def test_migrate_refuses_when_already_split(fake_git_repo):
    repo = fake_git_repo
    (repo / ".gitignore").write_text(_WIKI_STYLE_GITIGNORE)
    _write_legacy(repo, _graph({"alpha": _topic("Alpha")}))
    migrate_to_split(repo)
    with pytest.raises(TopicGraphError, match="already on the split layout"):
        migrate_to_split(repo)


def test_cmd_migrate_split_prints_upgrade_warning(fake_git_repo, capsys):
    from cli.commands.topics import cmd_topics_migrate_split

    repo = fake_git_repo
    (repo / ".gitignore").write_text(_WIKI_STYLE_GITIGNORE)
    _write_legacy(repo, _graph({"alpha": _topic("Alpha")}))
    cmd_topics_migrate_split(repo=str(repo))
    out = capsys.readouterr().out
    assert "Wrote 1 topic file(s)" in out
    assert "teammates must upgrade" in out


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


def test_non_dict_meta_sidecar_raises_topic_graph_error(tmp_path):
    _write_split_by_hand(tmp_path, _graph({"alpha": _topic("Alpha")}))
    (topic_split_dir(tmp_path) / "_meta.json").write_text("[1]")
    with pytest.raises(TopicGraphError, match="meta sidecar"):
        load_graph(tmp_path)


def test_split_writer_rejects_sidecar_and_traversal_ids(tmp_path):
    from lib.topics.core import write_split_graph

    for bad_id in ("_meta", "../escape", ".hidden"):
        with pytest.raises(TopicGraphError, match="invalid topic id"):
            write_split_graph(tmp_path, _graph({bad_id: _topic("Evil")}))
