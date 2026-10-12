"""F158 压缩保留的上下文: `regin session context` names feature, task, finish."""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from lib.features import attribution
from lib.tasks.run_prompt import _finish_block, _finish_command


def _context(session: str) -> str:
    result = CliRunner().invoke(app, ["session", "context", "--session", session])
    assert result.exit_code == 0, result.output
    return result.output


def test_session_context_names_the_feature_task_and_finish_command(
        tmp_db, feature_row, native_task, task_run_row):
    feature = feature_row("Claude 新功能Mod调研（二期）")
    task_id = native_task(title="限流告警与压缩上下文", feature_id=feature.id)
    run_id = task_run_row(task_id, trace_id="sess-run", status="running")

    out = _context("sess-run")

    assert out.startswith("Keep verbatim in the summary:\n")
    assert f"F{feature.id}: Claude 新功能Mod调研（二期）" in out
    assert f"task {task_id} (限流告警与压缩上下文), run {run_id}" in out
    assert _finish_command(task_id, run_id) in out
    # The same command the launch prompt's <finish> block hands the run.
    assert _finish_command(task_id, run_id) in _finish_block(run_id, task_id)


def test_a_launched_run_is_found_from_its_claude_session_id(
        tmp_db, feature_row, native_task, task_run_row):
    from lib.orm import SessionLocal
    from lib.orm.models.agent_runs import AgentRunSession

    feature = feature_row("f")
    task_id = native_task(title="t", feature_id=feature.id)
    run_id = task_run_row(task_id, trace_id="sdk-abc123", status="running")
    with SessionLocal() as session:
        session.add(AgentRunSession(trace_id="sdk-abc123",
                                    cli_session_id="cc-session-1"))
        session.commit()

    out = _context("cc-session-1")

    assert f"F{feature.id}: f" in out
    assert _finish_command(task_id, run_id) in out


def test_a_declared_primary_feature_is_named_without_a_task(tmp_db, feature_row):
    feature = feature_row("declared")
    attribution.attribute(feature.id, "sess-plain", primary=True)

    out = _context("sess-plain")

    assert out == f"Keep verbatim in the summary:\n- This session works on feature F{feature.id}: declared\n"
    assert "task" not in out


def test_a_session_without_feature_or_task_prints_nothing(tmp_db):
    assert _context("sess-nothing") == ""
