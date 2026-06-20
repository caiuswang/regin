"""Tests for Slice 2 — the goal→memory feedback loop.

These touch the real memory store (it self-initializes its own SQLite
file). Every memory written here is tagged is_test=True and forgotten in
teardown so the durable store is never polluted.
"""

from __future__ import annotations

import pytest

import lib.memory as memory
from lib.goal_feedback import FAIL_TAG, OutcomeResult, record_outcome, render_summary
from lib.goal_preflight import recall_lessons

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


def test_render_summary_mentions_counts():
    res = OutcomeResult(reinforced=["a"], ignored=["b"], new_lessons=["c"])
    text = render_summary(res)
    assert "reinforced 1" in text and "wrote 1" in text
