"""Prompt assembly (and its spilled context files) for the external
topic-proposal drafting agent."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.proposal_drafting import format_review_feedback_for_prompt
from lib.topics.proposal_artifacts import (
    OUTPUT_FILE, PRIOR_PROPOSAL_FILE, TOPIC_MAP_FILE,
)

log = get_activity_logger("topics")


def _finish_command(repo: Path, proposal_id: str) -> str:
    """The exact shell command the drafting agent runs as its final step to
    signal completion. Built with the server's interpreter + regin CLI path
    and an explicit `--repo`, so it works regardless of the target repo or
    whether `regin` is on the agent's PATH."""
    cli = settings.project_root / "cli" / "regin.py"
    parts = [
        sys.executable, str(cli), "topics", "proposal-finish",
        proposal_id, "--repo", str(repo),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _rel_to_repo(repo: Path, path: Path) -> str:
    """Repo-root-relative string for a path under `repo`, else the raw path."""
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def _topic_position_entry(repo: Path, wdir: Path, jdir: Path,
                          tid: str, topic: dict[str, Any]) -> dict[str, Any]:
    """One `topics[]` entry for the boundary map: bucket + one-line `covers` +
    the on-disk `wiki_path` / `json_path` (only when the file exists)."""
    covers = (topic.get("blurb") or topic.get("intent") or "").strip()
    if len(covers) > 240:
        covers = covers[:237] + "..."
    entry: dict[str, Any] = {
        "id": tid,
        "label": topic.get("label"),
        "parent_id": topic.get("parent_id"),
        "covers": covers,
    }
    wiki_file = wdir / f"{tid}.md"
    if wiki_file.is_file():
        entry["wiki_path"] = _rel_to_repo(repo, wiki_file)
    json_file = jdir / f"{tid}.json"
    if json_file.is_file():
        entry["json_path"] = _rel_to_repo(repo, json_file)
    return entry


def _existing_topics_summary(repo: Path) -> dict[str, Any]:
    """Boundary map for the drafting agent — WHERE each approved topic lives, not
    its full text. Per topic: id/label, the bucket (`parent_id`), a one-line
    `covers`, and the on-disk `wiki_path` / `json_path` the agent Reads when it
    drafts something adjacent. Plus `primary_owners`: a file → the ONE topic that
    already claims it as a *primary* ref, so the agent tiers a file it doesn't
    own as `reference` instead of minting a second primary — the exact
    shared-primary-ref boundary the pre-review gate would otherwise bounce.
    Positions instead of inlined section dumps keeps the prompt small and lets
    the agent pull only the neighbours its topic actually touches. Best effort: a
    load failure yields empty maps.
    """
    from lib.topics.core import topic_split_dir
    from lib.topics.validation import _collect_primary_ref_owners
    from lib.topics.wiki import wiki_dir

    try:
        graph = load_authoritative_graph(repo)
    except Exception:
        return {"topics": [], "primary_owners": {}}
    wdir = wiki_dir(repo)
    jdir = topic_split_dir(repo)
    entries: list[dict[str, Any]] = []
    owners: dict[str, list[str]] = {}
    for tid, topic in sorted((graph.get("topics") or {}).items()):
        if not isinstance(topic, dict):
            continue
        entries.append(_topic_position_entry(repo, wdir, jdir, tid, topic))
        _collect_primary_ref_owners(tid, topic, owners)
    primary_owners = {
        path: tids[0] for path, tids in sorted(owners.items()) if len(tids) == 1
    }
    return {"topics": entries, "primary_owners": primary_owners}


def _existing_topics_pointer(repo: Path, out_dir: Path) -> str:
    """Spill the boundary map to `<out_dir>/topic-map.json` and return the
    absolute path the agent Reads, or a placeholder when nothing readable
    could be written.

    Embedded, this map was 84% of the drafting prompt (43k of 51k chars on
    regin's own graph) and grows with every approved topic — an unbounded
    share of the agent's context spent on data it can fetch. Same contract as
    ``wiki_read_pointer``: probe the file after writing, so a failed spill
    degrades to a placeholder instead of a dangling path. Absolute because the
    agent may run under a working directory other than the repo root.
    """
    summary = _existing_topics_summary(repo)
    path = out_dir / TOPIC_MAP_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, sort_keys=True),
                        encoding="utf-8")
        if not path.is_file():
            return "(topic map unavailable)"
    except OSError:
        log.error("topic_map_spill_failed", exc_info=True)
        return "(topic map unavailable)"
    return str(path.resolve())


