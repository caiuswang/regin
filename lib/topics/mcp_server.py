"""`topics` — the topic-proposal lifecycle MCP server.

A stdio MCP server exposing the proposal review loop (list / show / diff /
apply / review-state / feedback) so an in-session agent can drive it
without the web UI. Mirrors `lib/memory/mcp_server.py`: regin imports
happen lazily inside each tool call so server startup stays instant, and
every tool returns a plain string — errors come back as one-line failures,
never exceptions through the MCP layer.

Deliberately no propose/draft tool: drafting spawns a long-running agent
subprocess, which would block this server. Use `regin topics` / the web UI
to start a run; review it from here.
"""

from __future__ import annotations

import os
import sys

# The server is spawned by the agent harness with an arbitrary cwd; make
# `lib.*` importable the same way `cli/regin.py` does.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("topics")


def _log():
    from lib.activity_log import get_activity_logger
    return get_activity_logger("topics")


def _domain_errors() -> tuple[type[Exception], ...]:
    from lib.topics import TopicGraphError
    return (TopicGraphError, LookupError, ValueError)


def _resolve_repo(repo: str) -> tuple[Optional[str], str]:
    """Resolve a registered repo name or repo path to the registered path.

    Returns `(repo_path, "")` on success, `(None, error_line)` otherwise —
    proposals live per-repo, so an unregistered path has no runs to serve.
    """
    from pathlib import Path
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models import Repo

    raw = (repo or "").strip()
    if not raw:
        return None, "repo is required — pass a registered repo name or absolute repo path"
    with SessionLocal() as s:
        row = s.exec(select(Repo).where(Repo.name == raw)).first()
        if row is None:
            resolved = str(Path(raw).expanduser().resolve())
            row = s.exec(select(Repo).where(Repo.path == resolved)).first()
    if row is None:
        return None, (f"unknown repo {raw!r} — not a registered repo name or "
                      f"path (register with `regin add-repo`)")
    return row.path, ""


def _run_line(repo_path: str, run: dict[str, Any]) -> str:
    from lib.topics import TopicGraphError
    from lib.topics.proposals import load_proposal, proposal_review_state
    review = "-"
    topic_count = 0
    try:
        proposal = load_proposal(repo_path, run["id"])
        review = proposal_review_state(proposal)
        topic_count = len(proposal.get("topics") or [])
    except TopicGraphError:
        review = "-"  # run exists but has no draft yet (e.g. still queued)
    return (f"- {run['id']} · run={run.get('state', 'unknown')}"
            f" · review={review} · topics={topic_count}")


@mcp.tool()
def proposal_list(repo: str, state: str = "") -> str:
    """List topic-proposal runs for a repo, newest first.

    Start here to find a run id for `proposal_show` / `proposal_diff` /
    `proposal_apply`.

    Args:
        repo: Registered repo name or absolute repo path.
        state: Optional run-state filter (queued | running | completed |
            failed | cancelled | timed_out | waiting_for_permission);
            empty lists every run.

    Returns:
        One line per run: id, run state, review state, topic count — or a
        note that none matched.
    """
    from lib.topics.proposals import list_proposal_runs
    repo_path, err = _resolve_repo(repo)
    if err:
        return err
    runs = list_proposal_runs(repo_path)
    if state:
        runs = [r for r in runs if r.get("state") == state]
    _log().read("mcp_proposal_list", repo_path=repo_path,
                state=state or None, count=len(runs))
    if not runs:
        return "no proposal runs" + (f" in state {state!r}" if state else "")
    return "\n".join(_run_line(repo_path, r) for r in runs)


def _topics_block(proposal: dict[str, Any]) -> str:
    topics = proposal.get("topics") or []
    if not topics:
        return "topics: none"
    lines = [
        f"- {t.get('id')} · {t.get('label') or t.get('id')}"
        f" · review_status={t.get('review_status') or 'pending'}"
        for t in topics
    ]
    return f"topics ({len(topics)}):\n" + "\n".join(lines)


def _thread_line(t: dict[str, Any]) -> str:
    comments = t.get("comments") or []
    opener = str(comments[0].get("body", "")).splitlines()[0][:100] if comments else ""
    topic = f" (topic {t['proposal_topic_id']})" if t.get("proposal_topic_id") else ""
    return (f"- #{t['id']} [{t.get('kind')}|{t.get('resolution_state')}]"
            f"{topic} {len(comments)} comment(s): {opener}")


def _open_feedback_block(repo_path: str, proposal_id: str) -> str:
    from lib.topics.proposals import list_proposal_feedback_threads
    threads = [t for t in list_proposal_feedback_threads(repo_path, proposal_id)
               if t.get("resolution_state") == "open"]
    if not threads:
        return "open feedback: none"
    return (f"open feedback ({len(threads)}):\n"
            + "\n".join(_thread_line(t) for t in threads))


