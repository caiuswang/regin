"""Unit tests for cli.deps.require_db decorator."""

from __future__ import annotations

import pytest

from cli import deps


def test_require_db_passes_through_when_db_exists(monkeypatch):
    """With db_exists() → True, the wrapped function runs normally."""
    monkeypatch.setattr(deps, "db_exists", lambda: True)

    @deps.require_db
    def cmd(args, flag=None):
        return ("ok", args, flag)

    assert cmd("args-value", flag="x") == ("ok", "args-value", "x")


def test_require_db_exits_when_db_missing(monkeypatch, capsys):
    """Missing DB → prints the init hint and SystemExit(1)."""
    monkeypatch.setattr(deps, "db_exists", lambda: False)

    @deps.require_db
    def cmd(args):
        return "should not run"

    with pytest.raises(SystemExit) as ei:
        cmd(None)
    assert ei.value.code == 1
    captured = capsys.readouterr()
    assert "init" in captured.out.lower()
    assert "database" in captured.out.lower()


def test_require_db_preserves_function_metadata(monkeypatch):
    """@functools.wraps keeps the original name + docstring."""
    @deps.require_db
    def do_thing(args):
        """the real docstring"""

    assert do_thing.__name__ == "do_thing"
    assert do_thing.__doc__ == "the real docstring"


def test_require_db_forwards_extra_args(monkeypatch):
    monkeypatch.setattr(deps, "db_exists", lambda: True)

    @deps.require_db
    def cmd(args, *extra, **kw):
        return (args, extra, kw)

    assert cmd("a", "b", "c", opt=1) == ("a", ("b", "c"), {"opt": 1})
