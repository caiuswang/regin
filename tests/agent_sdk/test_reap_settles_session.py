"""What a killed server leaves behind (`store.reap_orphaned_runs`).

Stopping `regin serve` takes the SDK child with it, so no `session.end` is ever
emitted: the run row is reaped at the next boot, but the `sessions` row keeps
its launch-time `status='active'` / `ended_at IS NULL`. Every reader answers
from those two fields, so the session sits in the UI as permanently Live —
unsteerable (the registry that made it steerable died) and, because it never
looks finished, never offered as something to continue.

These pin the settling half: which rows it closes, which it must not, and what
the ended row then says.
"""

from __future__ import annotations

import os

import pytest

from lib.agent_sdk import registry, store

_RUN = "sdk-reaptest01"
_CHILD = "dddddddd-0000-0000-0000-00000000000d"
_SEEN = "2026-01-01T03:04:05"


def _seed_session(trace_id: str, **over) -> None:
    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    fields = {"trace_id": trace_id, "started_at": _SEEN, "last_seen": _SEEN,
              "status": "active", "is_test": 0}
    fields.update(over)
    with SessionLocal() as session:
        session.add(SessionModel(**fields))
        session.commit()


def _row(trace_id: str):
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    with SessionLocal() as session:
        return session.exec(
            select(SessionModel).where(
                SessionModel.trace_id == trace_id)).first()


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_RUN)


def test_the_reap_settles_both_ids_of_the_run_it_orphans(flask_client):
    """The `sdk-…` half and the child it is also traced as. The session list
    and the trace view route on different ones of the two, so closing either
    alone leaves the other still claiming to be live."""
    _seed_session(_RUN)
    _seed_session(_CHILD)
    store.upsert_run(_RUN, status="running")
    store.set_cli_session(_RUN, _CHILD)

    store.reap_orphaned_runs()

    for trace_id in (_RUN, _CHILD):
        row = _row(trace_id)
        assert row.status == "ended"
        assert row.ended_reason == "server restarted"


def test_the_end_is_stamped_at_last_seen_not_at_reap_time(flask_client):
    """`last_seen` is the last moment regin has evidence the session existed.
    The reap runs at the next boot, which may be days later — dating the
    session to that would invent a stretch it was never running for."""
    _seed_session(_RUN)
    store.upsert_run(_RUN, status="running")

    store.reap_orphaned_runs()

    assert _row(_RUN).ended_at == _SEEN


def test_a_session_that_recorded_its_own_end_is_left_alone(flask_client):
    """That verdict came from the process that was actually there, and it
    outranks an inference drawn from a run row at boot."""
    _seed_session(_RUN, status="ended", ended_at="2026-01-01T09:00:00",
                  ended_reason="clear")
    store.upsert_run(_RUN, status="running")

    store.reap_orphaned_runs()

    row = _row(_RUN)
    assert row.ended_reason == "clear"
    assert row.ended_at == "2026-01-01T09:00:00"


def test_a_run_this_process_still_owns_keeps_its_live_session(flask_client):
    """The reap spares rows under this pid — an app built twice in one process
    would otherwise close the sessions it is itself running."""
    _seed_session(_RUN)
    store.upsert_run(_RUN, status="running", pid=os.getpid())

    store.reap_orphaned_runs()

    row = _row(_RUN)
    assert row.status == "active"
    assert row.ended_at is None


def test_end_trace_session_ignores_blank_ids(flask_client):
    """A run with no `cli_session_id` passes a None through the id list; it
    must not widen the `IN` clause to every row."""
    _seed_session(_CHILD)

    assert store.end_trace_session("", reason="server restarted") == []
    assert _row(_CHILD).status == "active"


def test_a_session_orphaned_by_an_earlier_boot_is_still_settled(flask_client):
    """The reap only touches rows it finds `running`. A run already `exited`
    from a previous boot would never be reached by a settle wired to that
    transition, and its session would stay green forever — which is exactly the
    state this was reported from."""
    _seed_session(_CHILD)
    store.upsert_run(_RUN, status="running")
    store.set_cli_session(_RUN, _CHILD)
    store.upsert_run(_RUN, status="exited", detail="server restarted")

    assert store.settle_ended_run_sessions() >= 1
    assert _row(_CHILD).status == "ended"


