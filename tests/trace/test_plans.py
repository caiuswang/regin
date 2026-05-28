"""Unit tests for lib.plans.

Monkey-patches the PLANS_DIR module attribute to redirect read paths
to a pytest tmp dir so the test doesn't depend on the user's real
~/.claude/plans.
"""

from __future__ import annotations

import os

from lib.trace import plans as plans_module
from lib.trace.plans import _extract_title, get_plan, list_plans


# ── _extract_title (pure) ────────────────────────────────────

def test_extract_title_from_first_h1():
    assert _extract_title("# My Plan\n\nstuff") == "My Plan"


def test_extract_title_strips_whitespace():
    assert _extract_title("#   Padded Title   ") == "Padded Title"


def test_extract_title_ignores_h2():
    assert _extract_title("## Subheading\n\nno h1") == "Untitled Plan"


def test_extract_title_missing_falls_back():
    assert _extract_title("just text") == "Untitled Plan"


# ── list_plans ───────────────────────────────────────────────

def test_list_plans_missing_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "_plans_dirs", lambda: [])
    assert list_plans() == []


def test_list_plans_orders_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "_plans_dirs", lambda: [("claude", str(tmp_path))])
    (tmp_path / "older.md").write_text("# Older\n")
    (tmp_path / "newer.md").write_text("# Newer\n")
    # Force a timestamp order — make older strictly older.
    os.utime(tmp_path / "older.md", (1, 1))
    os.utime(tmp_path / "newer.md", (1_000_000_000, 1_000_000_000))
    plans = list_plans()
    assert [p["filename"] for p in plans] == ["newer.md", "older.md"]
    assert plans[0]["title"] == "Newer"


def test_list_plans_skips_non_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "_plans_dirs", lambda: [("claude", str(tmp_path))])
    (tmp_path / "plan.md").write_text("# P\n")
    (tmp_path / "README.txt").write_text("ignored")
    plans = list_plans()
    assert [p["filename"] for p in plans] == ["plan.md"]


def test_list_plans_includes_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "_plans_dirs", lambda: [("claude", str(tmp_path))])
    (tmp_path / "plan.md").write_text("# Real Title\n\nbody\n")
    plans = list_plans()
    assert plans[0]["title"] == "Real Title"
    assert plans[0]["size"] > 0
    assert "T" in plans[0]["updated_at"]  # ISO 8601


# ── get_plan ─────────────────────────────────────────────────

def test_get_plan_returns_content(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "_plans_dirs", lambda: [("claude", str(tmp_path))])
    body = "# Title\n\nhello world"
    (tmp_path / "plan.md").write_text(body)
    result = get_plan("plan.md")
    assert result is not None
    assert result["content"] == body
    assert result["title"] == "Title"


def test_get_plan_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "_plans_dirs", lambda: [("claude", str(tmp_path))])
    assert get_plan("nothere.md") is None


def test_get_plan_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(plans_module, "_plans_dirs", lambda: [("claude", str(tmp_path))])
    assert get_plan("../etc/passwd") is None
    assert get_plan("a/b.md") is None
    assert get_plan("a\\b.md") is None
