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


def _spy_events(monkeypatch):
    """Capture `events.emit` calls so the notify-on-finish inbox event can be
    asserted without touching the message store."""
    from lib.agent_messages import events

    calls = []
    monkeypatch.setattr(events, "emit", lambda kind, **kw: calls.append((kind, kw)))
    return calls


def test_finish_emits_proposal_ready_event(fake_git_repo, tmp_db, monkeypatch):
    """The agent-signaled ingest is the authoritative completion, so it — not
    only the server-runner exit — must surface the `proposal.ready` inbox
    event. Regression for agent-signaled proposals that finished silently.

    The action link deep-links to the *specific* proposal run, and the card's
    trace_id is the real drafting-agent session (from $CLAUDE_CODE_SESSION_ID)
    so the footer resolves to the actual drafting session, not the wrapper."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "draft-sess-abc")
    calls = _spy_events(monkeypatch)

    finish_proposal_run(fake_git_repo, "run1")

    assert len(calls) == 1
    kind, kw = calls[0]
    assert kind == "proposal.ready"
    assert kw["trace_id"] == "draft-sess-abc"          # real drafting session
    assert kw["key"] == "proposal-ready:run1"
    assert kw["links"][0]["href"].endswith("?tab=proposals&proposal=run1")
    # the captured session id is persisted for the runner path to reuse
    assert load_proposal_status(fake_git_repo, "run1")["agent_trace_id"] == "draft-sess-abc"


def test_finish_falls_back_to_wrapper_trace_without_session_env(
    fake_git_repo, tmp_db, monkeypatch,
):
    """When $CLAUDE_CODE_SESSION_ID is absent, the card falls back to the
    synthetic `topic-proposal-<id>` wrapper trace — never an empty trace_id
    (which `events.emit` would drop)."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    calls = _spy_events(monkeypatch)

    finish_proposal_run(fake_git_repo, "run1")

    assert calls[0][1]["trace_id"] == "topic-proposal-run1"


def test_finish_noop_does_not_re_emit(fake_git_repo, tmp_db, monkeypatch):
    """A second (idempotent) finish is a no-op — it must not re-emit, so the
    inbox card is not needlessly superseded/re-surfaced."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)
    finish_proposal_run(fake_git_repo, "run1")  # first ingest emits

    calls = _spy_events(monkeypatch)
    second = finish_proposal_run(fake_git_repo, "run1")

    assert second["ingested"] is False
    assert calls == []


def test_runner_signaled_exit_does_not_re_notify(fake_git_repo, tmp_db, monkeypatch):
    """Regression: the agent's `proposal-finish` self-ingest is the authoritative
    notifier. When the server-runner exit later observes the *same* signalled
    run, it must NOT re-emit `proposal.ready` — a second emit dedups the inbox
    card via `msg_key` but re-fires the push channels (Feishu/webhook push on
    every `record_message`, supersede included), so the user gets two Feishu
    cards for one proposal. Guards lib/topics/proposal_external.py:_handle_agent_output."""
    from lib.topics import proposal_external as pe
    from lib.topics.proposals import run_control

    class _FakePopen:
        returncode = 0

        def poll(self):
            return 0

    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)

    calls = _spy_events(monkeypatch)
    # Authoritative completion: the agent signals → emits proposal.ready once.
    finish_proposal_run(fake_git_repo, "run1")
    assert [k for k, _ in calls] == ["proposal.ready"]

    # Runner exit later observes the signalled run. Stub the trace-span emits
    # (a different bus, not the inbox event bus) so only notify behaviour is
    # under test; the agent name differs on purpose (the real bug's fingerprint).
    monkeypatch.setattr(pe, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(pe, "_emit_session_end", lambda *a, **k: None)
    run_control.reset("run1")
    ctx = pe._AgentRunContext(
        repo=fake_git_repo, out_dir=out_dir,
        trace_id="topic-proposal-run1", proposal_id="run1", agent="claude-opus",
        before_topic=None,
        temp_output_path=out_dir / ".tmp" / "agent-output.json",
        output_path=out_dir / "agent-output.json",
        stdout_path=out_dir / "stdout.log", stderr_path=out_dir / "stderr.log",
        started=0.0, prompt_templates=None)
    result = pe._handle_agent_output(
        ctx, _FakePopen(), "out", "err", load_status(out_dir))

    # The signalled branch returns the persisted proposal AND fires no 2nd emit.
    assert result[0]["topics"][0]["id"] == "service"
    assert [k for k, _ in calls] == ["proposal.ready"]  # still exactly one


def test_finish_survives_notify_failure(fake_git_repo, tmp_db, monkeypatch):
    """A notify must never break the ingest it announces: if the event emit
    blows up, the run is still ingested and stamped completed."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)

    def boom(*_a, **_k):
        raise RuntimeError("inbox down")

    monkeypatch.setattr(
        "lib.topics.proposal_external.notify_proposal_ready", boom,
    )

    result = finish_proposal_run(fake_git_repo, "run1")

    assert result == {"proposal_id": "run1", "state": "completed", "ingested": True}
    assert load_proposal_status(fake_git_repo, "run1")["state"] == "completed"


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


