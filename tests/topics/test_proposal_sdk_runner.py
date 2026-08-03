"""Drafting through the Agent SDK tier instead of `subprocess.Popen`.

The port has to be invisible: same finish handshake, same cancellation, same
status bookkeeping, same `origin='llm-stage'` tagging — only the runner
changes, and only when both gates are on. So these tests pin the gate matrix
first, then drive the SDK path with a stubbed `supervisor.launch_run` standing
in for the agent (nothing here launches one).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from lib.agent_sdk import supervisor
from lib.settings import settings
from lib.topics import TopicGraphError, bootstrap
from lib.topics import proposal_external as pe
from lib.topics.proposals import (
    create_proposal_run, load_proposal, load_proposal_status, run_control,
)

PAYLOAD = {
    "topics": [{
        "id": "service", "label": "Service", "aliases": [],
        "intent": "Curated context for Service.", "status": "active",
        "refs": [{"path": "service/api.py", "role": "implementation"}],
        "edges": [], "commands": [], "include_globs": ["service/**"],
        "exclude_globs": [], "evidence_paths": ["service/api.py"],
    }],
    "notes": [],
    "wiki": "# Service\n",
}


def _cfg(command: str = "claude"):
    return type("Cfg", (), {"command": command, "args": ["-p"],
                            "timeout_seconds": 30, "cwd": None})()


def _configure_agent(monkeypatch, command: str = "claude"):
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": _cfg(command)})


@pytest.fixture
def sdk_on(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(settings.topic_evolution, "proposal_sdk_runner", True)


@pytest.fixture
def repo(fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo)
    return fake_git_repo


class FakeHandle:
    def __init__(self, trace_id="sdk-fakerun0001", status="exited",
                 detail="completed"):
        self.trace_id = trace_id
        self._outcome = supervisor.RunOutcome(trace_id, status, detail)
        self.stopped = False
        self._done = False

    def done(self):
        return self._done

    def wait(self, timeout=None):
        self._done = True
        return self._outcome

    def stop(self):
        self.stopped = True
        return True, "stopping"


@pytest.fixture
def stub_launch(monkeypatch):
    """Stand in for the drafting agent: record the launch, write its output."""
    seen = {}

    def _launch(prompt, *, cwd=None, env=None, one_shot=False,
                permission_mode="", model=""):
        seen.update(prompt=prompt, cwd=cwd, env=env or {},
                    one_shot=one_shot, permission_mode=permission_mode)
        output = (env or {}).get("REGIN_TOPIC_PROPOSAL_OUTPUT")
        if output:
            with open(output, "w") as fh:
                fh.write(json.dumps(PAYLOAD))
        return seen.setdefault("handle", FakeHandle())

    monkeypatch.setattr(supervisor, "launch_run", _launch)
    return seen


# ── the gate ──────────────────────────────────────────────────────────


def test_a_default_install_never_takes_the_sdk_path():
    """`agent_sdk.enabled` is the one an operator has to turn on; the drafting
    tier defaults to riding it."""
    assert pe._sdk_runner_enabled(_cfg()) is False


def test_the_sdk_being_enabled_is_enough_to_route_drafting_through_it(
        monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)

    assert pe._sdk_runner_enabled(_cfg()) is True


def test_the_tier_being_off_keeps_the_subprocess_path(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", False)
    monkeypatch.setattr(settings.topic_evolution, "proposal_sdk_runner", True)

    assert pe._sdk_runner_enabled(_cfg()) is False


def test_opting_the_drafting_runner_out_keeps_the_subprocess_path(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(settings.topic_evolution, "proposal_sdk_runner", False)

    assert pe._sdk_runner_enabled(_cfg()) is False


def test_an_agent_bound_to_another_cli_keeps_its_own_binary(sdk_on):
    """The tier launches the user's `claude`; a codex-bound run must not be
    silently rerouted to a different agent."""
    assert pe._sdk_runner_enabled(_cfg("codex")) is False
    assert pe._sdk_runner_enabled(_cfg("/usr/local/bin/claude")) is True


def test_both_gates_on_selects_the_sdk_runner(sdk_on):
    assert pe._sdk_runner_enabled(_cfg()) is True


def test_the_gated_off_run_still_uses_the_subprocess(
        monkeypatch, repo, tmp_path, tmp_db, allow_subprocess_spawn):
    """A regin install that never enables this behaves exactly as today."""
    def _explode(*a, **kw):
        raise AssertionError("the SDK runner must not be reachable when off")

    monkeypatch.setattr(supervisor, "launch_run", _explode)
    script = tmp_path / "agent.py"
    script.write_text(
        "import json, os\n"
        f"open(os.environ['REGIN_TOPIC_PROPOSAL_OUTPUT'], 'w')"
        f".write(json.dumps({PAYLOAD!r}))\n")
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python",
                                  "args": [str(script)],
                                  "timeout_seconds": 30, "cwd": None})()})

    create_proposal_run(repo, run_id="run1", agent="fake")

    assert load_proposal(repo, "run1")["topics"][0]["id"] == "service"


# ── the ported run ────────────────────────────────────────────────────


@pytest.fixture
def sdk_run(monkeypatch, repo, tmp_db, sdk_on, stub_launch):
    _configure_agent(monkeypatch)
    create_proposal_run(repo, run_id="run1", agent="fake")
    return stub_launch


def test_the_draft_is_ingested_from_the_temp_file_handshake(sdk_run, repo):
    assert load_proposal(repo, "run1")["topics"][0]["id"] == "service"
    assert load_proposal_status(repo, "run1")["state"] == "completed"


def test_the_agent_gets_the_proposal_handshake_in_its_environment(sdk_run):
    env = sdk_run["env"]

    assert env["REGIN_LLM_SURFACE"] == "topic-proposal-drafting"
    assert env["REGIN_TOPIC_PROPOSAL_ID"] == "run1"
    assert env["REGIN_TOPIC_PROPOSAL_TRACE_ID"] == "topic-proposal-run1"
    assert "topics proposal-finish" in env["REGIN_TOPIC_PROPOSAL_FINISH_CMD"]


def test_the_environment_is_an_overlay_not_a_copy_of_this_process(sdk_run):
    """The SDK merges it over the parent environment itself; passing the whole
    thing would also stomp the vars the SDK sets for its own child."""
    assert set(sdk_run["env"]) == {
        "REGIN_LLM_SURFACE", "REGIN_TOPIC_PROPOSAL_DIR",
        "REGIN_TOPIC_PROPOSAL_OUTPUT", "REGIN_TOPIC_PROPOSAL_CANONICAL_OUTPUT",
        "REGIN_TOPIC_PROPOSAL_TRACE_ID", "REGIN_TOPIC_PROPOSAL_ID",
        "REGIN_TOPIC_PROPOSAL_FINISH_CMD",
    }


def test_the_run_ends_with_its_draft(sdk_run):
    """Left open, the session would hold its slot until the idle timeout — long
    after the caller had the proposal."""
    assert sdk_run["one_shot"] is True


def test_the_prompt_is_the_drafting_instructions(sdk_run):
    assert "REGIN TOPIC PROPOSAL" in sdk_run["prompt"].upper()


def test_the_run_names_the_session_it_launched(sdk_run, repo):
    """A run regin owns knows its own trace id, so the review card links to a
    watchable session instead of one guessed at by title."""
    status = load_proposal_status(repo, "run1")

    assert status["agent_trace_id"] == "sdk-fakerun0001"
    assert status["agent_trace_url"] == "/trace/sessions/sdk-fakerun0001"


def test_a_failed_run_fails_the_proposal_with_its_detail(
        monkeypatch, repo, tmp_db, sdk_on):
    _configure_agent(monkeypatch)
    monkeypatch.setattr(
        supervisor, "launch_run",
        lambda prompt, **kw: FakeHandle(status="failed", detail="cli exploded"))

    with pytest.raises(TopicGraphError, match="exited with code 1"):
        create_proposal_run(repo, run_id="run1", agent="fake")

    status = load_proposal_status(repo, "run1")
    assert status["state"] == "failed"
    assert "cli exploded" in status["error"]


def test_a_refused_launch_fails_the_run_rather_than_hanging(
        monkeypatch, repo, tmp_db, sdk_on):
    _configure_agent(monkeypatch)

    def _refuse(prompt, **kw):
        raise supervisor.LaunchRefused("max_concurrent_runs reached")

    monkeypatch.setattr(supervisor, "launch_run", _refuse)

    with pytest.raises(TopicGraphError, match="failed to start"):
        create_proposal_run(repo, run_id="run1", agent="fake")

    assert load_proposal_status(repo, "run1")["state"] == "failed"


def test_a_stopped_run_is_cancelled_not_failed(monkeypatch, repo, tmp_db,
                                               sdk_on):
    """Stop is pressed while the agent works: the run must land `cancelled`,
    the state that survives the read-time coercions."""
    _configure_agent(monkeypatch)

    def _launch(prompt, **kw):
        run_control.request_cancel("run1")
        return FakeHandle()

    monkeypatch.setattr(supervisor, "launch_run", _launch)

    with pytest.raises(TopicGraphError, match="stopped by user"):
        create_proposal_run(repo, run_id="run1", agent="fake")

    assert load_proposal_status(repo, "run1")["state"] == "cancelled"
    run_control.reset("run1")


# ── the Stop endpoint's handle ────────────────────────────────────────


def test_stop_reaches_an_sdk_run_through_run_control():
    """`run_control` is how a request thread ends a run it has no other
    reference to; an SDK run has to be reachable the same way."""
    handle = FakeHandle()
    run_control.reset("run-stop")
    run_control.register("run-stop", pe._SdkRunProcess(handle))
    try:
        assert run_control.is_live("run-stop") is True
        run_control.request_cancel("run-stop")

        assert handle.stopped is True
        assert run_control.is_cancelled("run-stop") is True
    finally:
        run_control.reset("run-stop")


def test_a_finished_sdk_run_is_no_longer_live():
    handle = FakeHandle()
    handle.wait()
    run_control.reset("run-done")
    run_control.register("run-done", pe._SdkRunProcess(handle))
    try:
        assert run_control.is_live("run-done") is False
    finally:
        run_control.reset("run-done")
