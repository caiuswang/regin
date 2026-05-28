"""Unit tests for cli.output helpers.

Captures _stdout / _stderr via monkey-patch so the assertions are
deterministic without shelling out.
"""

from __future__ import annotations

import io

from cli import output


def test_echo_writes_to_stdout_sink(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(output, "_stdout", buf)
    output.echo("hello", "world")
    assert buf.getvalue() == "hello world\n"


def test_error_writes_to_stderr_sink(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(output, "_stderr", buf)
    output.error("oh no")
    assert buf.getvalue() == "oh no\n"


def test_table_renders_aligned_columns(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(output, "_stdout", buf)
    output.table(
        [("alice", 30), ("bob", 100)],
        headers=("name", "score"),
    )
    lines = buf.getvalue().splitlines()
    assert lines[0] == "name   score"
    assert lines[1] == "-----  -----"
    assert "alice" in lines[2]
    assert "bob" in lines[3]
    assert "100" in lines[3]


def test_table_no_headers_just_data(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(output, "_stdout", buf)
    output.table([("a", "b"), ("ccc", "dd")])
    assert buf.getvalue() == "a    b \nccc  dd\n"


def test_table_empty_rows_no_output(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(output, "_stdout", buf)
    output.table([])
    assert buf.getvalue() == ""
