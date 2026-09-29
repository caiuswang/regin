"""`regin topics proposal-*`: list, show, wiki, diff, apply, review state,
feedback threads, export and import."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from lib.topics import TopicGraphError

from ._common import _repo_path, topics_app


def _proposal_run_row(rp: Path, run: dict) -> dict:
    """One `proposal-list` row: run state + review state + topic count.

    A run with no draft yet (queued / failed before output) has no
    loadable proposal — report it with review '-' and zero topics
    rather than erroring the whole listing."""
    from lib.topics.proposals import (
        load_proposal, load_proposal_status, proposal_review_state,
    )
    review_state, topic_count = "-", 0
    try:
        proposal = load_proposal(rp, run["id"])
        review_state = proposal_review_state(proposal)
        topic_count = len(proposal.get("topics") or [])
    except (OSError, ValueError, TopicGraphError):
        pass
    try:
        status = load_proposal_status(rp, run["id"])
    except TopicGraphError:
        status = {}
    return {
        "id": run["id"],
        "state": run.get("state") or "unknown",
        "review_state": review_state,
        "topic_count": topic_count,
        "created_at": status.get("started_at") or run.get("last_activity_at"),
        "completed_at": status.get("completed_at"),
    }


def _print_proposal_run_table(rows: list[dict]) -> None:
    if not rows:
        print("No proposal runs.")
        return
    print(f"{'id':<18} {'state':<12} {'review':<18} {'topics':>6}  created / completed")
    for r in rows:
        stamps = f"{r['created_at'] or '-'} / {r['completed_at'] or '-'}"
        print(f"{r['id']:<18} {r['state']:<12} {r['review_state']:<18} "
              f"{r['topic_count']:>6}  {stamps}")


@topics_app.command(
    "proposal-list",
    help="List proposal runs (id, run state, review state, topic count)")
def cmd_topics_proposal_list(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    state: str | None = typer.Option(
        None, "--state", help="Filter by run state (e.g. completed, failed)"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import list_proposal_runs

    rp = _repo_path(repo)
    runs = list_proposal_runs(rp)
    if state:
        runs = [r for r in runs if r.get("state") == state]
    rows = [_proposal_run_row(rp, r) for r in runs]
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    _print_proposal_run_table(rows)


def _load_proposal_or_none(rp: Path, proposal_id: str) -> dict | None:
    from lib.topics.proposals import load_proposal
    try:
        return load_proposal(rp, proposal_id)
    except (OSError, ValueError, TopicGraphError):
        return None


def _proposal_topic_rows(topics: list) -> list[dict]:
    return [{
        "id": t.get("id"),
        "label": t.get("label") or t.get("id"),
        "review_status": t.get("review_status") or "pending",
    } for t in topics if isinstance(t, dict)]


def _thread_snippet(thread: dict) -> str:
    comments = thread.get("comments") or []
    body = str((comments[0] or {}).get("body") or "") if comments else ""
    lines = body.strip().splitlines()
    return lines[0] if lines else ""


def _feedback_thread_rows(threads: list[dict], open_only: bool = False) -> list[dict]:
    return [{
        "id": t.get("id"),
        "kind": t.get("kind"),
        "proposal_topic_id": t.get("proposal_topic_id"),
        "resolution_state": t.get("resolution_state"),
        "snippet": _thread_snippet(t),
    } for t in threads if not (open_only and t.get("resolution_state") != "open")]


def _print_proposal_show(
    proposal_id: str, status: dict, review_state: str | None,
    topic_rows: list[dict], thread_rows: list[dict],
) -> None:
    print(f"Proposal {proposal_id}")
    print(f"  run state: {status.get('state') or 'unknown'}")
    print(f"  review state: {review_state or '-'}")
    if status.get("agent"):
        print(f"  agent: {status['agent']}")
    if status.get("error"):
        print(f"  error: {status['error']}")
    print(f"  topics ({len(topic_rows)}):")
    for row in topic_rows:
        print(f"    {row['id']}  [{row['review_status']}]  {row['label']}")
    print(f"  open feedback threads ({len(thread_rows)}):")
    for t in thread_rows:
        print(f"    #{t['id']}  {t['kind']}  {t['resolution_state']}  {t['snippet']}")


@topics_app.command(
    "proposal-show",
    help="Show one proposal run: status, review state, topics, open feedback")
def cmd_topics_proposal_show(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import (
        list_proposal_feedback_threads, load_proposal_status,
        proposal_review_state,
    )

    rp = _repo_path(repo)
    try:
        status = load_proposal_status(rp, proposal_id)
    except TopicGraphError as exc:
        print(f"Proposal show failed: {exc}")
        raise typer.Exit(1)
    proposal = _load_proposal_or_none(rp, proposal_id)
    review_state = proposal_review_state(proposal) if proposal else None
    topic_rows = _proposal_topic_rows((proposal or {}).get("topics") or [])
    thread_rows = _feedback_thread_rows(
        list_proposal_feedback_threads(rp, proposal_id), open_only=True,
    )
    if as_json:
        print(json.dumps({
            "id": proposal_id,
            "status": status,
            "review_state": review_state,
            "topics": topic_rows,
            "open_feedback_threads": thread_rows,
        }, indent=2))
        return
    _print_proposal_show(proposal_id, status, review_state, topic_rows, thread_rows)


@topics_app.command(
    "proposal-wiki",
    help="Print a proposed topic's wiki page (the review agent pulls pages "
         "with this instead of hunting for a draft file on disk)")
def cmd_topics_proposal_wiki(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    topic: str | None = typer.Option(
        None, "--topic", help="Topic id; omit to list the ids in the draft"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    revision: int | None = typer.Option(
        None, "--revision", help="Revision id (default: the latest)"),
) -> None:
    rp = _repo_path(repo)
    proposal = _load_proposal_revision_or_none(rp, proposal_id, revision)
    if proposal is None:
        print(f"Proposal wiki failed: no such proposal run: {proposal_id}")
        raise typer.Exit(1)
    topics = proposal.get("topics") or []
    if not topic:
        for entry in topics:
            print(entry.get("id", "?"))
        return
    for entry in topics:
        if entry.get("id") == topic:
            print((entry.get("wiki") or "").strip())
            return
    known = ", ".join(t.get("id", "?") for t in topics) or "(none)"
    print(f"Proposal wiki failed: no topic {topic!r} in this draft. Known ids: {known}")
    raise typer.Exit(1)


def _load_proposal_revision_or_none(
    rp: Path, proposal_id: str, revision: int | None,
) -> dict | None:
    if revision is None:
        return _load_proposal_or_none(rp, proposal_id)
    from lib.topics.proposals import load_proposal_revision
    try:
        return load_proposal_revision(rp, proposal_id, revision)
    except (OSError, ValueError, TopicGraphError):
        return None


def _print_topic_delta_line(delta: dict) -> None:
    changes = []
    for key, label in (("ref_adds", "+ref"), ("ref_removes", "-ref"),
                       ("alias_adds", "+alias"), ("alias_removes", "-alias"),
                       ("edge_adds", "+edge"), ("edge_removes", "-edge"),
                       ("scalar_changes", "field")):
        n = len(delta.get(key) or [])
        if n:
            changes.append(f"{n} {label}")
    detail = ", ".join(changes) if changes else "no field-level changes"
    print(f"  {delta.get('kind', '?'):<8} {delta.get('topic_id')}  ({detail})")


def _print_diff_summary(diff: dict) -> None:
    target = f" → {diff['target_topic_id']}" if diff.get("target_topic_id") else ""
    print(f"strategy: {diff.get('strategy')}{target}")
    for delta in diff.get("topic_deltas") or []:
        _print_topic_delta_line(delta)
    errors = diff.get("introduced_errors") or []
    warnings = diff.get("graph_warnings") or []
    print(f"introduced errors: {len(errors)}, graph warnings: {len(warnings)}")
    for issue in errors:
        print(f"  error: {issue.get('message')}")
    print(f"applyable: {'yes' if diff.get('is_applyable') else 'NO'}")


@topics_app.command(
    "proposal-diff",
    help="Preview what applying a proposed topic would change (no writes)")
def cmd_topics_proposal_diff(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    topic_id: str = typer.Argument(..., help="Proposed topic id inside the run"),
    strategy: str = typer.Option(
        "create", "--strategy", help="create | replace | merge"),
    target: str | None = typer.Option(
        None, "--target", help="Approved topic id (merge/replace target)"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import diff_proposal_topic

    try:
        result = diff_proposal_topic(
            str(_repo_path(repo)), proposal_id, topic_id,
            strategy=strategy, target_topic_id=target,
        )
    except (LookupError, ValueError, TopicGraphError) as exc:
        print(f"Diff failed: {exc}")
        raise typer.Exit(1)
    if as_json:
        print(json.dumps(result, indent=2))
        return
    _print_diff_summary(result["diff"])


@topics_app.command(
    "proposal-apply",
    help="Apply a proposed topic to the approved graph (writes a snapshot)")
def cmd_topics_proposal_apply(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    topic_id: str = typer.Argument(..., help="Proposed topic id inside the run"),
    strategy: str = typer.Option(
        "create", "--strategy", help="create | replace | merge"),
    target: str | None = typer.Option(
        None, "--target", help="Approved topic id (merge/replace target)"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import apply_proposal_topic

    try:
        result = apply_proposal_topic(
            str(_repo_path(repo)), proposal_id, topic_id,
            strategy=strategy, target_topic_id=target,
        )
    except (LookupError, ValueError, TopicGraphError) as exc:
        print(f"Apply failed: {exc}")
        raise typer.Exit(1)
    if as_json:
        print(json.dumps(result, indent=2))
        if not result.get("ok"):
            raise typer.Exit(1)
        return
    if not result.get("ok"):
        print("Apply blocked: unresolvable errors")
        for issue in (result.get("diff") or {}).get("introduced_errors") or []:
            print(f"  error: {issue.get('message')}")
        raise typer.Exit(1)
    if result.get("already_applied"):
        print(f"{topic_id}: already applied (snapshot {result['snapshot_id']})")
        return
    print(f"Applied {topic_id} from {proposal_id} (snapshot {result['snapshot_id']})")


# The user-settable subset — draft/partially_applied/applied are derived
# states the apply path owns; mirrors the web review-state endpoint.
_CLI_REVIEW_STATES = ("pending_review", "changes_requested", "ready_to_apply")


@topics_app.command(
    "proposal-review-state",
    help="Set a proposal run's review state "
         "(pending_review | changes_requested | ready_to_apply)")
def cmd_topics_proposal_review_state(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    state: str = typer.Argument(..., help="New review state"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposals import set_proposal_review_state

    if state not in _CLI_REVIEW_STATES:
        print(f"Review state failed: state must be one of {list(_CLI_REVIEW_STATES)}")
        raise typer.Exit(1)
    try:
        set_proposal_review_state(_repo_path(repo), proposal_id, state)
    except TopicGraphError as exc:
        print(f"Review state failed: {exc}")
        raise typer.Exit(1)
    print(f"{proposal_id}: review state set to {state}")


def _print_feedback_threads(rp: Path, proposal_id: str, as_json: bool) -> None:
    from lib.topics.proposals import list_proposal_feedback_threads

    threads = list_proposal_feedback_threads(rp, proposal_id)
    if as_json:
        print(json.dumps(threads, indent=2))
        return
    if not threads:
        print(f"No feedback threads on {proposal_id}.")
        return
    for row in _feedback_thread_rows(threads):
        anchored = f" [{row['proposal_topic_id']}]" if row["proposal_topic_id"] else ""
        print(f"#{row['id']}  {row['kind']}{anchored}  {row['resolution_state']}  {row['snippet']}")


def _create_feedback_thread(
    rp: Path, proposal_id: str, *, body: str, topic: str | None,
    kind: str, as_json: bool,
) -> None:
    from lib.topics.proposals import create_proposal_feedback_thread

    try:
        thread = create_proposal_feedback_thread(
            rp, proposal_id,
            proposal_topic_id=topic,
            kind=kind,
            # The web files a whole-topic comment as proposal_summary; match
            # it so the auto-addressed sweep treats CLI feedback the same.
            anchor_kind="proposal_summary" if topic else "general",
            body=body,
        )
    except TopicGraphError as exc:
        print(f"Feedback failed: {exc}")
        raise typer.Exit(1)
    if as_json:
        print(json.dumps(thread, indent=2))
        return
    where = f" on topic {topic}" if topic else ""
    print(f"Feedback thread #{thread['id']} created{where} ({proposal_id})")


@topics_app.command(
    "proposal-feedback",
    help="Add a feedback thread to a proposal run (--body), or list its "
         "threads (--list)")
def cmd_topics_proposal_feedback(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    body: str | None = typer.Option(None, "--body", help="Comment body for a new thread"),
    topic: str | None = typer.Option(
        None, "--topic", help="Anchor the thread to this proposed topic id"),
    kind: str = typer.Option("comment", "--kind", help="Thread kind (default: comment)"),
    list_threads: bool = typer.Option(
        False, "--list", help="List feedback threads instead of creating one"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    if list_threads == bool(body):
        raise typer.BadParameter(
            "pass --body to create a thread or --list to list (exactly one)")
    rp = _repo_path(repo)
    if list_threads:
        _print_feedback_threads(rp, proposal_id, as_json)
        return
    _create_feedback_thread(
        rp, proposal_id, body=body, topic=topic, kind=kind, as_json=as_json,
    )


@topics_app.command(
    "proposal-export",
    help="Export an in-flight proposal run to a portable bundle "
         "(commit the JSON to share it — no server needed)")
def cmd_topics_proposal_export(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    out: str | None = typer.Option(
        None, "--out",
        help="Output path (default: .regin/topics/bundles/<id>.json)"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    import subprocess

    from lib.topics.proposals import export_proposal_bundle

    rp = _repo_path(repo)
    try:
        path = export_proposal_bundle(rp, proposal_id, out_path=out)
    except TopicGraphError as exc:
        print(f"Proposal export failed: {exc}")
        raise typer.Exit(1)
    print(f"Exported proposal {proposal_id} to {path}")
    ignored = subprocess.run(
        ["git", "-C", str(rp), "check-ignore", "-q", str(path)],
        capture_output=True,
    ).returncode == 0
    if ignored:
        print("NOTE: this repo's .gitignore still ignores the bundle "
              f"(no re-include block to patch) — stage it with: git add -f {path}")
    print("Commit the bundle to share it; teammates run "
          "`regin topics proposal-import <path>`.")


@topics_app.command(
    "proposal-import",
    help="Import a teammate's proposal bundle into the local DB "
         "(review continues locally in the WebUI/CLI)")
def cmd_topics_proposal_import(
    bundle_path: str = typer.Argument(..., help="Path to a proposal bundle JSON"),
    force: bool = typer.Option(
        False, "--force",
        help="Replace an existing local run (+ revisions + feedback) wholesale"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposals import import_proposal_bundle

    try:
        result = import_proposal_bundle(_repo_path(repo), bundle_path, force=force)
    except TopicGraphError as exc:
        print(f"Proposal import failed: {exc}")
        raise typer.Exit(1)
    if result["action"] == "refused":
        print(f"Proposal import refused: {result['message']}")
        print("Re-run with --force to replace the local run wholesale.")
        raise typer.Exit(1)
    verb = "Replaced local" if result["action"] == "replaced" else "Imported"
    print(f"{verb} proposal {result['proposal_id']}: "
          f"{result['revisions']} revision(s), "
          f"{result['threads']} feedback thread(s)")
