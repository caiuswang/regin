"""Which capture tier still holds a live session, and how to end it politely.

Two tiers can own a session that has not ended: a run this server launched
over the Claude Agent SDK (`lib/agent_sdk`), and a terminal the user started
that regin only reaches by typing into its tmux pane (`lib/agent_bridge`).
Settling such a session from the trace UI — Close, or Delete — is a DB write
that says nothing to the process behind it, so the row goes 'ended' while the
agent keeps emitting spans and reappears as a new partial trace. This module
is the missing half: ask which tier owns it, then end that process the way
the tier can be ended.

Tier resolution mirrors `bridge-interrupt`: ask the SDK registry first, since
a regin-launched child runs with REGIN_BRIDGE=0 and can therefore never have
a pane row, while a pane row alone never proves the claude in it is still
running.
"""

from __future__ import annotations

import time

from lib import agent_sdk
from lib.activity_log import get_activity_logger
from lib.agent_bridge import delivery

log = get_activity_logger("live_session")

TIER_SDK = "sdk"
TIER_TMUX = "tmux"

# claude's own quit command, typed into the pane rather than a signal: the
# REPL then tears its session down the way a human ending it would, emitting
# the SessionEnd that a kill would skip.
_EXIT_COMMAND = "/exit"

# The pane needs a beat between the Escape that cancels the turn and the
# command that quits it — text submitted while the cancel is still settling
# is read back as a steering message for the turn instead of as a command.
_SETTLE_SEC = 0.4

# How long to watch for the process to actually go away after `/exit` is
# submitted. The operator was promised the session would END, so `closed` has
# to mean "it is gone", not "we pressed the keys" — a claude that ignored the
# command (or a build whose quit needs a second confirmation) would otherwise
# be reported as closed while it keeps running and re-emitting spans.
#
# The budget is set from measurement, not taste: claude runs its SessionEnd
# hooks (regin's own trace capture among them) BEFORE leaving the tty, and a
# timed live close took 2.5s to change `pane_current_command`. A window merely
# "big enough" on an idle box turns every slow-but-successful close into a
# false "it is still running", so leave the headroom — nothing waits the full
# window except a session that genuinely did not quit.
_EXIT_POLL_SEC = 0.5
_EXIT_ATTEMPTS = 12

# ...and a wall-clock ceiling on top of the attempt count, because each probe
# can itself burn `delivery._TMUX_TIMEOUT_SEC`. Counting attempts alone, a
# wedged tmux socket would hold this request — and a Flask worker — for ~40s,
# four at a time under the trace list's batch pool.
_EXIT_WATCH_SEC = 8.0


def _tmux_liveness(trace_id: str) -> str:
    """`delivery.session_liveness`, degraded to UNKNOWN on any exception.

    The probe shells out to tmux and reads the pane registry, so it can fail
    for reasons that say nothing about the session. `manageability` promises
    callers it never raises (they use it to *word a confirmation*, and a
    probe that threw would block the very action it describes), so a broken
    probe has to resolve to an answer rather than propagate — and the honest
    answer is "could not tell", which is not the same as "it is gone".
    """
    try:
        return delivery.session_liveness(trace_id)
    except Exception:
        log.error("tmux_liveness_probe_failed", exc_info=True)
        return delivery.UNKNOWN


def _tmux_is_live(trace_id: str) -> bool:
    return _tmux_liveness(trace_id) == delivery.LIVE


def manageability(trace_id: str) -> dict:
    """Which tier can still end `trace_id`, and whether it is reachable now.

    Returns `{tier, live, starting, detail}`. `tier` is None when nothing
    here can act — an unowned session, a dead pane, or a bridge switched off
    — which is the ordinary answer for the interrupted sessions Close exists
    for. Read-only, and never raises.

    A *starting* SDK run reports its tier with `live` False on purpose:
    `is_sdk_owned` covers reserved-but-not-yet-connected ids, but only a
    registered run can be stopped, so promising a shutdown there would send
    the caller at a control that can only answer "no live agent session".
    """
    if agent_sdk.is_starting(trace_id):
        return {"tier": TIER_SDK, "live": False, "starting": True,
                "detail": "agent run is still starting; no channel to stop yet"}
    if agent_sdk.is_sdk_owned(trace_id):
        return {"tier": TIER_SDK, "live": True, "starting": False,
                "detail": "regin-launched agent run, still owned by this server"}
    if _tmux_is_live(trace_id):
        return {"tier": TIER_TMUX, "live": True, "starting": False,
                "detail": "live claude in a tmux pane regin can type into"}
    return {"tier": None, "live": False, "starting": False,
            "detail": "no manageable tier holds this session"}


