"""Live status for a run regin owns (`runner.live_state`,
`registry.run_live_state`).

A run's child is spawned with the bridge off, so it never registers a tmux
pane and the screen the terminal tier reads simply does not exist for it: the
NOW zone read "status unavailable" and the picker's dot never lit for the one
tier regin drives itself. The runner is in the same process and knows, so it
answers in the screen's own shape.

These tests pin what it reports, and the three-way tier answer that keeps a run
from ever being sent to tmux for an answer no pane can give.
"""

from __future__ import annotations

import time

import pytest

from lib.agent_events import ToolCall, ToolResult
from lib.agent_sdk import registry, runner as runner_mod, store

_TRACE = "sdk-live-1"


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)
    registry.release_run(_TRACE)


@pytest.fixture
def no_runs(monkeypatch):
    """No durable run rows, so the tier test answers off `agent_runs` alone
    without reaching the developer's own DB."""
    known: dict = {}
    monkeypatch.setattr(store, "find_run", known.get)
    return known


class _Loop:
    """Just the reachability surface `_live_runner` reads."""

    def __init__(self, running=True, closed=False):
        self._running = running
        self._closed = closed

    def is_running(self):
        return self._running

    def is_closed(self):
        return self._closed


class _Runner:
    def __init__(self, state, loop=None):
        self._state = state
        self.loop = loop or _Loop()

    def live_state(self):
        return self._state


def _runner(**kwargs):
    return runner_mod.AgentRunner(_TRACE, **kwargs)


# ── what the runner reports ──────────────────────────────────────

def test_a_turn_in_flight_reports_busy():
    run = _runner()
    run._open_turn()

    state = run.live_state()

    assert state.busy is True
    assert state.verb


def test_the_elapsed_clock_runs_from_when_the_turn_opened():
    run = _runner()
    turn = run._open_turn()
    turn.started = time.monotonic() - 42

    assert run.live_state().elapsed_sec == 42


def test_between_turns_it_reports_not_busy_rather_than_no_verdict():
    """`busy=False` is a verdict: the runner knows the session is at rest, so
    the card must read idle rather than "status unavailable"."""
    run = _runner()

    state = run.live_state()

    assert state.busy is False
    assert state.elapsed_sec is None


def test_a_background_subagent_between_turns_reports_busy():
    """The parent sits at the prompt while the agent it delegated to works;
    the picker dot must agree with `run_phase`, which says `working`."""
    from tests.agent_sdk.test_background_stream import (
        TaskNotification, TaskStarted)
    run = _runner()
    run._tasks.observe(TaskStarted("t1"))

    state = run.live_state()
    assert state.busy is True
    assert state.verb == 'Waiting on subagent'
    assert state.elapsed_sec is None

    run._tasks.observe(TaskNotification("t1", status="completed"))
    assert run.live_state().busy is False


def test_a_sink_is_not_the_agent_working():
    """A placeholder for an abandoned turn's late result is nobody's work —
    counting it would pin the card at "running" until the next prompt."""
    run = _runner()
    run._open_turn(sink=True)

    assert run.live_state().busy is False


def test_the_in_flight_tool_is_the_call_that_has_not_returned():
    """A result mid-turn retires that call; the one still open keeps being
    reported."""
    run = _runner()
    turn = run._open_turn()
    run._name_tool(ToolCall(trace_id=_TRACE, tool_name="Read",
                            tool_use_id="a"), turn)
    run._name_tool(ToolCall(trace_id=_TRACE, tool_name="Bash",
                            tool_use_id="b"), turn)
    run._name_tool(ToolResult(trace_id=_TRACE, tool_use_id="b"), turn)

    assert run.live_state().tool == "Read"


def test_no_tool_is_reported_while_no_turn_is_open():
    """A call abandoned with its turn never returns a result, so its name
    stays behind — reporting it would show a tool running on an idle session."""
    run = _runner()
    turn = run._open_turn()
    run._name_tool(ToolCall(trace_id=_TRACE, tool_name="Read",
                            tool_use_id="a"), turn)
    run._close_turn(turn)

    assert run.live_state().tool is None


def test_a_call_abandoned_by_an_earlier_turn_is_not_the_new_turns_tool():
    """An interrupted or denied call never sends a result, so it stays in the
    session-wide naming ledger for good. Reading the ledger made the next turn
    report a tool it never issued, at the moment the operator is most likely
    watching — the turn's own record dies with the turn instead."""
    run = _runner()
    abandoned = run._open_turn()
    run._name_tool(ToolCall(trace_id=_TRACE, tool_name="WebFetch",
                            tool_use_id="u2"), abandoned)
    run._close_turn(abandoned)
    run._open_turn()

    assert run.live_state().busy is True
    assert run.live_state().tool is None


