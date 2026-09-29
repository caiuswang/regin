"""Backfill `tier: "reference"` onto topic refs the wiki never mentions.

The `tier` axis (`lib.topics.core.REF_TIERS`) lets content-drift skip refs a
wiki only points at (see `lib.topics.content_drift`). Graphs authored before the
field exist read every ref as the default `primary`, so they still emit drift
debt for pointer-only files. This pass demotes the clear cases: a ref whose
path/basename never appears in its topic's wiki narrative is a pointer, not a
documented file, so it is tagged `reference`.

Conservative by construction:
  * only *demotes* — a ref that already carries any `tier` is left untouched, so
    a human/LLM classification is never overridden (this is also what makes the
    pass idempotent);
  * the mention test is *generous* (full path OR basename substring), so the
    safe error is "leave it primary" (keeps drifting, same as today), never
    "wrongly silence a file the wiki documents";
  * dry-run by default — a write happens only on `apply=True`, into the
    git-tracked base graph so the change reviews as a plain diff.

Operates on the base graph (shared / committed), not the machine-local overlay;
promote an overlay topic first to include it. Never raises — a backfill must not
break the CLI caller.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lib.activity_log import get_activity_logger
from lib.topics.core import load_graph, save_graph, slugify, TopicGraphError
from lib.topics.wiki import wiki_dir

log = get_activity_logger("topics")


def _is_mentioned(wiki_text_lower: str, path: str) -> bool:
    """Whether a ref path looks referenced in the wiki narrative. Generous on
    purpose: the full relative path OR the bare basename appearing anywhere
    counts as mentioned, so a documented file is never wrongly demoted — the
    only cost of a false "mentioned" is leaving a ref `primary` (unchanged from
    today)."""
    if path.lower() in wiki_text_lower:
        return True
    base = os.path.basename(path).lower()
    return bool(base) and base in wiki_text_lower


def _refs_to_demote(topic: dict[str, Any], wiki_text_lower: str) -> list[dict[str, Any]]:
    """Ref dicts of one topic that the wiki never mentions and that carry no
    explicit `tier` yet — the demotion candidates. Returns the live ref dicts so
    the caller can tag them in place."""
    out: list[dict[str, Any]] = []
    for ref in topic.get("refs", []):
        if not isinstance(ref, dict):
            continue
        path = ref.get("path")
        if not path or ref.get("tier"):        # respect an existing tier
            continue
        if not _is_mentioned(wiki_text_lower, path):
            out.append(ref)
    return out


def _read_wiki(wiki_root: Path, topic_id: str) -> str | None:
    """Lower-cased wiki body for a topic, or None when it has no wiki file (the
    topic can't be judged without a narrative to check refs against)."""
    wiki_path = wiki_root / f"{slugify(topic_id)}.md"
    if not wiki_path.exists():
        return None
    try:
        return wiki_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return None


def _scan_topics(graph: dict[str, Any], wiki_root: Path, topic_id: str | None,
                 ) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """({topic_id: refs to demote}, [topic_ids with no wiki]) in graph order."""
    to_demote: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []
    for tid, topic in graph.get("topics", {}).items():
        if topic_id is not None and tid != topic_id:
            continue
        wiki_text = _read_wiki(wiki_root, tid)
        if wiki_text is None:
            skipped.append(tid)
        else:
            to_demote[tid] = _refs_to_demote(topic, wiki_text)
    return to_demote, skipped


def _demote(refs: list[dict[str, Any]]) -> None:
    for ref in refs:
        ref["tier"] = "reference"


def backfill_reference_tiers(repo_path: str | Path, *, apply: bool = False,
                             topic_id: str | None = None) -> dict[str, Any]:
    """Tag wiki-unmentioned refs as `tier: "reference"`. Dry-run unless
    `apply=True`. Scope to one topic with `topic_id`.

    Returns `{demotions: [{topic_id, path}], skipped_no_wiki: [topic_id],
    applied: bool, topics_changed: [topic_id]}`. `applied` is True only when a
    write actually happened. Never raises."""
    try:
        graph = load_graph(repo_path)
    except (TopicGraphError, OSError):
        log.error("tier_backfill_load_failed", repo_path=str(repo_path), exc_info=True)
        return {"demotions": [], "skipped_no_wiki": [], "applied": False,
                "topics_changed": []}

    to_demote, skipped = _scan_topics(graph, wiki_dir(repo_path), topic_id)
    demotions = [{"topic_id": tid, "path": ref["path"]}
                 for tid, refs in to_demote.items() for ref in refs]
    changed = [tid for tid, refs in to_demote.items() if refs] if apply else []
    for tid in changed:
        _demote(to_demote[tid])

    if changed:
        save_graph(repo_path, graph)
        log.write("tier_backfill_applied", repo_path=str(repo_path),
                  refs=len(demotions), topics=len(changed))
    return {"demotions": demotions, "skipped_no_wiki": skipped,
            "applied": bool(changed), "topics_changed": changed}


__all__ = ["backfill_reference_tiers"]