def _await_exit(trace_id: str) -> str:
    """Watch the pane until it demonstrably stops running claude; returns the
    last reading (`delivery.GONE` / `LIVE` / `UNKNOWN`).

    The registry's `pane_pid` cannot answer this — it is the pane's SHELL,
    which outlives every claude that runs in it — so the evidence is the
    pane's foreground command, the same reading that declared the session
    live. It demands the POSITIVE `GONE`: a probe that merely failed (a tmux
    call that timed out on a loaded box) would otherwise settle the row while
    the agent is still running, which is the failure this watch exists for.
    """
    state = delivery.UNKNOWN
    deadline = time.monotonic() + _EXIT_WATCH_SEC
    for _ in range(_EXIT_ATTEMPTS):
        time.sleep(_EXIT_POLL_SEC)
        state = _tmux_liveness(trace_id)
        if state == delivery.GONE or time.monotonic() >= deadline:
            return state
    return state


def _close_tmux(trace_id: str) -> dict:
    """Cancel the turn in flight, type claude's quit command, confirm it quit.

    Three steps rather than one. `/exit` submitted mid-turn is queued as a
    steering message, so the session would stay up with the quit text sitting
    in its transcript — hence the Escape first. That Escape, though, leaves
    the composer somewhere a naively typed command does not survive, which is
    what `as_command` handles (`delivery._COMPOSER_RESET_KEYS`,
    `_COMMAND_TERMINATOR`). Finally the exit is *verified*: delivery can only
    report that the keys were submitted, and both of those hazards failed
    silently by submitting something else.

    Every leg runs through the guarded delivery paths, so a disabled bridge,
    an unreachable pane or a spent rate-limit token comes back as a structured
    refusal.
    """
    escaped = delivery.deliver_key(trace_id, "Escape")
    if not escaped.delivered:
        return {"closed": False, "interrupted": False,
                "detail": f"interrupt refused: {escaped.detail}"}
    time.sleep(_SETTLE_SEC)
    quit_result = delivery.deliver(trace_id, _EXIT_COMMAND, as_command=True)
    if not quit_result.delivered:
        return {"closed": False, "interrupted": True,
                "detail": f"turn cancelled, but {_EXIT_COMMAND} refused: "
                          f"{quit_result.detail}"}
    state = _await_exit(trace_id)
    if state != delivery.GONE:
        unclosed = ("the pane is still running claude"
                    if state == delivery.LIVE
                    else "regin could not confirm the pane exited")
        return {"closed": False, "interrupted": True,
                "detail": f"turn cancelled and {_EXIT_COMMAND} submitted, "
                          f"but {unclosed}"}
    return {"closed": True, "interrupted": True,
            "detail": f"turn cancelled, {_EXIT_COMMAND} sent; the pane exited"}


def graceful_close(trace_id: str) -> dict:
    """End the live process behind `trace_id` the way its tier allows.

    Returns `{tier, live, closed, interrupted, detail}`. `closed` False with
    `tier` None is "there was nothing to close", not a failure — callers
    settle the trace row either way, since a session regin cannot reach is
    exactly the interrupted session the manual close was built for.

    `interrupted` separates the two ways a tmux close fails: one where the
    pane was never touched, and one where the turn in flight *was* cancelled
    but the quit did not land. The operator is owed that distinction — the
    second leaves a running agent whose turn regin killed.
    """
    state = manageability(trace_id)
    result = {"tier": state["tier"], "live": state["live"],
              "closed": False, "interrupted": False,
              "detail": state["detail"]}
    if not state["live"]:
        return result
    if state["tier"] == TIER_SDK:
        delivered, detail = agent_sdk.stop_run(trace_id)
        result.update(closed=delivered, detail=detail)
    else:
        result.update(_close_tmux(trace_id))
    log.write("live_session_shutdown", trace_id=trace_id, tier=result["tier"],
              closed=result["closed"], detail=result["detail"])
    return result
