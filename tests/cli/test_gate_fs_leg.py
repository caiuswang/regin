"""`recall-ran` must survive the tree walk moving from MCP to the filesystem.

A session that walked `.regin/memory/tree/` with Read/Glob did run the arm;
one that did neither still has to fail, or the anti-skip is decorative.
"""

import json

import pytest

from lib.orm import SessionLocal
from lib.orm.models.trace import SessionSpan
from lib.trace.span_gates import GATES, RECALL_ARM, span_count


def _span(trace_id: str, name: str, **attrs) -> SessionSpan:
    return SessionSpan(trace_id=trace_id, span_id=f"{name}-{trace_id}",
                       name=name, start_time="2026-07-26T10:00:00",
                       attributes=json.dumps(attrs))


@pytest.fixture
def add_spans(tmp_path):
    made = []

    def _add(trace_id, *spans):
        with SessionLocal() as session:
            for s in spans:
                session.add(s)
                made.append(s)
            session.commit()
        return trace_id

    yield _add
    with SessionLocal() as session:
        for s in made:
            obj = session.get(SessionSpan, s.id)
            if obj is not None:
                session.delete(obj)
        session.commit()


def test_filesystem_walk_alone_passes_the_gate(add_spans):
    tid = add_spans("fs-only-trace", _span(
        "fs-only-trace", "tool.Read",
        file_path="/repo/.regin/memory/tree/agent-memory/a-lesson-abc.md"))
    assert span_count(tid, RECALL_ARM) == 1


def test_glob_over_the_tree_passes_the_gate(add_spans):
    tid = add_spans("glob-trace", _span(
        "glob-trace", "tool.Glob", pattern=".regin/memory/tree/**/*.md"))
    assert span_count(tid, RECALL_ARM) == 1


def test_reading_an_unrelated_file_does_not_pass(add_spans):
    """A generic Read must not be mistaken for a memory-tree walk."""
    tid = add_spans("unrelated-trace", _span(
        "unrelated-trace", "tool.Read", file_path="/repo/lib/memory/store.py"))
    assert span_count(tid, RECALL_ARM) == 0


def test_mcp_recall_still_passes(add_spans):
    tid = add_spans("mcp-trace",
                    _span("mcp-trace", "tool.mcp__memory__recall", query="x"))
    assert span_count(tid, RECALL_ARM) == 1


def test_memory_read_passes(add_spans):
    tid = add_spans("read-tool-trace", _span(
        "read-tool-trace", "tool.mcp__memory__memory_read", memory_id="abc"))
    assert span_count(tid, RECALL_ARM) == 1


def test_a_session_that_did_neither_still_fails(add_spans):
    tid = add_spans("skipped-trace",
                    _span("skipped-trace", "tool.Bash", command="ls"))
    assert span_count(tid, RECALL_ARM) == 0


def test_gate_capability_is_now_self_evident():
    """Read/Glob ship in every session, so 0 spans is a genuine skip — the
    INCONCLUSIVE escape hatch no longer applies to this gate."""
    assert GATES["recall-ran"].capability_self_evident is True
