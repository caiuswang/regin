"""Feature #140 S3 — what a kimi run needs from its workspace before launch.

D8: kimi blocks a first launch in an untrusted directory on an interactive
Trust prompt, so the provider pre-writes its trust record before the channel
opens a session. D7: the armed skill's invocation is the provider's syntax —
kimi's ACP expands `/skill:<name>` (probed on kimi 2.1.1), Claude keeps
`/<name>`. The skill menu and the worktree's copied config follow the same
provider split.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib.providers import kimi as kimi_mod
from lib.providers.registry import build_provider

FAKE = str(Path(__file__).parents[1] / "agent_acp" / "fake_acp_server.py")


@pytest.fixture
def trust_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "workspace-trust"
    monkeypatch.setattr(kimi_mod, "_WORKSPACE_TRUST_DIR", path)
    return path


def _record_name(root: Path) -> str:
    real = os.path.realpath(root)
    digest = hashlib.sha256(real.encode()).hexdigest()[:12]
    return f"wd_{os.path.basename(real)}_{digest}"


# ── D8: workspace trust ──────────────────────────────────────────────

def test_kimi_prepare_workspace_writes_trust_record(tmp_path, trust_dir):
    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real)

    build_provider("kimi").prepare_workspace(link)

    record = trust_dir / _record_name(real)
    assert [p.name for p in trust_dir.iterdir()] == [record.name]
    body = json.loads(record.read_text())
    assert body["root"] == os.path.realpath(real)
    assert isinstance(body["trustedAt"], int) and body["trustedAt"] > 0


def test_prepare_workspace_keeps_existing_record(tmp_path, trust_dir):
    repo = tmp_path / "repo"
    repo.mkdir()
    trust_dir.mkdir()
    record = trust_dir / _record_name(repo)
    original = '{"root":"%s","trustedAt":1}' % os.path.realpath(repo)
    record.write_text(original)

    build_provider("kimi").prepare_workspace(repo)

    assert record.read_text() == original


def test_claude_prepare_workspace_writes_nothing(tmp_path, trust_dir):
    build_provider("claude").prepare_workspace(tmp_path)
    assert not trust_dir.exists()


def test_channel_launch_prepares_workspace_before_session_new(
        tmp_db, tmp_path, trust_dir, monkeypatch):
    from lib.agent_acp import registry as acp_registry
    from lib.agent_acp.client import AcpClient
    from lib.agent_acp.launcher import launch_run
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTask, TASK_STATUS_ARMED
    from lib.tasks import runs

    repo = tmp_path / "fresh-worktree"
    repo.mkdir()
    seen = []
    real_new = AcpClient.session_new

    def _session_new(self, cwd, *args, **kwargs):
        seen.append((trust_dir / _record_name(repo)).exists())
        return real_new(self, cwd, *args, **kwargs)

    def _runner(prompt, **kwargs):
        env = {**(kwargs.pop("env") or {}),
               "FAKE_ACP_RECORD": str(tmp_path / "acp.jsonl")}
        return launch_run(prompt, cmd=[sys.executable, FAKE], env=env,
                          **kwargs)

    monkeypatch.setattr(AcpClient, "session_new", _session_new)
    monkeypatch.setattr(runs, "_channel_launcher", lambda _pid: _runner)
    monkeypatch.setattr(runs, "_launch_images", lambda _task: [])
    with SessionLocal() as session:
        task = AgentTask(title="kimi task", status=TASK_STATUS_ARMED,
                         source="test")
        session.add(task)
        session.commit()
        task_id = task.id
    runs.arm(task_id, params={"provider": "kimi", "cwd": str(repo)},
             verify_cmd=None, autorun=0)

    try:
        runs.start_run(task_id)
    finally:
        for handle in list(acp_registry._handles.values()):
            handle.close()

    assert seen == [True]


# ── D7: skill invocation ─────────────────────────────────────────────

def _armed_prompt(provider: str | None, skill: dict) -> str:
    from lib.tasks import run_prompt

    params = {"skill": skill}
    if provider:
        params["provider"] = provider
    return run_prompt._invocation(params)


def test_skill_invocation_is_provider_owned():
    skill = {"name": "goal-verified", "args": "fast", "also": ["tdd"]}

    kimi_line = _armed_prompt("kimi", skill)

    assert kimi_line == build_provider("kimi").skill_invocation(
        "goal-verified", "fast", ["tdd"])
    assert kimi_line.startswith("/skill:goal-verified fast\n\n")
    assert "Skill tool" not in kimi_line
    assert "`tdd`" in kimi_line
    assert _armed_prompt("kimi", {"name": "tdd"}) == "/skill:tdd\n\n"


def test_claude_skill_invocation_unchanged():
    skill = {"name": "goal-verified", "args": "fast",
             "also": ["tdd", "frontend-design:x"]}
    expected = ("/goal-verified fast\n\nAlso load and follow these skills "
                "with the Skill tool: `tdd`, `frontend-design:x`.\n\n")

    assert _armed_prompt(None, skill) == expected
    assert _armed_prompt("claude", skill) == expected
    assert _armed_prompt(None, {"name": "tdd"}) == "/tdd\n\n"
    assert _armed_prompt("kimi", {}) == ""


# ── skill menu ───────────────────────────────────────────────────────

def _skill(base: Path, *parts: str) -> None:
    path = base.joinpath(*parts)
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("x")


def test_skill_catalog_lists_provider_skills(tmp_path, flask_client):
    from lib.tasks.skill_catalog import available_skills

    home, repo = tmp_path / "home", tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _skill(repo, ".claude", "skills", "claude-only")
    _skill(repo, ".kimi-code", "skills", "kimi-project")
    _skill(repo, ".agents", "skills", "shared-agents")
    _skill(home, ".kimi-code", "skills", "kimi-user")
    _skill(home, ".claude", "skills", "claude-user")

    def names(provider):
        return {s["name"]: s["source"]
                for s in available_skills(repo, home=home, provider=provider)}

    assert names("kimi") == {"kimi-project": "project",
                             "shared-agents": "project",
                             "kimi-user": "user"}
    assert names("claude") == {"claude-only": "project",
                               "claude-user": "user"}

    resp = flask_client.get(
        f"/api/agent-runs/skill-options?cwd={repo}&provider=kimi")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["provider"] == "kimi"
    listed = {s["name"] for s in body["skills"]}
    assert {"kimi-project", "shared-agents"} <= listed
    assert "claude-only" not in listed

    bad = flask_client.get(
        f"/api/agent-runs/skill-options?cwd={repo}&provider=nope")
    assert bad.status_code == 400


# ── worktree config ──────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


def test_worktree_syncs_provider_project_dir(tmp_path):
    from lib.agent_sdk import worktree

    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".claude/\n.kimi-code/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "init")
    _skill(repo, ".claude", "skills", "claude-skill")
    _skill(repo, ".kimi-code", "skills", "kimi-skill")

    made = Path(worktree.ensure_worktree(repo, "feat/x")["path"])

    assert (made / ".claude" / "skills" / "claude-skill" / "SKILL.md").is_file()
    assert (made / ".kimi-code" / "skills" / "kimi-skill" / "SKILL.md").is_file()
    assert not (made / ".claude" / "worktrees").exists()
