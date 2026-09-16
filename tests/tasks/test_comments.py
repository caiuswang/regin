"""Comments — a conversation about something, with a polymorphic subject
(CAI-264).

Generic from the first row, and `proposal_feedback_*` is left untouched: that
table is this same shape welded to one subject by three FK columns, and
widening it would have made every one of its readers carry a kind column with
no second value.

`resolution_state` means only *has this conversation been dealt with*. Whether
the ticket is closed is the task's own `status` — two axes, two columns.
"""

from __future__ import annotations

import pytest

from lib.comments import store as comments
from lib.orm import SessionLocal
from lib.orm.models.comments import Comment, CommentThread
from lib.tasks import store as task_store


def test_a_comment_thread_names_its_subject_by_kind_and_id(native_task):
    task_id = native_task()
    thread = comments.open_thread("task", str(task_id))
    assert thread["subject_kind"] == "task"
    assert thread["subject_id"] == str(task_id)
    assert [t["id"] for t in comments.threads_for("task", str(task_id))] \
        == [thread["id"]]


def test_deleting_a_task_deletes_its_threads(native_task):
    task_id = native_task()
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body="a note")

    task_store.delete_task(task_id)
    assert comments.threads_for("task", str(task_id)) == []
    with SessionLocal() as session:
        assert session.get(CommentThread, thread["id"]) is None


def test_deleting_a_thread_deletes_its_comments(native_task):
    task_id = native_task()
    thread = comments.open_thread("task", str(task_id))
    comment = comments.add_comment(thread["id"], body="a note")

    comments.delete_thread(thread["id"])
    with SessionLocal() as session:
        assert session.get(Comment, comment["id"]) is None


def test_resolution_state_holds_only_open_and_resolved(native_task):
    task_id = native_task()
    thread = comments.open_thread("task", str(task_id))
    assert thread["resolution_state"] == "open"

    assert comments.resolve(thread["id"], "resolved")["resolution_state"] \
        == "resolved"
    assert comments.resolve(thread["id"], "open")["resolution_state"] == "open"
    with pytest.raises(comments.CommentRejected) as caught:
        comments.resolve(thread["id"], "closed")
    assert caught.value.field == "resolution_state"


def test_a_tasks_resolution_is_a_comment_not_a_resolution_state(native_task):
    """Two axes, two columns: a resolved conversation says nothing about
    whether the work shipped."""
    task_id = native_task()
    thread = comments.open_thread("task", str(task_id))
    comments.resolve(thread["id"], "resolved")
    assert task_store.get_task(task_id)["status"] == "idea"


def test_a_comment_carries_an_author_kind_and_an_author_id(native_task):
    thread = comments.open_thread("task", str(native_task()))
    comment = comments.add_comment(thread["id"], body="mine", author_id=7)
    assert comment["author_kind"] == "user" and comment["author_id"] == 7


def test_an_agent_comment_has_a_null_author_id(native_task):
    thread = comments.open_thread("task", str(native_task()))
    comment = comments.add_comment(thread["id"], body="from a run",
                                   author_kind="agent")
    assert comment["author_kind"] == "agent" and comment["author_id"] is None


def test_threads_are_flat(native_task):
    """No parent comment, no reply-to: a thread IS the nesting."""
    thread = comments.open_thread("task", str(native_task()))
    first = comments.add_comment(thread["id"], body="one")
    second = comments.add_comment(thread["id"], body="two")
    assert "parent_id" not in first and "reply_to" not in first
    assert [c["body"] for c in comments.comments_for(thread["id"])] \
        == ["one", "two"]
    assert second["thread_id"] == thread["id"]


def test_a_thread_has_no_anchor_columns():
    """A thread names its subject, never a span of text inside it."""
    names = set(CommentThread.model_fields)
    assert not names & {"anchor", "quoted_text", "anchor_start", "anchor_end",
                        "line", "offset"}


def test_the_proposal_feedback_tables_are_untouched(tmp_db):
    import sqlite3

    conn = sqlite3.connect(str(tmp_db))
    try:
        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(proposal_feedback_threads)")}
    finally:
        conn.close()
    assert "subject_kind" not in columns and "subject_id" not in columns
    assert "proposal_topic_id" in columns


def test_a_comment_body_over_the_bound_is_refused(native_task):
    thread = comments.open_thread("task", str(native_task()))
    with pytest.raises(comments.CommentRejected):
        comments.add_comment(thread["id"], body="x" * 9000)


def test_the_api_serves_threads_and_comments(flask_client, native_task):
    task_id = native_task()
    created = flask_client.post(f"/api/tasks/{task_id}/comments",
                                json={"body": "first"})
    assert created.status_code == 201
    thread_id = created.get_json()["thread"]["id"]

    listed = flask_client.get(f"/api/tasks/{task_id}/comments")
    assert listed.status_code == 200
    threads = listed.get_json()["threads"]
    assert [c["body"] for c in threads[0]["comments"]] == ["first"]

    patched = flask_client.patch(
        f"/api/tasks/{task_id}/comments/{thread_id}",
        json={"resolution_state": "resolved"})
    assert patched.status_code == 200
    assert patched.get_json()["thread"]["resolution_state"] == "resolved"


def test_there_is_no_mcp_surface_and_no_second_subject_kind():
    """The subject is widened exactly as far as tasks need."""
    from lib.orm.models.comments import SUBJECT_TASK
    import lib.comments as package

    assert SUBJECT_TASK == "task"
    assert not hasattr(package, "mcp")