def _prior_proposal_pointer(out_dir: Path, proposal: dict[str, Any] | None) -> str:
    """Spill the prior draft's structural JSON to `<out_dir>/prior-proposal.json`
    and return the absolute path the agent Reads.

    Same contract as `_existing_topics_pointer` and `wiki_read_pointer`: probe
    after writing so a failed spill degrades to a placeholder rather than a
    dangling path. Embedded, this was 14k of a measured 39.5k regenerate prompt
    — one topic's `refs` alone were 5.8k — for data the agent rarely needs in
    full and can always fetch.
    """
    path = out_dir / PRIOR_PROPOSAL_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_prior_proposal_for_prompt(proposal), indent=2, sort_keys=True),
            encoding="utf-8")
        if not path.is_file():
            return "(prior proposal unavailable)"
    except OSError:
        log.error("prior_proposal_spill_failed", exc_info=True)
        return "(prior proposal unavailable)"
    return str(path.resolve())


def _buckets_section(repo: Path, prior_draft: dict[str, Any] | None) -> str:
    """The `parent_id` picker, or "" on a scoped regenerate.

    The picker exists so a *new* topic can choose its bucket. A scoped
    regenerate re-derives named, already-approved pages whose `parent_id` is
    fixed, so the list is 2.8k of unusable context. Scope is read from the same
    `scope_topic_ids` key `_scoped_refresh_directive` uses — `_scope_for_regenerate`
    stays the only place that decides what a regenerate covers.
    """
    if (prior_draft or {}).get("scope_topic_ids"):
        return ""
    return (
        "Available buckets (pick one id for each topic's `parent_id`, or null "
        "if none fits):\n```json\n"
        + json.dumps(_bucket_summary(repo), indent=2, sort_keys=True)
        + "\n```\n"
    )


def _bucket_summary(repo: Path) -> list[dict[str, Any]]:
    """The top-level taxonomy buckets (id + label + blurb) a proposed topic
    can be placed under via `parent_id`. The reserved `unclassified` bucket
    is omitted — leaving `parent_id` null routes there, it's not a target."""
    try:
        graph = load_authoritative_graph(repo)
    except Exception:
        return []
    return [
        {"id": tid, "label": topic.get("label"),
         "blurb": topic.get("blurb") or topic.get("intent", "")}
        for tid, topic in sorted((graph.get("topics") or {}).items())
        if topic.get("kind") == "bucket" and tid != "unclassified"
    ]


def _format_template_section(prompt_templates: list[dict[str, Any]] | None) -> str:
    """Render injected prompt templates as a ## Custom Instructions block.

    Sits between the user's topic_request and the Rules block. Empty
    list (and None) collapse to no extra text, so the prompt is byte-
    identical for runs without templates.
    """
    if not prompt_templates:
        return ""
    lines = ["", "", "## Custom Instructions"]
    for template in prompt_templates:
        label = template.get("label") or template.get("slug") or "Template"
        body = (template.get("body") or "").strip()
        if not body:
            continue
        lines.extend(["", f"### {label}", "", body])
    return "\n".join(lines)


def _template_slugs(prompt_templates: list[dict[str, Any]] | None) -> list[str]:
    """Slug list for the injected prompt templates (skips entries without one)."""
    return [str(t.get("slug")) for t in (prompt_templates or []) if t.get("slug")]


def _prior_proposal_for_prompt(proposal: dict[str, Any] | None) -> dict[str, Any]:
    """Drop agent self-`notes` and every wiki body from the prior draft before
    it goes into a regenerate prompt.

    `notes` is the previous pass's editorial about its own choices (e.g.
    "these three topics replace the prior trace-span-repair draft"). It is
    not draft content, and replaying it biases the next draft toward
    repeating that reasoning. After a restore it is doubly wrong: the notes
    describe a draft the user just discarded, while run-level `notes` are
    never reverted to the restored revision's.

    Wiki bodies (the run-level `wiki` and each topic's `wiki`) are stripped
    because the prompt hands the agent a *pointer* to the on-disk prior
    `wiki.md` instead — the agent Reads what it needs rather than having every
    narrative embedded (and the same content embedded twice).
    """
    clean = {k: v for k, v in (proposal or {}).items()
             if k not in ("notes", "wiki")}
    metadata = clean.get("metadata")
    if isinstance(metadata, dict) and "notes" in metadata:
        clean["metadata"] = {k: v for k, v in metadata.items() if k != "notes"}
    if isinstance(clean.get("topics"), list):
        clean["topics"] = [_topic_without_wiki(t) for t in clean["topics"]]
    return clean


def _topic_without_wiki(topic: Any) -> Any:
    if not isinstance(topic, dict):
        return topic
    return {k: v for k, v in topic.items() if k != "wiki"}


def _sibling_refresh_section(repo: Path, out_dir: Path) -> str:
    """The "Sibling topics being refreshed" prompt section, or "" when this
    isn't a content-drift refresh batch with ≥1 other pending sibling.

    `out_dir` is `<repo>/.regin/topics/proposals/<id>`, so its name is the
    proposal id (see `_repo_and_id_from_out_dir`). For user/external proposals
    the id isn't in the content-drift set, so the helper returns "" and no
    section is emitted."""
    from lib.topics.agent_spawn import _sibling_refresh_context
    block = _sibling_refresh_context(repo, out_dir.name)
    if not block:
        return ""
    return (
        "\nSibling topics being refreshed in this same batch (keep your "
        "cross-references — edges and wiki mentions — consistent with these; "
        "do NOT treat their approved summary as final, they are being "
        f"rewritten too):\n\n{block}\n"
    )


