"""Unit tests for `regin memory` CLI commands.

Tests the memory list command with sorting and the suffix rendering of
importance and use_count (recall_count) metrics.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cli.commands.memory import memory_app
import lib.memory as memory


@pytest.fixture
def runner():
    """CliRunner for invoking typer commands."""
    return CliRunner()


def test_list_shows_importance_and_use_count_suffix(runner, tmp_memory_db):
    """Verify _print_memory_line appends imp and use metrics."""
    # Create a memory with specific importance and recall_count
    store = memory.get_store()
    mid = store.remember(memory.MemoryInput(
        body="test memory for suffix display",
        title="test memory for suffix display"[:80],
        importance=0.75,
    ))
    # Manually update recall_count to simulate a deliberate recall
    from lib.memory.models import Memory
    from lib.memory.engine import MemorySessionLocal
    from sqlmodel import select
    with MemorySessionLocal() as session:
        row = session.exec(select(Memory).where(Memory.id == mid)).first()
        if row:
            row.recall_count = 3
            session.add(row)
            session.commit()

    result = runner.invoke(memory_app, ["list"])
    assert result.exit_code == 0
    # Check that suffix appears in output
    assert "imp=0.75" in result.stdout
    assert "use=3" in result.stdout


def test_list_empty_shows_no_memories(runner, tmp_memory_db):
    """Verify empty memory DB shows appropriate message."""
    result = runner.invoke(memory_app, ["list"])
    assert result.exit_code == 0
    assert "no memories" in result.stdout


def test_list_sort_by_use_orders_by_recall_count(runner, tmp_memory_db):
    """Verify --sort use orders memories by recall_count descending."""
    store = memory.get_store()
    # Create three memories with different recall counts
    mid1 = store.remember(memory.MemoryInput(
        body="medium usage",
        title="medium usage"[:80],
        importance=0.5,
    ))
    mid2 = store.remember(memory.MemoryInput(
        body="high usage",
        title="high usage"[:80],
        importance=0.3,
    ))
    mid3 = store.remember(memory.MemoryInput(
        body="low usage",
        title="low usage"[:80],
        importance=0.8,
    ))

    # Set recall_count values
    from lib.memory.models import Memory
    from lib.memory.engine import MemorySessionLocal
    from sqlmodel import select
    with MemorySessionLocal() as session:
        m1 = session.exec(select(Memory).where(Memory.id == mid1)).first()
        m2 = session.exec(select(Memory).where(Memory.id == mid2)).first()
        m3 = session.exec(select(Memory).where(Memory.id == mid3)).first()
        if m1:
            m1.recall_count = 5
            session.add(m1)
        if m2:
            m2.recall_count = 10
            session.add(m2)
        if m3:
            m3.recall_count = 2
            session.add(m3)
        session.commit()

    result = runner.invoke(memory_app, ["list", "--sort", "use"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    # Filter out header/empty lines to get memory lines
    memory_lines = [l for l in lines if l.startswith("  ")]
    assert len(memory_lines) >= 3
    # Check order: high usage (10) should appear before medium (5) before low (2)
    output = result.stdout
    high_pos = output.find("high usage")
    medium_pos = output.find("medium usage")
    low_pos = output.find("low usage")
    assert high_pos < medium_pos < low_pos


def test_list_sort_by_importance_orders_by_importance(runner, tmp_memory_db):
    """Verify --sort importance orders memories by importance descending."""
    store = memory.get_store()
    # Create three memories with different importances
    mid1 = store.remember(memory.MemoryInput(
        body="low importance",
        title="low importance"[:80],
        importance=0.2,
    ))
    mid2 = store.remember(memory.MemoryInput(
        body="high importance",
        title="high importance"[:80],
        importance=0.9,
    ))
    mid3 = store.remember(memory.MemoryInput(
        body="medium importance",
        title="medium importance"[:80],
        importance=0.5,
    ))

    result = runner.invoke(memory_app, ["list", "--sort", "importance"])
    assert result.exit_code == 0
    output = result.stdout
    # Check order: high (0.9) should appear before medium (0.5) before low (0.2)
    high_pos = output.find("high importance")
    medium_pos = output.find("medium importance")
    low_pos = output.find("low importance")
    assert high_pos < medium_pos < low_pos


def test_list_sort_recent_default_behavior(runner, tmp_memory_db):
    """Verify --sort recent (default) maintains updated_at desc order."""
    store = memory.get_store()
    # Create three memories (will be ordered by creation, i.e., updated_at)
    mid1 = store.remember(memory.MemoryInput(
        body="first memory",
        title="first memory"[:80],
        importance=0.5,
    ))
    mid2 = store.remember(memory.MemoryInput(
        body="second memory",
        title="second memory"[:80],
        importance=0.5,
    ))
    mid3 = store.remember(memory.MemoryInput(
        body="third memory",
        title="third memory"[:80],
        importance=0.5,
    ))

    # Without sort arg, should be recent (desc)
    result_default = runner.invoke(memory_app, ["list"])
    # With explicit sort recent, should be same
    result_recent = runner.invoke(memory_app, ["list", "--sort", "recent"])
    assert result_default.exit_code == 0
    assert result_recent.exit_code == 0
    # Both should show memories in reverse creation order (most recent first)
    assert result_default.stdout == result_recent.stdout
    output = result_default.stdout
    third_pos = output.find("third memory")
    second_pos = output.find("second memory")
    first_pos = output.find("first memory")
    # Most recent (third) should appear first
    assert third_pos < second_pos < first_pos


def test_list_json_output_with_sort(runner, tmp_memory_db):
    """Verify --sort works with --json output."""
    store = memory.get_store()
    mid1 = store.remember(memory.MemoryInput(
        body="mem1",
        title="mem1"[:80],
        importance=0.3,
    ))
    mid2 = store.remember(memory.MemoryInput(
        body="mem2",
        title="mem2"[:80],
        importance=0.8,
    ))

    result = runner.invoke(memory_app, ["list", "--sort", "importance", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert len(rows) >= 2
    # Verify order: highest importance first
    assert rows[0]["importance"] >= rows[1]["importance"]


def test_list_filter_with_sort(runner, tmp_memory_db):
    """Verify --sort works together with filter options like --tier."""
    store = memory.get_store()
    mid1 = store.remember(memory.MemoryInput(
        body="lesson 1",
        title="lesson 1"[:80],
        importance=0.4,
        tier="lesson",
    ))
    mid2 = store.remember(memory.MemoryInput(
        body="lesson 2",
        title="lesson 2"[:80],
        importance=0.9,
        tier="lesson",
    ))
    mid3 = store.remember(memory.MemoryInput(
        body="decision 1",
        title="decision 1"[:80],
        importance=0.7,
        tier="decision",
    ))

    result = runner.invoke(memory_app, ["list", "--tier", "lesson", "--sort", "importance"])
    assert result.exit_code == 0
    output = result.stdout
    # Should only have lesson tier memories
    assert "decision 1" not in output
    # Verify ordering among lessons: 0.9 before 0.4
    lesson1_pos = output.find("lesson 1")
    lesson2_pos = output.find("lesson 2")
    if lesson1_pos >= 0 and lesson2_pos >= 0:
        assert lesson2_pos < lesson1_pos  # 0.9 importance before 0.4


def test_suffix_with_zero_importance(runner, tmp_memory_db):
    """Verify suffix handles zero importance correctly."""
    store = memory.get_store()
    mid = store.remember(memory.MemoryInput(
        body="zero importance test",
        title="zero importance test"[:80],
        importance=0.0,
    ))

    result = runner.invoke(memory_app, ["list"])
    assert result.exit_code == 0
    # Should show imp=0.00 (not hidden)
    assert "imp=0.00" in result.stdout


def test_suffix_with_zero_recall_count(runner, tmp_memory_db):
    """Verify suffix handles zero recall_count correctly."""
    store = memory.get_store()
    mid = store.remember(memory.MemoryInput(
        body="no recalls yet",
        title="no recalls yet"[:80],
        importance=0.5,
    ))

    result = runner.invoke(memory_app, ["list"])
    assert result.exit_code == 0
    # recall_count defaults to 0, should still show use=0
    assert "use=0" in result.stdout


def test_supersede_retires_old_and_inherits_fields(runner, tmp_memory_db):
    """`supersede` retires the old row (status=retired + superseded_by) and
    the replacement inherits kind/scope/tags/importance unless overridden —
    the non-destructive alternative to `forget`."""
    store = memory.get_store()
    old_id = store.remember(memory.MemoryInput(
        body="stale body", title="stale", kind="lesson",
        scope="repo:regin", tags=["send_to_user", "demo"], importance=0.7))

    result = runner.invoke(memory_app, [
        "supersede", old_id, "--body", "fresh body", "--title", "fresh"])
    assert result.exit_code == 0
    assert "old retired, chained" in result.stdout

    old = store.get_dict(old_id)
    assert old["status"] == "retired"
    new = store.get_dict(old["superseded_by"])
    # replacement is active, carries the new body/title, and inherits
    # kind/scope/tags/importance from the retired original
    expected = {
        "status": "active", "body": "fresh body", "title": "fresh",
        "kind": "lesson", "scope": "repo:regin",
        "tags": ["send_to_user", "demo"], "importance": 0.7,
    }
    assert {k: new[k] for k in expected} == expected
    # the retired original drops out of the active set
    active = [m["id"] for m in store.list_memories(status="active")]
    assert active == [old["superseded_by"]]


def test_supersede_rejects_titleless_lesson_with_friendly_error(runner,
                                                                tmp_memory_db):
    """Superseding a lesson without a title fails cleanly (Exit 1 + message),
    not with a raw ValueError traceback."""
    store = memory.get_store()
    old_id = store.remember(memory.MemoryInput(
        body="stale", title="stale", kind="lesson"))
    result = runner.invoke(memory_app, ["supersede", old_id,
                                        "--body", "no title here"])
    assert result.exit_code == 1
    assert "lesson requires a title" in result.stdout


def test_supersede_unknown_id_errors(runner, tmp_memory_db):
    """A missing old id exits non-zero and writes nothing."""
    result = runner.invoke(memory_app, ["supersede", "nope", "--body", "x"])
    assert result.exit_code == 1
    assert "not found" in result.stdout
    assert memory.get_store().list_memories(include_tests=True) == []


def test_remember_creates_memory(runner, tmp_memory_db):
    """`remember` is the explicit create path; it writes one active row."""
    result = runner.invoke(memory_app, [
        "remember", "prefer concise updates", "--kind", "preference",
        "--title", "concise"])
    assert result.exit_code == 0
    assert "remembered" in result.stdout
    rows = memory.get_store().list_memories(include_tests=True)
    assert len(rows) == 1
    assert rows[0]["kind"] == "preference" and rows[0]["title"] == "concise"


def test_remember_invalid_kind_errors_and_writes_nothing(runner, tmp_memory_db):
    result = runner.invoke(memory_app, [
        "remember", "x", "--kind", "bogus"])
    assert result.exit_code == 1
    assert "must be one of" in result.stdout
    assert memory.get_store().list_memories(include_tests=True) == []


def test_remember_files_under_meta_root(runner, tmp_memory_db):
    """`--topic preferences` links the new memory to the global meta-root, so
    it is reachable by the tree-nav browse leg."""
    result = runner.invoke(memory_app, [
        "remember", "always use the .venv interpreter", "--kind", "preference",
        "--topic", "preferences"])
    assert result.exit_code == 0
    assert "filed under topic preferences" in result.stdout
    store = memory.get_store()
    mid = store.list_memories(include_tests=True)[0]["id"]
    assert "preferences" in store.authoritative_topics_of(mid)


def test_remember_applies_tags_and_importance(runner, tmp_memory_db):
    """`--tags` is split + trimmed (empties dropped) and `--importance` is
    stored as given — the plumbing the round-trip otherwise leaves uncovered."""
    result = runner.invoke(memory_app, [
        "remember", "x", "--kind", "fact", "--importance", "0.9",
        "--tags", "env, tooling ,"])
    assert result.exit_code == 0
    row = memory.get_store().list_memories(include_tests=True)[0]
    assert row["importance"] == 0.9
    assert row["tags"] == ["env", "tooling"]


def test_remember_unknown_topic_errors_before_writing(runner, tmp_memory_db):
    """An unknown --topic exits non-zero and creates no memory (validated
    before the write)."""
    result = runner.invoke(memory_app, [
        "remember", "x", "--kind", "fact", "--topic", "no-such-node"])
    assert result.exit_code == 1
    assert "no topic node" in result.stdout
    assert memory.get_store().list_memories(include_tests=True) == []


def test_consolidate_skills_empty_is_clean(runner, tmp_memory_db):
    """The command registers and reports cleanly when nothing is promotable."""
    result = runner.invoke(memory_app, ["consolidate-skills"])
    assert result.exit_code == 0
    assert "no skill-memories over the promotion bar" in result.stdout
