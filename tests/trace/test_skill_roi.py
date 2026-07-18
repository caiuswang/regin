"""The skill-usage leaderboard behind /api/skill-reads.

Covers the three things the rollup gets wrong if written naively: the
local-time read_at window, applying the page's filters to the summary cards
as well as the feed, and scoping "never fired" to an unfiltered view.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lib.orm import SessionLocal
from lib.orm.models import Session as SessionModel, SkillRead
from lib.trace import trace_service


def _local(days_ago: float) -> str:
    """A read_at exactly as the ingest endpoint writes it: local, ISO, `T`."""
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def _seed(rows):
    with SessionLocal() as session:
        for skill_id, source, days_ago, sid in rows:
            session.add(SkillRead(
                skill_id=skill_id, session_id=sid,
                file_path=f"~/.claude/skills/{skill_id}/SKILL.md",
                source=source, read_at=_local(days_ago),
            ))
        session.commit()


def _seed_session(trace_id: str, *, is_test: int = 0):
    with SessionLocal() as session:
        session.add(SessionModel(
            trace_id=trace_id, title="s", is_test=is_test,
            started_at="2026-04-22 10:00:00", last_seen="2026-04-22 10:00:00",
        ))
        session.commit()


def _roi(**kw):
    defaults = dict(skill_filter=None, session_filter=None,
                    include_tests=False, cursor_token=None, size=100)
    _, stats, _ = trace_service.list_skill_reads_page(**{**defaults, **kw})
    return {r["skill_id"]: r for r in stats}


def test_source_split_counts_each_kind_separately():
    _seed([("alpha", "invoke", 1, "s1"), ("alpha", "invoke", 1, "s1"),
           ("alpha", "read", 1, "s1"), ("alpha", "launch", 1, "s2")])
    _seed_session("s1")
    _seed_session("s2")

    row = _roi()["alpha"]
    assert (row["total"], row["invokes"], row["reads"], row["launches"]) == (4, 2, 1, 1)
    assert row["sessions"] == 2


def test_windows_split_on_local_time_not_utc():
    """read_at is written with `datetime.now()` (local) while SQLite's
    `datetime('now')` is UTC. Comparing against the raw UTC value shifts the
    boundary by the machine's offset, so a read 1 day old could fall outside
    the 7-day window. Seeding just inside each edge pins the behaviour."""
    _seed([
        ("beta", "invoke", 0.1, "s1"),    # clearly recent
        ("beta", "invoke", 6.9, "s1"),    # just inside the 7d window
        ("beta", "invoke", 7.1, "s1"),    # just outside -> prior
        ("beta", "invoke", 13.9, "s1"),   # just inside the prior window
        ("beta", "invoke", 14.1, "s1"),   # older than both windows
    ])
    _seed_session("s1")

    row = _roi()["beta"]
    assert row["recent"] == 2, "reads inside 7d local-time window"
    assert row["prior"] == 2, "reads in the 7-14d window"
    assert row["total"] == 5


def test_same_day_read_is_not_pulled_in_by_separator_mismatch():
    """`read_at` is `T`-separated; SQLite's datetime() is space-separated,
    and 'T' (0x54) sorts above ' ' (0x20). An un-normalised cutoff therefore
    counts *earlier* reads on the boundary day as inside the window."""
    _seed([("gamma", "invoke", 7.4, "s1")])
    _seed_session("s1")

    assert _roi()["gamma"]["recent"] == 0


def test_skill_filter_applies_to_the_summary_not_just_the_feed():
    """The summary cards and the event feed must describe the same set —
    otherwise an active filter chip shows one skill's events beside every
    skill's totals."""
    _seed([("alpha", "invoke", 1, "s1"), ("omega", "invoke", 1, "s1")])
    _seed_session("s1")

    assert set(_roi()) == {"alpha", "omega"}
    assert set(_roi(skill_filter="alpha")) == {"alpha"}


def test_session_filter_applies_to_the_summary():
    _seed([("alpha", "invoke", 1, "s1"), ("alpha", "invoke", 1, "s2")])
    _seed_session("s1")
    _seed_session("s2")

    assert _roi()["alpha"]["total"] == 2
    assert _roi(session_filter="s1")["alpha"]["total"] == 1


def test_test_sessions_excluded_via_the_sessions_column():
    _seed([("alpha", "invoke", 1, "real"), ("alpha", "invoke", 1, "testy")])
    _seed_session("real")
    _seed_session("testy", is_test=1)

    assert _roi()["alpha"]["total"] == 1
    assert _roi(include_tests=True)["alpha"]["total"] == 2


@pytest.mark.parametrize("filter_kw", [
    {"skill_filter": "alpha"},
    {"session_filter": "s1"},
])
def test_never_fired_is_empty_under_any_filter(flask_client, filter_kw):
    """Under a filter the leaderboard holds one skill, so its complement
    would be "every other deployed skill" — a wrong answer, not a useful one."""
    _seed([("alpha", "invoke", 1, "s1")])
    _seed_session("s1")

    key, value = next(iter(filter_kw.items()))
    param = "skill" if key == "skill_filter" else "session"
    resp = flask_client.get(f"/api/skill-reads?{param}={value}")
    assert resp.get_json()["never_fired"] == []


def test_never_fired_lists_deployed_skills_with_no_activity(flask_client, monkeypatch):
    from lib.skills import skill_registry
    monkeypatch.setattr(skill_registry, "all_ids", lambda: ["alpha", "dormant"])
    _seed([("alpha", "invoke", 1, "s1")])
    _seed_session("s1")

    assert flask_client.get("/api/skill-reads").get_json()["never_fired"] == ["dormant"]


def test_summaries_are_omitted_mid_pagination():
    _seed([("alpha", "invoke", 1, "s1")])
    _seed_session("s1")
    page, _, _ = trace_service.list_skill_reads_page(
        skill_filter=None, session_filter=None, include_tests=False,
        cursor_token=None, size=100)
    _, stats, sessions = trace_service.list_skill_reads_page(
        skill_filter=None, session_filter=None, include_tests=False,
        cursor_token=page.next_cursor or "x", size=100)
    assert stats == [] and sessions == []
