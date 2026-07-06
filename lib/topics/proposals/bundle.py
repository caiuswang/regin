"""Portable proposal bundles: share an in-flight run without a server.

`export_proposal_bundle` writes a run's full review state — run
status/metadata, the current proposal, every revision (with topic
snapshots), and all feedback threads — into one JSON file under
`.regin/topics/bundles/`, which `.gitignore` re-includes so bundles
travel via ordinary commits. `import_proposal_bundle` recreates that
state in the importing machine's local SQLite so review continues
there. See `docs/topics/multi-user.md` ("Sharing an in-flight
proposal").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.topics import TopicGraphError, topic_dir, utc_now

from ._common import _topics_log

BUNDLE_VERSION = 1


def _default_bundle_path(repo_path: str | Path, proposal_id: str) -> Path:
    return topic_dir(repo_path) / "bundles" / f"{proposal_id}.json"


def _validate_proposal_id(proposal_id: str) -> None:
    if (not proposal_id or "/" in proposal_id or "\\" in proposal_id
            or proposal_id in {".", ".."}):
        raise TopicGraphError(f"invalid proposal id: {proposal_id}")


def export_proposal_bundle(
    repo_path: str | Path,
    proposal_id: str,
    out_path: str | Path | None = None,
) -> Path:
    """Write one JSON bundle for `proposal_id` and return its path.

    Bundle shape (all identity is machine-neutral — numeric SQLite PKs
    never cross machines; revisions travel by `revision_number`):

        {"bundle_version": 1, "proposal_id", "exported_at",
         "run": {state/stamps/metadata}, "proposal": <load_proposal dict>,
         "wiki": <combined wiki.md text or null>,
         "revisions": [{revision_number, kind, stamps, topics, ...}],
         "feedback_threads": [{anchor/resolution/created_by, comments}]}
    """
    from lib.topics.proposal_orm import orm_export_proposal_bundle_parts

    from .core_io import load_proposal

    repo = Path(repo_path)
    _validate_proposal_id(proposal_id)
    # Load first: for legacy runs this lazily promotes `proposal_topics`
    # rows into a system_migrated revision, which the parts export reads.
    proposal = load_proposal(repo, proposal_id)
    parts = orm_export_proposal_bundle_parts(repo, proposal_id)
    if parts is None:
        raise TopicGraphError(
            f"proposal not found in the local DB: {proposal_id} "
            "(a disk-only legacy run needs backfill_disk_proposals_to_orm first)")
    wiki_path = topic_dir(repo) / "proposals" / proposal_id / "wiki.md"
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "proposal_id": proposal_id,
        "exported_at": utc_now(),
        "run": parts["run"],
        "proposal": proposal,
        "wiki": wiki_path.read_text() if wiki_path.exists() else None,
        "revisions": parts["revisions"],
        "feedback_threads": parts["feedback_threads"],
    }
    path = (Path(out_path) if out_path is not None
            else _default_bundle_path(repo, proposal_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, indent=2))
    _topics_log().write(
        "proposal_bundle_exported",
        proposal_id=proposal_id, path=str(path),
        revisions=len(bundle["revisions"]),
        threads=len(bundle["feedback_threads"]),
        repo_path=str(repo),
    )
    return path


def _read_bundle(bundle_path: str | Path) -> dict[str, Any]:
    path = Path(bundle_path)
    if not path.exists():
        raise TopicGraphError(f"bundle not found: {path}")
    try:
        bundle = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise TopicGraphError(f"bundle is not valid JSON: {path} ({exc})")
    if not isinstance(bundle, dict):
        raise TopicGraphError(f"bundle must be a JSON object: {path}")
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise TopicGraphError(
            f"unsupported bundle_version {bundle.get('bundle_version')!r} "
            f"(this regin reads version {BUNDLE_VERSION})")
    _validate_proposal_id(str(bundle.get("proposal_id") or ""))
    return bundle


def import_proposal_bundle(
    repo_path: str | Path,
    bundle_path: str | Path,
    force: bool = False,
) -> dict[str, Any]:
    """Recreate a bundled proposal run in the local ORM for review.

    Refuses (action="refused" + message) when a run with the same id
    already exists locally, unless `force=True` replaces the local
    run + revisions + threads wholesale. Seeds review state only —
    never touches the approved graph or marks anything applied.

    Returns {proposal_id, revisions, threads, action: created|replaced|refused}.
    """
    from lib.topics.proposal_orm import orm_import_proposal_bundle

    repo = Path(repo_path)
    bundle = _read_bundle(bundle_path)
    result = orm_import_proposal_bundle(repo, bundle, force=force)
    if result["action"] == "refused":
        return result
    wiki = bundle.get("wiki")
    if wiki:
        out_dir = topic_dir(repo) / "proposals" / result["proposal_id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "wiki.md").write_text(wiki)
    _topics_log().write(
        "proposal_bundle_imported",
        proposal_id=result["proposal_id"], action=result["action"],
        revisions=result["revisions"], threads=result["threads"],
        bundle_path=str(bundle_path), repo_path=str(repo),
    )
    return result
