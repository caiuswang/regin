"""Tests for inbox retention: store.prune_messages / message_stats and the
opt-in auto-retention enforced by record_message."""

from datetime import datetime, timedelta

import pytest

from lib.agent_messages import store
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.agent_messages import AgentMessage


def _seed(*, trace_id="s1", created_days_ago=0, dismissed=False, pinned=False,
          is_test=False, msg_key=None) -> int:
    """Insert one row directly with a controlled created_at; return its id."""
    ts = (datetime.now() - timedelta(days=created_days_ago)).isoformat()
    with SessionLocal() as session:
        row = AgentMessage(
            trace_id=trace_id, body="b", msg_type="progress",
            msg_key=msg_key, created_at=ts, updated_at=ts,
            dismissed_at=ts if dismissed else None,
            pinned=1 if pinned else 0, is_test=1 if is_test else 0)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _ids() -> set:
    with SessionLocal() as session:
        return set(session.exec(select(AgentMessage.id)).all())


def test_criteria_free_call_raises():
    with pytest.raises(ValueError):
        store.prune_messages()


def test_zero_matches_returns_zero():
    assert store.prune_messages(older_than_days=30) == 0


def test_age_boundary_only_strictly_older():
    young = _seed(created_days_ago=1)
    old = _seed(created_days_ago=40)
    deleted = store.prune_messages(older_than_days=30)
    assert deleted == 1
    assert young in _ids() and old not in _ids()


def test_keep_pinned_protects_pinned():
    pinned = _seed(created_days_ago=40, pinned=True)
    plain = _seed(created_days_ago=40)
    assert store.prune_messages(older_than_days=30) == 1
    assert pinned in _ids() and plain not in _ids()
    # keep_pinned=False deletes it too
    assert store.prune_messages(older_than_days=30, keep_pinned=False) == 1
    assert pinned not in _ids()


def test_dismissed_only():
    live = _seed(created_days_ago=40)
    gone = _seed(created_days_ago=40, dismissed=True)
    assert store.prune_messages(dismissed_only=True) == 1
    assert live in _ids() and gone not in _ids()


def test_keep_newest_n():
    ids = [_seed(created_days_ago=d) for d in (1, 2, 3)]  # newest..oldest
    # keep=1 → retain the single newest matching, delete the other 2
    assert store.prune_messages(older_than_days=0, keep=1) == 2
    surviving = _ids()
    assert ids[0] in surviving and ids[1] not in surviving and ids[2] not in surviving


def test_include_tests_toggle():
    real = _seed(created_days_ago=40)
    test = _seed(created_days_ago=40, is_test=True)
    # protect test rows
    assert store.prune_messages(older_than_days=30, include_tests=False) == 1
    assert test in _ids() and real not in _ids()


def test_dry_run_deletes_nothing():
    _seed(created_days_ago=40)
    before = _ids()
    assert store.prune_messages(older_than_days=30, dry_run=True) == 1
    assert _ids() == before


def test_message_stats():
    _seed(created_days_ago=5)
    _seed(created_days_ago=1, dismissed=True)
    _seed(created_days_ago=2, pinned=True)
    s = store.message_stats()
    assert s["total"] == 3
    assert s["dismissed"] == 1
    assert s["pinned"] == 1
    assert s["unread"] == 2          # the dismissed one is excluded from unread
    assert s["oldest"] is not None


def test_auto_retention_off_by_default(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_messages, "retention_days", None, raising=False)
    _seed(created_days_ago=999)
    store.record_message(trace_id="s1", body="new")
    # nothing auto-deleted when retention is off
    assert store.message_stats()["total"] == 2


def test_auto_retention_prunes_when_set(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_messages, "retention_days", 30, raising=False)
    monkeypatch.setattr(settings.agent_messages, "retention_keep_pinned", True, raising=False)
    old = _seed(created_days_ago=999)
    pinned_old = _seed(created_days_ago=999, pinned=True)
    store.record_message(trace_id="s1", body="new")  # triggers _enforce_retention
    surviving = _ids()
    assert old not in surviving          # old plain row pruned
    assert pinned_old in surviving       # pinned shielded
