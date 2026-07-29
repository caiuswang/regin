"""`regin goal spawn <role>` — the portable form of the agent-arm dispatch.

Every test stubs the subprocess layer: an unstubbed call here would launch a
real `claude --print` (the guard in `conftest_support` turns that into a hard
failure rather than a 600s hang, but the intent is to never reach it).
"""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from cli.app import app
from lib import goal_spawn
from lib.goal_spawn import (
    GoalSpawnError, compose_prompt, role_definition, spawn_role,
)
from lib.settings import TopicProposalExternalAgent, settings

runner = CliRunner()


class _Completed:
    def __init__(self, stdout=b"VERDICT: SHIP", stderr=b"", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture
def captured_run(monkeypatch):
    """Swap the module's subprocess with a recorder, returning the calls."""
    calls = []

    class _Recorder:
        SubprocessError = subprocess.SubprocessError

        @staticmethod
        def run(argv, **kwargs):
            calls.append({"argv": argv, **kwargs})
            return _Completed()

    monkeypatch.setattr(goal_spawn, "subprocess", _Recorder)
    return calls


@pytest.fixture
def claude_agent(monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(
            command="claude", args=["--print"], timeout_seconds=42),
    })


@pytest.fixture
def kimi_agent(monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "kimi": TopicProposalExternalAgent(
            command="kimi", args=["-p", "{prompt}"],
            supports_allowed_tools=False),
    })


# ── role definitions come from the agent markdown, not a second copy ────────

@pytest.mark.parametrize("role", ["refiner", "builder", "verifier"])
def test_every_role_resolves_to_an_agent_definition(role):
    definition = role_definition(role)
    assert definition.path.name == f"goal-{role}.md"
    assert definition.system_prompt
    assert definition.tools


def test_builder_may_write_and_the_read_only_roles_may_not():
    """The grant must match what each role's prompt tells it to do — the
    verifier is explicitly read-only, so handing it Edit/Write would license
    the find-and-fix fusion the whole loop exists to prevent."""
    assert "Edit" in role_definition("builder").tools
    assert "Write" in role_definition("builder").tools
    for role in ("refiner", "verifier"):
        tools = role_definition(role).tools
        assert "Edit" not in tools and "Write" not in tools


def test_unknown_role_is_a_named_error():
    with pytest.raises(GoalSpawnError, match="unknown role"):
        role_definition("reviewer")


def test_composed_prompt_carries_the_charter_then_the_payload():
    definition = role_definition("verifier")
    prompt = compose_prompt(definition, "  goal: X  ")
    assert prompt.startswith(definition.system_prompt)
    assert "<task>\ngoal: X\n</task>" in prompt


# ── the subprocess contract ─────────────────────────────────────────────────

def test_prompt_on_stdin_with_the_role_tool_grant(captured_run, claude_agent):
    result = spawn_role("verifier", "check the diff")
    call = captured_run[0]
    assert call["argv"][:2] == ["claude", "--print"]
    assert call["argv"][2] == "--allowedTools"
    assert set(call["argv"][3].split(",")) == set(role_definition("verifier").tools)
    assert b"check the diff" in call["input"]
    assert result.stdout == "VERDICT: SHIP"


def test_prompt_token_agent_gets_no_stdin_and_no_grant(captured_run, kimi_agent):
    """Kimi takes the prompt as an argument and has no `--allowedTools`."""
    spawn_role("builder", "implement it")
    call = captured_run[0]
    assert call["input"] is None
    assert "--allowedTools" not in call["argv"]
    assert "{prompt}" not in " ".join(call["argv"])
    assert "implement it" in call["argv"][2]


def test_worker_gets_its_own_session_id_not_the_orchestrator_s(
    captured_run, claude_agent, monkeypatch,
):
    monkeypatch.setenv("REGIN_SESSION_ID", "orchestrator-session")
    result = spawn_role("refiner", "prune the roadmap")
    env = captured_run[0]["env"]
    assert env["REGIN_SESSION_ID"] == result.session_id
    assert result.session_id != "orchestrator-session"
    assert env["REGIN_LLM_SURFACE"] == "goal-refiner"


def test_explicit_session_id_is_honoured(captured_run, claude_agent):
    result = spawn_role("refiner", "x", session_id="pinned-id")
    assert result.session_id == "pinned-id"
    assert captured_run[0]["env"]["REGIN_SESSION_ID"] == "pinned-id"


def test_caller_cwd_is_the_fallback_when_the_agent_pins_none(
    captured_run, claude_agent, tmp_path,
):
    spawn_role("verifier", "x", cwd=tmp_path)
    assert captured_run[0]["cwd"] == str(tmp_path)


def test_agent_timeout_applies_unless_overridden(captured_run, claude_agent):
    spawn_role("verifier", "x")
    assert captured_run[0]["timeout"] == 42
    spawn_role("verifier", "x", timeout=7)
    assert captured_run[1]["timeout"] == 7


# ── failure modes are loud, never a silent empty success ────────────────────

def test_no_configured_agent_is_an_error(monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {})
    with pytest.raises(GoalSpawnError, match="no external agent configured"):
        spawn_role("verifier", "x")


def test_unknown_agent_id_is_an_error(claude_agent):
    with pytest.raises(GoalSpawnError, match="unknown external agent"):
        spawn_role("verifier", "x", agent_id="gpt")


def test_nonzero_exit_raises_with_the_worker_stderr(monkeypatch, claude_agent):
    class _Failing:
        SubprocessError = subprocess.SubprocessError

        @staticmethod
        def run(argv, **kwargs):
            return _Completed(stdout=b"", stderr=b"boom", returncode=3)

    monkeypatch.setattr(goal_spawn, "subprocess", _Failing)
    with pytest.raises(GoalSpawnError, match="exited 3.*boom"):
        spawn_role("verifier", "x")


def test_launch_failure_raises(monkeypatch, claude_agent):
    class _Missing:
        SubprocessError = subprocess.SubprocessError

        @staticmethod
        def run(argv, **kwargs):
            raise OSError("No such file or directory: 'claude'")

    monkeypatch.setattr(goal_spawn, "subprocess", _Missing)
    with pytest.raises(GoalSpawnError, match="failed to run"):
        spawn_role("verifier", "x")


# ── the CLI surface ─────────────────────────────────────────────────────────

def test_command_is_wired_into_the_app():
    result = runner.invoke(app, ["goal", "spawn", "--help"])
    assert "No such command" not in result.output
    assert result.exit_code == 0


def test_print_prompt_renders_without_spawning(monkeypatch):
    def _explode(*_a, **_k):
        raise AssertionError("--print-prompt must not spawn anything")

    monkeypatch.setattr(goal_spawn, "spawn_role", _explode)
    result = runner.invoke(
        app, ["goal", "spawn", "verifier", "--task", "goal: X", "--print-prompt"])
    assert result.exit_code == 0
    assert "<task>" in result.stdout


def test_missing_payload_exits_nonzero():
    result = runner.invoke(app, ["goal", "spawn", "verifier"])
    assert result.exit_code == 1


def test_task_file_is_read(tmp_path, captured_run, claude_agent):
    payload = tmp_path / "task.md"
    payload.write_text("roadmap + diff", encoding="utf-8")
    result = runner.invoke(
        app, ["goal", "spawn", "verifier", "--task-file", str(payload)])
    assert result.exit_code == 0
    assert b"roadmap + diff" in captured_run[0]["input"]


def test_json_output_reports_the_attribution(captured_run, claude_agent):
    import json

    result = runner.invoke(
        app, ["goal", "spawn", "verifier", "--task", "x", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["role"] == "verifier"
    assert payload["agent"] == "claude"
    assert payload["stdout"] == "VERDICT: SHIP"
    assert payload["session_id"].startswith("goal-verifier-")
