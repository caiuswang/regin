"""Characterization test: the editable ``memory-topic-classify`` skeleton renders
byte-identical to the pre-refactor hardcoded classifier prompt.

``_reference_compose`` reproduces the old ``topic_classify._compose_prompt`` —
the frozen ``_PROMPT_HEAD`` / ``_OUTPUT_FORMAT`` literals joined with the same
(unchanged, shared) ``_memories_block`` helper. Only the migrated surface body +
the new ``_compose_prompt`` wiring is under test. Edit the surface body
(``lib/prompts/surfaces/memory.py``) and this reference together.
"""

from __future__ import annotations

import lib.memory.topic_classify as tc

_FROZEN_PROMPT_HEAD = """You are classifying agent-memory entries onto a repo's topic taxonomy.

For each memory below, choose the topic node(s) it is genuinely ABOUT — the
subject of the lesson/gotcha/fact it teaches. Rules:
- Classify on the memory's SUBJECT, never on an incidental file path it mentions.
  A shared cross-cutting infra file (db/schema.sql, hook_manager/core.py,
  lib/skills/skill_router.py) appears across many memories and is NOT evidence
  that a memory is about that file's topic.
- Most memories map to exactly ONE topic. Add a SECOND (rarely a third) only
  when the memory genuinely teaches about two subsystems.
- Prefer the most SPECIFIC topic. A node tagged [category] is a broad
  container — pick it only when no specific child fits. NEVER return both a
  category and one of its children for the same memory; choose only the child.
- If no topic is genuinely related, return an empty list for that memory.
  Do not force a match.
- Use only topic ids from the taxonomy; never invent an id.

<taxonomy>
{taxonomy}
</taxonomy>"""

_FROZEN_OUTPUT_FORMAT = """<output_format>
Respond with ONLY a JSON array, one object per memory you were given:
  [{"id": "<the memory id>", "topics": ["<topic-id>", ...]}, ...]
Use an empty list for a memory with no genuinely related topic. Include every
memory id exactly once.
</output_format>"""


def _reference_compose(batch, taxonomy):
    head = _FROZEN_PROMPT_HEAD.replace("{taxonomy}", taxonomy)
    return "\n\n".join([head, tc._memories_block(batch), _FROZEN_OUTPUT_FORMAT]) + "\n"


def _run(batch, taxonomy):
    expected = _reference_compose(batch, taxonomy)
    actual = tc._compose_prompt(batch, taxonomy)
    return expected, actual


_TAXONOMY = "- auth: Auth — how login works\n- trace: Tracing — session spans"
_BATCH = [
    {"id": "m1", "title": "Read before Edit", "body": "Edit fails if not read."},
    {"id": "m2", "title": "", "body": "Spans are append-only."},
]


def test_parity_empty_batch_empty_taxonomy():
    # Edge case: 0 memories and empty taxonomy — the memories block is "".
    expected, actual = _run([], "")
    assert actual == expected


def test_parity_batch_with_taxonomy():
    expected, actual = _run(_BATCH, _TAXONOMY)
    assert actual == expected
    assert '<memory id="m1">' in actual
    # sanity: the literal JSON single braces in the output contract survive.
    assert '[{"id": "<the memory id>"' in actual
