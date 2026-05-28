"""Audit trail for tracking who changed what in the web dashboard.

Rows live in the `audit_log` table; writes are best-effort (never block
the main operation) and reads never surface a 500 (observability-only
surface, not load-bearing).

Both writer and reader route through `lib.orm.AuthSessionLocal()` — the
same dispatch the user-CRUD code uses (SQLite in standalone, MySQL in
shared mode).
"""

from __future__ import annotations

import json
from typing import Optional, Union

from sqlalchemy import func
from sqlmodel import select

from lib.logging_setup import get_logger
from lib.orm import AuthSessionLocal
from lib.orm.models import AuditLog


_log = get_logger(__name__)


def log_action(user_id: Optional[int], username: str, action: str,
               target: str, detail: Union[str, dict, None] = None) -> None:
    """Record an action in the audit log. Best-effort — swallows every
    error so a hiccup in the audit DB never breaks the originating call."""
    detail_str = json.dumps(detail) if isinstance(detail, dict) else detail
    try:
        with AuthSessionLocal() as session:
            row = AuditLog(
                user_id=user_id, username=username, action=action,
                target=target, detail=detail_str,
            )
            session.add(row)
            session.commit()
    except Exception as exc:
        _log.warning("audit_log.write_failed", error=str(exc),
                     action=action, target=target)


def _row_to_dict(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": row.username,
        "action": row.action,
        "target": row.target,
        "detail": row.detail,
        "created_at": row.created_at,
    }


def get_log(limit: int = 50, user: Optional[str] = None,
            action: Optional[str] = None) -> list[dict]:
    """Retrieve recent audit entries (legacy flat-list API)."""
    items, _total = get_log_page(page=0, size=limit, user=user, action=action)
    return items


def get_log_page(page: int = 0, size: int = 50,
                 user: Optional[str] = None,
                 action: Optional[str] = None) -> tuple[list[dict], int]:
    """Retrieve a page of audit entries plus the total matching count.

    Returns ``(items, total)``. Any exception returns ``([], 0)`` —
    the audit surface is observability, not load-bearing, so a
    transient DB hiccup must not 500 the dashboard.
    """
    try:
        with AuthSessionLocal() as session:
            filters = []
            if user:
                filters.append(AuditLog.username == user)
            if action:
                filters.append(AuditLog.action == action)

            count_stmt = select(func.count(AuditLog.id))
            for f in filters:
                count_stmt = count_stmt.where(f)
            total = session.exec(count_stmt).one()

            offset = max(0, page) * max(1, size)
            page_stmt = select(AuditLog)
            for f in filters:
                page_stmt = page_stmt.where(f)
            page_stmt = page_stmt.order_by(
                AuditLog.created_at.desc(), AuditLog.id.desc()
            ).limit(size).offset(offset)
            rows = session.exec(page_stmt).all()
            return [_row_to_dict(r) for r in rows], int(total)
    except Exception as exc:
        _log.warning("audit_log.read_failed", error=str(exc))
        return [], 0
