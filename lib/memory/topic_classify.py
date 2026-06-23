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

_PROMPT_HEAD = """You are classifying agent-memory entries onto a repo's topic taxonomy.

For each memory below, choose the topic node(s) it is genuinely ABOUT — the
subject of the lesson/gotcha/fact it teaches. Rules:
- Classify on the memory's SUBJECT, never on an incidental file path it mentions.
  A shared cross-cutting infra file (db/schema.sql, hook_manager/core.py,
  lib/skills/skill_router.py) appears across many memories and is NOT evidence
  that a memory is about that file's topic.
- Most memories map to exactly ONE topic. Add a SECOND (rarely a third) only
  when the memory genuinely teaches about two subsystems.
- Prefer the most SPECIFIC topic. A node tagged [category] is a broad
  container — pick it only when no specific child fits.
- If no topic is genuinely related, return an empty list for that memory.
  Do not force a match.
- Use only topic ids from the taxonomy; never invent an id.

<taxonomy>
{taxonomy}
</taxonomy>"""

_OUTPUT_FORMAT = """<output_format>
Respond with ONLY a JSON array, one object per memory you were given:
  [{"id": "<the memory id>", "topics": ["<topic-id>", ...]}, ...]
Use an empty list for a memory with no genuinely related topic. Include every
memory id exactly once.
</output_format>"""


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
    """Assemble the classification prompt for one batch of memories."""
    head = _PROMPT_HEAD.replace("{taxonomy}", taxonomy)
    return "\n\n".join([head, _memories_block(batch), _OUTPUT_FORMAT]) + "\n"


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


def _parse_batch(answer: str, batch_ids: set[str], valid_ids: set[str],
                 max_topics: int) -> "dict[str, list[str]] | None":
    """Map `{memory_id: [topic_id, ...]}` from one batch's answer, keeping only
    ids that were in the batch and topics that exist in the graph. None when the
    answer can't be parsed as an array at all."""
    items = _extract_json_array(answer)
    if items is None:
        return None
    result: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if mid in batch_ids:
            result[mid] = _clean_topics(item.get("topics"), valid_ids,
                                        max_topics)
    return result


def classify_memories(memories: list[dict], graph: dict, llm, *,
                      batch_size: int = 20, max_topics: int = 3
                      ) -> "dict[str, list[str]]":
    """Agentically classify each memory onto authoritative topic nodes.

    Returns `{memory_id: [topic_node_id, ...]}` covering every memory that the
    LLM placed (a memory with no related topic maps to `[]`). Raises
    `ClassifierUnavailable` when no batch produced any completion — the
    fail-loud contract that stops a silent heuristic fallback.

    Topic ids are validated against `graph`, so the result can never carry a
    node that isn't in the authoritative graph (no dangling links)."""
    if not memories:
        return {}
    valid_ids = set(graph.get("topics", {}))
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
        parsed = _parse_batch(answer, batch_ids, valid_ids, max_topics)
        if parsed is None:
            unparsed += 1
            log.error("topic_classify_unparseable", batch_start=start)
            continue
        assignments.update(parsed)
    if completed == 0:
        raise ClassifierUnavailable(
            "no LLM completion for any batch — is an external agent configured?")
    log.write("topic_classified", memories=len(memories),
              placed=len(assignments), batches=completed, unparsed=unparsed)
    return assignments


__all__ = ["classify_memories", "ClassifierUnavailable", "CLASSIFY_SOURCE"]
