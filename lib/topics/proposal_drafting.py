"""Drafting and validation helpers for topic proposals.

Pure functions that produce reviewable proposal artifacts. The
orchestration layer (`lib.topics.proposals`) calls these to build,
regenerate, and edit proposal runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.topics import TopicGraphError


PROPOSAL_VERSION = 1


class ProposalValidationError(ValueError):
    """Invalid agent-produced proposal output.

    Keeps the individual error strings on `.errors` so ingest paths can
    persist them machine-readably alongside the joined human message.
    """

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def _proposal_prior_draft(proposal_path: Path, wiki_path: Path) -> dict[str, Any]:
    return {
        "proposal": json.loads(proposal_path.read_text()),
        "wiki": wiki_path.read_text() if wiki_path.exists() else "",
    }


_ANCHOR_LABELS = {
    "proposal_summary": "proposal summary",
    "wiki_range": "wiki content",
    "general": "general review",
}


def _thread_header(thread: dict[str, Any]) -> str:
    parts: list[str] = []
    topic_id = thread.get("proposal_topic_id")
    if isinstance(topic_id, str) and topic_id:
        parts.append(f"topic `{topic_id}`")
    anchor_kind = thread.get("anchor_kind")
    anchor = thread.get("anchor") or {}
    if anchor_kind == "topic_field" and anchor.get("field"):
        parts.append(f"field `{anchor['field']}`")
    elif anchor_kind in _ANCHOR_LABELS:
        parts.append(_ANCHOR_LABELS[anchor_kind])
    return ", ".join(parts) or "general review"


def _thread_comment_lines(thread: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for comment in thread.get("comments") or []:
        body = comment.get("body")
        if not isinstance(body, str) or not body.strip():
            continue
        author = comment.get("author_kind") or "reviewer"
        lines.append(f"   - {author}: {body.strip()}")
    return lines


def format_review_feedback_for_prompt(feedback_threads: list[dict[str, Any]] | None) -> str:
    if not feedback_threads:
        return ""
    lines = ["Review feedback to address in this revision:"]
    for index, thread in enumerate(feedback_threads, start=1):
        lines.append(f"{index}. {_thread_header(thread)}")
        quoted_text = thread.get("quoted_text")
        if isinstance(quoted_text, str) and quoted_text.strip():
            lines.append(f'   Quoted text: "{quoted_text.strip()}"')
        lines.extend(_thread_comment_lines(thread))
    return "\n".join(lines)


def _write_proposal_artifacts(
    out_dir: Path,
    *,
    proposals: dict[str, Any],
    wiki: str,
    repo_path: Path | None = None,
    proposal_id: str | None = None,
    append_revision: bool = False,
    revision_kind: str = "generated",
) -> dict[str, Path]:
    """Write runtime artefacts (wiki.md) + persist proposal state to ORM.

    Proposal state (topics list, scope, metadata) goes to the ORM via
    `orm_save_proposal`; nothing else is written to disk (Phase E2
    source-of-truth flip).
    """
    wiki_path = out_dir / "wiki.md"
    wiki_body = wiki.strip()
    if not wiki_body:
        raise TopicGraphError("proposal provider returned an empty wiki")
    wiki_path.write_text(wiki_body)
    if repo_path is not None and proposal_id is not None:
        from lib.topics.proposal_orm import orm_save_proposal
        orm_save_proposal(
            repo_path,
            proposal_id,
            proposals,
            wiki=wiki_body,
            append_revision=append_revision,
            revision_kind=revision_kind,
        )
    return {"dir": out_dir, "wiki": wiki_path}


def _draft_proposal(
    *,
    repo: Path,
    out_dir: Path,
    proposal_id: str,
    topic_request: str | None = None,
    agent: str | None = None,
    prior_draft: dict[str, Any] | None = None,
    prompt_templates: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """Run the external tool-using agent and return (proposal, wiki).

    The agent explores the repo with its own Read/Glob/Grep tools — there
    is no pre-built evidence pack. This is the only drafting path.
    """
    from lib.topics.proposal_external import run_external_agent_proposal

    proposals, wiki = run_external_agent_proposal(
        repo=repo,
        out_dir=out_dir,
        proposal_id=proposal_id,
        topic_request=topic_request,
        agent_id=agent,
        prior_draft=prior_draft,
        prompt_templates=prompt_templates,
    )
    if topic_request:
        proposals["topic_request"] = topic_request
    errors = validate_proposal(proposals)
    if errors:
        raise TopicGraphError("; ".join(errors))
    return proposals, wiki


_TOPIC_LIST_FIELDS = (
    "aliases", "refs", "edges", "commands",
    "include_globs", "exclude_globs", "evidence_paths",
)


def _validate_ref_entry(ref: Any, prefix: str) -> list[str]:
    if not isinstance(ref, dict):
        return [f"{prefix} must be an object with a string `path`"]
    errors: list[str] = []
    if not isinstance(ref.get("path"), str):
        errors.append(f"{prefix}.path must be a string")
    for field in ("role", "tier"):
        value = ref.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{prefix}.{field} must be a string when present")
    return errors


def _validate_topic_id(topic: dict[str, Any], prefix: str, seen: set[str]) -> list[str]:
    topic_id = topic.get("id")
    if not topic_id:
        return [f"{prefix}.id is required"]
    if topic_id in seen:
        return [f"duplicate proposed topic id: {topic_id}"]
    seen.add(topic_id)
    return []


def _validate_topic_fields(topic: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    for field in ("label", "intent", "status"):
        if field not in topic:
            errors.append(f"{prefix}.{field} is required")
    for field in ("parent_id", "blurb"):
        value = topic.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{prefix}.{field} must be a string when present")
    for field in _TOPIC_LIST_FIELDS:
        if not isinstance(topic.get(field, []), list):
            errors.append(f"{prefix}.{field} must be a list")
    refs = topic.get("refs", [])
    if isinstance(refs, list):
        for index, ref in enumerate(refs):
            errors.extend(_validate_ref_entry(ref, f"{prefix}.refs[{index}]"))
    return errors


def _proposal_has_wiki(proposal: dict[str, Any]) -> bool:
    """Mirror `_combined_proposal_wiki`: non-empty iff any per-topic `wiki`
    page is non-blank, falling back to the legacy top-level `wiki` string."""
    for topic in proposal.get("topics") or []:
        if isinstance(topic, dict) and str(topic.get("wiki") or "").strip():
            return True
    return bool(str(proposal.get("wiki") or "").strip())


def validate_proposal(proposal: dict[str, Any], *, require_wiki: bool = False) -> list[str]:
    """Single validation layer for proposal payloads.

    `require_wiki` is opt-in for the agent-output ingest paths; review-time
    callers (accept/merge/update/downgrade) validate structure only, so a
    wiki-less draft they already handle stays editable.
    """
    errors: list[str] = []
    if proposal.get("version") != PROPOSAL_VERSION:
        errors.append("proposal version must be 1")
    if not isinstance(proposal.get("topics"), list):
        errors.append("proposal topics must be a list")
        return errors
    seen: set[str] = set()
    for index, topic in enumerate(proposal["topics"]):
        prefix = f"topics[{index}]"
        if not isinstance(topic, dict):
            errors.append(f"{prefix} must be an object")
            continue
        errors.extend(_validate_topic_id(topic, prefix, seen))
        errors.extend(_validate_topic_fields(topic, prefix))
    if require_wiki and not _proposal_has_wiki(proposal):
        errors.append(
            "proposal has an empty wiki: add a non-blank per-topic `wiki` page "
            "(topics[].wiki) or a top-level `wiki` string"
        )
    return errors
