"""Characterization test: the editable ``topic-split-leaf`` skeleton renders
byte-identical to the pre-refactor hardcoded ``_PROMPT.format(...)`` in
``lib/topics/split_leaf.py``.

A capturing fake ``llm`` records the exact string ``propose_clusters`` passes to
``complete``; ``_reference_prompt`` reproduces the old builder — the frozen
``_FROZEN_SPLIT_PROMPT`` (a ``str.format`` template, escaped JSON braces intact)
filled via the same (unchanged) ``_memories_block`` helper. Edit the surface
body (``lib/prompts/surfaces/topics.py``) and this reference together.
"""

from __future__ import annotations

import importlib

split_leaf = importlib.import_module("lib.topics.split_leaf")

# Verbatim copy of the pre-refactor `_PROMPT` (a str.format template: single
# braces are slots, the doubled braces are escaped literal JSON braces).
_FROZEN_SPLIT_PROMPT = """You are reorganizing one over-large topic in a repo's knowledge base.

The topic "{label}" has accumulated too many memories to navigate. Group them
into {lo}-{hi} coherent SUB-THEMES so a future reader can drill into the right
cluster. Rules:
- Each memory goes in exactly ONE sub-theme. Cover every memory id given.
- A sub-theme needs at least a few memories — don't make singletons.
- Give each a short Title-Case label and a one-line intent written as a router
  card ("drill in here when …"), not a description.
- Group by SUBJECT (what the lesson teaches), not by incidental file mentions.

<topic-intent>{intent}</topic-intent>

<memories>
{memories}
</memories>

<output_format>
Respond with ONLY a JSON array:
  [{{"label": "...", "intent": "...", "memory_ids": ["<id>", ...]}}, ...]
</output_format>
"""


class _CaptureLLM:
    """Records the prompt passed to ``complete`` and returns an empty array
    (truthy, so ``propose_clusters`` does not raise; parses to no clusters)."""

    def __init__(self):
        self.prompt = None

    def complete(self, prompt, **_):
        self.prompt = prompt
        return "[]"


_LEAF = {"label": "Session trace", "intent": "  span   ingest\n and merge  "}
_MEMORIES = [
    {"id": "m1", "title": "Append-only spans", "body": "session_spans is append-only."},
    {"id": "m2", "title": "Merge at read time", "body": "dedup/reparent in merge.py."},
]


def _reference_prompt(leaf_node, memories, *, lo, hi):
    return _FROZEN_SPLIT_PROMPT.format(
        label=leaf_node.get("label") or "topic",
        intent=" ".join((leaf_node.get("intent") or "").split())[:400],
        memories=split_leaf._memories_block(memories), lo=lo, hi=hi)


def test_split_surface_renders_byte_identical():
    llm = _CaptureLLM()
    split_leaf.propose_clusters(_LEAF, _MEMORIES, llm, lo=2, hi=5)
    assert llm.prompt == _reference_prompt(_LEAF, _MEMORIES, lo=2, hi=5)


def test_split_surface_honours_custom_bounds():
    llm = _CaptureLLM()
    split_leaf.propose_clusters(_LEAF, _MEMORIES, llm, lo=3, hi=7)
    assert llm.prompt == _reference_prompt(_LEAF, _MEMORIES, lo=3, hi=7)
    # The JSON example keeps single braces (escaped-brace collapse survives).
    assert '[{"label": "...", "intent": "...", "memory_ids": ["<id>", ...]}, ...]' in llm.prompt
