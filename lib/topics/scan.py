"""Filesystem scanning and validation for the approved topic graph.

Reads from + writes to the same `topic.json` file that `core` owns,
but adds the scan + validate operations that refresh refs on approved
topics from the working tree.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from lib.topics.core import (
    DEFAULT_EXCLUDES,
    DEFAULT_REF_TIER,
    EDGE_TYPES,
    IGNORED_DIRS,
    REF_ROLES,
    REF_TIERS,
    ROLE_ORDER,
    SCHEMA_VERSION,
    TOPIC_STATUSES,
    TopicGraphError,
    ValidationResult,
    _valid_id,
    empty_graph,
    is_generated_path,
    load_graph,
    load_local_graph,
    match_glob,
    normalize,
    save_local_graph,
    slugify,
    topic_dir,
    write_graph_to_disk,
)
from lib.topics.graph_io import load_authoritative_graph, sync_snapshot_from_disk
from lib.topics.ignores import load_ignore_rules
from lib.activity_log import get_activity_logger as _get_activity_logger


def _topics_log():
    return _get_activity_logger("topics")


def validate(repo_path: str | Path) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        graph = load_authoritative_graph(repo_path)
    except TopicGraphError as exc:
        return ValidationResult([str(exc)], [])

    if graph.get("version") != SCHEMA_VERSION:
        errors.append("topic.json version must be 1")
    if not isinstance(graph.get("repo"), str) or not graph.get("repo"):
        errors.append("topic.json repo must be a non-empty string")
    topics = graph.get("topics")
    if not isinstance(topics, dict):
        errors.append("topic.json topics must be an object")
        return ValidationResult(errors, warnings)

    aliases: dict[str, str] = {}
    for topic_id, topic in topics.items():
        if not _valid_id(topic_id):
            errors.append(f"topic id {topic_id!r} must use lowercase letters, digits, dots, underscores, or hyphens")
        if not isinstance(topic, dict):
            errors.append(f"topic {topic_id} must be an object")
            continue
        _validate_topic(repo_path, topic_id, topic, topics, aliases, errors, warnings)
    return ValidationResult(errors, warnings)


def _validate_topic(
    repo_path: str | Path,
    topic_id: str,
    topic: dict[str, Any],
    topics: dict[str, Any],
    aliases: dict[str, str],
    errors: list[str],
    warnings: list[str],
) -> None:
    for field in ("label", "intent", "status"):
        if not isinstance(topic.get(field), str) or not topic.get(field):
            errors.append(f"topic {topic_id} field {field} must be a non-empty string")
    if topic.get("status") not in TOPIC_STATUSES:
        errors.append(f"topic {topic_id} has invalid status {topic.get('status')!r}")

    for field in ("aliases", "refs", "edges", "commands", "include_globs", "exclude_globs"):
        if not isinstance(topic.get(field, []), list):
            errors.append(f"topic {topic_id} field {field} must be a list")

    for alias in topic.get("aliases", []):
        key = normalize(alias)
        if key in aliases:
            errors.append(f"duplicate alias {alias!r} on topics {aliases[key]} and {topic_id}")
        aliases[key] = topic_id

    _validate_refs(repo_path, topic_id, topic, errors, warnings)
    _validate_edges(topic_id, topic, topics, errors)


def _validate_edges(
    topic_id: str,
    topic: dict[str, Any],
    topics: dict[str, Any],
    errors: list[str],
) -> None:
    for edge in topic.get("edges", []):
        if not isinstance(edge, dict):
            errors.append(f"topic {topic_id} edges must be objects")
            continue
        target = edge.get("target")
        if target not in topics:
            errors.append(f"topic {topic_id} edge target does not exist: {target}")
        if edge.get("type", "related") not in EDGE_TYPES:
            errors.append(f"topic {topic_id} edge has invalid type {edge.get('type')!r}")


def _validate_refs(
    repo_path: str | Path,
    topic_id: str,
    topic: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    seen_refs: set[str] = set()
    for ref in topic.get("refs", []):
        if not isinstance(ref, dict):
            errors.append(f"topic {topic_id} refs must be objects")
            continue
        path = ref.get("path")
        role = ref.get("role")
        tier = ref.get("tier")
        # role and tier are optional; topics own them (assigned by the proposal
        # LLM or by hand), so scan never invents one — but a set-but-unknown
        # value is still an error.
        if role is not None and role not in REF_ROLES:
            errors.append(f"topic {topic_id} ref {path!r} has invalid role {role!r}")
        if tier is not None and tier not in REF_TIERS:
            errors.append(f"topic {topic_id} ref {path!r} has invalid tier {tier!r}")
        if not isinstance(path, str) or not path:
            errors.append(f"topic {topic_id} has ref with missing path")
            continue
        if path in seen_refs:
            warnings.append(f"topic {topic_id} has duplicate ref {path}")
        seen_refs.add(path)
        if not (Path(repo_path) / path).exists():
            errors.append(f"topic {topic_id} ref does not exist: {path}")


def scan(
    repo_path: str | Path,
    *,
    staged: bool = False,
) -> dict[str, Any]:
    repo = Path(repo_path)
    matcher = load_ignore_rules(repo)
    graph = load_authoritative_graph(repo)
    files = staged_files(repo) if staged else list_repo_files(repo)
    files = [path for path in files if not matcher.is_ignored(path)]

    covered: set[str] = set()
    updated_topics: list[str] = []
    apply_refs = _apply_staged_refs if staged else _apply_full_refs
    for topic_id, topic in graph.get("topics", {}).items():
        matched = refs_for_topic(files, topic)
        if matched is None:
            continue
        if apply_refs(topic, matched, covered):
            updated_topics.append(topic_id)

    # Only persist when refs actually changed. A timestamp-only write
    # would re-stamp the overlay into every commit, churning with no
    # real change. Scan-refreshed refs are machine-local, so the touched
    # topics (whole entries, including their base content) are written to
    # the gitignored `topic.local.json` overlay — `topic.json` is left
    # untouched.
    if updated_topics:
        overlay = load_local_graph(repo)
        for topic_id in updated_topics:
            overlay["topics"][topic_id] = graph["topics"][topic_id]
        save_local_graph(repo, overlay)
        sync_snapshot_from_disk(repo, reason="scan")
    _topics_log().write(
        "topic_graph_scanned",
        repo_path=str(repo), staged=staged,
        updated_topic_count=len(updated_topics),
        covered_ref_count=len(covered),
    )
    return {
        "updated_topics": updated_topics,
        "covered_ref_count": len(covered),
    }


def _ref_sort_key(ref: dict[str, str]) -> tuple[int, str]:
    role = ref.get("role")
    return (ROLE_ORDER.index(role) if role in ROLE_ORDER else 99, ref.get("path", ""))


def _ref_with_meta(path: str, role: Any, tier: Any = None) -> dict[str, str]:
    """A ref dict carrying its curated `role` and `tier`, each only when valid.

    Both axes are owned by the proposal LLM / human edits, never invented by
    scan, so an unknown or absent value is simply omitted. `tier` is omitted
    when it is the default (`primary`) too, so the graph's canonical form for a
    normal ref stays the bare `{path, role?}` — only an explicit `reference`
    tier is persisted. A full rescan must round-trip an existing `tier` (see
    `_apply_full_refs`), or drift-exclusion would silently reset each scan.
    """
    ref: dict[str, str] = {"path": path}
    if role in REF_ROLES:
        ref["role"] = role
    if tier in REF_TIERS and tier != DEFAULT_REF_TIER:
        ref["tier"] = tier
    return ref


def _apply_staged_refs(topic: dict[str, Any], matched: list[dict[str, str]], covered: set[str]) -> bool:
    """Incrementally ADD newly-matching refs; never drop existing ones.

    Staged scans see only this commit's files, so removing unmatched
    refs would zero out every topic whose globs this commit didn't touch.
    New refs are added without a role. Returns whether refs changed.
    """
    existing_refs = [ref for ref in topic.get("refs", []) if isinstance(ref, dict)]
    existing_paths = {ref.get("path") for ref in existing_refs}
    covered.update(ref["path"] for ref in matched)
    covered.update(ref["path"] for ref in existing_refs if ref.get("path"))
    additions = [{"path": ref["path"]} for ref in matched if ref["path"] not in existing_paths]
    if not additions:
        return False
    topic["refs"] = sorted(existing_refs + additions, key=_ref_sort_key)
    return True


def _apply_full_refs(topic: dict[str, Any], matched: list[dict[str, str]], covered: set[str]) -> bool:
    """Authoritatively reconcile refs to the glob-matched file set.

    Drops refs whose file/glob no longer matches and adds new matches,
    but preserves the curated `role` of any path that survives. Returns
    whether refs changed.
    """
    existing_meta = {
        ref.get("path"): (ref.get("role"), ref.get("tier"))
        for ref in topic.get("refs", []) if isinstance(ref, dict) and ref.get("path")
    }
    new_refs = sorted(
        (_ref_with_meta(ref["path"], *existing_meta.get(ref["path"], (None, None)))
         for ref in matched),
        key=_ref_sort_key,
    )
    covered.update(ref["path"] for ref in matched)
    if topic.get("refs", []) == new_refs:
        return False
    topic["refs"] = new_refs
    return True


def refs_for_topic(files: list[str], topic: dict[str, Any]) -> list[dict[str, str]] | None:
    include = topic.get("include_globs") or []
    if not include:
        return None
    exclude = list(DEFAULT_EXCLUDES) + list(topic.get("exclude_globs") or [])
    refs: list[dict[str, str]] = []
    for path in files:
        if any(match_glob(path, pattern) for pattern in include) and not any(match_glob(path, pattern) for pattern in exclude):
            refs.append({"path": path})
    return sorted(refs, key=_ref_sort_key)


def is_repo_content_path(path: str) -> bool:
    return not any(match_glob(path, pattern) for pattern in DEFAULT_EXCLUDES) and not is_generated_path(path)


def list_repo_files(repo_path: str | Path) -> list[str]:
    matcher = load_ignore_rules(repo_path)
    try:
        return [path for path in tracked_files(repo_path) if not matcher.is_ignored(path)]
    except TopicGraphError:
        paths: list[str] = []
        root = Path(repo_path)
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            rel_dir = Path(current).relative_to(root)
            for filename in files:
                rel = (rel_dir / filename).as_posix()
                if is_repo_content_path(rel) and not matcher.is_ignored(rel):
                    paths.append(rel)
        return sorted(paths)


def tracked_files(repo_path: str | Path) -> list[str]:
    return git_lines(repo_path, ["ls-files"])


def staged_files(repo_path: str | Path) -> list[str]:
    return git_lines(repo_path, ["diff", "--cached", "--name-only", "--diff-filter=ACMR"])


def git_lines(repo_path: str | Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise TopicGraphError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return sorted(line for line in result.stdout.splitlines() if line)


def update_topic(repo_path: str | Path, topic_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    graph = load_authoritative_graph(repo_path)
    topic = graph.get("topics", {}).get(topic_id)
    if not topic:
        raise TopicGraphError(f"topic not found: {topic_id}")
    allowed = {"label", "aliases", "intent", "status", "refs", "edges", "commands", "include_globs", "exclude_globs"}
    for key, value in patch.items():
        if key in allowed:
            topic[key] = value
    # Single-topic edits route to the local overlay (whole-topic override),
    # leaving the git-tracked base `topic.json` untouched.
    overlay = load_local_graph(repo_path)
    overlay["topics"][topic_id] = topic
    save_local_graph(repo_path, overlay)
    # Sync the snapshot BEFORE validate — `validate` now reads the
    # authoritative graph (snapshot-first), so we must update the
    # snapshot before validating or it sees the old state.
    sync_snapshot_from_disk(repo_path, reason="topic_update")
    result = validate(repo_path)
    if not result.ok:
        raise TopicGraphError("; ".join(result.errors))
    _topics_log().write(
        "approved_topic_edited",
        topic_id=topic_id, repo_path=str(repo_path),
        patched_keys=sorted(k for k in patch if k in allowed),
    )
    return topic | {"id": topic_id}


def promote_topic(repo_path: str | Path, topic_id: str) -> dict[str, Any]:
    """Make the local overlay's view of `topic_id` permanent in the
    git-tracked base `topic.json`, then drop it from the overlay.

    This is the only sanctioned path (besides `bootstrap`) that mutates
    the base graph. It promotes either an overlay-added/overridden topic
    (copied into the base) or a tombstoned deletion (removed from the
    base). The effective `merge(base, overlay)` is unchanged — promotion
    only relocates where the topic lives so it travels via git instead of
    staying machine-local — so the snapshot stays consistent (no drift)
    and the base's top-level fields are preserved.
    """
    try:
        base = load_graph(repo_path)
    except TopicGraphError:
        base = empty_graph(repo_path)  # first promote bootstraps the base
    overlay = load_local_graph(repo_path)
    base_topics = base.setdefault("topics", {})
    overlay_topics = overlay.setdefault("topics", {})
    tombstones = overlay.setdefault("deleted_topics", [])

    if topic_id in overlay_topics:
        base_topics[topic_id] = overlay_topics.pop(topic_id)
        action = "added"
    elif topic_id in tombstones:
        base_topics.pop(topic_id, None)
        overlay["deleted_topics"] = [t for t in tombstones if t != topic_id]
        action = "removed"
    else:
        raise TopicGraphError(
            f"nothing to promote: {topic_id} is not in the local overlay"
        )

    # Preserve base top-level fields (no re-stamp) so the merged disk stays
    # hash-equal to the snapshot; promotion never changes the live graph.
    write_graph_to_disk(repo_path, base)
    save_local_graph(repo_path, overlay)
    _topics_log().write(
        "topic_promoted",
        topic_id=topic_id, action=action, repo_path=str(repo_path),
    )
    return {"topic_id": topic_id, "action": action}


def promote_all_topics(repo_path: str | Path) -> dict[str, Any]:
    """Promote every pending change in the local overlay into the base graph
    in a single pass: all overlay-added/overridden topics are copied into the
    git-tracked `topic.json`, and all tombstoned deletions are removed from it.

    Equivalent to calling `promote_topic` for each id in the overlay, but it
    reads, mutates, and writes the two graphs once instead of per topic.
    Returns the ids that were added and removed (a no-op leaves both empty).
    """
    try:
        base = load_graph(repo_path)
    except TopicGraphError:
        base = empty_graph(repo_path)  # first promote bootstraps the base
    overlay = load_local_graph(repo_path)
    base_topics = base.setdefault("topics", {})
    overlay_topics = overlay.setdefault("topics", {})
    tombstones = overlay.setdefault("deleted_topics", [])

    added = sorted(overlay_topics.keys())
    removed = sorted(tombstones)
    for topic_id in added:
        base_topics[topic_id] = overlay_topics[topic_id]
    for topic_id in removed:
        base_topics.pop(topic_id, None)
    overlay["topics"] = {}
    overlay["deleted_topics"] = []

    if not added and not removed:
        return {"added": [], "removed": []}

    # Preserve base top-level fields (no re-stamp) so the merged disk stays
    # hash-equal to the snapshot; promotion never changes the live graph.
    write_graph_to_disk(repo_path, base)
    save_local_graph(repo_path, overlay)
    _topics_log().write(
        "topics_promoted_all",
        added=added, removed=removed, repo_path=str(repo_path),
    )
    return {"added": added, "removed": removed}


def _prune_inbound_edges(topics: dict[str, Any], target_id: str) -> int:
    """Drop edges whose target is `target_id` from each topic, in place.

    Returns the number of edges removed.
    """
    removed = 0
    for sibling in topics.values():
        edges = sibling.get("edges") or []
        kept = [e for e in edges if e.get("target") != target_id]
        if len(kept) != len(edges):
            removed += len(edges) - len(kept)
            sibling["edges"] = kept
    return removed


def _drop_from_overlay(overlay: dict[str, Any], topic_id: str) -> None:
    """Remove `topic_id` from the overlay entirely — its entry and any
    tombstone for it."""
    overlay.setdefault("topics", {}).pop(topic_id, None)
    tombstones = overlay.setdefault("deleted_topics", [])
    overlay["deleted_topics"] = [t for t in tombstones if t != topic_id]


def delete_topic(repo_path: str | Path, topic_id: str) -> dict[str, Any]:
    """Permanently remove an approved topic from the graph.

    A hard delete (the inverse of `promote` / `bootstrap`): the topic is
    removed wherever it lives — the git-tracked base `topic.json` and the
    local overlay — any tombstone for its id is cleared, inbound edges
    that would dangle are pruned from sibling topics in both stores, and
    its per-topic `wiki/<id>.md` is removed. The snapshot is re-synced to
    the reduced graph. For a reversible "send back to draft" use
    `downgrade_topic_to_proposal` instead.
    """
    merged = load_authoritative_graph(repo_path)
    if topic_id not in merged.get("topics", {}):
        raise TopicGraphError(f"topic not found: {topic_id}")

    try:
        base = load_graph(repo_path)
    except TopicGraphError:
        base = empty_graph(repo_path)
    overlay = load_local_graph(repo_path)
    base_topics = base.setdefault("topics", {})

    base_changed = base_topics.pop(topic_id, None) is not None
    _drop_from_overlay(overlay, topic_id)

    # Prune inbound edges (target == topic_id) from siblings in both stores,
    # so the reduced graph has no dangling edge targets.
    base_pruned = _prune_inbound_edges(base_topics, topic_id)
    pruned_edges = base_pruned + _prune_inbound_edges(overlay["topics"], topic_id)
    base_changed = base_changed or base_pruned > 0

    # Only touch the git-tracked base when it actually changed — an
    # overlay-only delete must not reformat or create the base graph files.
    if base_changed:
        write_graph_to_disk(repo_path, base)
    save_local_graph(repo_path, overlay)

    wiki_file = topic_dir(repo_path) / "wiki" / f"{slugify(topic_id)}.md"
    wiki_removed = wiki_file.exists()
    if wiki_removed:
        wiki_file.unlink()

    # Re-sync the snapshot to the reduced merged graph; a cold repo with no
    # snapshot yet auto-seeds on the next read, so a None return is fine.
    sync_snapshot_from_disk(repo_path, reason="delete")
    result = validate(repo_path)
    if not result.ok:
        raise TopicGraphError("; ".join(result.errors))
    # Regenerate the wiki index so it no longer links the deleted topic.
    # Lazy import avoids a wiki<->scan import cycle. Best-effort: a failure
    # here must not undo the delete.
    try:
        from lib.topics.wiki import generate_wiki
        generate_wiki(repo_path)
    except Exception:  # noqa: BLE001 — index regen is cosmetic vs the delete
        pass
    _topics_log().write(
        "topic_deleted",
        topic_id=topic_id, repo_path=str(repo_path),
        pruned_edges=pruned_edges, wiki_removed=wiki_removed,
    )
    return {"topic_id": topic_id, "pruned_edges": pruned_edges, "wiki_removed": wiki_removed}


_HOOK_PRELUDE = """#!/bin/sh
# Generated by regin topics install-hook.
set -eu

