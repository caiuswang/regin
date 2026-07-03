"""Characterization test: the editable ``topic-group-buckets`` skeleton renders
byte-identical to the pre-refactor hardcoded ``_PROMPT.format(...)`` in
``lib/topics/group_topics.py``.

A capturing fake ``llm`` records the exact string ``propose_buckets`` passes to
``complete``; ``_reference_prompt`` reproduces the old builder — the frozen
``_FROZEN_GROUP_PROMPT`` filled via the same (unchanged) ``_topics_block``
helper. Edit the surface body (``lib/prompts/surfaces/topics.py``) and this
reference together.
"""

from __future__ import annotations

import importlib

group_topics = importlib.import_module("lib.topics.group_topics")

# Verbatim copy of the pre-refactor `_PROMPT` (a str.format template).
_FROZEN_GROUP_PROMPT = """You are organizing a repo's knowledge base whose topics are all
sitting at the top level, unbucketed. Group them into {lo}-{hi} coherent
top-level BUCKETS so a future reader can drill into the right area. Rules:
- Each topic goes in exactly ONE bucket. Cover every topic id given.
- Give each bucket a short Title-Case label and a one-line intent written as a
  router card ("drill in here when …"), not a description.
- Group by SUBJECT (what the topic is about), not by incidental overlaps.

<topics>
{topics}
</topics>

<output_format>
Respond with ONLY a JSON array:
  [{{"label": "...", "intent": "...", "topic_ids": ["<id>", ...]}}, ...]
</output_format>
"""


class _CaptureLLM:
    """Records the prompt and returns an empty array (truthy → no raise)."""

    def __init__(self):
        self.prompt = None

    def complete(self, prompt, **_):
        self.prompt = prompt
        return "[]"


_TOPICS = [
    {"id": "t1", "label": "Trace", "intent": "span ingest"},
    {"id": "t2", "label": "Memory", "intent": "recall + reflect"},
    {"id": "t3", "label": "Topics", "intent": "taxonomy + routing"},
]


def _reference_prompt(flat_topics, *, lo, hi):
    return _FROZEN_GROUP_PROMPT.format(
        topics=group_topics._topics_block(flat_topics), lo=lo, hi=hi)


def test_group_surface_renders_byte_identical():
    llm = _CaptureLLM()
    group_topics.propose_buckets(_TOPICS, llm, lo=3, hi=8)
    assert llm.prompt == _reference_prompt(_TOPICS, lo=3, hi=8)


def test_group_surface_honours_custom_bounds():
    llm = _CaptureLLM()
    group_topics.propose_buckets(_TOPICS, llm, lo=2, hi=4)
    assert llm.prompt == _reference_prompt(_TOPICS, lo=2, hi=4)
    assert '[{"label": "...", "intent": "...", "topic_ids": ["<id>", ...]}, ...]' in llm.prompt
