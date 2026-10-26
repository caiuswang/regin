"""F180 #8 — a sealed tmux run's `Stop` does not kill the pane while its own
input queue still holds a message (typed straight into the pane, past
regin's sealed send): that message runs as the next turn, and the next
`Stop` asks again."""

from __future__ import annotations

from hook_manager.core import HookPayload
from hook_manager.handlers import task_run_end
from lib.trace import queued_prompts
from tests.tasks.test_tmux_launch import _armed, _run, tmux  # noqa: F401


def _stop(trace_id):
    task_run_end.handle_stop(HookPayload(event="Stop", session_id=trace_id))


def _kills(state):
    return [c for c in state.calls if c[0] == "kill-session"]


def test_tmux_stop_keeps_pane_while_queue_nonempty(flask_client, tmux,
                                                   monkeypatch):
    task_id = _armed(flask_client, launch="tmux", one_shot=True)
    run = _run(flask_client, task_id)
    name = f"task-{task_id}-a{run['attempt']}"
    assert flask_client.post(f"/api/tasks/runs/{run['id']}/finish",
                             json={"reason": "built it"}).status_code == 200
    typed = [{"content": "one more thing", "enqueued_at": "t"}]
    monkeypatch.setattr(queued_prompts, "current_queued_prompts",
                        lambda trace_id: list(typed))

    _stop(run["trace_id"])

    assert _kills(tmux) == []
    assert name in tmux.live

    typed.clear()
    _stop(run["trace_id"])

    assert len(_kills(tmux)) == 1
    assert name not in tmux.live
