"""A child of a live run that this process never aliased.

`registry._aliases` only learns a child while the process is watching it
arrive, and a run outlives that: restart the server, resume the run, and every
earlier child of it is a session the registry has no link for while
`agent_run_sessions` still does. Two resolvers then answer the same question
differently — `resumable` reads the DB and refuses ("that run is live"), the
registry reads memory and reports nothing steerable — and the trace page draws
the intersection: no Resume button, no composer, no notice. An empty rail.

The trigger is here too. `agent_runs.cli_session_id` pins the FIRST child
forever, so "resume this run" used to reopen the conversation the operator
abandoned and strand the one they were reading — manufacturing the very state
above from a single click on Resume.
"""

from __future__ import annotations

import pytest

from lib.agent_sdk import registry, store
from web.blueprints import agent_runs as routes

_RUN = "sdk-stranded1"
_FIRST = "child-first-half"
_LATEST = "child-second-half"


@pytest.fixture(autouse=True)
def clean_registry():
    """The registry is module state; a leaked run would own the next test."""
    registry._durable.clear()
    yield
    registry._runs.clear()
    registry._reserved.clear()
    registry._aliases.clear()
    registry._live_children.clear()
    registry._durable.clear()


def _link(run: str, *children: str) -> None:
    store.upsert_run(run, status="running")
    for child in children:
        store.set_cli_session(run, child)


def _hold(run: str, live_child: str) -> None:
    """Put `run` in the registry as this process holds it after a resume —
    driving `live_child`, with no alias for any earlier child."""
    registry._runs[run] = object()
    registry._aliases[live_child] = run
    registry._live_children[run] = live_child


# ── the cause: which conversation "resume this run" means ─────


def test_latest_cli_session_is_the_newest_child(tmp_db):
    _link(_RUN, _FIRST, _LATEST)

    assert store.latest_cli_session(_RUN) == _LATEST
    # …and the pinned column is untouched, which other readers depend on.
    assert store.cli_session_for(_RUN) == _FIRST


def test_latest_cli_session_falls_back_to_the_pinned_column(tmp_db):
    store.upsert_run(_RUN, status="running")
    assert store.latest_cli_session(_RUN) is None
    _link(_RUN, _FIRST)
    assert store.latest_cli_session(_RUN) == _FIRST


def test_resuming_from_a_later_child_resumes_that_child(tmp_db):
    """The reported bug: the operator is reading `_LATEST` and clicks Resume,
    and the route reopened `_FIRST` — a conversation a day older."""
    _link(_RUN, _FIRST, _LATEST)

    assert routes._resume_target(_LATEST) == (_LATEST, _RUN)


def test_resuming_by_the_runs_own_id_takes_the_newest_child(tmp_db):
    """`sdk-…` names no conversation, so it has to pick one — the one the run
    was last on, not the one it started with."""
    _link(_RUN, _FIRST, _LATEST)

    assert routes._resume_target(_RUN) == (_LATEST, _RUN)


def test_a_run_that_named_no_child_still_refuses(tmp_db):
    store.upsert_run(_RUN, status="exited")

    with pytest.raises(routes._BadRequest):
        routes._resume_target(_RUN)


# ── the symptom: the stranded child's rail ────────────────────


def test_a_child_the_registry_never_saw_resolves_through_the_db(tmp_db):
    _link(_RUN, _FIRST, _LATEST)
    _hold(_RUN, _LATEST)

    assert registry.owning_run(_FIRST) == _RUN
    assert registry.is_sdk_owned(_FIRST) is True
    # …and it is told where the conversation actually continued, which is what
    # puts the composer back on the page.
    assert registry.live_child(_FIRST) == _LATEST


def test_the_live_child_itself_reports_no_successor(tmp_db):
    _link(_RUN, _FIRST, _LATEST)
    _hold(_RUN, _LATEST)

    assert registry.live_child(_LATEST) is None


def test_a_child_of_a_dead_run_is_not_owned(tmp_db):
    """The DB link is durable and outlives the process that made it; only the
    registry says whether anything is holding the run NOW."""
    _link(_RUN, _FIRST, _LATEST)
    registry._runs["sdk-someone-else"] = object()

    assert registry.is_sdk_owned(_FIRST) is False
    assert registry.live_child(_FIRST) is None


def test_nothing_live_skips_the_lookup_entirely(tmp_db, monkeypatch):
    """`owning_run` sits under every steer and every liveness read. With no
    run held the answer cannot change, so it must not cost a query."""
    _link(_RUN, _FIRST, _LATEST)

    def _boom(_session_id):
        raise AssertionError("owning_run queried the DB with nothing live")

    monkeypatch.setattr(store, "sdk_trace_for_cli_session", _boom)
    assert registry.owning_run(_FIRST) == _FIRST


def test_an_unknown_session_is_unchanged(tmp_db):
    _link(_RUN, _FIRST, _LATEST)
    _hold(_RUN, _LATEST)

    assert registry.owning_run("some-hook-only-session") == "some-hook-only-session"
    assert registry.is_sdk_owned("some-hook-only-session") is False


# ── the two windows the rail used to reappear in ──────────────


def test_a_run_held_only_as_a_reservation_still_resolves_its_children(tmp_db):
    """The resume's own startup window — precisely when a page is sitting on a
    child of the run being relaunched."""
    _link(_RUN, _FIRST, _LATEST)
    registry.reserve_run(_RUN)

    assert registry.owning_run(_FIRST) == _RUN
    assert registry.is_sdk_owned(_FIRST) is True


def test_is_driving_is_false_for_a_retired_child(tmp_db):
    """What stops the map poll clearing a true `ended_at`."""
    _link(_RUN, _FIRST, _LATEST)
    _hold(_RUN, _LATEST)

    assert registry.is_driving(_LATEST) is True
    assert registry.is_driving(_FIRST) is False
    # The run's own id names the run, not a conversation, so it always is.
    assert registry.is_driving(_RUN) is True


def test_is_driving_is_false_before_a_new_child_reports_itself(tmp_db):
    """The window `live_child` alone could not see: the run is held, no child
    is recorded yet, and nothing proves it moved to the ended session."""
    _link(_RUN, _FIRST, _LATEST)
    registry._runs[_RUN] = object()

    assert registry.live_child(_FIRST) is None
    assert registry.is_driving(_FIRST) is False


def test_a_run_coming_alive_reopens_cached_misses(tmp_db):
    """A miss cached while nothing was live must not outlive the resume that
    makes it a hit — that is the stranded child, cached wrong forever."""
    _link(_RUN, _FIRST, _LATEST)
    assert registry.owning_run(_FIRST) == _FIRST      # nothing live: a miss

    registry.register_run(_RUN, object())
    assert registry.owning_run(_FIRST) == _RUN
