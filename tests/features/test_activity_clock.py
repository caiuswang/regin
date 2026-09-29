"""Last activity reads one clock.

`sessions.last_seen` is written host-local by the trace ingest, while every
pipeline column is naive UTC. The index normalizes both before comparing them
and emits UTC with a `Z`, so a UTC+N host neither sees a session from the last
N hours as "just now" nor floats it above newer work.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import text

from lib.auth import create_token
from lib.defects import store as defect_store
from lib.features import events, index as index_module
from lib.orm import SessionLocal
from lib.tasks import store as task_store

_ZULU = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_OLD = "2020-01-01 00:00:00"


@pytest.fixture
def dubai(monkeypatch):
    """The process — and so SQLite's `localtime` — runs at UTC+4."""
    monkeypatch.setenv("TZ", "Asia/Dubai")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _exec(sql: str, **binds) -> None:
    with SessionLocal() as session:
        session.exec(text(sql).bindparams(**binds))
        session.commit()


def _age_everything() -> None:
    """Every pipeline operand back to one old instant, so the test's own
    write is the only recent one."""
    _exec("UPDATE feature_events SET at = :o", o=_OLD)
    _exec("UPDATE features SET created_at = :o", o=_OLD)
    _exec("UPDATE defects SET updated_at = :o, opened_at = :o", o=_OLD)
    _exec("UPDATE agent_tasks SET updated_at = :o", o=_OLD)


def _activity(feature_id: int) -> str:
    return {r.id: r for r in index_module.page().items}[feature_id].last_activity


def _zulu(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _headers() -> dict:
    return {"Authorization": f"Bearer {create_token(1, 'viewer', 'viewer')}"}


def test_feature_activity_normalizes_local_session_stamp(
        dubai, flask_client, tmp_db, session_row, task_lane, feature_row):
    feature = feature_row()
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)
    local = started.astimezone().replace(tzinfo=None).isoformat(timespec="microseconds")
    session_row("t-dubai", started=local)
    task_lane(900, feature.id, [("t-dubai", "done")])
    _age_everything()

    body = flask_client.get("/api/features", headers=_headers()).get_json()
    row = {r["id"]: r for r in body["items"]}[feature.id]

    assert row["last_activity"] == _zulu(started)


@pytest.mark.parametrize("stamp", [
    "{naive}",                   # 26: host-local, microseconds
    "{naive_s}",                 # 19: host-local, seconds
    "{utc}Z",                    # 20
    "{utc}.000Z",                # 24
    "{utc}.000000+00:00",        # 32
])
def test_every_session_shape_lands_on_one_instant(
        dubai, stamp, session_row, task_lane, feature_row):
    feature = feature_row()
    at = datetime(2099, 3, 1, 22, 30, 5, tzinfo=timezone.utc)
    local = at.astimezone().replace(tzinfo=None)
    written = stamp.format(naive=local.isoformat(timespec="microseconds"),
                           naive_s=local.isoformat(timespec="seconds"),
                           utc=at.strftime("%Y-%m-%dT%H:%M:%S"))
    session_row("t-shape", started=written)
    task_lane(901, feature.id, [("t-shape", "done")])

    assert _activity(feature.id) == "2099-03-01T22:30:05Z"


def test_feature_activity_sort_across_clocks(
        dubai, session_row, task_lane, defect_row, feature_row):
    """A's session was 2h ago on the local clock, B's defect 1h ago in UTC.
    Compared raw, A's local stamp reads 2h *ahead* of B's and wins."""
    a, b = feature_row("A"), feature_row("B")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    local_2h = (now - timedelta(hours=2)).astimezone().replace(tzinfo=None)
    session_row("t-a", started=local_2h.isoformat(timespec="microseconds"))
    task_lane(902, a.id, [("t-a", "done")])
    defect_row(b.id)
    _age_everything()
    _exec("UPDATE defects SET updated_at = :u",
          u=(now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"))

    ids = [r.id for r in index_module.page(filters={"sort": "activity:desc"}).items]

    assert ids.index(b.id) < ids.index(a.id)


def _defect_id(feature_id: int) -> int:
    with SessionLocal() as session:
        return session.exec(text(
            "SELECT id FROM defects WHERE feature_id = :f"
        ).bindparams(f=feature_id)).one()[0]


def _within_a_minute(stamp: str) -> bool:
    at = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return abs(datetime.now(timezone.utc) - at) < timedelta(minutes=1)


def test_feature_activity_counts_events_defects_tasks(
        feature_row, defect_row, task_lane):
    """A ship, a defect changing state and a task settling are each activity."""
    feature = feature_row()
    defect_row(feature.id)
    task_lane(903, feature.id, [])

    _age_everything()
    assert _activity(feature.id) == "2020-01-01T00:00:00Z"
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    assert _within_a_minute(_activity(feature.id))

    _age_everything()
    defect_store.close(_defect_id(feature.id), missing_check="a unit test")
    assert _within_a_minute(_activity(feature.id))

    _age_everything()
    task_store.set_status(903, "done")
    assert _within_a_minute(_activity(feature.id))


def test_archived_task_is_not_activity(feature_row, task_lane):
    feature = feature_row()
    task_lane(904, feature.id, [])
    _age_everything()
    _exec("UPDATE agent_tasks SET status = 'archived', updated_at = :n",
          n="2099-01-01 00:00:00")

    assert _activity(feature.id) == "2020-01-01T00:00:00Z"


def test_feature_activity_emits_zulu(
        flask_client, tmp_db, session_row, task_lane, defect_row, feature_row):
    """I1: whatever operand wins — none, a session, a defect, an event —
    the emitted stamp is UTC with a `Z`."""
    feature_row("only a spec")
    with_session = feature_row("with a session")
    session_row("t-z", started="2099-01-01T08:00:00.123456")
    task_lane(905, with_session.id, [("t-z", "done")])
    defect_row(feature_row("with a defect").id)
    feature_row("shipped", stage=events.SHIPPED)
    _exec("UPDATE features SET created_at = :o WHERE title = 'only a spec'", o=_OLD)
    _exec("DELETE FROM feature_events WHERE feature_id ="
          " (SELECT id FROM features WHERE title = 'only a spec')")

    body = flask_client.get("/api/features", headers=_headers()).get_json()

    stamps = {r["title"]: r["last_activity"] for r in body["items"]}
    assert len(stamps) == 4
    assert all(_ZULU.match(s) for s in stamps.values()), stamps
    assert stamps["only a spec"] == "2020-01-01T00:00:00Z"

