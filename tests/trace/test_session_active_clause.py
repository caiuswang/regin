"""`_session_active_clause` — the ONE definition of "active" on the server.

The `active=` filter, the `active_count` facet and the green badge all share
it, so a row that falls into neither branch disappears from both filtered
pages and is counted by nothing. That partition is the property worth pinning:
the two branches were rewritten from an explicit `last_seen < cutoff` into
`not_(alive)` when live SDK runs were folded in, and a complement is easy to
get subtly wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def _seed(db_module, rows):
    from lib.orm.engine import SessionLocal
    from lib.orm.models import Session as SessionModel
    with SessionLocal() as db:
        for tid, status, last_seen, started in rows:
            db.add(SessionModel(trace_id=tid, status=status,
                                last_seen=last_seen, started_at=started))
        db.commit()


def _split(monkeypatch, live_ids):
    from lib.orm.engine import SessionLocal
    from lib.orm.models import Session as SessionModel
    from web.blueprints.trace.sessions import listing as S
    monkeypatch.setattr(S, '_live_sdk_session_ids', lambda: live_ids)
    with SessionLocal() as db:
        def ids(want):
            return {r[0] for r in db.query(SessionModel.trace_id)
                    .filter(S._session_active_clause(want)).all()}
        return ids(True), ids(False)


def test_active_and_inactive_partition_every_row(tmp_db, monkeypatch):
    now = datetime.now()
    fresh = (now - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%S')
    stale = (now - timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%S')
    rows = [
        ('p_fresh', 'active', fresh, stale),
        ('p_stale', 'active', stale, stale),
        ('p_ended_fresh', 'ended', fresh, stale),
        ('p_ended_stale', 'ended', stale, stale),
        ('p_null_status', None, stale, stale),
        ('p_sdk_stale', 'active', stale, stale),
        ('p_sdk_ended', 'ended', stale, stale),
    ]
    _seed(tmp_db, rows)
    active, inactive = _split(monkeypatch, {'p_sdk_stale', 'p_sdk_ended'})

    all_ids = {r[0] for r in rows}
    assert not (active & inactive), 'a row landed in BOTH branches'
    assert all_ids - active - inactive == set(), 'a row vanished from both'

    # The point of folding the registry in: silence is not evidence against a
    # run regin is holding.
    assert 'p_sdk_stale' in active
    # …but `_aliases` outlives a child `/clear` retired, so the runner still
    # answers for an ended row. `not_ended` is what keeps it out.
    assert 'p_sdk_ended' in inactive
    assert active == {'p_fresh', 'p_sdk_stale'}


def test_the_clause_is_unchanged_without_a_live_runner(tmp_db, monkeypatch):
    """With no SDK tier (the default — it is gated off), the clause must be
    exactly the recency rule it always was."""
    now = datetime.now()
    fresh = (now - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%S')
    stale = (now - timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%S')
    _seed(tmp_db, [('p_fresh', 'active', fresh, stale),
                   ('p_stale', 'active', stale, stale)])
    active, inactive = _split(monkeypatch, set())
    assert active == {'p_fresh'}
    assert inactive == {'p_stale'}
