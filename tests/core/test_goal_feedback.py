"""Tests for Slice 2 — the goal→memory feedback loop.

These touch the real memory store (it self-initializes its own SQLite
file). Every memory written here is tagged is_test=True and forgotten in
teardown so the durable store is never polluted.
"""

from __future__ import annotations

import pytest

import lib.memory as memory
from lib.goal_feedback import FAIL_TAG, OutcomeResult, record_outcome, render_summary
from lib.goal_preflight import recall_lessons, record_offered

pytestmark = pytest.mark.skipif(
    not memory.enabled(), reason="memory store disabled in this environment")


@pytest.fixture
def seed_lesson():
    """Create a real lesson, yield its id, forget it afterwards."""
    created: list[str] = []

    def _make(body: str, tags: list[str], *, is_test: bool = False):
        # is_test=False so recall (which excludes test rows) can surface it;
        # teardown forgets it either way, so the durable store stays clean.
        mid = memory.remember(body, kind="lesson", tags=tags,
                              importance=0.5, is_test=is_test)
        created.append(mid)
        return mid

    yield _make
    for mid in created:
        memory.forget(mid)


def _recall_count(mid: str) -> int:
    mem = memory.get(mid)
    return getattr(mem, "recall_count", 0) or 0 if mem else 0


def test_included_lesson_is_reinforced(seed_lesson):
    mid = seed_lesson("Test the empty (0-item) state of any list view.",
                      ["frontend"])
    before = _recall_count(mid)
    result = record_outcome("refactor the inbox view", included_ids=[mid])
    assert mid in result.reinforced
    assert _recall_count(mid) > before  # the engaged signal bumped it


def test_offered_but_unused_lesson_is_ignored_not_reinforced(seed_lesson):
    mid = seed_lesson("Some tangential lesson.", ["frontend"])
    before = _recall_count(mid)
    result = record_outcome("x", included_ids=[], offered_ids=[mid])
    assert mid in result.ignored
    assert mid not in result.reinforced
    assert _recall_count(mid) == before  # untouched; decay is reflect's job


def test_failures_become_new_lessons(seed_lesson):
    result = record_outcome(
        "refactor inbox", failures=["Pagination broke at 0 items — always test the empty page."],
        tags=["frontend"])
    try:
        assert len(result.new_lessons) == 1
        mem = memory.get(result.new_lessons[0])
        tags = getattr(mem, "tags", None) or []
        assert FAIL_TAG in tags
        assert "frontend" in tags
    finally:
        for mid in result.new_lessons:
            memory.forget(mid)


def test_blank_failures_are_dropped():
    result = record_outcome("x", failures=["   ", ""])
    assert result.new_lessons == []


def test_recall_lessons_surfaces_a_seeded_lesson(seed_lesson):
    seed_lesson("When refactoring an inbox filter, verify counts against the API.",
                ["frontend"])
    hits = recall_lessons("refactor the inbox filter counts", ["frontend"])
    # Best-effort recall; the seeded lesson should be among the hits.
    assert any("inbox" in (h.get("snippet") or "").lower() for h in hits)


def test_recall_lessons_degrades_to_empty_on_no_match():
    hits = recall_lessons("zzqq nonexistent topic wwxx", ["frontend"])
    assert isinstance(hits, list)  # never raises, always a list


def test_offering_a_lesson_does_not_reinforce_it(seed_lesson):
    # recall_lessons is a mechanical probe — surfacing a lesson must NOT bump
    # its recall_count (that is reserved for deliberate use via --included).
    mid = seed_lesson("Offer-no-reinforce: verify inbox filter counts vs API.",
                      ["frontend"])
    before = _recall_count(mid)
    recall_lessons("verify the inbox filter counts", ["frontend"])
    assert _recall_count(mid) == before


def test_record_offered_logs_injection_for_session(seed_lesson):
    mid = seed_lesson("Offered-set lesson for a session.", ["frontend"])
    sid = "test-goal-preflight-session-001"
    n = record_offered(sid, [{"id": mid}], "some goal")
    assert n == 1
    assert mid in memory.get_store().injected_memory_ids(sid)


