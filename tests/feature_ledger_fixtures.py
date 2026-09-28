"""Fixtures for the feature-ledger acceptance items (CAI-242).

Imported by both `tests/features/conftest.py` and `tests/defects/conftest.py`
rather than living in either: the ledger and the defect ledger read the same
tables, and duplicating a `sessions`-row builder in two conftests is how the
two copies drift apart.

What every test here needs: rows in the tables the ledger reads (`features`,
`feature_events`, `sessions`, `agent_run_sessions`, `agent_task_runs`), and —
for the remote half only — a tracker that can be pinned or made unreachable.

The tracker fixtures shrank with their reach. A feature's stage, rounds and
escape verdict are derived from rows in this database now, so no read path
touches a tracker at all; only an explicit `refresh` of a remotely-tracked
feature does.
"""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.orm import SessionLocal


@pytest.fixture
def session_row():
    """Insert a `sessions` row, returning its trace id."""
    def _make(trace_id: str, *, cost=None, title="a session",
              started="2026-09-01 10:00:00", active_work_ms=1000, prompts=0,
              input_tokens=None, output_tokens=None, cache_read_tokens=None,
              cache_creation_tokens=None):
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO sessions (trace_id, title, started_at, last_seen,"
                " cost_usd, active_work_ms, input_tokens, output_tokens,"
                " cache_read_tokens, cache_creation_tokens, prompts)"
                " VALUES (:t, :ti, :s, :s, :c, :a, :i, :o, :cr, :cc, :p)"
            ).bindparams(t=trace_id, ti=title, s=started, c=cost, p=prompts,
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
def feature_row():
    """A `features` row plus its `specced` event, at a named stage."""
    def _make(title="a feature", *, stage=None, origin="native",
              external_ref=None):
        from lib.features import events, store as feature_store

        row = feature_store.create(title, origin=origin,
                                   external_ref=external_ref)
        if stage is not None:
            events.append(row.id, stage, declared_by=events.OPERATOR)
        return row
    return _make


@pytest.fixture
def task_lane():
    """A task under a feature, and its runs — the SDK attribution lane.

    Two joins where there used to be four CTEs: the run names a task, the task
    names one feature, and that is the whole path.
    """
    def _make(task_id: int, feature_id, runs):
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO agent_tasks (id, title, status, source,"
                " feature_id) VALUES (:i, :t, 'doing', 'test', :f)"
            ).bindparams(i=task_id, t=f"task {task_id}", f=feature_id))
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

    Its reach is much smaller than it was: no read path consults a tracker
    any more, so this only affects an explicit `refresh` or an adoption.
    """
    from lib.settings import settings
    monkeypatch.setattr(settings.task_integrations, "linear_enabled", False)
    monkeypatch.setattr(settings.task_integrations, "linear_api_key", None)


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

    def _resolve(text_ref: str, provider_id: str = ""):
        return _Provider(), ExternalRef(provider="linear", key=text_ref,
                                        url=f"https://x/{text_ref}")

    from lib.tasks.integrations import registry
    monkeypatch.setattr(registry, "resolve_ref", _resolve)
    import lib.features.sync as sync_mod
    monkeypatch.setattr(sync_mod.registry, "resolve_ref", _resolve)

    def _set(key: str, *, state_category="started", title="t"):
        issues[key] = ExternalIssue(
            provider="linear", key=key, url=f"https://x/{key}", title=title,
            state=state_category.title(), state_category=state_category)
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
    already replaced the resolver, which is the order a sync test needs when
    it adopts a feature first and then pulls the tracker away.
    """
    def _refuse():
        from lib.tasks.integrations import IntegrationError, registry

        def _raise(text_ref, provider_id=""):
            raise IntegrationError("no tracker is configured")
        monkeypatch.setattr(registry, "resolve_ref", _raise)
        import lib.features.sync as sync_mod
        monkeypatch.setattr(sync_mod.registry, "resolve_ref", _raise)
    return _refuse


@pytest.fixture
def defect_row():
    """Insert a `defects` row, attributed or not, blamed at the feature's
    current last event.

    Through `store.record` for the blame pointer, because the pointer is the
    thing most of these tests are about: a hand-written row with a NULL
    `blamed_event_id` reads as a defect nobody has blamed, which is a
    different state from one blamed against a shipped feature.
    """
    counter = {"n": 0}

    def _make(feature_id=None, *, status="open", symptom=None,
              opened_at="2026-09-01 10:00:00", session_id=None):
        from lib.defects import store as defect_store

        counter["n"] += 1
        body = symptom or f"symptom {counter['n']}"
        row = defect_store.record(body, feature_id=feature_id,
                                  session_id=session_id)
        with SessionLocal() as session:
            session.exec(text(
                "UPDATE defects SET status = :st, opened_at = :o"
                " WHERE id = :i"
            ).bindparams(st=status, o=opened_at, i=row.id))
            session.commit()
        return body
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
