"""Shared helpers for topics endpoints."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.topics import TopicGraphError, bootstrap, topic_detail, topic_path, topic_summary
from lib.topics.proposal_providers import list_proposal_providers
from lib.topics.proposals import (
    list_proposal_feedback_threads, list_proposal_revisions, list_proposal_runs,
    load_proposal, load_proposal_revision, load_proposal_status,
)


def _repo_path_or_404(name: str):
    with SessionLocal() as session:
        repo = session.exec(select(Repo).where(Repo.name == name)).first()
        if repo is None:
            return None
        return repo.path


def _error(exc: Exception, status: int = 400):
    return jsonify({"ok": False, "error": str(exc)}), status


def _ensure_topic_graph(repo_path: str):
    if not topic_path(repo_path).exists():
        bootstrap(repo_path)


def _proposal_wiki(repo_path: str, proposal_id: str) -> str:
    wiki_path = topic_path(repo_path).parent / "proposals" / proposal_id / "wiki.md"
    return wiki_path.read_text() if wiki_path.exists() else ""


def _proposal_provider_from_payload(proposal: dict) -> str:
    return proposal.get("provider") or proposal.get("metadata", {}).get("provider") or "unknown"


_TITLE_MAX_LEN = 80


def _title_from_topic_request(topic_request: str) -> str:
    """Condense a raw user prompt into a short run title.

    The prompt can be a multi-line paragraph; the title is meant to be a
    one-line label for the list row and detail header. Take the first
    non-empty line and cap it. The full prompt stays in the row's
    `topic_request` field for tooltips / full display.
    """
    first_line = next((ln.strip() for ln in topic_request.splitlines() if ln.strip()), "")
    if len(first_line) <= _TITLE_MAX_LEN:
        return first_line
    return first_line[: _TITLE_MAX_LEN - 1].rstrip() + "…"


def _proposal_complexity_from_payload(proposal: dict) -> str:
    metadata = proposal.get("metadata") or {}
    return metadata.get("requested_complexity") or metadata.get("complexity") or "standard"


def _proposal_review_state_from_payload(proposal: dict | None) -> str:
    if not proposal:
        return "draft"
    return proposal.get("status") or proposal.get("metadata", {}).get("proposal_status") or "draft"


def _proposal_run_row(repo_path: str, run: dict) -> dict:
    row = dict(run)
    proposal = None
    if row.get("has_topics"):
        try:
            proposal = load_proposal(repo_path, row["id"])
        except OSError:
            proposal = None
    topics = proposal.get("topics", []) if proposal else []
    reviewed_count = sum(1 for topic in topics if topic.get("review_status"))
    # Infer provider for the row: the proposal's stored provider when
    # topics.json is on disk; otherwise fall back to "external-agent"
    # when status.json names an agent (the external-agent flow writes
    # status.json before the agent finishes drafting topics.json), else
    # "unknown".
    if proposal:
        provider = _proposal_provider_from_payload(proposal)
    elif row.get("agent"):
        provider = "external-agent"
    else:
        provider = "unknown"
    topic_request = ((proposal or {}).get("topic_request") or "").strip() if proposal else ""
    if topic_request:
        title = _title_from_topic_request(topic_request)
    elif topics:
        first_label = (topics[0].get("label") or topics[0].get("id") or "").strip()
        if len(topics) == 1 or not first_label:
            title = first_label or None
        else:
            title = f"{first_label} + {len(topics) - 1} more"
    else:
        title = None
    row.update({
        "provider": provider,
        "complexity": _proposal_complexity_from_payload(proposal or {}),
        "review_state": _proposal_review_state_from_payload(proposal),
        "revision_count": row.get("revision_count") or (proposal.get("revision", {}).get("revision_number") if proposal else 0) or 0,
        "latest_revision_number": row.get("latest_revision_number") or (proposal.get("revision", {}).get("revision_number") if proposal else None),
        "draft_topic_count": len(topics),
        "reviewed_count": reviewed_count,
        "pending_count": len(topics) - reviewed_count,
        "topic_request": topic_request or None,
        "title": title,
        "updated_at": Path(row["path"]).stat().st_mtime if row.get("path") and Path(row["path"]).exists() else None,
    })
    return row


def _proposal_topic_row(topic: dict, *, feedback_thread_count: int = 0) -> dict:
    return {
        "id": topic.get("id"),
        "label": topic.get("label") or topic.get("id"),
        "review_status": topic.get("review_status") or "pending",
        "evidence_count": len(topic.get("evidence_paths", [])),
        "proposed_ref_count": len(topic.get("refs", [])),
        "feedback_thread_count": feedback_thread_count,
        "target_topic_hint": topic.get("accepted_topic") or topic.get("merged_topic"),
        "intent_preview": topic.get("intent", ""),
    }


def _wiki_workspace_payload(repo_path: str, selected_topic_id: str | None) -> dict:
    summary = topic_summary(repo_path)
    topic_rows = []
    for topic in summary["topics"]:
        topic_rows.append({
            **topic,
            "broken_ref_count": len(topic.get("broken_refs", [])),
            "intent_preview": topic.get("intent", ""),
        })

    valid_ids = {row["id"] for row in topic_rows}
    # If the URL points at a topic that was deleted after the user
    # last bookmarked it, fall through to the first remaining topic
    # rather than 500'ing the whole workspace.
    if selected_topic_id and selected_topic_id not in valid_ids:
        selected_topic_id = None
    if not selected_topic_id and topic_rows:
        selected_topic_id = topic_rows[0]["id"]
    try:
        selected_topic = topic_detail(repo_path, selected_topic_id) if selected_topic_id else None
    except TopicGraphError:
        selected_topic = None

    return {
        "repo": Path(repo_path).name,
        "table": topic_rows,
        "selected_topic_id": selected_topic_id,
        "selected_topic": selected_topic,
        "validation": summary["validation"],
    }


def _proposal_workspace_payload(
    repo_path: str,
    *,
    selected_proposal_id: str | None,
    selected_draft_topic_id: str | None,
    selected_revision_id: str | None,
) -> dict:
    runs = [_proposal_run_row(repo_path, run) for run in list_proposal_runs(repo_path)]
    if not selected_proposal_id and runs:
        preferred_run = next(
            (
                run for run in runs
                if run.get("provider") != "approved-topic-downgrade"
            ),
            None,
        )
        selected_proposal_id = (preferred_run or runs[0])["id"]

    selected_run = next((run for run in runs if run["id"] == selected_proposal_id), None)
    proposal = None
    status = selected_run or None
    wiki = ""
    draft_topic_rows: list[dict] = []
    selected_draft_topic = None
    feedback_threads: list[dict] = []
    revisions: list[dict] = []
    selected_revision = None

    if selected_run and (selected_run.get("has_topics") or selected_run.get("has_wiki")):
        try:
            revisions = list_proposal_revisions(repo_path, selected_run["id"])
            if selected_revision_id:
                try:
                    selected_revision_int = int(selected_revision_id)
                except ValueError:
                    selected_revision_int = None
            else:
                selected_revision_int = None
            if selected_revision_int is not None:
                try:
                    proposal = load_proposal_revision(repo_path, selected_run["id"], selected_revision_int)
                except TopicGraphError:
                    proposal = load_proposal(repo_path, selected_run["id"]) if selected_run.get("has_topics") else None
            else:
                proposal = load_proposal(repo_path, selected_run["id"]) if selected_run.get("has_topics") else None
            status = load_proposal_status(repo_path, selected_run["id"])
            feedback_threads = list_proposal_feedback_threads(
                repo_path,
                selected_run["id"],
                revision_id=proposal.get("revision", {}).get("id") if proposal else None,
            )
        except (OSError, TopicGraphError):
            proposal = None
            selected_revision_int = None
        if proposal:
            selected_revision = proposal.get("revision")
            if selected_revision is None and revisions:
                selected_revision = revisions[0]
            wiki = proposal.get("wiki") or _proposal_wiki(repo_path, selected_run["id"])
        topics = proposal.get("topics", []) if proposal else []
        # The `conflicts_with_approved` flag is gone — the DiffPanel
        # consults `/diff`'s `valid_strategies_by_topic` for that signal.
        topic_feedback_counts: dict[str, int] = {}
        for feedback_thread in feedback_threads:
            topic_id = feedback_thread.get("proposal_topic_id")
            if isinstance(topic_id, str) and topic_id:
                topic_feedback_counts[topic_id] = topic_feedback_counts.get(topic_id, 0) + 1
        draft_topic_rows = [
            _proposal_topic_row(topic, feedback_thread_count=topic_feedback_counts.get(topic.get("id"), 0))
            for topic in topics
        ]
        if not selected_draft_topic_id and draft_topic_rows:
            selected_draft_topic_id = draft_topic_rows[0]["id"]
        selected_draft_topic = next((topic for topic in topics if topic.get("id") == selected_draft_topic_id), None)

    from lib.prompt_templates import list_templates

    return {
        "repo": Path(repo_path).name,
        "providers": list_proposal_providers(),
        "prompt_templates": list_templates(),
        "runs": runs,
        "selected_proposal_id": selected_proposal_id,
        "selected_run": selected_run,
        "selected_status": status,
        "draft_topics": draft_topic_rows,
        "selected_draft_topic_id": selected_draft_topic_id,
        "selected_draft_topic": selected_draft_topic,
        "proposal": proposal,
        "wiki_preview": wiki,
        "revisions": revisions,
        "selected_revision_id": selected_revision.get("id") if selected_revision else None,
        "selected_revision": selected_revision,
        "feedback_threads": feedback_threads,
        "feedback_summary": {
            "thread_count": len(feedback_threads),
            "open_thread_count": sum(1 for thread in feedback_threads if thread.get("resolution_state") == "open"),
            "selected_topic_thread_count": sum(
                1
                for thread in feedback_threads
                if selected_draft_topic_id and thread.get("proposal_topic_id") == selected_draft_topic_id
            ),
        },
    }


def _workspace_summary_payload(repo_path: str) -> dict:
    summary = topic_summary(repo_path)
    runs = [_proposal_run_row(repo_path, run) for run in list_proposal_runs(repo_path)]
    broken_ref_count = sum(len(topic.get("broken_refs", [])) for topic in summary["topics"])
    return {
        "repo": Path(repo_path).name,
        "approved_topic_count": len(summary["topics"]),
        "proposal_run_count": len(runs),
        "active_proposal_count": sum(
            1 for run in runs if run.get("state") in {"queued", "running", "waiting_for_permission"}
        ),
        "broken_ref_count": broken_ref_count,
    }
