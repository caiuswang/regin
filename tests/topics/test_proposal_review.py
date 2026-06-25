"""Phase 1 — LLM-written review notes for proposal runs.

A completed run gets a single `review_note` feedback thread (created_by=agent)
carrying a REGENERATE/ACCEPT/DISMISS recommendation; the note rides the
existing feedback machinery into the next regenerate. Gated on
`auto_review_notes` (off by default) and best-effort. The reviewer LLM is
stubbed so tests never spawn an agent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.settings import settings
from lib.topics.content_drift import emit_refresh_proposal
from lib.topics.core import topic_path
from lib.topics.proposal_drafting import format_review_feedback_for_prompt
from lib.topics.proposal_review import (
    _parse_recommendation,
    generate_review_note,
    maybe_generate_review_note,
)
from lib.topics.proposals import list_proposal_feedback_threads
from lib.topics.snapshots import resolve_or_create_repo


class _StubLLM:
    """LLMProvider stub: returns a canned completion regardless of prompt."""

    def __init__(self, answer):
        self._answer = answer

    def complete(self, prompt, *, max_tokens=1024):
        del prompt, max_tokens
        return self._answer


class _RaisingLLM:
    def complete(self, prompt, *, max_tokens=1024):
        del prompt, max_tokens
        raise RuntimeError("boom")


def _make_proposal(repo: Path) -> str:
    """Register the repo + mint one real proposal run; returns its id."""
    (repo / "a.py").write_text("x\n")
    p = topic_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "repo": repo.name, "topics": {
        "t1": {
            "label": "T", "intent": "t", "status": "active", "aliases": [],
            "refs": [{"path": "a.py"}], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": [],
        },
    }}))
    resolve_or_create_repo(str(repo))
    return emit_refresh_proposal(repo, "t1", ["a.py"])


def _review_notes(repo: Path, pid: str) -> list[dict]:
    return [t for t in list_proposal_feedback_threads(repo, pid)
            if t["kind"] == "review_note"]


# ── recommendation parsing ──────────────────────────────────────


@pytest.mark.parametrize("answer,expected", [
    ("blah\nRECOMMENDATION: REGENERATE", "REGENERATE"),
    ("RECOMMENDATION: accept", "ACCEPT"),
    ("RECOMMENDATION = DISMISS", "DISMISS"),
    ("free text mentioning regenerate somewhere", "REGENERATE"),
    ("nothing structured here", "ACCEPT"),   # neutral default
])
def test_parse_recommendation(answer, expected):
    assert _parse_recommendation(answer) == expected


# ── generate_review_note (manual / ungated) ─────────────────────


def test_generate_review_note_creates_agent_thread(fake_git_repo):
    pid = _make_proposal(fake_git_repo)
    thread = generate_review_note(
        fake_git_repo, pid,
        llm=_StubLLM("Coverage looks thin.\nRECOMMENDATION: REGENERATE"),
    )
    assert thread is not None
    assert thread["kind"] == "review_note"
    assert thread["created_by"] == "agent"
    assert thread["resolution_state"] == "open"
    body = thread["comments"][0]["body"]
    assert "recommendation: REGENERATE" in body
    # Persisted: exactly one review_note thread on the run.
    assert len(_review_notes(fake_git_repo, pid)) == 1


def test_generate_review_note_none_when_llm_silent(fake_git_repo):
    """No agent output → no note (best-effort), and nothing persisted."""
    pid = _make_proposal(fake_git_repo)
    assert generate_review_note(fake_git_repo, pid, llm=_StubLLM(None)) is None
    assert _review_notes(fake_git_repo, pid) == []


# ── maybe_generate_review_note (gated trigger) ──────────────────


def test_maybe_review_note_gated_off_by_default(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", False)
    # Even with a working reviewer wired in, the gate must short-circuit.
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer",
        lambda: _StubLLM("RECOMMENDATION: ACCEPT"),
    )
    pid = _make_proposal(fake_git_repo)
    assert maybe_generate_review_note(fake_git_repo, pid) is False
    assert _review_notes(fake_git_repo, pid) == []


def test_maybe_review_note_best_effort_on_failure(fake_git_repo, monkeypatch):
    """A reviewer that raises must not propagate — the run is already done."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer", lambda: _RaisingLLM(),
    )
    pid = _make_proposal(fake_git_repo)
    assert maybe_generate_review_note(fake_git_repo, pid) is False
    assert _review_notes(fake_git_repo, pid) == []


def test_maybe_review_note_creates_when_enabled(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer",
        lambda: _StubLLM("ok\nRECOMMENDATION: ACCEPT"),
    )
    pid = _make_proposal(fake_git_repo)
    assert maybe_generate_review_note(fake_git_repo, pid) is True
    assert len(_review_notes(fake_git_repo, pid)) == 1


# ── carry-forward into the next run (the existing rail) ──────────


def test_review_note_carried_into_regenerate_prompt(fake_git_repo):
    """An open agent review note is eligible for, and formatted into, the
    regenerate prompt by the existing feedback machinery."""
    pid = _make_proposal(fake_git_repo)
    generate_review_note(
        fake_git_repo, pid,
        llm=_StubLLM("Missing the cron path.\nRECOMMENDATION: REGENERATE"),
    )
    open_threads = [t for t in list_proposal_feedback_threads(fake_git_repo, pid)
                    if t["resolution_state"] == "open"]
    prompt = format_review_feedback_for_prompt(open_threads)
    assert "Review feedback to address" in prompt
    assert "recommendation: REGENERATE" in prompt


# ── end-to-end: the sync create-run trigger ─────────────────────


def test_auto_review_note_on_create_run(stub_proposal_provider, fake_git_repo, monkeypatch):
    """With the gate on, a real create_proposal_run mints the note via the
    wired trigger in core_io — no manual call."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer",
        lambda: _StubLLM("looks good\nRECOMMENDATION: ACCEPT"),
    )
    resolve_or_create_repo(str(fake_git_repo))
    from lib.topics.proposals import create_proposal_run

    create_proposal_run(fake_git_repo, run_id="p-auto")
    notes = _review_notes(fake_git_repo, "p-auto")
    assert len(notes) == 1
    assert notes[0]["created_by"] == "agent"
    assert "recommendation: ACCEPT" in notes[0]["comments"][0]["body"]