def test_each_session_is_stamped_with_its_own_run_reason(flask_client):
    """A blanket reason would tell an operator their session died in a restart
    when the run had failed on its own."""
    _seed_session(_RUN)
    store.upsert_run(_RUN, status="failed", detail="claude not on PATH")

    store.settle_ended_run_sessions()

    assert _row(_RUN).ended_reason == "claude not on PATH"


def test_a_starting_run_is_not_settled(flask_client):
    """`starting` is a live state — the child is being spawned. Ending its
    session would strand a run that is about to emit its own `session.start`."""
    _seed_session(_RUN)
    store.upsert_run(_RUN, status="starting")

    store.settle_ended_run_sessions()

    assert _row(_RUN).status == "active"


def test_a_stale_sibling_row_does_not_end_a_running_run_session(flask_client):
    """`cli_session_id` carries a plain index, so an older `exited` row and a
    currently `running` one can name the same child — the shape a relaunch
    leaves behind. Settling on the strength of the stale row would end the
    conversation this process is running: this module's own bug, inverted."""
    _seed_session(_CHILD)
    store.upsert_run("sdk-stale01", status="exited", detail="server restarted")
    store.set_cli_session("sdk-stale01", _CHILD)
    store.upsert_run(_RUN, status="running", pid=os.getpid())
    store.set_cli_session(_RUN, _CHILD)

    store.reap_orphaned_runs()

    row = _row(_CHILD)
    assert row.status == "active"
    assert row.ended_at is None


def test_a_session_a_terminal_is_driving_is_not_settled(flask_client,
                                                        monkeypatch):
    """A session regin once launched can be reopened by the operator with
    `claude --resume` in a terminal. Nothing in `agent_runs` records that — the
    run stays `exited` — so only the pane registry can say it is live."""
    from lib.agent_sdk import store as sdk_store

    _seed_session(_CHILD)
    store.upsert_run(_RUN, status="exited", detail="server restarted")
    store.set_cli_session(_RUN, _CHILD)
    monkeypatch.setattr(sdk_store, "_still_claimed", lambda tid: tid == _CHILD)

    store.settle_ended_run_sessions()

    assert _row(_CHILD).status == "active"


@pytest.mark.parametrize("probe, why", [
    (lambda _tid: (_ for _ in ()).throw(RuntimeError("registry down")),
     "a raised probe"),
    (lambda _tid: (None, False), "a query the store could not answer"),
])
def test_an_unreadable_pane_registry_spares_the_session(flask_client,
                                                        monkeypatch, probe,
                                                        why):
    """Fail closed on BOTH shapes. `resolve_reachable_pane` reports a locked DB
    or a drifted schema as `(None, answered=False)` rather than raising — read
    through `get_reachable_pane`, which drops that flag, an unreadable registry
    would look exactly like "no terminal holds this session" and the sweep
    would end a live one, stamped "server restarted"."""
    from lib.agent_bridge import store as bridge_store

    _seed_session(_RUN)
    store.upsert_run(_RUN, status="exited", detail="server restarted")
    monkeypatch.setattr(bridge_store, "resolve_reachable_pane", probe)

    store.settle_ended_run_sessions()

    assert _row(_RUN).status == "active", why


def test_a_run_whose_pid_is_still_alive_is_not_reaped(flask_client):
    """A row naming a LIVE process belongs to a peer, not to a corpse.

    `pid != os.getpid()` is true of every run a *running* `regin serve` owns,
    so any second process that builds the app — a CLI, a test pointed at the
    real DB — used to reap that server's live runs and, in the sweep that
    follows, settle the sessions they were driving. The operator then met a
    conversation the server was still steering, marked ended.
    """
    _seed_session(_RUN)
    # The parent of this process: alive by construction, and never our own pid.
    store.upsert_run(_RUN, status="running", pid=os.getppid())

    assert store.reap_orphaned_runs() == 0

    assert _row(_RUN).status == "active"


def test_a_run_whose_pid_is_gone_is_still_reaped(flask_client):
    """The sweep's whole purpose survives the liveness probe."""
    _seed_session(_RUN)
    store.upsert_run(_RUN, status="running", pid=_dead_pid())

    assert store.reap_orphaned_runs() == 1

    assert _row(_RUN).status == "ended"


def _dead_pid() -> int:
    """A pid no process holds: spawned, exited, and reaped by `run`.

    A real child rather than a large number picked as "probably free" — the
    reap turns on this pid genuinely being absent, and a guess that collided
    with a live process would fail only sometimes.
    """
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid
