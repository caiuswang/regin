"""Diff-scoped topic-wiki freshness audit ("wiki debt").

Answers, for the files a change touched: which approved topics now need wiki
attention — either they have no per-topic wiki yet (`missing`) or a ref file
they cover materially drifted from its stored digest (`drifted`).

This is the *detection* half of the goal-verified close-out wiki check: the
agent that just changed code runs it scoped to its own diff (`changed_since`),
then decides whether to queue a draft/refresh proposal for the human to
approve. It mutates nothing, takes no gate (reporting is always safe), and
never raises — best-effort like the rest of `lib/topics`.
"""

from __future__ import annotations

from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.topics import slugify
from lib.topics.content_drift import detect_drifted_topics, emit_refresh_proposal
from lib.topics.drift import _git
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.wiki import wiki_dir

log = get_activity_logger("topics")


def _notify_drift(repo_path, row: dict[str, Any],
                  session_trace_id: str | None = None) -> None:
    """Surface a detected content-drift as an inbox event deep-linked to the
    repo's Topics view, where the queued refresh proposal is reviewed /
    regenerated. Keyed per repo+topic with `once` so a still-open drift card
    isn't re-surfaced on every audit; the card is cleared by
    `content_drift.resolve_drift_card` when the drift is applied or dismissed.

    The card's `trace_id` is the *stable* synthetic `DRIFT_CARD_TRACE`, not the
    detecting session — the `once` dedup and `resolve_drift_card` dismissal are
    both keyed on it, so it must not vary per audit. Since that sentinel is not
    a navigable session (`events.NON_SESSION_TRACE_IDS` suppresses its footer
    link), the real detecting session — when known — is surfaced as an explicit
    "Detected in session" action link instead, so the card still links back to
    the run that raised it. `events.emit` is best-effort and gated by the
    `content.drift` kind."""
    from lib.agent_messages import events
    from lib.topics.content_drift import DRIFT_CARD_TRACE, drift_card_key
    paths = ", ".join(row.get("drifted_paths") or []) or "its covered files"
    queued = (" A refresh proposal is queued;" if row.get("proposal_id")
              else "")
    links = [{"label": "Review in Topics",
              "href": events.topics_url(repo_path)}]
    if session_trace_id:
        links.append({"label": "Detected in session",
                      "href": events.session_url(session_trace_id)})
    events.emit(
        "content.drift", trace_id=DRIFT_CARD_TRACE,
        title=f"Wiki drift: {row['topic_id']}",
        body=(f"Topic **{row['topic_id']}** drifted from its code — {paths} "
              f"changed since the wiki was digested.{queued} review or "
              f"regenerate it in the Topics view."),
        key=drift_card_key(repo_path, row["topic_id"]),
        links=links, once=True)


def _changed_files(repo_path, changed_since: str) -> Optional[set[str]]:
    """Repo-relative paths changed between `changed_since` and HEAD. None when
    git couldn't run, so the caller can tell "no changes" from "can't scope"."""
    lines = _git(repo_path, ["diff", "--name-only", changed_since, "HEAD"])
    return None if lines is None else set(lines)


def _topic_ref_paths(topic: dict[str, Any]) -> list[str]:
    return [ref.get("path") for ref in topic.get("refs", [])
            if isinstance(ref, dict) and ref.get("path")]


def _narrow(paths: list[str], changed: Optional[set]) -> list[str]:
    """Paths kept that fall inside the diff; all of them when unscoped."""
    return paths if changed is None else [p for p in paths if p in changed]


def _topic_debt(topic_id: str, topic: dict[str, Any], *, changed: Optional[set],
                drifted_all: list[str], wiki_root) -> Optional[dict[str, Any]]:
    """One topic's debt row, or None when it is out of scope or healthy.
    `missing` (no wiki yet) beats `drifted` (a covered ref changed since the
    wiki was digested)."""
    changed_refs = _narrow(_topic_ref_paths(topic), changed)
    if changed is not None and not changed_refs:
        return None  # untouched by this diff — out of scope
    drifted = _narrow(drifted_all, changed)
    if not (wiki_root / f"{slugify(topic_id)}.md").exists():
        status = "missing"
    elif drifted:
        status = "drifted"
    else:
        return None  # healthy — nothing to report
    return {"topic_id": topic_id, "status": status,
            "drifted_paths": drifted, "changed_refs": changed_refs}


def wiki_debt(repo_path, *,
              changed_since: Optional[str] = None) -> list[dict[str, Any]]:
    """Approved topics that need wiki attention, optionally scoped to a diff.

    Returns `[{topic_id, status, drifted_paths, changed_refs}]` where `status`
    is `"missing"` or `"drifted"`; healthy topics are omitted. With
    `changed_since` (a git ref), only topics owning at least one file changed
    between that ref and HEAD are considered, and `drifted_paths` is likewise
    narrowed to the diff; without it every topic is audited repo-wide. Never
    raises — returns `[]` on any failure."""
    try:
        graph = load_authoritative_graph(repo_path)
        wiki_root = wiki_dir(repo_path)
        drift_map = {item["topic_id"]: item["drifted_paths"]
                     for item in detect_drifted_topics(repo_path)}
        changed = (None if changed_since is None
                   else (_changed_files(repo_path, changed_since) or set()))

        out: list[dict[str, Any]] = []
        for topic_id, topic in graph.get("topics", {}).items():
            row = _topic_debt(topic_id, topic, changed=changed,
                              drifted_all=drift_map.get(topic_id, []),
                              wiki_root=wiki_root)
            if row is not None:
                out.append(row)
        return out
    except Exception:  # noqa: BLE001 - audit must never break the caller
        log.error("wiki_debt_failed", exc_info=True)
        return []


def emit_wiki_debt_proposals(repo_path, *,
                             changed_since: Optional[str] = None,
                             session_trace_id: Optional[str] = None
                             ) -> list[dict[str, Any]]:
    """Detect wiki debt, then emit a fast, agent-free *stub* refresh proposal
    for each `drifted` topic (an idempotent DB write via `emit_refresh_proposal`
    — no agent, no gate). `missing` topics stay report-only: there is no
    agent-free way to author a brand-new wiki, so drafting those remains a
    human/server action.

    `session_trace_id` is the Claude Code session running this emit (the CLI
    passes `$CLAUDE_CODE_SESSION_ID`); when set, each drift card links back to
    it as its "Detected in session" run.

    Returns the debt rows (same shape as `wiki_debt`), each annotated with a
    `proposal_id` — the stub id for an emitted `drifted` row, else None. Never
    raises; a per-topic emit failure leaves that row's `proposal_id` None."""
    rows = wiki_debt(repo_path, changed_since=changed_since)
    for row in rows:
        proposal_id = None
        if row["status"] == "drifted":
            try:
                proposal_id = emit_refresh_proposal(
                    repo_path, row["topic_id"], row["drifted_paths"])
            except Exception:  # noqa: BLE001 - one emit must not sink the batch
                log.error("wiki_debt_emit_failed",
                          topic_id=row["topic_id"], exc_info=True)
            row["proposal_id"] = proposal_id
            _notify_drift(repo_path, row, session_trace_id)
        else:
            row["proposal_id"] = proposal_id
    return rows


__all__ = ["wiki_debt", "emit_wiki_debt_proposals"]
