"""Fixtures for the feature-ledger acceptance items (CAI-242).

Imported by both `tests/features/conftest.py` and `tests/defects/conftest.py`
rather than living in either: the ledger and the defect ledger read the same
tables, and duplicating a `sessions`-row builder in two conftests is how the
two copies drift apart.

The two things every test here needs: rows in the tables the ledger reads
(`sessions`, `agent_run_sessions`, `agent_task_runs`, `agent_task_links`), and
a tracker that can be made unreachable on demand. The latter is the fixture
`lib/defects/tracker.py`'s deliberate refusal forces on us — and `escapes` now
puts a tracker fetch on the *attribution write* too, so both the read path and
the write path need it.
"""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.orm import SessionLocal


@pytest.fixture
def session_row():
    """Insert a `sessions` row, returning its trace id."""
    def _make(trace_id: str, *, cost=None, title="a session",
              started="2026-09-01 10:00:00", active_work_ms=1000,
              input_tokens=None, output_tokens=None, cache_read_tokens=None,
              cache_creation_tokens=None):
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO sessions (trace_id, title, started_at, last_seen,"
                " cost_usd, active_work_ms, input_tokens, output_tokens,"
                " cache_read_tokens, cache_creation_tokens)"
                " VALUES (:t, :ti, :s, :s, :c, :a, :i, :o, :cr, :cc)"
            ).bindparams(t=trace_id, ti=title, s=started, c=cost,
                         a=active_work_ms, i=input_tokens, o=output_tokens,
                         cr=cache_read_tokens, cc=cache_creation_tokens))
            session.commit()
        return trace_id
    return _make


@pytest.fixture
def run_alias():
    """Join a run trace to the conversation trace it drove."""
    def _make(run_trace: str, conversation_trace: str):
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO agent_run_sessions (trace_id, cli_session_id)"
                " VALUES (:r, :c)"
            ).bindparams(r=run_trace, c=conversation_trace))
            session.commit()
    return _make


@pytest.fixture
def task_lane():
    """A task, its tracker link(s), and its runs — the SDK attribution lane."""
    def _make(task_id: int, keys, runs):
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO agent_tasks (id, title, status, source)"
                " VALUES (:i, :t, 'doing', 'test')"
            ).bindparams(i=task_id, t=f"task {task_id}"))
            for key in keys:
                session.exec(text(
                    "INSERT INTO agent_task_links"
                    " (task_id, provider, external_key, url)"
                    " VALUES (:i, 'linear', :k, :u)"
                ).bindparams(i=task_id, k=key, u=f"https://x/{key}"))
            for attempt, (trace, status) in enumerate(runs, start=1):
                session.exec(text(
                    "INSERT INTO agent_task_runs"
                    " (task_id, trace_id, attempt, status)"
                    " VALUES (:i, :t, :a, :s)"
                ).bindparams(i=task_id, t=trace, a=attempt, s=status))
            session.commit()
    return _make


@pytest.fixture
def no_tracker(monkeypatch):
    """Force every tracker read to refuse, on a machine that has a real key.

    Same lever as `tests/defects/test_tracker_rounds.py:84-91` — pinned off
    rather than skipped, so the assertion runs everywhere.

    The native provider is dropped from the registry as well, because it holds
    no credential to pin off: leaving it registered would make this fixture's
    38 call sites mean "Linear is off" where every one of them means "nothing
    answers". A test that wants the native tracker *on* with Linear off asks
    for that directly rather than through this.
    """
    from lib.settings import settings
    from lib.tasks.integrations import registry
    monkeypatch.setattr(settings.task_integrations, "linear_enabled", False)
    monkeypatch.setattr(settings.task_integrations, "linear_api_key", None)
    monkeypatch.delitem(registry._PROVIDER_BUILDERS, "regin", raising=False)


