"""Agentic topic classifier (`lib/memory/topic_classify.py`)."""

import json

import pytest

from lib.memory.topic_classify import (classify_memories, ClassifierUnavailable,
                                       _taxonomy_digest)

GRAPH = {"topics": {
    "topic-a": {"label": "Alpha", "intent": "the alpha subsystem"},
    "topic-b": {"label": "Beta", "intent": "the beta subsystem"},
    "topic-c": {"label": "Gamma", "intent": ""},
}}


class StubLLM:
    """Returns queued responses, one per `complete()` call (None when drained,
    which is exactly what an unconfigured agent yields). Records prompts."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []

    def complete(self, prompt, *, max_tokens=1024):
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else None


def test_maps_single_and_multi_topic():
    mems = [{"id": "mem-0", "title": "a", "body": "x"},
            {"id": "mem-1", "title": "b", "body": "y"}]
    ans = json.dumps([{"id": "mem-0", "topics": ["topic-a"]},
                      {"id": "mem-1", "topics": ["topic-a", "topic-b"]}])
    out = classify_memories(mems, GRAPH, StubLLM([ans]))
    assert out == {"mem-0": ["topic-a"], "mem-1": ["topic-a", "topic-b"]}


def test_drops_invalid_dedups_and_caps():
    mems = [{"id": "mem-0", "title": "a", "body": "x"}]
    ans = json.dumps([{"id": "mem-0",
                       "topics": ["topic-a", "nope", "topic-a",
                                  "topic-b", "topic-c"]}])
    out = classify_memories(mems, GRAPH, StubLLM([ans]), max_topics=2)
    assert out["mem-0"] == ["topic-a", "topic-b"]


def test_empty_topics_means_unbound():
    mems = [{"id": "mem-0", "title": "a", "body": "x"}]
    ans = json.dumps([{"id": "mem-0", "topics": []}])
    assert classify_memories(mems, GRAPH, StubLLM([ans])) == {"mem-0": []}


def test_batches_by_size():
    mems = [{"id": f"mem-{i}", "title": "a", "body": "x"} for i in range(3)]
    resp = [json.dumps([{"id": f"mem-{i}", "topics": ["topic-a"]}])
            for i in range(3)]
    llm = StubLLM(resp)
    out = classify_memories(mems, GRAPH, llm, batch_size=1)
    assert len(llm.prompts) == 3
    assert out == {f"mem-{i}": ["topic-a"] for i in range(3)}


def test_unavailable_when_no_completion():
    mems = [{"id": "mem-0", "title": "a", "body": "x"}]
    with pytest.raises(ClassifierUnavailable):
        classify_memories(mems, GRAPH, StubLLM([None]))
    with pytest.raises(ClassifierUnavailable):
        classify_memories(mems, GRAPH, StubLLM([]))


def test_empty_input_returns_empty_not_unavailable():
    llm = StubLLM([])
    assert classify_memories([], GRAPH, llm) == {}
    assert llm.prompts == []


def test_unparseable_batch_skipped_others_kept():
    mems = [{"id": "mem-0", "title": "a", "body": "x"},
            {"id": "mem-1", "title": "b", "body": "y"}]
    good = json.dumps([{"id": "mem-1", "topics": ["topic-b"]}])
    llm = StubLLM(["not json at all", good])
    out = classify_memories(mems, GRAPH, llm, batch_size=1)
    assert out == {"mem-1": ["topic-b"]}


def test_drops_ancestor_when_child_selected():
    graph = {"topics": {
        "parent": {"label": "P", "intent": ""},
        "child": {"label": "C", "intent": "", "parent_id": "parent"},
        "other": {"label": "O", "intent": ""},
    }}
    mems = [{"id": "mem-0", "title": "a", "body": "x"}]
    ans = json.dumps([{"id": "mem-0",
                       "topics": ["parent", "child", "other"]}])
    out = classify_memories(mems, graph, StubLLM([ans]))
    assert out == {"mem-0": ["child", "other"]}


def test_ignores_ids_outside_batch():
    mems = [{"id": "mem-0", "title": "a", "body": "x"}]
    ans = json.dumps([{"id": "mem-0", "topics": ["topic-a"]},
                      {"id": "ghost", "topics": ["topic-b"]}])
    assert classify_memories(mems, GRAPH, StubLLM([ans])) == {"mem-0": ["topic-a"]}


def test_taxonomy_digest_lists_ids():
    digest = _taxonomy_digest(GRAPH)
    for needle in ("topic-a: Alpha", "topic-b: Beta", "topic-c: Gamma"):
        assert needle in digest


def test_taxonomy_digest_tags_categories():
    graph = {"topics": {
        "parent": {"label": "P", "intent": ""},
        "child": {"label": "C", "intent": "", "parent_id": "parent"},
    }}
    lines = {ln.split(":")[0]: ln for ln in _taxonomy_digest(graph).splitlines()}
    assert "[category]" in lines["- parent"]
    assert "[category]" not in lines["- child"]
