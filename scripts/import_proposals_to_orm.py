#!/usr/bin/env python
"""One-shot importer: `.regin/topics/proposals/*` → ORM rows.

Phase D of the topic-proposal refactor. Walks every registered repo,
reads each on-disk proposal directory, and inserts:

  - `proposal_runs` row per proposal (idempotent via PK = proposal_id).
  - `proposal_topics` row per draft topic (idempotent via the
    `(run_id, topic_id)` unique index).

Then for any repo with a non-empty `topic.json` and NO existing
`graph_snapshots` row, seeds one `is_latest=1` snapshot capturing the
current approved graph as `reason='import'`. After this seed, every
reader that flips to `load_authoritative_graph` has a snapshot to read.

Idempotent: re-running is safe. Existing ProposalRun rows are
left alone; only missing ones get inserted. Same for ProposalTopic.
Snapshots only seed if the repo has zero rows for itself.

Usage:
    python scripts/import_proposals_to_orm.py [--dry-run] [--repo NAME]

Exit codes:
    0   success
    1   one or more repos had errors (per-proposal errors logged but
        don't fail the whole run; this code surfaces script-level fails)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

# repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.orm import SessionLocal  # noqa: E402
from lib.orm.models import (  # noqa: E402
    GraphSnapshot, ProposalRun, ProposalTopic, Repo,
)
from lib.topics.core import topic_path  # noqa: E402

log = logging.getLogger("import_proposals")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("skipping %s: %s", path, exc)
        return None


def _row_from_status(
    repo_id: int,
    proposal_id: str,
    status: dict,
    proposal: dict | None,
) -> ProposalRun:
    """Compose a ProposalRun row from on-disk artefacts."""
    metadata = (proposal or {}).get("metadata") or {}
    return ProposalRun(
        id=proposal_id,
        repo_id=repo_id,
        provider=(proposal or {}).get("provider") or status.get("provider") or "unknown",
        scope=(proposal or {}).get("scope", "all"),
        state=status.get("state") or "unknown",
        agent_id=status.get("agent"),
        complexity=metadata.get("requested_complexity") or metadata.get("complexity") or "standard",
        started_at=status.get("started_at") or status.get("updated_at") or utc_now(),
        completed_at=status.get("completed_at"),
        updated_at=status.get("updated_at"),
        error=status.get("error"),
        error_detail=status.get("error_detail"),
        prompt_template_slugs=json.dumps(
            status.get("prompt_template_ids") or metadata.get("prompt_template_ids") or []
        ),
        evidence_hash=None,
        regenerate_scope=None,
        metadata_json=json.dumps(metadata),
        topic_request=(proposal or {}).get("topic_request") or metadata.get("topic_request"),
    )


def _row_from_proposed_topic(run_id: str, topic: dict) -> ProposalTopic:
    return ProposalTopic(
        run_id=run_id,
        topic_id=topic.get("id") or "",
        label=topic.get("label") or topic.get("id") or "",
        intent=topic.get("intent") or "",
        status=topic.get("status") or "active",
        aliases_json=json.dumps(topic.get("aliases") or []),
        refs_json=json.dumps(topic.get("refs") or []),
        edges_json=json.dumps(topic.get("edges") or []),
        commands_json=json.dumps(topic.get("commands") or []),
        include_globs_json=json.dumps(topic.get("include_globs") or []),
        exclude_globs_json=json.dumps(topic.get("exclude_globs") or []),
        evidence_paths_json=json.dumps(topic.get("evidence_paths") or []),
        source=topic.get("source"),
        review_status=topic.get("review_status"),
        accepted_topic_id=topic.get("accepted_topic"),
        accepted_at=topic.get("accepted_at"),
        merged_topic_id=topic.get("merged_topic"),
        merged_at=topic.get("merged_at"),
        ignored_at=topic.get("ignored_at"),
        downgraded_from=topic.get("downgraded_from"),
        downgraded_at=topic.get("downgraded_at"),
        replaced_existing=1 if topic.get("replaced_existing") else 0,
    )


def _import_proposal(
    session,
    repo: Repo,
    proposal_dir: Path,
    *,
    dry_run: bool,
) -> tuple[bool, int]:
    """Import one `.regin/topics/proposals/<id>/` directory.

    Returns `(run_inserted, topics_inserted)`.
    """
    proposal_id = proposal_dir.name
    topics_path = proposal_dir / "topics.json"
    status_path = proposal_dir / "status.json"

    proposal = _read_json(topics_path) if topics_path.exists() else None
    status = _read_json(status_path) if status_path.exists() else None
    if status is None:
        # Inferred status when only topics.json is on disk (matches what
        # load_proposal_status does today).
        if proposal is not None:
            status = {"state": "completed"}
        else:
            log.warning("skipping %s: neither status.json nor topics.json", proposal_dir)
            return False, 0

    existing_run = session.get(ProposalRun, proposal_id)
    run_inserted = False
    if existing_run is None:
        if dry_run:
            log.info("[dry-run] would INSERT ProposalRun(id=%s, repo=%s, state=%s)",
                     proposal_id, repo.name, status.get("state"))
        else:
            session.add(_row_from_status(repo.id, proposal_id, status, proposal))
        run_inserted = True

    topics_inserted = 0
    proposed = (proposal or {}).get("topics") or []
    for topic in proposed:
        if not isinstance(topic, dict):
            continue
        topic_id = topic.get("id") or ""
        if not topic_id:
            continue
        existing_topic = session.exec(
            select(ProposalTopic)
            .where(ProposalTopic.run_id == proposal_id)
            .where(ProposalTopic.topic_id == topic_id)
        ).first()
        if existing_topic is not None:
            continue
        if dry_run:
            log.info("[dry-run] would INSERT ProposalTopic(run=%s, topic=%s, status=%s)",
                     proposal_id, topic_id, topic.get("review_status") or "pending")
        else:
            session.add(_row_from_proposed_topic(proposal_id, topic))
        topics_inserted += 1

    return run_inserted, topics_inserted


def _seed_snapshot_from_disk(
    session,
    repo: Repo,
    *,
    dry_run: bool,
) -> bool:
    """If repo has no snapshots, seed one from the current `topic.json`."""
    existing = session.exec(
        select(GraphSnapshot).where(GraphSnapshot.repo_id == repo.id)
    ).first()
    if existing is not None:
        return False

    graph_path = topic_path(repo.path)
    if not graph_path.exists():
        log.debug("no topic.json at %s — skipping snapshot seed", graph_path)
        return False
    graph = _read_json(graph_path)
    if not graph:
        return False

    if dry_run:
        log.info("[dry-run] would seed GraphSnapshot for repo=%s (%d topics)",
                 repo.name, len(graph.get("topics", {})))
        return True

    snap = GraphSnapshot(
        repo_id=repo.id,
        taken_at=utc_now(),
        reason="import",
        graph_json=json.dumps(graph),
        wiki_pages_json="{}",
        diff_summary_json=json.dumps({"reason": "import_seed"}),
        pinned=0,
        is_latest=1,
    )
    session.add(snap)
    return True


def import_repo(session, repo: Repo, *, dry_run: bool) -> dict[str, int]:
    repo_path = Path(repo.path)
    proposals_dir = repo_path / ".regin/topics/proposals"
    counts = {"runs": 0, "topics": 0, "snapshot": 0}
    if proposals_dir.exists():
        for proposal_dir in sorted(proposals_dir.iterdir()):
            if not proposal_dir.is_dir():
                continue
            try:
                run_inserted, topics_inserted = _import_proposal(
                    session, repo, proposal_dir, dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                log.error("error importing %s: %s", proposal_dir, exc)
                continue
            if run_inserted:
                counts["runs"] += 1
            counts["topics"] += topics_inserted

    if _seed_snapshot_from_disk(session, repo, dry_run=dry_run):
        counts["snapshot"] = 1

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Log actions without writing.")
    parser.add_argument("--repo", help="Only import this repo (by name).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    total = {"runs": 0, "topics": 0, "snapshot": 0}
    with SessionLocal() as session:
        repos = list(session.exec(select(Repo).order_by(Repo.id)))
        if args.repo:
            repos = [r for r in repos if r.name == args.repo]
        if not repos:
            log.warning("no repos matched")
            return 0
        for repo in repos:
            counts = import_repo(session, repo, dry_run=args.dry_run)
            log.info("repo=%s: +%d runs, +%d topics, +%d snapshot",
                     repo.name, counts["runs"], counts["topics"], counts["snapshot"])
            for k, v in counts.items():
                total[k] += v
        if not args.dry_run:
            session.commit()

    log.info("DONE: %d runs, %d topics, %d snapshots seeded (dry_run=%s)",
             total["runs"], total["topics"], total["snapshot"], args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
