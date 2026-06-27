"""Tests for notify-on-finish proposal completion + the stranded-run reaper.

Covers the swap from a blocking-timeout wait to an agent-emitted finish
signal: `finish_proposal_run` ingests + is idempotent, an invalid output
fails the run explicitly, the server-side runner skips a re-ingest once the
agent has signalled, and a run whose watcher died is reaped to `failed`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lib.topics import bootstrap, utc_now
from lib.topics.proposal_external import (
    _proposal_wait_timeout, load_status, write_status,
)
from lib.topics.proposals import (
    finish_proposal_run,
    list_proposal_revisions,
    load_proposal,
    load_proposal_status,
    reap_stranded_proposal_runs,
)
from lib.topics import TopicGraphError


def _commit_service(repo):
    (repo / "service").mkdir(exist_ok=True)
    (repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(repo), "add", "."])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", "service"])
    bootstrap(repo)


_PAYLOAD = {
    "topics": [{
        "id": "service", "label": "Service", "aliases": [],
        "intent": "Curated context for Service.", "status": "active",
        "refs": [{"path": "service/api.py", "role": "implementation"}],
        "edges": [], "commands": [], "include_globs": ["service/**"],
        "exclude_globs": [], "evidence_paths": ["service/api.py"],
    }],
    "notes": [], "wiki": "# Service\n",
}


def _seed_run(repo, run_id="run1", *, state="running"):
    out_dir = repo / ".regin/topics/proposals" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    write_status(out_dir, {
        "state": state, "trace_id": f"topic-proposal-{run_id}",
        "agent": "fake", "started_at": utc_now(), "completed_at": None,
        "error": None, "pid": None, "prompt_template_ids": [],
    })
    return out_dir


def _write_temp_output(out_dir, payload=None):
    temp = out_dir / ".tmp" / "agent-output.json"
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps(payload or _PAYLOAD))
    return temp


def _backdate_run(run_id, ts="2000-01-01T00:00:00"):
    """Age a run's activity timestamps so the grace window has elapsed —
    write_status always restamps updated_at to now, so the reaper's
    staleness can only be exercised by editing the row directly."""
    from lib.orm import SessionLocal
    from lib.orm.models import ProposalRun

    with SessionLocal() as s:
        run = s.get(ProposalRun, run_id)
        run.updated_at = ts
        run.started_at = ts
        s.commit()


def test_finish_ingests_and_marks_completed(fake_git_repo, tmp_db):
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)

    result = finish_proposal_run(fake_git_repo, "run1")

    assert result == {"proposal_id": "run1", "state": "completed", "ingested": True}
    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "completed"
    assert status["agent_signaled"] is True
    proposal = load_proposal(fake_git_repo, "run1")
    assert proposal["topics"][0]["id"] == "service"
    assert (out_dir / "agent-output.json").exists()  # temp copied to canonical


def test_finish_is_idempotent(fake_git_repo, tmp_db):
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)

    finish_proposal_run(fake_git_repo, "run1")
    second = finish_proposal_run(fake_git_repo, "run1")

    assert second["ingested"] is False
    assert second["state"] == "completed"
    # Exactly one revision — the second call did not re-persist.
    assert len(list_proposal_revisions(fake_git_repo, "run1")) == 1


class _InlineThread:
    """Run the job synchronously so the regenerate kickoff is deterministic."""

    def __init__(self, target=None, kwargs=None, daemon=None, **_):
        self._target = target
        self._kwargs = kwargs or {}

    def start(self):
        self._target(**self._kwargs)


def test_real_regenerate_kickoff_clears_signal_and_appends_revision(
    fake_git_repo, tmp_db, monkeypatch,
):
    """Drive the REAL regenerate kickoff (`start_external_regenerate_run`),
    not a hand-cleared flag. The prior completed run left `agent_signaled=True`;
    the kickoff must clear it, or the agent's `proposal-finish` short-circuits
    as already-ingested and no `regenerated` revision is ever appended."""
    from lib.topics.proposals import start_external_regenerate_run

    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)
    finish_proposal_run(fake_git_repo, "run1")  # revision 1, kind "generated"
    assert load_status(out_dir)["agent_signaled"] is True

    ingest_results = []

    def agent_then_finish(*, repo, out_dir, proposal_id, topic_request=None,
                          agent=None, prior_draft=None, prompt_templates=None):
        # Mimic the drafting agent: write fresh output, then call proposal-finish.
        _write_temp_output(Path(out_dir))
        ingest_results.append(finish_proposal_run(repo, proposal_id))
        proposal = load_proposal(repo, proposal_id)
        wiki = (Path(out_dir) / "wiki.md").read_text()
        return proposal, wiki

    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs._draft_proposal", agent_then_finish)
    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs.threading.Thread", _InlineThread)

    start_external_regenerate_run(fake_git_repo, "run1")

    # The agent's finish actually ingested (flag was cleared at kickoff).
    assert ingest_results and ingest_results[0]["ingested"] is True
    revisions = list_proposal_revisions(fake_git_repo, "run1")
    assert len(revisions) == 2  # appended, not overwritten or no-op'd
    latest = max(revisions, key=lambda r: r["revision_number"])
    assert latest["kind"] == "regenerated"
    first = min(revisions, key=lambda r: r["revision_number"])
    assert first["kind"] == "generated"


def _set_run_proposal_status(run_id, status):
    """Force the run-level review state, simulating a prior apply."""
    from lib.orm import SessionLocal
    from lib.orm.models import ProposalRun

    with SessionLocal() as s:
        run = s.get(ProposalRun, run_id)
        meta = json.loads(run.metadata_json or "{}")
        meta["proposal_status"] = status
        run.metadata_json = json.dumps(meta)
        s.commit()


def test_regenerate_after_applied_resets_run_status_to_pending(
    fake_git_repo, tmp_db, monkeypatch,
):
    """Regression: regenerating an already-applied proposal must leave the
    run-level proposal_status at pending_review so the fresh draft is
    appliable. write_status used to round-trip the stale 'applied' — spread
    into the status dict by _run_to_status_dict — back over the pending_review
    that the finish ingest had just written, stranding the new draft as
    un-appliable while the header badge still read 'applied'."""
    from lib.topics.proposals import start_external_regenerate_run

    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)
    finish_proposal_run(fake_git_repo, "run1")  # revision 1 (generated)

    # Simulate a prior apply: run-level review state advanced to "applied".
    _set_run_proposal_status("run1", "applied")
    assert load_proposal(fake_git_repo, "run1")["status"] == "applied"

    def agent_then_finish(*, repo, out_dir, proposal_id, topic_request=None,
                          agent=None, prior_draft=None, prompt_templates=None):
        _write_temp_output(Path(out_dir))
        finish_proposal_run(repo, proposal_id)
        proposal = load_proposal(repo, proposal_id)
        wiki = (Path(out_dir) / "wiki.md").read_text()
        return proposal, wiki

    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs._draft_proposal", agent_then_finish)
    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs.threading.Thread", _InlineThread)

    start_external_regenerate_run(fake_git_repo, "run1")

    # The freshly regenerated draft must be appliable again.
    assert load_proposal(fake_git_repo, "run1")["status"] == "pending_review"


def test_finish_with_invalid_output_fails_run(fake_git_repo, tmp_db):
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir, {"topics": [], "wiki": ""})  # empty wiki → invalid

    with pytest.raises(TopicGraphError, match="invalid agent output"):
        finish_proposal_run(fake_git_repo, "run1")

    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "failed"
    assert status["error"]


def test_finish_unknown_run_raises(fake_git_repo, tmp_db):
    _commit_service(fake_git_repo)
    with pytest.raises(TopicGraphError, match="no proposal run to finish"):
        finish_proposal_run(fake_git_repo, "ghost")


def test_wait_timeout_zero_means_unbounded(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.topic_evolution, "proposal_run_timeout_seconds", 0)
    assert _proposal_wait_timeout() is None
    monkeypatch.setattr(settings.topic_evolution, "proposal_run_timeout_seconds", 120)
    assert _proposal_wait_timeout() == 120


def test_reaper_fails_stranded_run(fake_git_repo, tmp_db):
    _commit_service(fake_git_repo)
    _seed_run(fake_git_repo)
    # Backdate so it's past the grace window with no live subprocess.
    _backdate_run("run1")

    reaped = reap_stranded_proposal_runs(fake_git_repo)

    assert reaped == 1
    assert load_proposal_status(fake_git_repo, "run1")["state"] == "failed"


def test_reaper_skips_recent_and_signaled_runs(fake_git_repo, tmp_db):
    _commit_service(fake_git_repo)
    # Recent run (updated_at = now) — within grace, not reaped.
    _seed_run(fake_git_repo, "recent")
    # Signaled run, even if old — not reaped (signal beats staleness).
    out_dir = _seed_run(fake_git_repo, "signaled")
    status = load_status(out_dir)
    status["agent_signaled"] = True
    write_status(out_dir, status)
    _backdate_run("signaled")

    assert reap_stranded_proposal_runs(fake_git_repo) == 0
    assert load_proposal_status(fake_git_repo, "recent")["state"] == "running"
    assert load_proposal_status(fake_git_repo, "signaled")["state"] == "running"
