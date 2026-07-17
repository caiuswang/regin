"""Phase 1 — LLM-written review notes for proposal runs.

A completed run gets a single `review_note` feedback thread (created_by=agent)
carrying a REGENERATE/ACCEPT/DISMISS recommendation; the note rides the
existing feedback machinery into the next regenerate. Gated on
`auto_review_notes` (off by default) and best-effort. The reviewer LLM is
stubbed so tests never spawn an agent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.settings import settings
from lib.topics import TopicGraphError
from lib.topics.content_drift import emit_refresh_proposal
from lib.topics.core import write_split_graph
from lib.topics.proposal_drafting import format_review_feedback_for_prompt
from lib.topics.proposal_review import (
    _build_prompt,
    _finish_block,
    _parse_recommendation,
    _review_finish_command,
    _review_output_path,
    finish_review_note,
    generate_review_note,
    maybe_generate_review_note,
)
from lib.topics.proposals import list_proposal_feedback_threads
from lib.topics.snapshots import resolve_or_create_repo


class _StubLLM:
    """LLMProvider stub: returns a canned completion regardless of prompt."""

    def __init__(self, answer):
        self._answer = answer

    def complete(self, prompt, *, max_tokens=1024, cwd=None):
        del prompt, max_tokens, cwd
        return self._answer


class _RaisingLLM:
    def complete(self, prompt, *, max_tokens=1024, cwd=None):
        del prompt, max_tokens, cwd
        raise RuntimeError("boom")


class _StubReviewer:
    """Reviewer stub for the async path: yields a launchable spawn spec so
    `start_review_run` proceeds (the actual spawn is stubbed separately)."""

    def spawn_spec(self, *, surface_id=None):
        from lib.memory.adapters import SpawnSpec
        return SpawnSpec(argv=["true"], timeout=1, cwd=None, surface_id=None)


class _RaisingReviewer:
    def spawn_spec(self, *, surface_id=None):
        raise RuntimeError("boom")


def _fake_spawn(answer):
    """A stand-in for the `_spawn_review_agent` seam: simulates the review
    agent by writing `answer` to the output file and running the finish ingest
    synchronously (no real subprocess, no thread)."""
    def spawn(repo, proposal_id, spec, prompt):
        del spec, prompt
        _review_output_path(repo, proposal_id).write_text(answer)
        finish_review_note(repo, proposal_id, source="test")
    return spawn


def _seed_review_output(repo: Path, pid: str, answer: str) -> None:
    path = _review_output_path(repo, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(answer)


def _make_proposal(repo: Path) -> str:
    """Register the repo + mint one real proposal run; returns its id."""
    (repo / "a.py").write_text("x\n")
    write_split_graph(repo, {"version": 1, "repo": repo.name,
                             "updated_at": "2026-01-01T00:00:00Z", "topics": {
        "t1": {
            "label": "T", "intent": "t", "status": "active", "aliases": [],
            "refs": [{"path": "a.py"}], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": [],
        },
    }})
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
    # Structured recommendation for the UI badge (not just prose in the body).
    assert thread["metadata"]["recommendation"] == "REGENERATE"
    # Persisted: exactly one review_note thread on the run.
    assert len(_review_notes(fake_git_repo, pid)) == 1


def test_generate_review_note_passes_repo_path_as_cwd(fake_git_repo):
    """The reviewer must inspect the proposal's own repo, not wherever the
    host process happens to be running from (regression: it used to default
    to `cwd=None`, so a proposal for another repo got reviewed against the
    wrong tree)."""
    pid = _make_proposal(fake_git_repo)
    seen = {}

    class _SpyLLM:
        def complete(self, prompt, *, max_tokens=1024, cwd=None):
            del prompt, max_tokens
            seen["cwd"] = cwd
            return "RECOMMENDATION: ACCEPT"

    generate_review_note(fake_git_repo, pid, llm=_SpyLLM())
    assert seen["cwd"] == fake_git_repo


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
    """A reviewer that raises while launching must not propagate — the run is
    already done."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer", lambda: _RaisingReviewer(),
    )
    pid = _make_proposal(fake_git_repo)
    assert maybe_generate_review_note(fake_git_repo, pid) is False
    assert _review_notes(fake_git_repo, pid) == []