ROOT=$(git rev-parse --show-toplevel)
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi
"""

_PRE_COMMIT_BODY = """
"$PY" "$ROOT/cli/regin.py" topics check --repo "$ROOT"
"$PY" "$ROOT/cli/regin.py" topics scan --repo "$ROOT" --staged
# Stage the approved graph + per-topic wiki narratives so they travel
# with this commit. .gitignore un-ignores exactly these (topic.json or
# the split topics/*.json, and wiki/*.md); the local topic.local.json
# overlay is never staged.
[ -f "$ROOT/.regin/topics/topic.json" ] && git add "$ROOT/.regin/topics/topic.json"
[ -d "$ROOT/.regin/topics/topics" ] && git add "$ROOT/.regin/topics/topics"
[ -d "$ROOT/.regin/topics/wiki" ] && git add "$ROOT/.regin/topics/wiki"
"""

# After git pull / merge, mirror upstream's approved topics into the
# local snapshot DB. `|| true` keeps a stale topic.json from breaking
# the user's git workflow.
_POST_MERGE_BODY = """
"$PY" "$ROOT/cli/regin.py" topics import --repo "$ROOT" --reason git_pull --quiet || true
"""

# $3 is git's "branch_flag" — 1 for branch checkout, 0 for file
# checkout. We only care about branch switches; the file-checkout case
# can't have changed topic.json on disk.
_POST_CHECKOUT_BODY = """
[ "${3:-0}" = "1" ] || exit 0
"$PY" "$ROOT/cli/regin.py" topics import --repo "$ROOT" --reason git_pull --quiet || true
"""

# After a commit lands, follow any file renames it introduced into the topic
# refs (overlay) + memory paths. `|| true` keeps drift from ever blocking the
# commit; the command itself is a no-op unless `mechanical_autoapply` is on.
_POST_COMMIT_BODY = """
"$PY" "$ROOT/cli/regin.py" topics drift --repo "$ROOT" || true
"""

_HOOK_BODIES: dict[str, str] = {
    "pre-commit": _PRE_COMMIT_BODY,
    "post-commit": _POST_COMMIT_BODY,
    "post-merge": _POST_MERGE_BODY,
    "post-checkout": _POST_CHECKOUT_BODY,
}


def _write_hook(hooks_dir: Path, name: str, body: str) -> Path:
    hook_path = hooks_dir / name
    hook_path.write_text(_HOOK_PRELUDE + body)
    current = os.stat(hook_path).st_mode
    os.chmod(hook_path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def install_topic_hooks(repo_path: str | Path) -> dict[str, Path]:
    """Install regin's three git hooks for the topic-sync flow.

    Returns `{hook_name: written_path}`. Overwrites existing hooks at
    those paths — same behaviour as the prior single-hook installer,
    kept for consistency.

    `pre-commit` stamps the approved `topic.json` + per-topic wiki files
    into the commit;
    `post-merge` and `post-checkout` call `regin topics import` so a
    teammate's git-shipped approved graph lands in the local snapshot
    DB without a shared database.
    """
    repo = Path(repo_path)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    installed = {
        name: _write_hook(hooks_dir, name, body)
        for name, body in _HOOK_BODIES.items()
    }
    _topics_log().write(
        "topic_hooks_installed",
        repo_path=str(repo),
        hooks=[str(p) for p in installed.values()],
    )
    return installed


def install_pre_commit_hook(repo_path: str | Path) -> Path:
    """Back-compat wrapper around `install_topic_hooks`.

    Returns just the pre-commit path so existing callers (and the prior
    test surface) keep working; new code should call `install_topic_hooks`
    directly to see all three installed paths.
    """
    return install_topic_hooks(repo_path)["pre-commit"]
