"""Regression guard: the agent-facing CLI commands stay wired into the app.

`regin session-id` (and friends) are invoked by skills via the real interpreter
form `.venv/bin/python cli/regin.py <cmd>`. Each command module under
`cli/commands/` is wired into the root Typer app in `cli/app.py` by hand, so a
stray edit that drops a module from the import list or deletes its
`register(app)` / `add_typer(...)` line silently un-wires the command — it then
fails at runtime with Click's `No such command '<cmd>'` (exit 2), which is
exactly how `regin session-id` regressed and broke the goal-verified loop across
sessions.

These tests assert the assembled app still recognizes the agent-facing commands,
invoking them through Typer's CliRunner exactly as the agent does. A silent
un-wiring becomes a red test here instead of a runtime failure mid-session.
"""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app

runner = CliRunner()

# Commands the skills/hooks invoke programmatically and must never silently
# disappear. Each entry is the argv to probe; `--help` is enough to prove the
# command resolves without actually running its body.
_AGENT_FACING = [
    ["session-id", "--help"],   # goal-verified preflight/feedback + gate session id
    ["messages", "--help"],     # inbox retention stats / prune
    ["gate", "--help"],         # anti-skip span gates
    ["goal", "--help"],         # preflight roadmap router
    ["memory", "--help"],       # recall-for-task
]


def _assert_resolves(argv: list[str]) -> None:
    result = runner.invoke(app, argv)
    # Click emits exit code 2 + "No such command" when a command is un-wired.
    assert "No such command" not in result.output, (
        f"`regin {argv[0]}` is not wired into cli/app.py — "
        f"restore its import + register(app)/add_typer(...) line.\n{result.output}"
    )
    assert result.exit_code == 0, (
        f"`regin {' '.join(argv)}` exited {result.exit_code}:\n{result.output}"
    )


def test_agent_facing_commands_are_registered():
    for argv in _AGENT_FACING:
        _assert_resolves(argv)


def test_session_id_command_is_callable():
    # The exact failure from the trace: `No such command 'session-id'`.
    result = runner.invoke(app, ["session-id"])
    assert "No such command" not in result.output
    # Cache miss prints nothing and exits 0 (soft, composable failure by design);
    # a hit prints an id. Either way it must not be an unrecognized command.
    assert result.exit_code == 0


def test_session_id_reads_the_portable_override(monkeypatch):
    # The escape hatch for a harness that exports no session id of its own.
    from lib import session_probe
    monkeypatch.setenv(session_probe._PORTABLE_ENV, "portable-sid")
    result = runner.invoke(app, ["session-id"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "portable-sid"


def test_session_id_from_trace_falls_back_to_the_live_session(monkeypatch, tmp_path):
    from lib import session_probe
    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionRow

    from datetime import datetime

    for name in session_probe.env_candidates():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    # `last_seen` must be fresh on the span writer's clock (naive local
    # isoformat) — the fallback only answers for a recently-active session.
    now = datetime.now().isoformat()
    with SessionLocal() as s:
        s.add(SessionRow(trace_id="sid-fallback",
                         cwd=str(tmp_path.resolve()), started_at=now,
                         last_seen=now, origin="session",
                         is_test=0, span_count=0, skill_reads=0, file_edits=0,
                         rule_checks=0, plan_enters=0, prompts=0, tool_calls=0))
        s.commit()

    # Off by default — the env miss stays a miss unless you opt in.
    assert runner.invoke(app, ["session-id"]).stdout.strip() == ""
    result = runner.invoke(app, ["session-id", "--from-trace"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "sid-fallback"
