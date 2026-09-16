"""Fixtures for the task-capture tests."""

from __future__ import annotations

import pytest

from tests.feature_ledger_fixtures import (  # noqa: F401
    counted_queries, defect_row, feature_row, no_tracker, run_alias,
    session_row, task_lane,
)


@pytest.fixture
def mint_key(tmp_db):
    """Mint a real API key against the test DB → `(plaintext, row)`."""
    from lib import api_keys

    def _mint(name: str = "test key", scopes=("task:capture",)):
        return api_keys.mint_key(name, list(scopes))

    return _mint


@pytest.fixture(autouse=True)
def _reset_capture_rate_limit():
    """The limiter's history is module-level and outlives `tmp_db`, so key id 1
    in one test would otherwise spend key id 1's budget in the next."""
    from lib.tasks import ratelimit

    ratelimit._HISTORY.clear()
    yield
    ratelimit._HISTORY.clear()


@pytest.fixture
def capture_enabled(monkeypatch):
    """Arm the capture route (off by default, like the bridge)."""
    from lib.settings import TasksConfig, settings

    monkeypatch.setattr(settings, "tasks", TasksConfig(capture_enabled=True))
    return settings.tasks


@pytest.fixture
def native_task(tmp_db):
    """Insert an `agent_tasks` row directly and return its id.

    Straight through the ORM rather than `store.capture_task`, because tests
    need rows in statuses capture cannot produce — a `running` task, a settled
    one — and capture always lands `idea`.
    """
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTask, TASK_STATUS_IDEA

    def _make(*, title="a task", body=None, status=TASK_STATUS_IDEA,
              feature_id=None, assignee=None, source="test",
              tags_json=None, project_id=None):
        with SessionLocal() as session:
            row = AgentTask(title=title, body=body, status=status,
                            source=source, feature_id=feature_id,
                            assignee=assignee, tags_json=tags_json,
                            project_id=project_id)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id
    return _make


@pytest.fixture
def task_run_row():
    """Insert an `agent_task_runs` row for a task."""
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTaskRun

    def _make(task_id: int, *, trace_id=None, attempt=1, status="done",
              ended_at="2026-09-01 12:00:00"):
        with SessionLocal() as session:
            row = AgentTaskRun(task_id=task_id, trace_id=trace_id,
                               attempt=attempt, status=status,
                               started_at="2026-09-01 10:00:00",
                               ended_at=ended_at)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id
    return _make