def test_maybe_review_note_starts_job_when_enabled(fake_git_repo, monkeypatch):
    """The gated trigger now *starts the detached job* (returns True) and the
    reviewer submits its note via the finish path — here simulated by the spawn
    seed."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer", lambda: _StubReviewer(),
    )
    monkeypatch.setattr(
        "lib.topics.proposal_review._spawn_review_agent",
        _fake_spawn("ok\nRECOMMENDATION: ACCEPT"),
    )
    pid = _make_proposal(fake_git_repo)
    assert maybe_generate_review_note(fake_git_repo, pid) is True
    assert len(_review_notes(fake_git_repo, pid)) == 1


def test_maybe_review_note_bounced_on_shared_primary(fake_git_repo, monkeypatch):
    """Pre-review gate: a draft that introduces a shared-primary-ref boundary
    violation is bounced with an auto REGENERATE note and the agent reviewer is
    never spawned."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    pid = _make_proposal(fake_git_repo)  # approved graph: t1 owns a.py primary
    # A NEW draft topic also claims a.py primary → introduced collision.
    monkeypatch.setattr(
        "lib.topics.proposals.load_proposal",
        lambda repo, p: {"topics": [
            {"id": "t2", "label": "T2", "intent": "x", "status": "active",
             "refs": [{"path": "a.py"}]},
        ]},
    )
    spawned = {"called": False}
    monkeypatch.setattr(
        "lib.topics.proposal_review.start_review_run",
        lambda *a, **k: spawned.__setitem__("called", True) or True,
    )
    assert maybe_generate_review_note(fake_git_repo, pid) is True
    assert spawned["called"] is False              # agent NOT spawned
    notes = _review_notes(fake_git_repo, pid)
    assert len(notes) == 1
    assert notes[0]["metadata"]["recommendation"] == "REGENERATE"
    assert "a.py" in notes[0]["comments"][0]["body"]


def test_maybe_review_note_spawns_when_draft_clean(fake_git_repo, monkeypatch):
    """A clean draft (no introduced errors/collisions) passes the gate and the
    agent reviewer is started as before."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    pid = _make_proposal(fake_git_repo)  # refresh of t1, no new collision
    spawned = {"called": False}
    monkeypatch.setattr(
        "lib.topics.proposal_review.start_review_run",
        lambda *a, **k: spawned.__setitem__("called", True) or True,
    )
    assert maybe_generate_review_note(fake_git_repo, pid) is True
    assert spawned["called"] is True               # agent spawned, not bounced
    assert _review_notes(fake_git_repo, pid) == []  # no auto note written


def test_maybe_review_note_no_note_when_no_agent(fake_git_repo, monkeypatch):
    """Gate on but no external agent configured → nothing to launch, no note,
    no crash (spawn_spec None)."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)

    class _Unconfigured:
        def spawn_spec(self, *, surface_id=None):
            return None
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer", lambda: _Unconfigured(),
    )
    pid = _make_proposal(fake_git_repo)
    assert maybe_generate_review_note(fake_git_repo, pid) is False
    assert _review_notes(fake_git_repo, pid) == []


# ── finish_review_note (notify-on-finish ingest) ────────────────


def test_finish_review_note_writes_from_output_file(fake_git_repo):
    pid = _make_proposal(fake_git_repo)
    _seed_review_output(fake_git_repo, pid, "Solid draft.\nRECOMMENDATION: DISMISS")
    thread = finish_review_note(fake_git_repo, pid, source="agent")
    assert thread is not None
    assert thread["kind"] == "review_note"
    assert thread["created_by"] == "agent"
    assert thread["metadata"]["recommendation"] == "DISMISS"
    assert len(_review_notes(fake_git_repo, pid)) == 1


