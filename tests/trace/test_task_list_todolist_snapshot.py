"""`_fetch_session_task_list` must fold snapshot-style task writes.

Kimi's `TodoList` resends the WHOLE list on every write instead of one task per
span, so the query that only looked at `tool.TaskCreate`/`tool.TaskUpdate`
returned None for a Kimi session — killing the inline TASK LIST card, the header
`tasks N/M` badge and the mobile LiveTaskSheet at once. The per-task Task* fold
must keep behaving identically.

Snapshot identity is scoped per writing agent, and finished tasks are never
retired: both are load-bearing, since main agent and subagents share one
trace_id and the header badge filters `deleted` rows out entirely.
"""

from __future__ import annotations

import json

from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from web.blueprints.trace.sessions import _fetch_session_task_list


def _seed(trace_id, span_id, name, start_time, attrs, agent_id=None):
    with SessionLocal() as session:
        session.add(SessionSpan(
            trace_id=trace_id, span_id=span_id, parent_id=None,
            name=name, kind="internal", start_time=start_time,
            attributes=json.dumps(attrs), agent_id=agent_id,
        ))
        session.commit()


def _todos(*pairs):
    return {'todos': [
        {'task_id': str(i), 'subject': subject, 'status': status}
        for i, (subject, status) in enumerate(pairs, 1)
    ]}


def test_todolist_span_yields_a_snapshot(tmp_db):
    tid = "t-kimi-todo"
    _seed(tid, "A", "tool.TodoList", "2026-06-16 18:58:43",
          _todos(("Add provider metadata", "in_progress"),
                 ("Make views provider-aware", "pending")))

    out = _fetch_session_task_list(tid)
    assert out is not None
    assert [(t['task_id'], t['subject'], t['status']) for t in out['final']] == [
        ('todo-1', 'Add provider metadata', 'in_progress'),
        ('todo-2', 'Make views provider-aware', 'pending'),
    ]
    # One event per task, all pinned to the writing span.
    assert [e['span_id'] for e in out['events']] == ["A", "A"]


def test_todolist_status_flips_across_snapshots(tmp_db):
    tid = "t-kimi-flip"
    _seed(tid, "A", "tool.TodoList", "2026-06-16 18:58:43",
          _todos(("Ship it", "in_progress"), ("Verify", "pending")))
    _seed(tid, "B", "tool.TodoList", "2026-06-16 19:02:00",
          _todos(("Ship it", "completed"), ("Verify", "in_progress")))

    final = _fetch_session_task_list(tid)['final']
    assert [(t['status'], t['current_span_id']) for t in final] == [
        ('completed', 'B'), ('in_progress', 'B'),
    ]
    assert final[0]['created_span_id'] == 'A'


def test_replacing_the_list_keeps_subjects_with_their_own_task(tmp_db):
    # Observed in the reference Kimi session: a new plan reuses position 1 for
    # a different task. Position-keyed identity would leave the old subject
    # glued to the new task's status.
    tid = "t-kimi-replace"
    _seed(tid, "A", "tool.TodoList", "2026-06-16 18:58:43",
          _todos(("Old plan step", "in_progress")))
    _seed(tid, "B", "tool.TodoList", "2026-06-16 19:10:00",
          _todos(("Fresh plan step", "in_progress")))

    final = _fetch_session_task_list(tid)['final']
    by_subject = {t['subject']: t['status'] for t in final}
    assert by_subject == {'Old plan step': 'deleted',
                          'Fresh plan step': 'in_progress'}


def test_completed_task_is_never_retired(tmp_db):
    # The session header filters `deleted` out of the task badge, so retiring a
    # finished task erases work that actually shipped.
    tid = "t-kimi-completed"
    _seed(tid, "A", "tool.TodoList", "2026-06-16 18:58:43",
          _todos(("Shipped it", "completed")))
    _seed(tid, "B", "tool.TodoList", "2026-06-16 19:10:00",
          _todos(("Fresh plan step", "in_progress")))

    out = _fetch_session_task_list(tid)
    assert {t['subject']: t['status'] for t in out['final']} == {
        'Shipped it': 'completed', 'Fresh plan step': 'in_progress'}
    assert [e for e in out['events'] if e.get('status') == 'deleted'] == []


