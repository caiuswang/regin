"""The status `/live` shows for a run regin owns (`registry.run_phase`).

Every other session's phase is inferred from span timestamps, and for this tier
that inference is actively wrong: a five-minute tool call emits no spans, so a
child plainly at work ages past the stale threshold and the card says
"inactive". The runner is in the same process and knows, so for these sessions
it is asked instead.

These tests pin what it answers, and that the serve path lets it win — while
still falling back to the heuristic for the sessions regin merely traces, which
is all a terminal session has.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_sdk import registry


class _Runner:
    """Just the surface `run_phase` reads."""

    def __init__(self, *, turn=False, stopping=False):
        self.turn_in_flight = turn
        self.stop_requested = stopping


_TRACE = "sdk-phase-1"


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)
    registry.release_run(_TRACE)


def _park(kind, trace_id=_TRACE):
    """Park a call of `kind` on `trace_id` and return its registry id."""
    loop = asyncio.new_event_loop()
    try:
        ask = registry.PendingAsk(
            trace_id=trace_id, tool_use_id=f"tu-{kind}", tool_input={},
            future=loop.create_future(), loop=loop, kind=kind)
        return registry.register_ask(ask)
    finally:
        loop.close()


def test_a_session_regin_does_not_own_has_no_verdict():
    """None, not a guess: the caller keeps its span heuristic, which is the
    only thing a terminal session has."""
    assert registry.run_phase("someone-elses-session") is None


def test_a_claimed_id_whose_child_is_not_up_yet_is_starting():
    """`is_sdk_owned` is already true here — the launch holds the id — but
    there is no runner to ask, and "working" would be a lie about a child that
    has not spoken."""
    registry.reserve_run(_TRACE, object())

    assert registry.run_phase(_TRACE) == 'starting'


def test_an_idle_run_is_idle_however_long_it_has_been_quiet():
    """The heuristic's whole failure mode: silence is not staleness for a
    session whose runner is right here."""
    registry.register_run(_TRACE, _Runner())

    assert registry.run_phase(_TRACE) == 'idle'


def test_a_turn_in_flight_is_working():
    registry.register_run(_TRACE, _Runner(turn=True))

    assert registry.run_phase(_TRACE) == 'working'


def test_a_stop_already_asked_for_outranks_the_turn_it_is_ending():
    registry.register_run(_TRACE, _Runner(turn=True, stopping=True))

    assert registry.run_phase(_TRACE) == 'stopping'


@pytest.mark.parametrize("kind,expected", [
    ('question', 'waiting-input'),
    ('plan', 'waiting-permission'),
    ('tool', 'waiting-permission'),
])
def test_a_park_is_read_from_the_registry_not_guessed_from_spans(kind,
                                                                 expected):
    """`_asks` is ground truth. The span heuristic calls a park only when its
    PENDING row happens to be the newest span, so a lost or demoted span read
    as "working" while the child was genuinely blocked."""
    registry.register_run(_TRACE, _Runner(turn=True))
    _park(kind)

    assert registry.run_phase(_TRACE) == expected


def test_a_question_outranks_a_permission_parked_alongside_it():
    """One assistant message routinely carries several tool calls, so a session
    can hold both. The question is the one only the operator can clear."""
    registry.register_run(_TRACE, _Runner(turn=True))
    _park('tool')
    _park('question')

    assert registry.run_phase(_TRACE) == 'waiting-input'


def test_a_park_outranks_the_stop_that_has_not_landed_yet():
    """A stop cannot complete while a park holds the turn open, so the park is
    what the operator still has to act on."""
    registry.register_run(_TRACE, _Runner(turn=True, stopping=True))
    _park('question')

    assert registry.run_phase(_TRACE) == 'waiting-input'


def test_the_phase_is_reachable_through_the_child_session_alias():
    """An operator can be on `/live` under the id the CLI reports rather than
    the run's own."""
    registry.register_run(_TRACE, _Runner(turn=True))
    registry.register_alias("child-session", _TRACE)

    assert registry.run_phase("child-session") == 'working'


# ── The serve path ────────────────────────────────────────────────────

def test_the_runner_verdict_replaces_the_span_derived_main_phase():
    from web.blueprints.trace import sessions

    phase, agent_phase = sessions._session_phase({}, [], False, 'working')

    assert agent_phase['main'] == 'working'
    assert phase == 'working'


def test_an_owned_run_is_never_reported_inactive_stale():
    """The bug this exists to fix: `ended=True` plus no activity is exactly the
    input that produced "inactive" for a session that was plainly working."""
    from web.blueprints.trace import sessions

    _, agent_phase = sessions._session_phase({}, [], True, 'working')

    assert agent_phase['main'] == 'working'


def test_without_an_override_the_heuristic_still_decides():
    from web.blueprints.trace import sessions

    _, agent_phase = sessions._session_phase({}, [], True, None)

    assert agent_phase['main'] == 'ended'


def test_a_subagent_still_rolls_up_over_an_overridden_main():
    """The override speaks for main only — a blocked subagent has to surface
    while main reads idle, which is the rollup's whole job."""
    from web.blueprints.trace import sessions

    roster = [{'agent_id': 'a1', 'status': 'waiting'}]
    phase, agent_phase = sessions._session_phase({}, roster, False, 'idle')

    assert agent_phase['main'] == 'idle'
    assert phase == 'waiting-input'


def test_the_new_phases_rank_above_idle_in_the_rollup():
    """A finished subagent must not let a starting/stopping session roll up to
    the quieter verdict."""
    from web.blueprints.trace import sessions

    for override in ('starting', 'stopping'):
        roster = [{'agent_id': 'a1', 'status': 'finished'}]
        phase, _ = sessions._session_phase({}, roster, False, override)

        assert phase == override


def test_the_serve_path_asks_the_registry_and_survives_it_failing(monkeypatch):
    """Every SDK read on this path is best-effort: the tier is optional and off
    by default, and a session's status line must not depend on it."""
    from lib import agent_sdk
    from web.blueprints.trace import sessions

    monkeypatch.setattr(agent_sdk, "run_phase", lambda tid: 'stopping')
    assert sessions._owned_run_phase("sdk-run") == 'stopping'

    def _boom(tid):
        raise RuntimeError("no sdk here")

    monkeypatch.setattr(agent_sdk, "run_phase", _boom)
    assert sessions._owned_run_phase("sdk-run") is None