def test_finish_review_note_idempotent(fake_git_repo):
    """A resumed session re-running the finish command can't double-post."""
    pid = _make_proposal(fake_git_repo)
    _seed_review_output(fake_git_repo, pid, "RECOMMENDATION: ACCEPT")
    assert finish_review_note(fake_git_repo, pid) is not None
    assert finish_review_note(fake_git_repo, pid) is None
    assert len(_review_notes(fake_git_repo, pid)) == 1


def test_finish_review_note_missing_output_raises(fake_git_repo):
    """Signalled with no usable output → visible failure, no empty note."""
    pid = _make_proposal(fake_git_repo)
    with pytest.raises(TopicGraphError):
        finish_review_note(fake_git_repo, pid, source="agent")
    assert _review_notes(fake_git_repo, pid) == []


# ── prompt wiring: finish command baked in (resume-survivable) ──


def test_async_prompt_bakes_finish_command_and_output_path(fake_git_repo):
    """The async prompt = the (editable) review body + a submit block appended
    OUTSIDE render_surface, so the literal finish command + output path survive
    even a stored/edited review-prompt row."""
    proposal = {"topics": [{"id": "t1", "intent": "x", "refs": [{"path": "a.py"}]}]}
    prompt = _build_prompt(proposal, "") + _finish_block(fake_git_repo, "pX")
    assert "<submit>" in prompt
    assert str(_review_output_path(fake_git_repo, "pX")) in prompt
    assert "review-finish" in prompt
    # the literal finish command (not just the substring) is present verbatim
    assert _review_finish_command(fake_git_repo, "pX") in prompt


def test_finish_block_survives_stored_prompt_row(fake_git_repo, monkeypatch):
    """Regression: render_surface prefers a stored/edited prompt row over the
    registry default. A stored review body without any placeholder must still
    yield an async prompt carrying the submit block + finish command — because
    it is appended outside render_surface, not interpolated into the body."""
    monkeypatch.setattr(
        "lib.prompts.resolve._stored_body",
        lambda surface_id: "OLD CUSTOM REVIEW BODY — no placeholders here.",
    )
    proposal = {"topics": [{"id": "t1", "intent": "x", "refs": []}]}
    prompt = _build_prompt(proposal, "") + _finish_block(fake_git_repo, "pX")
    # the editable body is the stored (custom) one …
    assert "OLD CUSTOM REVIEW BODY" in prompt
    # … but the mechanical hand-off is still present
    assert "<submit>" in prompt
    assert _review_finish_command(fake_git_repo, "pX") in prompt


def test_sync_prompt_has_no_finish_block(fake_git_repo):
    """The synchronous (stdout-parsed) manual path must not tell the agent to
    self-submit — that would double-post."""
    proposal = {"topics": [{"id": "t1", "intent": "x", "refs": []}]}
    assert "<submit>" not in _build_prompt(proposal, "")


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
    """With the gate on, a real create_proposal_run starts the review job via
    the wired trigger in core_io — no manual call. The spawn seam simulates the
    reviewer submitting through the finish path."""
    monkeypatch.setattr(settings.topic_evolution, "auto_review_notes", True)
    monkeypatch.setattr(
        "lib.memory.adapters.resolve_proposal_reviewer", lambda: _StubReviewer(),
    )
    monkeypatch.setattr(
        "lib.topics.proposal_review._spawn_review_agent",
        _fake_spawn("looks good\nRECOMMENDATION: ACCEPT"),
    )
    resolve_or_create_repo(str(fake_git_repo))
    from lib.topics.proposals import create_proposal_run

    create_proposal_run(fake_git_repo, run_id="p-auto")
    notes = _review_notes(fake_git_repo, "p-auto")
    assert len(notes) == 1
    assert notes[0]["created_by"] == "agent"
    assert "recommendation: ACCEPT" in notes[0]["comments"][0]["body"]
