"""Global meta-roots — a cross-repo taxonomy overlay for memories that are
**not** about any one repo's code.

The authoritative topic graph (`.regin/topics/topic.json`) is per-repo and
models a repo's *code subsystems*. But two important classes of memory have
no home there:

  * **skill-usage** lessons — how to apply regin's skills/workflows
    (goal-verified, ui-ux, topic-router…), independent of any repo;
  * **user preferences** — durable working-style conventions that apply
    everywhere.

This module supplies a small, bundled set of global *bucket* roots
(`skills`, `preferences`) plus seed leaves, defined in the sibling
`meta_roots.json`, and an overlay merge so the memory navigation surface
(`index_root` / `index_expand` / `index_fetch`) shows them alongside a
repo's own roots — from *any* repo.

It is a **read-time overlay only**: it is applied where the graph is read
for navigation/recall, never written into a repo's `topic.json` or its
versioned snapshots. So the per-repo drift detection, routing, scan, and
proposal machinery never see these nodes and stay untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_META_PATH = Path(__file__).with_name("meta_roots.json")


def load_global_meta_topics() -> dict[str, Any]:
    """The bundled global meta-root taxonomy as a `{node_id: node}` map.

    Returns `{}` if the file is missing or unreadable — the overlay is then
    a no-op, never a hard failure (navigation degrades to repo-only roots).
    """
    try:
        data = json.loads(_META_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_meta_roots(graph: dict[str, Any]) -> dict[str, Any]:
    """Overlay the global meta-roots onto a repo's authoritative graph.

    A repo topic always wins on an id collision (the repo graph is the
    override), so the overlay can only *add* nodes, never shadow a real one.
    Returns a new dict; the input is not mutated. A missing/empty meta file
    leaves the graph unchanged.
    """
    meta = load_global_meta_topics()
    if not meta:
        return graph
    merged = dict(graph)
    topics: dict[str, Any] = dict(meta)
    topics.update(graph.get("topics") or {})  # repo topics take precedence
    merged["topics"] = topics
    return merged


__all__ = ["load_global_meta_topics", "merge_meta_roots"]