@pytest.fixture
def tracker_issue(monkeypatch):
    """Pin what the tracker answers for a key, without a network call."""
    from lib.tasks.integrations.base import (
        ExternalIssue, ExternalRef, IssueTrackerProvider,
    )
    issues: dict = {}

    class _Provider(IssueTrackerProvider):
        provider_id = "linear"
        display_name = "Linear"

        def is_configured(self):
            return True

        def fetch_issue(self, ref):
            if ref.key not in issues:
                from lib.tasks.integrations import IntegrationError
                raise IntegrationError(f"no issue {ref.key}")
            return issues[ref.key]

    def _resolve(key: str):
        return _Provider(), ExternalRef(provider="linear", key=key,
                                        url=f"https://x/{key}")

    import lib.defects.tracker as tracker_mod
    monkeypatch.setattr(tracker_mod, "resolve", _resolve)

    def _set(key: str, *, state_category="started", rounds=1, title="t"):
        issues[key] = ExternalIssue(
            provider="linear", key=key, url=f"https://x/{key}", title=title,
            state=state_category.title(), state_category=state_category,
            rounds=rounds)
    return _set


@pytest.fixture
def posted_spans(monkeypatch):
    """Capture every span the commands under test try to leave."""
    posted: list = []

    def fake(trace_id, name, attributes=None, **kw):
        posted.append({"trace_id": trace_id, "name": name,
                       "attributes": attributes or {}})
        return True

    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, "post_span", fake)
    return posted


@pytest.fixture
def attributing_trace(monkeypatch):
    """Pin the trace id of the session *running the command*.

    Provenance is decided by comparing this against `--session`, so it has to
    be injectable — otherwise the test's answer depends on whoever ran pytest.
    """
    def _set(trace_id):
        import lib.session_probe as probe
        monkeypatch.setattr(probe, "resolve", lambda: trace_id)
    return _set


@pytest.fixture
def tracker_down(monkeypatch):
    """Make every tracker resolution refuse, after other fixtures pinned it.

    Distinct from `no_tracker`: this one wins even when `tracker_issue` has
    already replaced `resolve`, which is the order a ledger test needs when it
    declares attributions first and then pulls the tracker away.
    """
    def _refuse():
        import lib.defects.tracker as tracker_mod
        from lib.defects.tracker import TrackerUnavailable

        def _raise(key):
            raise TrackerUnavailable("no tracker is configured")
        monkeypatch.setattr(tracker_mod, "resolve", _raise)
    return _refuse


@pytest.fixture
def defect_row():
    """Insert a `defects` row directly, attributed or not.

    Straight SQL rather than `store.record`: the index tests need a key that
    exists *only* because a defect named it, and the recording path fetches
    the tracker to snapshot `blamed_against_state`.
    """
    counter = {"n": 0}

    def _make(feature_key, *, status="open", symptom=None,
              opened_at="2026-09-01 10:00:00"):
        counter["n"] += 1
        text_ = symptom or f"symptom {counter['n']}"
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO defects (feature_key, symptom, symptom_key,"
                " status, opened_at) VALUES (:f, :s, :k, :st, :o)"
            ).bindparams(f=feature_key, s=text_, o=opened_at,
                         k=f"sym-{counter['n']}", st=status))
            session.commit()
        return text_
    return _make


@pytest.fixture
def cached_issue():
    """Put a normalized issue in the feature-keyed cache without a fetch."""
    import json

    def _make(key, *, provider="linear", title="t", state_category="started",
              rounds=1, synced_at="2026-09-01 12:00:00"):
        snapshot = {"provider": provider, "key": key,
                    "url": f"https://x/{key}", "title": title,
                    "state": state_category.title(),
                    "state_category": state_category, "rounds": rounds}
        with SessionLocal() as session:
            session.exec(text(
                "INSERT OR REPLACE INTO issue_cache"
                " (provider, external_key, snapshot_json, synced_at)"
                " VALUES (:p, :k, :s, :t)"
            ).bindparams(p=provider, k=key, s=json.dumps(snapshot),
                         t=synced_at))
            session.commit()
        return snapshot
    return _make


@pytest.fixture
def counted_queries():
    """Count the statements a block of code sends to SQLite.

    The index's contract is *one query for the whole page* — a promise a row
    count cannot be allowed to break — and only a counter at the engine can
    prove it, since a per-row query is invisible in the result.
    """
    import contextlib
    from sqlalchemy import event

    from lib.orm.engine import get_engine

    class _Count:
        value = 0

    @contextlib.contextmanager
    def _counting():
        count = _Count()
        engine = get_engine()

        def _before(conn, cursor, statement, params, context, many):
            count.value += 1

        event.listen(engine, "before_cursor_execute", _before)
        try:
            yield count
        finally:
            event.remove(engine, "before_cursor_execute", _before)
    return _counting
