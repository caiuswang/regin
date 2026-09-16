"""The writer and reader of `comment_threads` and `comments`.

Every write lands here for the reason `lib/tasks/store.py` owns `agent_tasks`:
the resolution vocabulary, the body bound and the author-kind rule all have to
hold whichever caller asked, and a second writer is how two copies of a rule
drift apart.

**A thread names its subject and nothing more.** There are no anchor columns —
no quoted text, no offsets — because an anchor is a claim about a version of
the subject, and a task's body is overwritten in place, so an anchor would go
silently wrong the first time somebody edited around it.

**Flat threads.** A thread IS the nesting; a reply-to inside one would be a
second tree nobody asked for.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import delete, select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.comments import (
    AUTHOR_KINDS, AUTHOR_USER, Comment, COMMENT_BODY_MAX, CommentThread,
    RESOLUTION_OPEN, RESOLUTION_STATES,
)

log = get_activity_logger("tasks")


class CommentRejected(ValueError):
    """A comment or thread the tables will not take — a 400 naming the field."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def serialize_thread(row: CommentThread) -> dict:
    return {"id": row.id, "subject_kind": row.subject_kind,
            "subject_id": row.subject_id,
            "resolution_state": row.resolution_state,
            "created_by": row.created_by, "created_at": row.created_at,
            "updated_at": row.updated_at}


def serialize_comment(row: Comment) -> dict:
    return {"id": row.id, "thread_id": row.thread_id,
            "author_kind": row.author_kind, "author_id": row.author_id,
            "body": row.body, "created_at": row.created_at,
            "updated_at": row.updated_at}


def open_thread(subject_kind: str, subject_id: str,
                created_by: Optional[int] = None) -> dict:
    """Start a conversation about `(subject_kind, subject_id)`.

    The subject is not checked to exist: `subject_id` is opaque here by
    design, and resolving it would put this module's one generic table in the
    business of knowing what every kind of subject is.
    """
    now = _now()
    with SessionLocal() as session:
        row = CommentThread(subject_kind=subject_kind,
                            subject_id=str(subject_id),
                            resolution_state=RESOLUTION_OPEN,
                            created_by=created_by,
                            created_at=now, updated_at=now)
        session.add(row)
        session.commit()
        session.refresh(row)
        view = serialize_thread(row)
    log.write("comment_thread_opened", subject_kind=subject_kind,
              subject_id=str(subject_id), thread_id=view["id"])
    return view


def add_comment(thread_id: int, *, body: str, author_kind: str = AUTHOR_USER,
                author_id: Optional[int] = None) -> Optional[dict]:
    """Post into a thread. None when the thread is gone.

    An agent's comment carries `author_kind='agent'` and a NULL `author_id`:
    the id is dropped rather than filled with a sentinel, because a sentinel
    would be indistinguishable from a user regin lost track of.
    """
    text = (body or "").strip()
    if not text:
        raise CommentRejected("a comment needs a body", "body")
    if len(text) > COMMENT_BODY_MAX:
        raise CommentRejected(
            f"body exceeds {COMMENT_BODY_MAX} characters", "body")
    if author_kind not in AUTHOR_KINDS:
        raise CommentRejected(
            f"author_kind must be one of {', '.join(AUTHOR_KINDS)}",
            "author_kind")
    now = _now()
    with SessionLocal() as session:
        thread = session.get(CommentThread, thread_id)
        if thread is None:
            return None
        row = Comment(thread_id=thread_id, author_kind=author_kind,
                      author_id=None if author_kind != AUTHOR_USER
                      else author_id,
                      body=text, created_at=now, updated_at=now)
        session.add(row)
        thread.updated_at = now
        session.add(thread)
        session.commit()
        session.refresh(row)
        view = serialize_comment(row)
    log.write("comment_added", thread_id=thread_id, comment_id=view["id"])
    return view


def resolve(thread_id: int, state: str) -> Optional[dict]:
    """Mark a conversation dealt with, or reopen it.

    Says nothing about the subject's own lifecycle: whether the ticket is
    closed is the task's `status`, and one column carrying both would be two
    axes on one.
    """
    if state not in RESOLUTION_STATES:
        raise CommentRejected(
            f"resolution_state must be one of {', '.join(RESOLUTION_STATES)}",
            "resolution_state")
    with SessionLocal() as session:
        row = session.get(CommentThread, thread_id)
        if row is None:
            return None
        row.resolution_state = state
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        view = serialize_thread(row)
    log.write("comment_thread_resolved", thread_id=thread_id, state=state)
    return view


def threads_for(subject_kind: str, subject_id: str) -> list[dict]:
    """Every thread about one subject, oldest first."""
    with SessionLocal() as session:
        rows = session.exec(
            select(CommentThread)
            .where(CommentThread.subject_kind == subject_kind,
                   CommentThread.subject_id == str(subject_id))
            .order_by(CommentThread.id)).all()
        out = [serialize_thread(row) for row in rows]
    log.read("comment_threads_read", subject_kind=subject_kind,
             subject_id=str(subject_id), count=len(out))
    return out


def comments_for(thread_id: int) -> list[dict]:
    """One thread's comments, oldest first. Flat, always."""
    with SessionLocal() as session:
        rows = session.exec(
            select(Comment).where(Comment.thread_id == thread_id)
            .order_by(Comment.id)).all()
        return [serialize_comment(row) for row in rows]


def thread_with_comments(subject_kind: str, subject_id: str) -> list[dict]:
    """The subject's threads, each carrying its comments."""
    return [{**thread, "comments": comments_for(thread["id"])}
            for thread in threads_for(subject_kind, subject_id)]


def delete_thread(thread_id: int) -> bool:
    """Remove a thread and its comments. True when one was removed.

    The comments go explicitly rather than relying on the FK: SQLite enforces
    `ON DELETE CASCADE` only with `foreign_keys` on, which is a connection
    setting no writer here should have to depend on.
    """
    with SessionLocal() as session:
        row = session.get(CommentThread, thread_id)
        if row is None:
            return False
        session.exec(delete(Comment).where(Comment.thread_id == thread_id))
        session.delete(row)
        session.commit()
    log.write("comment_thread_deleted", thread_id=thread_id)
    return True


def delete_for_subject(session, subject_kind: str, subject_id: str) -> int:
    """Remove every thread about a subject, in the caller's transaction.

    Takes the session rather than opening one, because the only caller is the
    subject's own delete path and that path is mid-transaction — a second
    connection there deadlocks against the first on SQLite.

    There is no FK pointing at the subject to cascade from; the subject's
    delete path IS the cascade.
    """
    rows = session.exec(
        select(CommentThread.id)
        .where(CommentThread.subject_kind == subject_kind,
               CommentThread.subject_id == str(subject_id))).all()
    ids = [row if isinstance(row, int) else row[0] for row in rows]
    if not ids:
        return 0
    session.exec(delete(Comment).where(Comment.thread_id.in_(ids)))
    session.exec(delete(CommentThread).where(CommentThread.id.in_(ids)))
    log.write("comment_threads_deleted_with_subject",
              subject_kind=subject_kind, subject_id=str(subject_id),
              count=len(ids))
    return len(ids)