def test_finish_invalid_output_persists_structured_validation_errors(fake_git_repo, tmp_db):
    """The failed run carries machine-readable `validation_errors` (plus the
    `failed_validation` retry marker) alongside the human `error` string, and
    the raised message doubles as the agent's retry prompt."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir, {"topics": [{"id": "svc"}], "wiki": ""})

    with pytest.raises(TopicGraphError, match="invalid agent output") as excinfo:
        finish_proposal_run(fake_git_repo, "run1")

    message = str(excinfo.value)
    assert "topics[0].label is required" in message
    assert "re-run" in message and "retry" in message
    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "failed"
    assert status["failed_validation"] is True
    assert "topics[0].label is required" in status["validation_errors"]
    assert any("empty wiki" in error for error in status["validation_errors"])


def test_finish_retry_after_validation_failure_completes_and_clears(fake_git_repo, tmp_db):
    """finish → invalid (failed + flag) → agent fixes the output file →
    finish again → completed, with the flag and structured errors cleared."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir, {"topics": [], "wiki": ""})
    with pytest.raises(TopicGraphError):
        finish_proposal_run(fake_git_repo, "run1")
    assert load_proposal_status(fake_git_repo, "run1")["failed_validation"] is True

    _write_temp_output(out_dir)  # the agent fixes its output, then re-signals
    result = finish_proposal_run(fake_git_repo, "run1")

    assert result == {"proposal_id": "run1", "state": "completed", "ingested": True}
    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "completed"
    assert status["agent_signaled"] is True
    assert not status.get("failed_validation")
    assert not status.get("validation_errors")
    assert not status.get("error")
    assert load_proposal(fake_git_repo, "run1")["topics"][0]["id"] == "service"
    assert len(list_proposal_revisions(fake_git_repo, "run1")) == 1


def test_finish_no_retry_for_cancelled_run(fake_git_repo, tmp_db):
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo, state="cancelled")
    _write_temp_output(out_dir)  # valid output must not matter

    result = finish_proposal_run(fake_git_repo, "run1")

    assert result["ingested"] is False
    assert load_proposal_status(fake_git_repo, "run1")["state"] == "cancelled"


