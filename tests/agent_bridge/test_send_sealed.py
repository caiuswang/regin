"""F180 #8 — a send to a task run its report sealed answers `SendRefused`: the
row is closed as refused, nothing reaches the session, and the composer is
told which task and run to comment on instead."""

from __future__ import annotations

from lib.agent_bridge import store as bridge_store
from tests.tasks.test_finish_pending_messages import (  # noqa: F401 — fixtures
    _finish, _send, loop, runners, typed)
from tests.tasks.test_tmux_launch import (  # noqa: F401 — fixtures
    _armed, _run, no_sdk, tmux)


def _row(trace_id, row_id):
    return next(r for r in bridge_store.list_bridge_messages(
        session_id=trace_id) if r["id"] == row_id)


def _assert_refused(sent, run):
    assert sent["delivered"] is False and sent["reason"] == "run_sealed"
    assert (sent["task_id"], sent["run_id"]) == (run["task_id"], run["id"])
    assert sent["detail"]
    row = _row(run["trace_id"], sent["id"])
    assert row["delivered"] == 0
    assert row["delivery_detail"] == "refused: run sealed"


def test_a_send_after_a_tmux_seal_is_refused(flask_client, tmux, typed):
    run = _run(flask_client, _armed(flask_client, launch="tmux",
                                    one_shot=True))
    assert _finish(flask_client, run["id"]).status_code == 200

    sent = _send(flask_client, run["trace_id"], "补充：还要支持暗色")

    _assert_refused(sent, run)
    assert typed == [], "a refused send must never reach the pane"
    assert bridge_store.list_pending_steers(run["trace_id"]) == []


def test_a_send_after_an_sdk_seal_is_refused(flask_client, no_sdk, runners):
    run = _run(flask_client, _armed(flask_client, one_shot=True))
    runner = runners(run["trace_id"])
    assert _finish(flask_client, run["id"]).status_code == 200

    sent = _send(flask_client, run["trace_id"], "补充：还要支持暗色")

    _assert_refused(sent, run)
    assert runner.pending_prompts() == []


def test_a_session_outside_any_task_run_is_untouched(flask_client, tmux,
                                                     typed):
    sent = _send(flask_client, "not-a-task-run", "hello")

    assert sent["delivered"] is True and "reason" not in sent


def test_a_token_send_to_a_sealed_run_is_refused(flask_client, tmux, typed,
                                                 monkeypatch):
    from lib.settings import settings

    monkeypatch.setattr(settings.agent_bridge, "token", "t0k")
    run = _run(flask_client, _armed(flask_client, launch="tmux",
                                    one_shot=True))
    assert _finish(flask_client, run["id"]).status_code == 200

    sent = flask_client.post(
        "/api/bridge/messages",
        json={"session_id": run["trace_id"], "text": "late"},
        headers={"Authorization": "Bearer t0k"}).get_json()

    _assert_refused(sent, run)
    assert typed == []
