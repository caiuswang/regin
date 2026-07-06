"""One-way migration from the legacy single ``topic.json`` to the split
per-topic layout (``.regin/topics/topics/<topic_id>.json`` + ``_meta.json``).

The split layout shrinks the git merge surface to "did someone else touch
the same topic id". Reads stay dual-format (``core.load_graph``), so a
migrated repo only breaks for teammates running a pre-split regin —
hence the upgrade-before-migrate warning the CLI prints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.activity_log import get_activity_logger
from lib.topics.core import (
    TopicGraphError,
    read_json,
    split_layout_active,
    topic_path,
    topic_split_dir,
    write_split_graph,
)
from lib.topics.scan import install_topic_hooks


# Appended to the repo's existing `.regin` re-include block. Order matters
# inside gitignore (later rules win): un-ignore the dir, ignore its
# contents, then un-ignore exactly the JSON payload.
SPLIT_GITIGNORE_LINES = (
    "!.regin/topics/topics/",
    ".regin/topics/topics/*",
    "!.regin/topics/topics/*.json",
    "!.regin/topics/topics/_meta.json",
)
_GITIGNORE_ANCHOR = ".regin/topics/*"


def patch_gitignore(repo_path: str | Path) -> str:
    """Idempotently extend the repo's ``.regin`` re-include block so the
    split dir's JSON files travel via git.

    Returns ``"patched"``, ``"already_patched"``, or ``"no_block"`` (no
    ``.gitignore`` or no wiki-style re-include block to extend — nothing
    written; the caller surfaces a manual-edit hint).
    """
    gitignore = Path(repo_path) / ".gitignore"
    if not gitignore.exists():
        return "no_block"
    lines = gitignore.read_text().splitlines()
    if _GITIGNORE_ANCHOR not in lines:
        return "no_block"
    missing = [line for line in SPLIT_GITIGNORE_LINES if line not in lines]
    if not missing:
        return "already_patched"
    at = lines.index(_GITIGNORE_ANCHOR) + 1
    lines[at:at] = missing
    gitignore.write_text("\n".join(lines) + "\n")
    return "patched"


def migrate_to_split(repo_path: str | Path) -> dict[str, Any]:
    """Convert a legacy repo to the split layout.

    Writes the per-topic files + ``_meta.json``, deletes the legacy
    ``topic.json``, patches the repo's ``.gitignore`` re-include block,
    and re-installs the git hooks (whose pre-commit body now stages the
    split dir). Refuses when there is no legacy file to migrate.
    """
    repo = Path(repo_path)
    legacy = topic_path(repo)
    if split_layout_active(repo):
        raise TopicGraphError(
            f"already on the split layout: {topic_split_dir(repo)}"
            + (f" (stray legacy {legacy} left in place — remove it manually)"
               if legacy.exists() else "")
        )
    if not legacy.exists():
        raise TopicGraphError(f"nothing to migrate: {legacy} does not exist")

    graph = read_json(legacy)
    split_dir = write_split_graph(repo, graph)
    legacy.unlink()
    gitignore = patch_gitignore(repo)
    hooks = (install_topic_hooks(repo)
             if (repo / ".git").is_dir() else {})
    get_activity_logger("topics").write(
        "topics_migrated_to_split",
        repo_path=str(repo),
        topic_count=len(graph.get("topics") or {}),
        gitignore=gitignore,
        hooks_installed=sorted(hooks),
    )
    return {
        "split_dir": split_dir,
        "topic_count": len(graph.get("topics") or {}),
        "removed_legacy": legacy,
        "gitignore": gitignore,
        "hooks": {name: str(path) for name, path in hooks.items()},
    }