def _scoped_refresh_directive(prior_draft: dict[str, Any]) -> str:
    """Regenerate-only block that narrows the redraft to the drifted topics.

    Empty unless the run is a scoped content-drift regenerate. When present it
    names exactly the topics whose refs drifted (with the drifted files) and
    tells the agent to re-derive only those pages; every other topic is
    preserved verbatim by the splice, so the agent may omit them from its
    output entirely. Correctness does not depend on the agent obeying this —
    the splice discards any non-scoped topic bodies regardless — it only saves
    the agent from re-exploring untouched areas."""
    topic_ids = prior_draft.get("scope_topic_ids") or []
    if not topic_ids:
        return ""
    drifted_paths = prior_draft.get("scope_drifted_paths") or {}
    lines = []
    for topic_id in topic_ids:
        paths = drifted_paths.get(topic_id) or []
        listed = ", ".join(f"`{p}`" for p in paths) if paths else "(this topic's refs)"
        lines.append(f"- **{topic_id}** — drifted files: {listed}")
    listing = "\n".join(lines)
    return f"""Scoped refresh — the code changed under only these topics:
{listing}

Re-derive the wiki page for ONLY the topics listed above, from their current
refs. You may omit every other topic from your output: unchanged topics are
preserved as-is and must NOT be rewritten. Focus your exploration on the
drifted files.

"""


def _prior_reference_block(repo: Path, out_dir: Path,
                           prior_draft: dict[str, Any] | None) -> str:
    """The regenerate-only 'Prior draft reference' block, or '' on a fresh run.

    The static guidance is the editable ``topic-proposal-regenerate`` surface
    (``lib/prompts/surfaces/regenerate.py``); this builder assembles only the
    runtime context it interpolates and passes the result as the
    ``prior_reference`` variable of the drafting skeleton. A broken user edit
    degrades to the built-in default inside ``render_surface``.

    Neither half of the prior draft is embedded: the wiki is an absolute-path
    *pointer* to ``proposals/<id>/wiki.md`` and the structural proposal JSON is
    spilled to ``proposals/<id>/prior-proposal.json``, so the agent Reads what it
    needs instead of carrying both in the prompt. Each pointer is probed against
    the live file at prompt-build time, so a stale or vanished file degrades to a
    placeholder rather than dangling."""
    if not prior_draft:
        return ""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.regenerate import SURFACE_ID
    from lib.topics.wiki import wiki_read_pointer

    feedback_reference = ""
    feedback_block = format_review_feedback_for_prompt(prior_draft.get("feedback_threads") or [])
    if feedback_block:
        feedback_reference = f"\n{feedback_block}\n\n"
    return render_surface(SURFACE_ID, {
        "review_feedback": feedback_reference,
        "scope_directive": _scoped_refresh_directive(prior_draft),
        "prior_proposal_pointer": _prior_proposal_pointer(
            out_dir, prior_draft.get("proposal")),
        "prior_wiki_pointer": wiki_read_pointer(
            repo, out_dir / "wiki.md", absolute=True),
    })


def _instructions(
    repo: Path,
    topic_request: str | None,
    out_dir: Path,
    temp_output_path: Path,
    *,
    prior_draft: dict[str, Any] | None = None,
    prompt_templates: list[dict[str, Any]] | None = None,
) -> str:
    """Build the drafting agent's task prompt.

    The body is the editable ``topic-proposal-drafting`` surface
    (``lib/prompts/surfaces/drafting.py``); this function only assembles the
    runtime context it interpolates. A broken user edit degrades to the built-in
    default inside ``render_surface`` — the prompt is never left unbuildable.
    """
    from lib.prompts import render_surface
    from lib.prompts.surfaces.drafting import SURFACE_ID

    default_request = (
        "No specific topic request was provided. "
        "Propose the most useful uncovered topics from the repository."
    )
    context = {
        "topic_request": topic_request or default_request,
        "prior_reference": _prior_reference_block(repo, out_dir, prior_draft),
        "custom_instructions": _format_template_section(prompt_templates),
        "temp_output_path": str(temp_output_path),
        "output_file": str(out_dir / OUTPUT_FILE),
        "finish_cmd": _finish_command(repo, out_dir.name),
        "existing_topics_pointer": _existing_topics_pointer(repo, out_dir),
        "buckets_section": _buckets_section(repo, prior_draft),
        "sibling_section": _sibling_refresh_section(repo, out_dir),
    }
    return render_surface(SURFACE_ID, context)
