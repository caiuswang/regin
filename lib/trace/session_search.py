"""The session list's `q` predicate, shared by `GET /api/sessions` and the
`@`-reference menu so the two find the same sessions for the same words."""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlmodel import select

from lib.orm.models import Session as SessionModel, SessionSpan


def search_clause(search: str, scope: str):
    """`scope` is `title`, `prompt`, or anything else for both."""
    # COLLATE NOCASE via .ilike on SQLite; for MySQL the default
    # collation already matches case-insensitively on varchar columns.
    title_clause = SessionModel.title.ilike(f"%{search}%")
    # Substring match on the `text` attribute of `prompt` spans.
    # json_extract isolates the field so a hit on (say) a tool
    # input that happens to contain the term doesn't pollute the
    # result set. SQLite 3.38+ ships json1 by default.
    prompt_clause = SessionModel.trace_id.in_(
        select(SessionSpan.trace_id)
        .where(SessionSpan.name == 'prompt')
        .where(
            func.json_extract(SessionSpan.attributes, '$.text')
            .ilike(f"%{search}%")
        )
    )
    return {'title': title_clause, 'prompt': prompt_clause}.get(
        scope, or_(title_clause, prompt_clause))