@mcp.tool()
def proposal_show(repo: str, proposal_id: str) -> str:
    """Show one proposal run: run status, review state, per-topic review
    status, and its open feedback threads.

    Args:
        repo: Registered repo name or absolute repo path.
        proposal_id: A run id from `proposal_list`.

    Returns:
        A header line (run state · review state), then a `topics` block
        (id · label · review_status) and an `open feedback` block.
    """
    from lib.topics import TopicGraphError
    from lib.topics.proposals import (
        load_proposal, load_proposal_status, proposal_review_state,
    )
    repo_path, err = _resolve_repo(repo)
    if err:
        return err
    try:
        status = load_proposal_status(repo_path, proposal_id)
    except _domain_errors() as e:
        return str(e)
    try:
        proposal = load_proposal(repo_path, proposal_id)
        review = proposal_review_state(proposal)
        topics_block = _topics_block(proposal)
    except TopicGraphError:
        review = "-"
        topics_block = "topics: none (no draft yet)"
    _log().read("mcp_proposal_show", repo_path=repo_path, proposal_id=proposal_id)
    head = (f"{proposal_id} · run={status.get('state', 'unknown')} · review={review}")
    if status.get("error"):
        head += f"\nrun error: {status['error']}"
    return "\n".join([head, topics_block,
                      _open_feedback_block(repo_path, proposal_id)])


def _issues_block(label: str, issues: list[dict[str, Any]]) -> str:
    if not issues:
        return f"{label}: none"
    lines = [f"- [{i.get('severity')}] {i.get('code')}: {i.get('message')}"
             for i in issues]
    return f"{label} ({len(issues)}):\n" + "\n".join(lines)


def _deltas_block(deltas: list[dict[str, Any]]) -> str:
    if not deltas:
        return "topic_deltas: none"
    lines = [
        f"- {d.get('kind')} {d.get('topic_id')}: "
        f"+{len(d.get('alias_adds', []))}/-{len(d.get('alias_removes', []))} aliases, "
        f"+{len(d.get('ref_adds', []))}/-{len(d.get('ref_removes', []))} refs, "
        f"+{len(d.get('edge_adds', []))}/-{len(d.get('edge_removes', []))} edges, "
        f"{len(d.get('scalar_changes', []))} field change(s)"
        for d in deltas
    ]
    return f"topic_deltas ({len(deltas)}):\n" + "\n".join(lines)


def _dropped_line(dropped: Optional[dict[str, Any]]) -> str:
    counts = {k: len(v) for k, v in (dropped or {}).items() if v}
    if not counts:
        return "dropped_items: none"
    return "dropped_items: " + ", ".join(f"{k}={n}" for k, n in sorted(counts.items()))


def _diff_summary(diff: dict[str, Any], dropped: Optional[dict[str, Any]]) -> str:
    return "\n".join([
        f"is_applyable: {'yes' if diff.get('is_applyable') else 'NO'}",
        _deltas_block(diff.get("topic_deltas") or []),
        _issues_block("introduced_errors", diff.get("introduced_errors") or []),
        _issues_block("graph_warnings", diff.get("graph_warnings") or []),
        _dropped_line(dropped),
    ])


@mcp.tool()
def proposal_diff(repo: str, proposal_id: str, topic_id: str,
                  strategy: str = "create", target_topic_id: str = "") -> str:
    """Preview what applying one proposed topic would change — side-effect
    free (the read-only twin of `proposal_apply`).

    Args:
        repo: Registered repo name or absolute repo path.
        proposal_id: A run id from `proposal_list`.
        topic_id: The proposed topic's id (see `proposal_show`).
        strategy: create | replace | merge.
        target_topic_id: Required for merge — the approved topic to fold
            the proposal into; ignored otherwise.

    Returns:
        A compact summary: is_applyable, per-topic delta counts,
        introduced_errors (blocking), graph_warnings (advisory), and
        the items the default resolution options would silently drop.
    """
    from lib.topics.proposals import diff_proposal_topic
    repo_path, err = _resolve_repo(repo)
    if err:
        return err
    try:
        payload = diff_proposal_topic(
            repo_path, proposal_id, topic_id,
            strategy=strategy, target_topic_id=target_topic_id or None,
        )
    except _domain_errors() as e:
        return str(e)
    _log().read("mcp_proposal_diff", repo_path=repo_path,
                proposal_id=proposal_id, topic_id=topic_id, strategy=strategy)
    return _diff_summary(payload["diff"], payload.get("dropped_items"))