def test_a_late_result_is_still_named_after_its_turn_is_gone():
    """The reason the naming ledger is left alone: `_leave_a_sink` exists so an
    abandoned turn's late result still lands, and it is named from there.
    Pruning the ledger with the turn would file it as `unknown`."""
    run = _runner()
    abandoned = run._open_turn()
    run._name_tool(ToolCall(trace_id=_TRACE, tool_name="WebFetch",
                            tool_use_id="u2"), abandoned)
    run._close_turn(abandoned)

    named = run._name_tool(ToolResult(trace_id=_TRACE, tool_use_id="u2"))

    assert named.tool_name == "WebFetch"


def test_it_names_the_session_the_operator_is_looking_at():
    run = _runner(cwd="/tmp/wt")
    run._session_id = "child-abc"

    state = run.live_state()

    assert state.session_id == "child-abc"
    assert state.cwd == "/tmp/wt"


def test_a_field_this_tier_does_not_carry_is_none_not_zero():
    """The screen's decoration, which the runner has no equivalent for. A
    default would render as a real measurement of nothing."""
    run = _runner()
    run._open_turn()

    state = run.live_state()

    assert state.tokens is None
    assert state.ctx_pct is None
    assert state.draft is None


# ── the tier answer ──────────────────────────────────────────────

def test_a_session_regin_never_launched_is_not_this_tier(no_runs):
    """The only answer that sends a caller to tmux."""
    answer = registry.run_live_state("someone-elses-session")

    assert answer.sdk is False
    assert answer.state is None


def test_a_run_this_process_owns_answers_with_its_runners_state(no_runs):
    from lib.agent_bridge.screen_state import ScreenState

    state = ScreenState(busy=True, verb="Working")
    registry.register_run(_TRACE, _Runner(state))

    assert registry.run_live_state(_TRACE) == registry.RunLiveState(True, state)


def test_a_run_this_process_does_not_own_is_still_this_tier(no_runs):
    """The case that must not collapse into "not ours": the run outlived the
    server that owned it, and a caller reading it as a terminal session would
    probe tmux for a child that never had a pane."""
    no_runs[_TRACE] = {"trace_id": _TRACE, "status": "running"}

    answer = registry.run_live_state(_TRACE)

    assert answer.sdk is True
    assert answer.state is None


def test_a_registered_run_whose_loop_has_gone_gives_no_verdict(no_runs):
    """A runner is unregistered as it stops, but one whose loop died under it
    is still listed — and the turn it was holding would leave a busy dot lit
    on a session nothing can move."""
    from lib.agent_bridge.screen_state import ScreenState

    registry.register_run(_TRACE, _Runner(ScreenState(busy=True),
                                          loop=_Loop(running=False)))

    answer = registry.run_live_state(_TRACE)

    assert answer.sdk is True
    assert answer.state is None


def test_a_claimed_id_whose_child_is_not_up_yet_is_this_tier(no_runs):
    registry.reserve_run(_TRACE, object())

    answer = registry.run_live_state(_TRACE)

    assert answer.sdk is True
    assert answer.state is None


def test_the_state_is_reachable_through_the_child_session_alias(no_runs):
    from lib.agent_bridge.screen_state import ScreenState

    registry.register_run(_TRACE, _Runner(ScreenState(busy=True)))
    registry.register_alias("child-session", _TRACE)

    assert registry.run_live_state("child-session").state.busy is True


# ── the sweep ────────────────────────────────────────────────────

def test_the_sweep_keys_a_run_by_the_child_session_id(no_runs):
    """The id the picker holds. Keying by the run's own `sdk-…` id would light
    a dot on a row the list does not show."""
    from lib.agent_bridge.screen_state import ScreenState

    registry.register_run(_TRACE, _Runner(ScreenState(busy=True)))
    registry.register_alias("child-session", _TRACE)

    assert list(registry.run_live_states()) == ["child-session"]


def test_a_run_whose_child_has_not_named_itself_keys_by_the_run_id(no_runs):
    from lib.agent_bridge.screen_state import ScreenState

    registry.register_run(_TRACE, _Runner(ScreenState(busy=False)))

    assert list(registry.run_live_states()) == [_TRACE]


def test_a_run_with_no_verdict_is_absent_from_the_sweep(no_runs):
    registry.register_run(_TRACE, _Runner(None, loop=_Loop(closed=True)))

    assert registry.run_live_states() == {}
