"""Unit tests for lib.trace.plan_context.

Redirects PLAN_STATE_DIR so tests don't trample the user's real
~/.claude/traces state.
"""

from __future__ import annotations

import json
import os

import pytest

from lib.trace import plan_context


@pytest.fixture
def tmp_plan_dir(tmp_path, monkeypatch):
    d = tmp_path / "plan-state"
    d.mkdir()
    monkeypatch.setattr(plan_context, "PLAN_STATE_DIR", str(d))
    return d


# ── enter_plan ───────────────────────────────────────────────

def test_enter_plan_creates_state_file(tmp_plan_dir):
    state = plan_context.enter_plan("sess-a", "my-plan.md")
    assert state["plan_filename"] == "my-plan.md"
    assert state["draft_completed"] is False
    assert len(state["session_span_id"]) == 16
    assert len(state["draft_span_id"]) == 16
    assert state["review_span_id"] is None
    assert state["session_parent_id"] is None

    # Persisted to disk.
    f = tmp_plan_dir / "sess-a_plan.json"
    assert f.exists()
    on_disk = json.loads(f.read_text())
    assert on_disk["plan_filename"] == "my-plan.md"


def test_enter_plan_records_parent_id(tmp_plan_dir):
    state = plan_context.enter_plan("sess-b", "plan.md", session_parent_id="parent-span")
    assert state["session_parent_id"] == "parent-span"


def test_enter_plan_overwrites_existing_state(tmp_plan_dir):
    first = plan_context.enter_plan("sess-c", "plan1.md")
    second = plan_context.enter_plan("sess-c", "plan2.md")
    assert first["session_span_id"] != second["session_span_id"]
    assert plan_context.get_plan_state("sess-c")["plan_filename"] == "plan2.md"


# ── get_plan_state ───────────────────────────────────────────

def test_get_plan_state_missing_returns_none(tmp_plan_dir):
    assert plan_context.get_plan_state("nope") is None


def test_get_plan_state_returns_written_state(tmp_plan_dir):
    plan_context.enter_plan("sess-d", "plan.md")
    state = plan_context.get_plan_state("sess-d")
    assert state is not None
    assert state["plan_filename"] == "plan.md"


def test_get_plan_state_malformed_json_returns_none(tmp_plan_dir):
    f = tmp_plan_dir / "broken_plan.json"
    f.write_text("{not valid")
    assert plan_context.get_plan_state("broken") is None


# ── update_span_ids ──────────────────────────────────────────

def test_update_span_ids_patches_draft(tmp_plan_dir):
    plan_context.enter_plan("sess-e", "plan.md")
    state = plan_context.update_span_ids("sess-e", draft_span_id="new-draft")
    assert state["draft_span_id"] == "new-draft"


def test_update_span_ids_missing_session_returns_none(tmp_plan_dir):
    assert plan_context.update_span_ids("nope", draft_span_id="x") is None


def test_update_span_ids_none_leaves_field_unchanged(tmp_plan_dir):
    plan_context.enter_plan("sess-f", "plan.md")
    original = plan_context.get_plan_state("sess-f")
    state = plan_context.update_span_ids("sess-f")  # no args → no changes
    assert state["session_span_id"] == original["session_span_id"]
    assert state["draft_span_id"] == original["draft_span_id"]


# ── mark_draft_complete ──────────────────────────────────────

def test_mark_draft_complete_flips_flag_and_allocates_review(tmp_plan_dir):
    plan_context.enter_plan("sess-g", "plan.md")
    state = plan_context.mark_draft_complete("sess-g")
    assert state["draft_completed"] is True
    assert state["review_span_id"] is not None
    assert len(state["review_span_id"]) == 16
    assert state["review_start_time"] is not None


def test_mark_draft_complete_missing_session_returns_none(tmp_plan_dir):
    assert plan_context.mark_draft_complete("nope") is None


# ── exit_plan ────────────────────────────────────────────────

def test_exit_plan_removes_state_and_returns_final(tmp_plan_dir):
    plan_context.enter_plan("sess-h", "plan.md")
    final = plan_context.exit_plan("sess-h")
    assert final is not None
    assert final["plan_filename"] == "plan.md"
    # File should be gone.
    assert not (tmp_plan_dir / "sess-h_plan.json").exists()
    # And subsequent reads return None.
    assert plan_context.get_plan_state("sess-h") is None


def test_exit_plan_missing_session_is_noop(tmp_plan_dir):
    # exit_plan on an unknown session returns None and doesn't raise.
    assert plan_context.exit_plan("unknown") is None