def test_finish_no_retry_for_plain_failed_run(fake_git_repo, tmp_db):
    """A run failed for a non-validation reason (reaped, non-zero exit) has no
    `failed_validation` marker — a late finish signal stays a no-op."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    status = load_status(out_dir)
    status.update(state="failed", error="external agent exited with code 2")
    write_status(out_dir, status)
    _write_temp_output(out_dir)

    result = finish_proposal_run(fake_git_repo, "run1")

    assert result["ingested"] is False
    assert load_proposal_status(fake_git_repo, "run1")["state"] == "failed"


def test_finish_no_retry_after_signaled_success(fake_git_repo, tmp_db):
    """A stale `failed_validation` marker never overrides the signaled guard:
    once a run ingested successfully, a re-signal stays a no-op."""
    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir)
    finish_proposal_run(fake_git_repo, "run1")

    result = finish_proposal_run(fake_git_repo, "run1")

    assert result["ingested"] is False
    assert len(list_proposal_revisions(fake_git_repo, "run1")) == 1


def test_runner_invalid_output_persists_validation_errors_and_allows_retry(
    fake_git_repo, tmp_db, monkeypatch,
):
    """The server-runner ingest path (`_handle_agent_output` → `_fail`) writes
    the same structured `validation_errors` + `failed_validation` marker, so
    an agent that outlives the runner can still fix its file and re-signal."""
    from lib.topics import proposal_external as pe

    class _FakePopen:
        returncode = 0

        def poll(self):
            return 0

    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir, {"topics": [{"id": "svc"}], "wiki": ""})
    monkeypatch.setattr(pe, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(pe, "_emit_session_end", lambda *a, **k: None)
    ctx = pe._AgentRunContext(
        repo=fake_git_repo, out_dir=out_dir,
        trace_id="topic-proposal-run1", proposal_id="run1", agent="fake",
        before_topic=pe._read_topic_signature(fake_git_repo),
        temp_output_path=out_dir / ".tmp" / "agent-output.json",
        output_path=out_dir / "agent-output.json",
        stdout_path=out_dir / "stdout.log", stderr_path=out_dir / "stderr.log",
        started=0.0, prompt_templates=None)

    with pytest.raises(TopicGraphError, match="output invalid"):
        pe._handle_agent_output(ctx, _FakePopen(), "done", "", load_status(out_dir))

    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "failed"
    assert status["failed_validation"] is True
    assert "topics[0].label is required" in status["validation_errors"]

    _write_temp_output(out_dir)
    assert finish_proposal_run(fake_git_repo, "run1")["ingested"] is True
    assert not load_proposal_status(fake_git_repo, "run1").get("validation_errors")


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


def _regenerate_reuse_state(run_id, old_ts="2000-01-01T00:00:00"):
    """Model a regenerate that reused an old run id: the *activity* timestamps
    the reaper's cheap list snapshot exposes (`last_activity_at` → the prior
    revision's / started_at) are stale from the previous cycle, but the
    kickoff `write_status` just bumped `updated_at` to now."""
    from lib.orm import SessionLocal
    from lib.orm.models import ProposalRun

    with SessionLocal() as s:
        run = s.get(ProposalRun, run_id)
        run.started_at = old_ts
        run.updated_at = utc_now()  # the regenerate kickoff just wrote status
        s.commit()


def test_reaper_skips_freshly_restarted_regenerate(fake_git_repo, tmp_db):
    """A regenerate reuses the run id, so the list snapshot's `last_activity_at`
    is the prior cycle's (stale) and `is_live` is briefly False before the new
    subprocess registers — making a healthy, just-restarted run look stranded.
    The reaper must key off the authoritative `updated_at` the kickoff bumped
    and NOT stamp the live run `failed` mid-flight (the transient 'failed' badge
    users saw right after clicking Regenerate)."""
    _commit_service(fake_git_repo)
    _seed_run(fake_git_repo)  # state=running
    _regenerate_reuse_state("run1")

    reaped = reap_stranded_proposal_runs(fake_git_repo)

    assert reaped == 0
    assert load_proposal_status(fake_git_repo, "run1")["state"] == "running"


def test_stale_validation_marker_does_not_reopen_retry_gate(fake_git_repo, tmp_db):
    """The proven attack sequence: validation-fail (marker set) → a LATER
    non-validation failure of a fresh attempt (crash-style _fail, or reap)
    must close the gate — the additive metadata merge would otherwise carry
    the stale marker and let a late finish re-ingest stale output."""
    from lib.topics.proposal_external import _fail, external_trace_id

    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    # Attempt 1: validation failure sets the marker.
    _write_temp_output(out_dir, payload={"version": 1, "topics": [], "notes": []})
    with pytest.raises(TopicGraphError):
        finish_proposal_run(fake_git_repo, "run1")
    assert load_proposal_status(fake_git_repo, "run1").get("failed_validation") is True

    # Attempt 2 crashes for a non-validation reason.
    status = load_status(out_dir)
    status.update(state="running", error=None, agent_signaled=False)
    write_status(out_dir, status)
    with pytest.raises(TopicGraphError):
        _fail(out_dir, external_trace_id("run1"), load_status(out_dir),
              "failed", "external agent exited with code 2")

    _write_temp_output(out_dir)
    result = finish_proposal_run(fake_git_repo, "run1")
    assert result["ingested"] is False
    assert load_proposal_status(fake_git_repo, "run1")["state"] == "failed"


def test_reap_clears_stale_validation_marker(fake_git_repo, tmp_db):
    from lib.topics.proposals.reap import reap_stranded_proposal_runs

    _commit_service(fake_git_repo)
    out_dir = _seed_run(fake_git_repo)
    _write_temp_output(out_dir, payload={"version": 1, "topics": [], "notes": []})
    with pytest.raises(TopicGraphError):
        finish_proposal_run(fake_git_repo, "run1")

    # Simulate a stranded restart with direct column edits (write_status's
    # write-time invariant re-promotes running→completed while the stale
    # completed_at rides in the dict, and read-time normalization does the
    # same — the columns are what the reaper predicate sees). The stale
    # failed_validation marker stays in the metadata bag: that is the
    # subject under test.
    from lib.orm import SessionLocal
    from lib.orm.models import ProposalRun
    with SessionLocal() as s:
        run = s.get(ProposalRun, "run1")
        run.state = "running"
        run.error = None
        run.error_detail = None
        run.completed_at = None
        s.commit()
    _backdate_run("run1")
    assert reap_stranded_proposal_runs(fake_git_repo) == 1

    _write_temp_output(out_dir)
    result = finish_proposal_run(fake_git_repo, "run1")
    assert result["ingested"] is False
    assert load_proposal_status(fake_git_repo, "run1")["state"] == "failed"
