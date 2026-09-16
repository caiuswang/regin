"""`_session_active_clause` — the SQL half of the session-liveness rule.

Its frontend twin is `sessionLiveness` in frontend/src/utils/sessionActivity.js;
the `active=` filter, the `active_count` facet and every status chip read from
one of the two, so a disagreement shows up as a badge contradicting the count
printed above it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from lib.orm.models.trace import Session as SessionModel
from web.blueprints.trace.sessions import _session_active_clause


def _stamp(minutes_ago: float) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def rows(tmp_path, monkeypatch):
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(tmp_path / 'trace.db'))
    db_module.init_db()
    from lib.orm.engine import SessionLocal

    seed = {
        # The reported bug: /exit landed but SessionEnd never fired, so the
        # marker says active forever while nothing has been ingested.
        'lost-end': ('active', _stamp(180)),
        'really-live': ('active', _stamp(0.5)),
        'ended-recent': ('ended', _stamp(0.5)),
        'unmarked-fresh': (None, _stamp(0.5)),
        'unmarked-stale': (None, _stamp(180)),
    }
    with SessionLocal() as s:
        for tid, (status, last_seen) in seed.items():
            s.add(SessionModel(trace_id=tid, status=status,
                               started_at=_stamp(240), last_seen=last_seen))
        s.commit()
    return SessionLocal


def _ids(SessionLocal, want_active):
    with SessionLocal() as s:
        return set(s.exec(select(SessionModel.trace_id)
                          .where(_session_active_clause(want_active))).all())


def test_a_stale_active_marker_is_not_counted_active(rows):
    assert 'lost-end' not in _ids(rows, True)
    assert 'lost-end' in _ids(rows, False)


def test_a_live_session_survives_the_recency_check(rows):
    assert 'really-live' in _ids(rows, True)


def test_ended_stays_inactive_however_recent(rows):
    assert 'ended-recent' not in _ids(rows, True)


def test_unmarked_rows_still_fall_back_to_recency(rows):
    active = _ids(rows, True)
    assert 'unmarked-fresh' in active and 'unmarked-stale' not in active


def test_the_two_branches_partition_every_row(rows):
    active, inactive = _ids(rows, True), _ids(rows, False)
    with rows() as s:
        every = set(s.exec(select(SessionModel.trace_id)).all())
    assert active | inactive == every
    assert active & inactive == set()
