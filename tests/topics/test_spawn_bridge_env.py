"""Agents spawned by the server never inherit the server's bridge opt-in.

`regin serve` runs inside a tmux pane and its `REGIN_BRIDGE=1` / `TMUX_PANE`
travel into every process it launches, so a drafting or review agent's own
SessionStart hook would claim the server's pane as its bridge pane — and a
`/live` steer aimed at the agent would be typed into the operator's terminal.
Every server-side spawn therefore hands its child `REGIN_BRIDGE=0`.
"""

from __future__ import annotations

import subprocess

import pytest

from lib.topics import bootstrap
from lib.topics import proposal_external as pe
from lib.topics import proposal_review as pr
from lib.topics.proposals import create_proposal_run

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


@pytest.fixture
def repo(fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo)
    return fake_git_repo


def test_the_drafting_subprocess_child_sees_the_bridge_off(
        monkeypatch, repo, tmp_path, tmp_db, allow_subprocess_spawn):
    """Asserted from inside the child: the value the agent's own hooks read."""
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    seen = tmp_path / "seen-bridge.txt"
    script = tmp_path / "agent.py"
    script.write_text(
        "import json, os\n"
        f"open({str(seen)!r}, 'w')"
        ".write(os.environ.get('REGIN_BRIDGE', '<unset>'))\n"
        f"open(os.environ['REGIN_TOPIC_PROPOSAL_OUTPUT'], 'w')"
        f".write(json.dumps({PAYLOAD!r}))\n")
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python",
                                  "args": [str(script)],
                                  "timeout_seconds": 30, "cwd": None})()})

    create_proposal_run(repo, run_id="run1", agent="fake")

    assert seen.read_text() == "0"


def test_the_drafting_spawn_keeps_the_rest_of_the_environment(
        monkeypatch, repo, tmp_db):
    """Only the bridge flag is forced; the handshake overlay and the inherited
    environment still reach the agent."""
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("REGIN_SPAWN_ENV_CANARY", "kept")
    seen = {}

    def _spy(ctx, instructions, env, command, cwd, status):
        seen["env"] = env
        raise RuntimeError("stop after the env is built")

    monkeypatch.setattr(pe, "_run_via_subprocess", _spy)
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python", "args": [],
                                  "timeout_seconds": 30, "cwd": None})()})

    with pytest.raises(RuntimeError, match="stop after"):
        create_proposal_run(repo, run_id="run2", agent="fake")

    assert seen["env"]["REGIN_BRIDGE"] == "0"
    assert seen["env"]["REGIN_SPAWN_ENV_CANARY"] == "kept"
    assert seen["env"]["REGIN_TOPIC_PROPOSAL_ID"] == "run2"


class _FakeProc:
    def communicate(self, _input=None, timeout=None):
        del timeout
        return b"", b""

    def kill(self):
        return None


class _RecordingSubprocess:
    """Stands in for the module's `subprocess`, capturing the spawn env."""

    PIPE = subprocess.PIPE
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.env = None

    def Popen(self, argv, **kwargs):  # noqa: N802 - mirrors subprocess.Popen
        del argv
        self.env = kwargs.get("env")
        return _FakeProc()


def test_the_review_agent_spawn_turns_the_bridge_off(monkeypatch, repo):
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    fake = _RecordingSubprocess()
    monkeypatch.setattr(pr, "subprocess", fake)
    spec = type("Spec", (), {"argv": ["true"], "timeout": 5, "cwd": None,
                             "surface_id": "topic-proposal-review"})()

    pr._review_agent_worker(repo, "run1", spec, "review this")

    assert fake.env["REGIN_BRIDGE"] == "0"
    assert fake.env["REGIN_TOPIC_REVIEW_ID"] == "run1"
    assert fake.env["REGIN_LLM_SURFACE"] == "topic-proposal-review"
