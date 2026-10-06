"""Handler: Stop → end a reported one-shot tmux task run's session.

`Stop` is the turn boundary the tmux tier can see: the agent ran its
`task finish` inside this turn, and the turn is now over, so its last answer
and every trace span are already written. Ending the session earlier — from the
report itself — would cut that answer off mid-turn.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def handle_stop(payload: HookPayload) -> HookResponse | None:
    if not payload.session_id:
        return None
    try:
        from lib.tasks.run_ending import end_reported_tmux
        end_reported_tmux(payload.session_id)
    except Exception:
        pass
    return None
