"""Announce a `/clear` succession to every open stream.

`/clear` ends the conversation and opens another in the same tmux pane, so the
page an operator is holding names a session that is over while the agent works
on under an id nothing on that page points at. The successor is knowable
(`store.clear_successor`) but only from two facts written by two independent
hook processes: the predecessor's `ended_reason = 'clear'`, committed by the
span ingest, and the heir's `bridge_panes` row, written at SessionStart.

Neither site can know it is the later of the two, so both call `announce` and
the walk decides: whichever runs once both facts exist resolves a successor and
raises the frame, and the earlier one resolves None and says nothing. Raising
the frame twice would be harmless anyway — the client's jump is a
`router.replace` onto an id it may already be on — but it does not happen,
because the first caller has nothing to name.

The frame names the PREDECESSOR as `trace_id`: that is the id the stranded page
is mounted on, and therefore the only one it can match itself against.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger

log = get_activity_logger("agent_bridge")


def announce(predecessor_trace_id: str | None) -> str | None:
    """Broadcast the successor frame for `predecessor_trace_id`, if any.

    Returns the successor it named, or None when the succession is not (yet)
    resolvable. Best-effort throughout: this runs inside a span ingest a hook
    is blocking on and inside the loopback notify route, and a fan-out failure
    must cost neither of them anything.
    """
    if not predecessor_trace_id:
        return None
    # Everything below is inside the guard, gates included: the settings read
    # and the subscriber count are the caller's first contact with this module
    # and would otherwise raise straight into a span ingest that answers a
    # blocking hook, turning a fan-out detail into a 500 on the POST.
    try:
        from lib.settings import settings
        # The bridge off means there is no terminal the successor could be
        # typed into, so moving the operator onto it would only strand them
        # somewhere else. Same gate the summary's successor read sits behind.
        if not settings.agent_bridge.enabled:
            return None
        from lib.notifications import hub
        # With nobody watching there is no frame to build, and the walk costs
        # queries the ingest would be paying for on the hook's latency.
        if not hub.subscriber_count():
            return None
        from lib.agent_bridge import store
        successor = store.clear_successor(predecessor_trace_id)
        if not successor:
            return None
        hub.broadcast_event(hub.SUCCESSOR_EVENT, {
            'trace_id': predecessor_trace_id,
            'successor_trace_id': successor,
        })
    except Exception:  # noqa: BLE001 - an announcement must never fail its caller
        log.error("bridge_successor_announce_failed",
                  trace_id=predecessor_trace_id, exc_info=True)
        return None
    log.write("bridge_successor_announced", trace_id=predecessor_trace_id,
              successor=successor)
    return successor