def test_record_offered_noop_without_session_id():
    assert record_offered(None, [{"id": "x"}], "g") == 0
    assert record_offered("", [{"id": "x"}], "g") == 0


def test_record_offered_noop_without_lessons():
    assert record_offered("sid", [], "g") == 0


def test_render_summary_mentions_counts():
    res = OutcomeResult(reinforced=["a"], ignored=["b"], new_lessons=["c"])
    text = render_summary(res)
    assert "reinforced 1" in text and "wrote 1" in text


# --- topic short-path filing (--topic) ------------------------------------

def _an_existing_topic_node() -> str | None:
    """A real authoritative topic node id from this repo's graph, or None
    when the graph is empty/absent so the test can skip rather than guess."""
    from lib.settings import settings
    from lib.topics.route import load_authoritative_graph
    nodes = load_authoritative_graph(str(settings.project_root)).get("topics", {})
    return next(iter(nodes), None)


def test_topic_files_new_failure_lesson_under_node():
    node = _an_existing_topic_node()
    if node is None:
        pytest.skip("no authoritative topic graph in this environment")
    result = record_outcome(
        "refactor goal feedback",
        failures=["Always file a failure-lesson under its subsystem topic."],
        topics=[node], is_test=True)
    try:
        assert result.linked_topics == [node]
        assert result.unresolved_topics == []
        mid = result.new_lessons[0]
        assert node in memory.get_store().authoritative_topics_of(mid)
    finally:
        for mid in result.new_lessons:
            memory.forget(mid)


def test_topic_slashed_short_path_resolves_to_leaf_node():
    node = _an_existing_topic_node()
    if node is None:
        pytest.skip("no authoritative topic graph in this environment")
    # A parent/child-style short path — only the leaf segment is the node id.
    result = record_outcome(
        "x", failures=["A rule."], topics=[f"some/parent/{node}"], is_test=True)
    try:
        assert result.linked_topics == [node]
        assert result.unresolved_topics == []
    finally:
        for mid in result.new_lessons:
            memory.forget(mid)


def test_unknown_topic_does_not_crash_and_is_reported():
    result = record_outcome(
        "x", failures=["A rule."], topics=["zzqq-no-such-topic-node-wwxx"],
        is_test=True)
    try:
        assert result.new_lessons  # lesson still written despite the bad topic
        assert result.linked_topics == []
        assert result.unresolved_topics == ["zzqq-no-such-topic-node-wwxx"]
    finally:
        for mid in result.new_lessons:
            memory.forget(mid)


def test_topic_with_no_new_failures_links_nothing():
    # --topic but zero failures => nothing to file, no crash. A valid topic is
    # NOT reported as unresolved just because there was nothing to attach it to.
    result = record_outcome("x", failures=[], topics=["agent-memory"])
    assert result.linked_topics == []
    assert result.unresolved_topics == []
    assert result.new_lessons == []


def _topic_link_count() -> int:
    from lib.memory.models import MemoryAuthoritativeTopic
    from sqlmodel import select
    from lib.memory.store import MemorySessionLocal
    with MemorySessionLocal() as s:
        return len(s.exec(select(MemoryAuthoritativeTopic)).all())


def test_forgetting_a_topic_filed_lesson_leaves_no_orphan_links():
    node = _an_existing_topic_node()
    if node is None:
        pytest.skip("no authoritative topic graph in this environment")
    before = _topic_link_count()
    result = record_outcome(
        "x", failures=["A rule."], topics=[node], is_test=True)
    assert result.linked_topics == [node]
    assert _topic_link_count() == before + 1  # the link landed
    for mid in result.new_lessons:
        memory.forget(mid)
    assert _topic_link_count() == before  # forget cascaded — no orphan


def test_render_summary_and_dict_surface_topics():
    from lib.goal_feedback import outcome_to_dict
    res = OutcomeResult(new_lessons=["c"], linked_topics=["agent-memory"],
                        unresolved_topics=["bogus"])
    text = render_summary(res)
    assert "agent-memory" in text and "bogus" in text
    d = outcome_to_dict(res)
    assert d["linked_topics"] == ["agent-memory"]
    assert d["unresolved_topics"] == ["bogus"]
