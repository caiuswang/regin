"""Reap stranded external-agent proposal runs.

The in-process runner detects the common "agent exited without finishing"
case directly (its subprocess `communicate()` returns). This reaper covers
the case the runner can't see: the watcher itself is gone — `regin serve`
was restarted mid-run, so the daemon thread and its `Popen` handle no
longer exist. Such a run is pinned non-terminal forever and the frontend
poller pings it indefinitely.

A run is stranded when it is non-terminal, has **no live local
subprocess** owning it, never emitted the finish signal, and has gone
quiet past a grace window (its agent session, traced under
`topic-proposal-<id>`, is no longer making progress). It is marked
`failed` — the explicit terminal state the old silent path never set.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.topics import topic_dir, utc_now

_ACTIVE_STATES = frozenset({"queued", "running", "waiting_for_permission"})


def _seconds_since(timestamp: str | None) -> float | None:
    """Age in seconds of an ISO-8601 (UTC, naive) timestamp, or None."""
    if not timestamp:
        return None
    try:
        when = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - when).total_seconds()


def _is_stranded(repo: Path, run: dict, grace_seconds: int) -> bool:
    """True when this non-terminal run has no live watcher and has gone
    quiet past the grace window."""
    from . import run_control

    if run.get("state") not in _ACTIVE_STATES:
        return False
    proposal_id = run["id"]
    if run_control.is_live(proposal_id):
        return False
    status = run.get("status") or {}
    if status.get("agent_signaled"):
        return False
    last_seen = status.get("updated_at") or run.get("last_activity_at") or run.get("started_at")
    age = _seconds_since(last_seen)
    return age is not None and age >= grace_seconds


def reap_stranded_proposal_runs(repo_path: str | Path) -> int:
    """Mark stranded runs `failed`; return how many were reaped.

    Safe to call opportunistically (e.g. when listing runs): it only ever
    advances a quiet, unwatched, non-terminal run to a terminal state.
    """
    repo = Path(repo_path)
    grace = max(0, settings.topic_evolution.proposal_stranded_grace_seconds)
    from .core_io import list_proposal_runs
    from lib.topics.proposal_external import load_status, write_status

    log = get_activity_logger("topics")
    reaped = 0
    for run in list_proposal_runs(repo):
        if not _is_stranded(repo, run, grace):
            continue
        proposal_id = run["id"]
        out_dir = topic_dir(repo) / "proposals" / proposal_id
        status = load_status(out_dir) or {}
        if status.get("agent_signaled") or status.get("state") not in _ACTIVE_STATES:
            continue
        status.update(
            state="failed",
            error="agent session ended without emitting the completion signal",
            completed_at=utc_now(),
        )
        write_status(out_dir, status)
        log.write("proposal_run_reaped", proposal_id=proposal_id)
        reaped += 1
    return reaped
