"""Span rows as search documents: body assembly, categories, prefiltering."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib.fulltext.span_docs import (
    category_of, row_to_doc, search_session_spans, span_body,
)


def _row(name, attrs, span_id="sp1", duration_ms=1200, agent_id=None):
    return (span_id, name, json.dumps(attrs), duration_ms, agent_id)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE session_spans (id INTEGER PRIMARY KEY, trace_id TEXT, "
        "span_id TEXT, name TEXT, attributes TEXT, duration_ms INTEGER, "
        "agent_id TEXT)"
    )
    return c


def _insert(conn, trace_id, name, attrs, span_id="sp1", agent_id=None):
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
        "duration_ms, agent_id) VALUES (?,?,?,?,?,?)",
        (trace_id, span_id, name, json.dumps(attrs), 100, agent_id),
    )


def test_body_gathers_every_readable_field_of_an_edit_span():
    body = span_body("tool.Edit", {
        "file_path": "lib/gate.py", "diff": "-old\n+new redis line",
    })
    assert "lib/gate.py" in body
    assert "+new redis line" in body


def test_body_gathers_shell_command_and_both_streams():
    body = span_body("tool.Bash", {
        "command": "pytest -q", "stdout": "3 passed", "stderr": "warn: slow",
    })
    assert "pytest -q" in body and "3 passed" in body and "warn: slow" in body


def test_body_renders_rule_verdicts_with_their_marks():
    body = span_body("rule.check", {"applicable_rules": [
        {"id": "R-114", "violated": True, "summary": "bare except"},
        {"id": "R-2", "violated": False, "summary": "ok"},
    ]})
    assert "✗ R-114  bare except" in body
    assert "✓ R-2  ok" in body


def test_prompt_prefers_the_expanded_text_over_the_collapsed_echo():
    assert span_body("prompt", {"text": "/cmd", "expanded_text": "full body"}) \
        == "full body"


def test_thinking_body_comes_from_thinking_text():
    assert "chain" in span_body("assistant.thinking", {"thinking_text": "a chain"})


@pytest.mark.parametrize("name,expected", [
    ("prompt", "prompt"),
    ("assistant.thinking", "thinking"),
    ("assistant_response", "assistant"),
    ("rule.check", "rule"),
    ("tool.Edit", "edit"),
    ("tool.Write", "edit"),
    ("tool.Bash", "shell"),
    ("tool.Task", "agent"),
    ("tool.Read", "tool"),
    ("session.start", "system"),
])
def test_category_of(name, expected):
    assert category_of(name) == expected


def test_redacted_span_keeps_its_title_but_exposes_no_lines():
    doc = row_to_doc(_row("tool.Read", {"redacted": True, "stdout": "secret"}))
    assert doc.body == "content redacted"
    assert "secret" not in doc.body


def test_compacted_span_is_tagged_in_its_row_meta():
    doc = row_to_doc(_row("tool.Read", {"compacted": True}))
    assert "compacted" in doc.tag


def test_subagent_span_says_so_in_its_crumb():
    doc = row_to_doc(_row("tool.Bash", {"turn_index": 4}, agent_id="ag1"))
    assert "subagent" in doc.crumb and "turn #4" in doc.crumb


def test_unparseable_attributes_degrade_to_an_empty_bag():
    doc = row_to_doc(("sp1", "tool.Bash", "{not json", 10, None))
    assert doc.title == "tool.Bash" and doc.body == ""


def test_search_finds_a_term_that_only_exists_inside_a_diff(conn):
    _insert(conn, "t1", "tool.Edit",
            {"file_path": "a.py", "diff": "+ rateLimit = 5"}, span_id="e1")
    _insert(conn, "t1", "tool.Bash", {"command": "ls"}, span_id="b1")
    hits = search_session_spans(conn, ["t1"], "rateLimit", 10)
    assert [h.doc.id for h in hits] == ["e1"]
    assert hits[0].hits == 1


def test_search_is_scoped_to_the_named_trace_ids(conn):
    _insert(conn, "t1", "tool.Bash", {"command": "redis-cli"}, span_id="a")
    _insert(conn, "t2", "tool.Bash", {"command": "redis-cli"}, span_id="b")
    assert [h.doc.id for h in search_session_spans(conn, ["t1"], "redis", 10)] == ["a"]
    assert len(search_session_spans(conn, ["t1", "t2"], "redis", 10)) == 2


def test_search_matches_on_the_span_name_alone(conn):
    _insert(conn, "t1", "tool.Bash", {"command": "ls"}, span_id="a")
    assert [h.doc.id for h in search_session_spans(conn, ["t1"], "Bash", 10)] == ["a"]


def test_search_matches_a_cjk_query_stored_as_unicode_escapes(conn):
    _insert(conn, "t1", "assistant_response", {"text": "读取 中文 内容"}, span_id="a")
    assert [h.doc.id for h in search_session_spans(conn, ["t1"], "中文", 10)] == ["a"]


def test_search_does_not_treat_a_percent_query_as_a_wildcard(conn):
    _insert(conn, "t1", "tool.Bash", {"command": "coverage 100%"}, span_id="a")
    _insert(conn, "t1", "tool.Bash", {"command": "unrelated"}, span_id="b")
    assert [h.doc.id for h in search_session_spans(conn, ["t1"], "100%", 10)] == ["a"]


def test_search_returns_nothing_without_a_trace_scope(conn):
    _insert(conn, "t1", "tool.Bash", {"command": "redis"}, span_id="a")
    assert search_session_spans(conn, [], "redis", 10) == []


def test_sub_names_the_command_when_a_shell_span_has_no_file(conn):
    doc = row_to_doc(_row("tool.Bash", {"command": "pytest -q tests/\nsecond line"}))
    assert "pytest -q tests/" in doc.sub
    assert "second line" not in doc.sub, "one line only — the row is single-line"


def test_sub_prefers_the_file_path_over_the_command():
    doc = row_to_doc(_row("tool.Edit", {"file_path": "a/b.py", "command": "x"}))
    assert "a/b.py" in doc.sub and "x" not in doc.sub


def test_crumb_carries_the_subject_so_it_is_not_just_the_title():
    doc = row_to_doc(_row("tool.Bash", {"command": "make build"}))
    assert doc.crumb.endswith("make build")
    assert doc.title == "tool.Bash", "title is the span NAME (spec §02)"
