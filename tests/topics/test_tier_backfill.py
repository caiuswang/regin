"""Backfill `tier: "reference"` onto wiki-unmentioned refs.

Dry-run by default; conservative (generous mention test → only demotes clearly
unmentioned refs); idempotent (never overrides an existing tier); writes the
git-tracked base graph.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.topics.core import load_graph, slugify, topic_path
from lib.topics.tier_backfill import backfill_reference_tiers
from lib.topics.wiki import wiki_dir


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


def _write_wiki(repo: Path, topic_id: str, body: str) -> None:
    wd = wiki_dir(repo)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / f"{slugify(topic_id)}.md").write_text(body)


def _refs(repo: Path, topic_id: str) -> dict:
    """Map path -> ref dict from the on-disk base graph."""
    graph = load_graph(repo)
    return {r["path"]: r for r in graph["topics"][topic_id]["refs"]}


def test_unmentioned_ref_is_demoted_on_apply(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([
        {"path": "lib/core.py"},          # mentioned below → stays primary
        {"path": "lib/example.py"},       # never mentioned → reference
    ])})
    _write_wiki(repo, "t1", "# T1\n\nThe entry point is `core.py`, which does X.\n")

    result = backfill_reference_tiers(repo, apply=True)

    assert result["applied"] is True
    assert result["demotions"] == [{"topic_id": "t1", "path": "lib/example.py"}]
    refs = _refs(repo, "t1")
    assert refs["lib/example.py"]["tier"] == "reference"
    assert "tier" not in refs["lib/core.py"]      # mentioned → untouched


def test_dry_run_writes_nothing(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([{"path": "lib/example.py"}])})
    _write_wiki(repo, "t1", "# T1\n\nno file names here\n")
    before = topic_path(repo).read_text()

    result = backfill_reference_tiers(repo)   # apply defaults to False

    assert result["applied"] is False
    assert result["demotions"] == [{"topic_id": "t1", "path": "lib/example.py"}]
    # graph on disk is byte-for-byte unchanged
    assert topic_path(repo).read_text() == before


def test_mentioned_by_full_path_stays_primary(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([{"path": "lib/topics/__init__.py"}])})
    # basename is generic, but the full path appears → mentioned
    _write_wiki(repo, "t1", "See `lib/topics/__init__.py` for the re-exports.\n")

    result = backfill_reference_tiers(repo, apply=True)

    assert result["demotions"] == []
    assert "tier" not in _refs(repo, "t1")["lib/topics/__init__.py"]


def test_existing_tier_is_never_overridden(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([
        {"path": "lib/a.py", "tier": "primary"},      # explicit primary, unmentioned
        {"path": "lib/b.py", "tier": "reference"},    # already reference
    ])})
    _write_wiki(repo, "t1", "# T1\n\nnothing named\n")

    result = backfill_reference_tiers(repo, apply=True)

    assert result["demotions"] == []                  # both already tagged
    assert result["applied"] is False
    refs = _refs(repo, "t1")
    assert refs["lib/a.py"]["tier"] == "primary"      # not flipped to reference
    assert refs["lib/b.py"]["tier"] == "reference"


def test_idempotent_second_run_is_noop(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([{"path": "lib/example.py"}])})
    _write_wiki(repo, "t1", "# T1\n\nno names\n")

    assert backfill_reference_tiers(repo, apply=True)["applied"] is True
    # a ref tagged reference on the first pass carries a tier → skipped now
    second = backfill_reference_tiers(repo, apply=True)
    assert second["demotions"] == []
    assert second["applied"] is False


def test_topic_without_wiki_is_skipped_not_crashed(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([{"path": "lib/example.py"}])})  # no wiki file

    result = backfill_reference_tiers(repo)

    assert result["skipped_no_wiki"] == ["t1"]
    assert result["demotions"] == []


def test_topic_scope_limits_to_one(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {
        "t1": _topic([{"path": "lib/a.py"}]),
        "t2": _topic([{"path": "lib/b.py"}]),
    })
    _write_wiki(repo, "t1", "no names\n")
    _write_wiki(repo, "t2", "no names\n")

    result = backfill_reference_tiers(repo, apply=True, topic_id="t1")

    assert [d["topic_id"] for d in result["demotions"]] == ["t1"]
    assert "tier" not in _refs(repo, "t2")["lib/b.py"]   # t2 untouched