def test_dropped_task_is_retired_once(tmp_db):
    tid = "t-kimi-drop"
    _seed(tid, "A", "tool.TodoList", "2026-06-16 18:58:43",
          _todos(("Kept", "pending"), ("Dropped", "pending")))
    _seed(tid, "B", "tool.TodoList", "2026-06-16 19:00:00",
          _todos(("Kept", "completed")))
    _seed(tid, "C", "tool.TodoList", "2026-06-16 19:01:00",
          _todos(("Kept", "completed")))

    out = _fetch_session_task_list(tid)
    deleted = [e for e in out['events'] if e.get('status') == 'deleted']
    # Retired on the snapshot that dropped it, and not re-emitted afterwards.
    assert [e['span_id'] for e in deleted] == ["B"]


def test_claude_todowrite_span_is_not_folded(tmp_db):
    # Claude's task list reaches the trace via TaskCreate/TaskUpdate. TodoWrite
    # spans carry no `todos` snapshot and must not enter this fold at all.
    tid = "t-claude-todowrite"
    _seed(tid, "A", "tool.TodoWrite", "2026-06-16 18:58:43",
          _todos(("Write the test", "completed")))
    assert _fetch_session_task_list(tid) is None


def test_subagent_snapshot_never_retires_the_main_agents_tasks(tmp_db):
    # Main agent and subagents share one trace_id. Without per-agent identity
    # the middle snapshot retired BOTH main tasks (one of them completed) and
    # the last one retired the subagent's — fabricating "deleted" rows for
    # live work in every TASK LIST card.
    tid = "t-subagent-scope"
    _seed(tid, "s1", "tool.TodoList", "2026-06-16 10:00:00",
          _todos(("Main task A", "in_progress"), ("Main task B", "completed")))
    _seed(tid, "s2", "tool.TodoList", "2026-06-16 10:01:00",
          _todos(("Subagent task X", "in_progress")), agent_id="ag1")
    _seed(tid, "s3", "tool.TodoList", "2026-06-16 10:02:00",
          _todos(("Main task A", "completed"), ("Main task B", "completed")))

    out = _fetch_session_task_list(tid)
    assert [e for e in out['events'] if e.get('status') == 'deleted'] == []
    assert {t['subject']: t['status'] for t in out['final']} == {
        'Main task A': 'completed',
        'Main task B': 'completed',
        'Subagent task X': 'in_progress',
    }


def test_same_subject_in_two_agents_stays_two_tasks(tmp_db):
    # Identity is (agent, subject): a subagent working on an identically-named
    # task must not inherit or overwrite the main agent's status.
    tid = "t-subagent-samename"
    _seed(tid, "s1", "tool.TodoList", "2026-06-16 10:00:00",
          _todos(("Run the tests", "in_progress")))
    _seed(tid, "s2", "tool.TodoList", "2026-06-16 10:01:00",
          _todos(("Run the tests", "completed")), agent_id="ag1")

    final = _fetch_session_task_list(tid)['final']
    assert sorted(t['status'] for t in final) == ['completed', 'in_progress']


def test_task_create_update_fold_is_unchanged(tmp_db):
    tid = "t-claude-tasks"
    _seed(tid, "A", "tool.TaskCreate", "2026-04-22 10:00:00",
          {"task_id": "1", "subject": "Write", "status": "pending"})
    _seed(tid, "B", "tool.TaskUpdate", "2026-04-22 10:00:01",
          {"task_id": "1", "status": "completed"})

    out = _fetch_session_task_list(tid)
    assert out['events'] == [
        {"span_id": "A", "timestamp": "2026-04-22 10:00:00",
         "task_id": "1", "subject": "Write", "status": "pending"},
        {"span_id": "B", "timestamp": "2026-04-22 10:00:01",
         "task_id": "1", "status": "completed"},
    ]
    assert out['final'][0]['current_span_id'] == "B"


def test_todolist_without_todos_still_returns_none(tmp_db):
    tid = "t-kimi-empty"
    _seed(tid, "A", "tool.TodoList", "2026-06-16 18:58:43",
          {"tool_name": "TodoList"})
    assert _fetch_session_task_list(tid) is None


def test_snapshot_ids_cannot_collide_with_a_provider_task_id(tmp_db):
    """A provider that emits BOTH span families in one trace must not have its
    TaskCreate task '1' merged with the first TodoList subject."""
    tid = "t-both-families"
    _seed(tid, "A", "tool.TaskCreate", "2026-06-16 18:58:40",
          {"task_id": "1", "subject": "Provider task", "status": "pending"})
    _seed(tid, "B", "tool.TodoList", "2026-06-16 18:58:41",
          _todos(("Snapshot task", "in_progress")))

    out = _fetch_session_task_list(tid)
    ids = {t['task_id'] for t in out['final']}
    subjects = {t['subject'] for t in out['final']}

    assert ids == {"1", "todo-1"}
    assert subjects == {"Provider task", "Snapshot task"}
