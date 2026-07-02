"""Agentic classification of memories onto authoritative topic nodes.

Replaces the deterministic ref-path heuristic (`best_topic_for_text`) that
`regin memory link-topics` used with an LLM that reads each memory's *subject*
and returns the genuinely related topic-node ids — zero, one, or several. The
hard match ranks by raw ref-path hit count, so a memory that merely *mentions*
a shared infra file (`db/schema.sql`, `hook_manager/core.py`) gets vacuumed onto
an unrelated topic; classifying on the subject avoids that and supports the
multi-topic memories the path match could never express.

Fail-loud by contract: with no external agent configured the LLM `complete`
returns None and `classify_memories` raises `ClassifierUnavailable`. The caller
must surface that, never silently fall back to the heuristic.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lib.activity_log import get_activity_logger

log = get_activity_logger("memory")

CLASSIFY_SOURCE = "agent"

# The classification prompt (rules + taxonomy slot + output contract) now lives
# as the editable `memory-topic-classify` surface
# (lib/prompts/surfaces/memory.py::_DEFAULT_BODY_TOPIC_CLASSIFY). `_compose_prompt`
# below only wires the runtime context (the taxonomy digest + the batch block).


class ClassifierUnavailable(RuntimeError):
    """The agentic classifier could not reach an LLM (no agent configured, or
    every batch failed to complete). Raised instead of falling back to the
    deterministic heuristic — that fallback must be an explicit caller choice."""


def _taxonomy_digest(graph: dict) -> str:
    """One line per topic node: `- <id>: <label> — <intent>` (intent clipped).
    Nodes that are someone's parent are tagged `[category]` so the model can
    prefer the specific leaf over the broad container."""
    topics = graph.get("topics", {})
    parents = {n.get("parent_id") for n in topics.values() if n.get("parent_id")}
    lines = []
    for tid, node in topics.items():
        label = (node.get("label") or "").strip()
        intent = " ".join((node.get("intent") or "").split())[:200]
        base = f"- {tid}: {label} — {intent}" if intent else f"- {tid}: {label}"
        lines.append(base + (" [category]" if tid in parents else ""))
    return "\n".join(lines)


def _memories_block(batch: list[dict]) -> str:
    """The batch rendered for the prompt: id, title, and a clipped body."""
    parts = []
    for m in batch:
        title = (m.get("title") or "").strip()
        body = " ".join((m.get("body") or "").split())[:1200]
        parts.append(f'<memory id="{m["id"]}">\n{title}\n{body}\n</memory>')
    return "\n".join(parts)


def _compose_prompt(batch: list[dict], taxonomy: str) -> str:
    """Assemble the classification prompt for one batch of memories via the
    editable `memory-topic-classify` surface. Only wires the runtime context;
    a broken user edit degrades to the built-in default inside `render_surface`."""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.memory import TOPIC_CLASSIFY_SURFACE_ID
    context = {"taxonomy": taxonomy, "memories_block": _memories_block(batch)}
    return render_surface(TOPIC_CLASSIFY_SURFACE_ID, context)


def _extract_json_array(answer: str):
    """Parse a JSON array out of model output, tolerating markdown fences and
    surrounding prose. Returns None when no array can be parsed."""
    text = re.sub(r"```(?:json)?", "", answer)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _clean_topics(raw: Any, valid_ids: set[str], max_topics: int) -> list[str]:
    """The valid, deduped topic ids from one item's `topics`, capped."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for t in raw:
        if isinstance(t, str) and t in valid_ids and t not in out:
            out.append(t)
    return out[:max_topics]


def _drop_ancestors(topics: list[str], nodes: dict) -> list[str]:
    """Keep only the most specific nodes: drop any topic that is an ancestor
    (via parent_id) of another selected topic, so a memory is never bound to
    both a leaf and its parent category (redundant — subtree navigation already
    surfaces a leaf-bound memory under its parent)."""
    selected = set(topics)
    redundant: set[str] = set()
    for t in topics:
        cur = nodes.get(t, {}).get("parent_id")
        while cur:
            if cur in selected:
                redundant.add(cur)
            cur = nodes.get(cur, {}).get("parent_id")
    return [t for t in topics if t not in redundant]


def _parse_batch(answer: str, batch_ids: set[str], nodes: dict,
                 max_topics: int) -> "dict[str, list[str]] | None":
    """Map `{memory_id: [topic_id, ...]}` from one batch's answer, keeping only
    ids that were in the batch and topics that exist in the graph (most-specific
    only). None when the answer can't be parsed as an array at all."""
    items = _extract_json_array(answer)
    if items is None:
        return None
    valid_ids = set(nodes)
    result: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if mid in batch_ids:
            cleaned = _clean_topics(item.get("topics"), valid_ids, max_topics)
            result[mid] = _drop_ancestors(cleaned, nodes)
    return result


def classify_memories(memories: list[dict], graph: dict, llm, *,
                      batch_size: int = 20, max_topics: int = 3,
                      stats: "dict | None" = None
                      ) -> "dict[str, list[str]]":
    """Agentically classify each memory onto authoritative topic nodes.

    Returns `{memory_id: [topic_node_id, ...]}` covering every memory that the
    LLM placed (a memory with no related topic maps to `[]`). Raises
    `ClassifierUnavailable` when no batch produced any completion — the
    fail-loud contract that stops a silent heuristic fallback.

    Topic ids are validated against `graph`, so the result can never carry a
    node that isn't in the authoritative graph (no dangling links).

    When `stats` (a mutable dict) is passed it is filled with
    `{memories, placed, batches, unparsed}` so a caller can surface the
    *silently dropped* memories — those in a batch that returned no completion
    or unparseable JSON never enter the result, and `len(memories) - placed`
    counts them. Without it the count is invisible (the old behaviour)."""
    if not memories:
        if stats is not None:
            stats.update(memories=0, placed=0, batches=0, unparsed=0)
        return {}
    nodes = graph.get("topics", {})
    assignments: dict[str, list[str]] = {}
    taxonomy = _taxonomy_digest(graph)
    completed = unparsed = 0
    for start in range(0, len(memories), batch_size):
        batch = memories[start:start + batch_size]
        batch_ids = {m["id"] for m in batch}
        answer = llm.complete(_compose_prompt(batch, taxonomy), max_tokens=4096)
        if not answer:
            log.error("topic_classify_no_completion", batch_start=start)
            continue
        completed += 1
        parsed = _parse_batch(answer, batch_ids, nodes, max_topics)
        if parsed is None:
            unparsed += 1
            log.error("topic_classify_unparseable", batch_start=start)
            continue
        assignments.update(parsed)
    if completed == 0:
        raise ClassifierUnavailable(
            "no LLM completion for any batch — is an external agent configured?")
    if stats is not None:
        stats.update(memories=len(memories), placed=len(assignments),
                     batches=completed, unparsed=unparsed)
    log.write("topic_classified", memories=len(memories),
              placed=len(assignments), batches=completed, unparsed=unparsed)
    return assignments


__all__ = ["classify_memories", "ClassifierUnavailable", "CLASSIFY_SOURCE"]
