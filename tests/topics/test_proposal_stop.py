"""Stop / cancel an in-flight proposal run.

Covers the cross-thread cancellation machinery added for the Stop button:
the `run_control` process registry, the `stop_proposal_run` state
transition, and the worker-side guards that keep a user cancel from being
downgraded to `failed` or upgraded to `completed`.
"""

from __future__ import annotations

import pytest

from lib.topics import TopicGraphError, topic_dir, utc_now
from lib.topics.proposals import load_proposal_status, run_control, stop_proposal_run


class FakePopen:
    """Minimal Popen stand-in: `poll`/`terminate` are all run_control touches."""

    def __init__(self, alive: bool = True):
        self._alive = alive
        self.terminated = False
        self.pid = 4321
        self.returncode = None if alive else -15

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.returncode = -15


def _seed_running_run(repo, proposal_id, *, pid=4321):
    from lib.topics.proposal_external import write_status

    out_dir = topic_dir(repo) / "proposals" / proposal_id
    out_dir.mkdir(parents=True, exist_ok=True)
    write_status(out_dir, {
        "state": "running",
        "trace_id": f"topic-proposal-{proposal_id}",
        "agent": "claude",
        "started_at": utc_now(),
        "completed_at": None,
        "error": None,
        "pid": pid,
        "prompt_template_ids": [],
    })
    return out_dir


# ── run_control registry ─────────────────────────────────────────────


def test_request_cancel_terminates_live_process():
    pid = "20260526T120000Z"
    proc = FakePopen(alive=True)
    run_control.reset(pid)
    run_control.register(pid, proc)
    try:
        assert run_control.request_cancel(pid) is True
        assert proc.terminated is True
        assert run_control.is_cancelled(pid) is True
        # release drops only the handle; the flag survives so the worker
        # can still observe it while finalising the run.
        run_control.release(pid)
        assert run_control.is_cancelled(pid) is True
    finally:
        run_control.reset(pid)
    assert run_control.is_cancelled(pid) is False


def test_request_cancel_without_live_process_flags_only():
    pid = "20260526T120001Z"
    run_control.reset(pid)
    try:
        # Nothing registered (still queued / already exited): no signal sent,
        # but the flag is set so the worker finalises as cancelled.
        assert run_control.request_cancel(pid) is False
        assert run_control.is_cancelled(pid) is True
    finally:
        run_control.reset(pid)


def test_reset_clears_stale_flag_so_regenerate_is_not_insta_cancelled():
    pid = "20260526T120002Z"
    run_control.request_cancel(pid)
    assert run_control.is_cancelled(pid) is True
    run_control.reset(pid)  # a fresh run with the reused id starts clean
    assert run_control.is_cancelled(pid) is False


# ── stop_proposal_run ────────────────────────────────────────────────


def test_stop_proposal_run_marks_cancelled(fake_git_repo):
    proposal_id = "20260526T120100Z"
    _seed_running_run(fake_git_repo, proposal_id)
    try:
        result = stop_proposal_run(str(fake_git_repo), proposal_id)
        assert result["stopped"] is True
        assert result["state"] == "cancelled"
        status = load_proposal_status(str(fake_git_repo), proposal_id)
        assert status["state"] == "cancelled"
        assert status["error"] is None
        assert status["completed_at"]
    finally:
        run_control.reset(proposal_id)


def test_stop_proposal_run_noop_when_terminal(fake_git_repo):
    from lib.topics.proposal_external import write_status

    proposal_id = "20260526T120200Z"
    out_dir = _seed_running_run(fake_git_repo, proposal_id)
    write_status(out_dir, {"state": "completed", "completed_at": utc_now(), "error": None})

    result = stop_proposal_run(str(fake_git_repo), proposal_id)
    assert result["stopped"] is False
    assert result["already_terminal"] is True
    assert load_proposal_status(str(fake_git_repo), proposal_id)["state"] == "completed"


def test_stop_proposal_run_missing_raises(fake_git_repo):
    with pytest.raises(TopicGraphError):
        stop_proposal_run(str(fake_git_repo), "does-not-exist")


# ── worker-side terminal-state guards ────────────────────────────────


def test_record_thread_failure_preserves_cancelled(fake_git_repo):
    """The killed subprocess surfaces as an exception in the worker; the
    cancel must not be rewritten to `failed`."""
    from lib.topics.proposal_external import write_status
    from lib.topics.proposals.external_jobs import _record_thread_failure

    proposal_id = "20260526T120300Z"
    out_dir = _seed_running_run(fake_git_repo, proposal_id)
    write_status(out_dir, {"state": "cancelled", "completed_at": utc_now(), "error": None})

    _record_thread_failure(out_dir, "claude", RuntimeError("subprocess terminated"))

    assert load_proposal_status(str(fake_git_repo), proposal_id)["state"] == "cancelled"


def test_handle_agent_output_cancelled_path_raises_and_marks(fake_git_repo):
    """When the cancel flag is set, the runner's output handler stamps
    `cancelled` and raises BEFORE reaching the non-zero-exit → failed path."""
    from lib.topics.proposal_external import _AgentRunContext, _handle_agent_output

    proposal_id = "20260526T120400Z"
    out_dir = _seed_running_run(fake_git_repo, proposal_id)
    run_control.reset(proposal_id)
    run_control.request_cancel(proposal_id)
    try:
        ctx = _AgentRunContext(
            repo=fake_git_repo,
            out_dir=out_dir,
            trace_id=f"topic-proposal-{proposal_id}",
            proposal_id=proposal_id,
            agent="claude",
            before_topic=None,
            temp_output_path=out_dir / ".tmp" / "agent-output.json",
            output_path=out_dir / "agent-output.json",
            stdout_path=out_dir / "stdout.log",
            stderr_path=out_dir / "stderr.log",
            started=0.0,
            prompt_templates=None,
        )
        with pytest.raises(TopicGraphError):
            _handle_agent_output(ctx, FakePopen(alive=False), "out", "err", {"state": "running"})
        assert load_proposal_status(str(fake_git_repo), proposal_id)["state"] == "cancelled"
    finally:
        run_control.reset(proposal_id)