@mcp.tool()
def proposal_apply(repo: str, proposal_id: str, topic_id: str,
                   strategy: str = "create", target_topic_id: str = "") -> str:
    """MUTATES the approved topic graph: applies one proposed topic
    (create / replace / merge) and commits a new graph snapshot.

    Run `proposal_diff` first with the same arguments to preview. The run
    must be in review state `ready_to_apply` (or `partially_applied`) —
    use `proposal_review_state` to advance it.

    Args:
        repo: Registered repo name or absolute repo path.
        proposal_id: A run id from `proposal_list`.
        topic_id: The proposed topic's id (see `proposal_show`).
        strategy: create | replace | merge.
        target_topic_id: Required for merge — the approved topic to fold
            the proposal into; ignored otherwise.

    Returns:
        On success, the committed snapshot id + applied delta summary; on
        a no-op re-apply, `already applied` with the prior snapshot id;
        when the diff has unresolvable errors, the blocking error list
        (nothing is written in that case).
    """
    from lib.topics.proposals import apply_proposal_topic
    repo_path, err = _resolve_repo(repo)
    if err:
        return err
    try:
        result = apply_proposal_topic(
            repo_path, proposal_id, topic_id,
            strategy=strategy, target_topic_id=target_topic_id or None,
        )
    except _domain_errors() as e:
        return str(e)
    _log().write("mcp_proposal_apply", repo_path=repo_path,
                 proposal_id=proposal_id, topic_id=topic_id, strategy=strategy,
                 ok=bool(result.get("ok")),
                 already_applied=bool(result.get("already_applied")),
                 snapshot_id=result.get("snapshot_id"))
    if result.get("already_applied"):
        return (f"already applied (no-op) — prior snapshot "
                f"{result.get('snapshot_id')}")
    if not result.get("ok"):
        diff = result.get("diff") or {}
        return ("NOT applied — unresolvable errors:\n"
                + _issues_block("introduced_errors",
                                diff.get("introduced_errors") or [])
                + "\n" + _dropped_line(result.get("dropped_items")))
    applied = result.get("applied_diff") or {}
    return "\n".join([
        f"applied — snapshot {result.get('snapshot_id')}",
        _deltas_block(applied.get("topic_deltas") or []),
        _dropped_line(result.get("dropped_items")),
    ])


@mcp.tool()
def proposal_review_state(repo: str, proposal_id: str, state: str) -> str:
    """Set a proposal run's review state (e.g. mark it `ready_to_apply` so
    `proposal_apply` will accept it, or `changes_requested` to send it
    back for a redraft).

    Args:
        repo: Registered repo name or absolute repo path.
        proposal_id: A run id from `proposal_list`.
        state: One of draft | pending_review | changes_requested |
            ready_to_apply | partially_applied | applied.

    Returns:
        A confirmation line with the new state, or a one-line failure.
    """
    from lib.topics.proposals import set_proposal_review_state
    repo_path, err = _resolve_repo(repo)
    if err:
        return err
    try:
        set_proposal_review_state(repo_path, proposal_id, state)
    except _domain_errors() as e:
        return str(e)
    _log().write("mcp_proposal_review_state", repo_path=repo_path,
                 proposal_id=proposal_id, review_state=state)
    return f"{proposal_id} review state set to {state}"


@mcp.tool()
def proposal_feedback_add(repo: str, proposal_id: str, body: str,
                          topic_id: str = "", kind: str = "review_note") -> str:
    """Open a feedback thread on a proposal run — the channel a regenerate
    reads to redraft, and reviewers read in the web UI.

    Args:
        repo: Registered repo name or absolute repo path.
        proposal_id: A run id from `proposal_list`.
        body: The feedback text (required).
        topic_id: Optional proposed-topic id to anchor the thread to; empty
            leaves it run-level.
        kind: Thread kind (default `review_note`).

    Returns:
        A confirmation line with the new thread id, or a one-line failure.
    """
    from lib.topics.proposals import create_proposal_feedback_thread
    repo_path, err = _resolve_repo(repo)
    if err:
        return err
    try:
        thread = create_proposal_feedback_thread(
            repo_path, proposal_id,
            proposal_topic_id=topic_id or None,
            kind=kind or "review_note",
            body=body,
            created_by="agent",
        )
    except _domain_errors() as e:
        return str(e)
    _log().write("mcp_proposal_feedback_add", repo_path=repo_path,
                 proposal_id=proposal_id, feedback_thread_id=thread.get("id"),
                 proposal_topic_id=topic_id or None, kind=kind or "review_note")
    anchored = f" on topic {topic_id}" if topic_id else ""
    return f"feedback thread #{thread.get('id')} added to {proposal_id}{anchored}"


@mcp.tool()
def proposal_feedback_list(repo: str, proposal_id: str) -> str:
    """List every feedback thread on a proposal run (open and resolved).

    Args:
        repo: Registered repo name or absolute repo path.
        proposal_id: A run id from `proposal_list`.

    Returns:
        One line per thread: id, kind, resolution state, anchored topic,
        comment count, and the opening comment's first line — or a note
        that the run has no feedback.
    """
    from lib.topics.proposals import (
        list_proposal_feedback_threads, load_proposal_status,
    )
    repo_path, err = _resolve_repo(repo)
    if err:
        return err
    try:
        load_proposal_status(repo_path, proposal_id)  # existence check
        threads = list_proposal_feedback_threads(repo_path, proposal_id)
    except _domain_errors() as e:
        return str(e)
    _log().read("mcp_proposal_feedback_list", repo_path=repo_path,
                proposal_id=proposal_id, count=len(threads))
    if not threads:
        return f"no feedback threads on {proposal_id}"
    return "\n".join(_thread_line(t) for t in threads)


if __name__ == "__main__":
    mcp.run()
