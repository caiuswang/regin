"""CRUD for the agent → human message channel (`send_to_user` inbox).

Single source of truth for writing and reading `agent_messages`. Called
from three places, all through `record_message`:

  * the PostToolUse hook, when an `mcp__*__send_to_user` call lands
    (the real ingest path — see `hook_manager/handlers/post_tool_trace`),
  * the web API (read + read/ack/dismiss state mutations),
  * tests, which seed synthetic rows with `is_test=True`.

Keeping every write behind this module means supersede-by-key, the body
cap, link normalization, and webhook dispatch all happen in exactly one
place regardless of caller.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete as sa_delete
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.agent_messages.push import registry as push
from lib.orm import SessionLocal
from lib.orm.models.agent_messages import (
    AgentMessage, DEFAULT_MESSAGE_TYPE, MESSAGE_TYPES,
)

log = get_activity_logger("agent_messages")

# Generous body ceiling — `send_to_user` is "content the user must see
# exactly as written", so the cap exists only to bound a pathological
# payload, not to trim normal progress notes.
_BODY_MAX = 16_000


def _now() -> str:
    return datetime.now().isoformat()


def _normalize_type(msg_type: Optional[str]) -> str:
    return msg_type if msg_type in MESSAGE_TYPES else DEFAULT_MESSAGE_TYPE


def _normalize_links(links) -> Optional[str]:
    """Coerce a links arg into a JSON array of {label, href} or None.

    Accepts a list of bare strings (used as both label and href) or a
    list of dicts already shaped {label, href}. Anything else → None.
    """
    if not links or not isinstance(links, (list, tuple)):
        return None
    out = []
    for item in links:
        if isinstance(item, str) and item.strip():
            out.append({"label": item, "href": item})
        elif isinstance(item, dict) and item.get("href"):
            out.append({"label": item.get("label") or item["href"],
                        "href": item["href"]})
    return json.dumps(out) if out else None


def _load_links(raw: Optional[str]):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _serialize(m: AgentMessage) -> dict:
    return {
        "id": m.id, "trace_id": m.trace_id, "span_id": m.span_id,
        "agent_id": m.agent_id, "agent_type": m.agent_type,
        "msg_type": m.msg_type, "title": m.title, "body": m.body,
        "msg_key": m.msg_key, "links": _load_links(m.links),
        "pinned": bool(m.pinned), "version": m.version,
        "webhook_status": m.webhook_status,
        "read_at": m.read_at, "acked_at": m.acked_at,
        "dismissed_at": m.dismissed_at, "is_test": bool(m.is_test),
        "created_at": m.created_at, "updated_at": m.updated_at,
    }


def _find_live_keyed(session, trace_id: str, msg_key: str):
    """Most-recent non-dismissed message with this (session, key), or None."""
    stmt = (select(AgentMessage)
            .where(AgentMessage.trace_id == trace_id,
                   AgentMessage.msg_key == msg_key,
                   AgentMessage.dismissed_at.is_(None))
            .order_by(AgentMessage.id.desc())
            .limit(1))
    return session.exec(stmt).first()


def _upsert(session, *, trace_id, body, mtype, title, msg_key, links_json,
            span_id, agent_id, agent_type, is_test, now) -> AgentMessage:
    existing = _find_live_keyed(session, trace_id, msg_key) if msg_key else None
    if existing is not None:
        existing.body = body
        existing.title = title
        existing.msg_type = mtype
        existing.links = links_json
        existing.version = (existing.version or 1) + 1
        existing.updated_at = now
        existing.read_at = None  # the updated card re-surfaces as unread
        if span_id:
            existing.span_id = span_id
        session.add(existing)
        return existing
    row = AgentMessage(
        trace_id=trace_id, body=body, msg_type=mtype, title=title,
        msg_key=msg_key, links=links_json, span_id=span_id,
        agent_id=agent_id, agent_type=agent_type,
        is_test=1 if is_test else 0, created_at=now, updated_at=now)
    session.add(row)
    return row


def _set_webhook_status(message_id: int, status: str) -> None:
    with SessionLocal() as session:
        row = session.get(AgentMessage, message_id)
        if row is not None:
            row.webhook_status = status
            session.add(row)
            session.commit()


def record_message(*, trace_id: str, body: str, msg_type: Optional[str] = None,
                    title: Optional[str] = None, msg_key: Optional[str] = None,
                    links=None, span_id: Optional[str] = None,
                    agent_id: Optional[str] = None,
                    agent_type: Optional[str] = None, is_test: bool = False,
                    dispatch_webhook: bool = True) -> Optional[dict]:
    """Persist one agent → user message (insert, or supersede if `msg_key`
    matches a live message in the same session). Returns the serialized
    row, or None if `trace_id` is missing."""
    if not trace_id:
        return None
    mtype = _normalize_type(msg_type)
    body = (body or "")[:_BODY_MAX]
    links_json = _normalize_links(links)
    now = _now()
    with SessionLocal() as session:
        row = _upsert(session, trace_id=trace_id, body=body, mtype=mtype,
                      title=title, msg_key=msg_key, links_json=links_json,
                      span_id=span_id, agent_id=agent_id, agent_type=agent_type,
                      is_test=is_test, now=now)
        session.commit()
        session.refresh(row)
        data = _serialize(row)
    log.write("message_recorded", message_id=data["id"], trace_id=trace_id,
              msg_type=mtype, superseded=data["version"] > 1)
    if dispatch_webhook:
        status = push.maybe_dispatch(data)
        if status is not None:
            _set_webhook_status(data["id"], status)
            data["webhook_status"] = status
    _enforce_retention()
    return data


def live_keyed_message(trace_id: str, msg_key: str) -> Optional[dict]:
    """The live (non-dismissed) message for this (session, key), or None.
    Lets a programmatic producer notify once and skip re-surfacing an inbox
    card the user hasn't dismissed yet."""
    if not trace_id or not msg_key:
        return None
    with SessionLocal() as session:
        row = _find_live_keyed(session, trace_id, msg_key)
        return _serialize(row) if row is not None else None


def dismiss_keyed(trace_id: str, msg_key: str) -> int:
    """Dismiss the live keyed message, resolving a notification once its
    underlying condition is handled. Returns rows dismissed (0 or 1)."""
    if not trace_id or not msg_key:
        return 0
    now = _now()
    with SessionLocal() as session:
        row = _find_live_keyed(session, trace_id, msg_key)
        if row is None:
            return 0
        row.dismissed_at = now
        session.add(row)
        session.commit()
    return 1


# ── Reads ────────────────────────────────────────────────────

def list_session_messages(trace_id: str,
                          include_dismissed: bool = False) -> list[dict]:
    """All messages for one session, oldest first (the Messages tab feed)."""
    with SessionLocal() as session:
        stmt = select(AgentMessage).where(AgentMessage.trace_id == trace_id)
        if not include_dismissed:
            stmt = stmt.where(AgentMessage.dismissed_at.is_(None))
        stmt = stmt.order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        rows = session.exec(stmt).all()
        log.read("session_messages_listed", trace_id=trace_id, count=len(rows))
        return [_serialize(r) for r in rows]


def _session_titles(session, trace_ids: list[str]) -> dict:
    """Map trace_id → session title for inbox display (one query)."""
    if not trace_ids:
        return {}
    from lib.orm.models import Session as SessionModel
    rows = session.exec(
        select(SessionModel.trace_id, SessionModel.title)
        .where(SessionModel.trace_id.in_(trace_ids))).all()
    return {r[0]: r[1] for r in rows}


def list_inbox(*, unread_only: bool = False, include_tests: bool = False,
               types: Optional[list[str]] = None, limit: int = 200) -> list[dict]:
    """Cross-session inbox feed, newest first. Dismissed rows excluded."""
    with SessionLocal() as session:
        stmt = select(AgentMessage).where(AgentMessage.dismissed_at.is_(None))
        if not include_tests:
            stmt = stmt.where(AgentMessage.is_test == 0)
        if unread_only:
            stmt = stmt.where(AgentMessage.read_at.is_(None))
        if types:
            stmt = stmt.where(AgentMessage.msg_type.in_(types))
        stmt = stmt.order_by(AgentMessage.created_at.desc(),
                             AgentMessage.id.desc()).limit(limit)
        rows = session.exec(stmt).all()
        titles = _session_titles(session, [r.trace_id for r in rows])
        log.read("inbox_listed", count=len(rows), unread_only=unread_only)
        out = []
        for r in rows:
            d = _serialize(r)
            d["session_title"] = titles.get(r.trace_id)
            out.append(d)
        return out


def unread_count(include_tests: bool = False) -> int:
    """Count of un-read, non-dismissed messages — drives the nav badge."""
    with SessionLocal() as session:
        stmt = select(AgentMessage).where(
            AgentMessage.read_at.is_(None),
            AgentMessage.dismissed_at.is_(None))
        if not include_tests:
            stmt = stmt.where(AgentMessage.is_test == 0)
        return len(session.exec(stmt).all())


# ── State mutations ──────────────────────────────────────────

def _stamp(message_ids: list[int], field: str, *, only_if_unset: bool) -> int:
    """Set `field` = now on the given rows; return how many were changed."""
    if not message_ids:
        return 0
    now = _now()
    changed = 0
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentMessage).where(AgentMessage.id.in_(message_ids))).all()
        for r in rows:
            if only_if_unset and getattr(r, field) is not None:
                continue
            setattr(r, field, now)
            session.add(r)
            changed += 1
        session.commit()
    return changed


def mark_read(message_ids: list[int]) -> int:
    n = _stamp(message_ids, "read_at", only_if_unset=True)
    log.write("messages_marked_read", count=n)
    return n


def ack(message_id: int) -> int:
    # Acking implies reading; stamp read_at too if still unset.
    _stamp([message_id], "read_at", only_if_unset=True)
    n = _stamp([message_id], "acked_at", only_if_unset=True)
    log.write("message_acked", message_id=message_id)
    return n


def dismiss(message_id: int) -> int:
    n = _stamp([message_id], "dismissed_at", only_if_unset=True)
    log.write("message_dismissed", message_id=message_id)
    return n


def set_pinned(message_id: int, pinned: bool) -> bool:
    with SessionLocal() as session:
        row = session.get(AgentMessage, message_id)
        if row is None:
            return False
        row.pinned = 1 if pinned else 0
        session.add(row)
        session.commit()
    log.write("message_pinned", message_id=message_id, pinned=pinned)
    return True


# ── Retention / pruning ──────────────────────────────────────
# The inbox is otherwise grow-forever: keyless sends always insert, and
# `dismissed_at` is a soft flag (rows linger). Pruning is the only hard
# delete — kept behind this module like every other write.

def _prune_conditions(*, older_than_days, dismissed_only, keep_pinned,
                      include_tests) -> list:
    """Build the candidate-row filters shared by prune + its dry-run."""
    conds = []
    if older_than_days is not None:
        cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
        conds.append(AgentMessage.created_at < cutoff)
    if dismissed_only:
        conds.append(AgentMessage.dismissed_at.is_not(None))
    if keep_pinned:
        conds.append(AgentMessage.pinned == 0)
    if not include_tests:
        conds.append(AgentMessage.is_test == 0)
    return conds


def prune_messages(*, older_than_days: Optional[int] = None,
                   dismissed_only: bool = False, keep: Optional[int] = None,
                   keep_pinned: bool = True, include_tests: bool = True,
                   dry_run: bool = False) -> int:
    """Hard-delete inbox messages matching the criteria; return the count
    deleted (or that *would* be, when `dry_run`).

    At least one of `older_than_days` / `dismissed_only` / `keep` must be
    given — a criteria-free call raises `ValueError` rather than wiping the
    whole inbox. Pinned rows are protected unless `keep_pinned=False`; test
    rows are protected only when `include_tests=False`. `keep=N` retains the
    N newest matching rows and deletes the older remainder.
    """
    if older_than_days is None and not dismissed_only and keep is None:
        raise ValueError("prune_messages needs at least one criterion: "
                         "older_than_days, dismissed_only, or keep")
    conds = _prune_conditions(
        older_than_days=older_than_days, dismissed_only=dismissed_only,
        keep_pinned=keep_pinned, include_tests=include_tests)
    with SessionLocal() as session:
        stmt = (select(AgentMessage.id).where(*conds)
                .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()))
        ids = list(session.exec(stmt).all())
        to_delete = ids[keep:] if keep is not None else ids
        if not dry_run and to_delete:
            session.exec(sa_delete(AgentMessage)
                         .where(AgentMessage.id.in_(to_delete)))
            session.commit()
    if not dry_run:
        log.write("messages_pruned", count=len(to_delete),
                  older_than_days=older_than_days, dismissed_only=dismissed_only,
                  keep=keep)
    return len(to_delete)


def _enforce_retention() -> None:
    """Auto-prune past `settings.agent_messages.retention_days`, if set.

    Called after each write so an opt-in retention window keeps the inbox
    bounded with no manual step. Off by default (None → keep forever, the
    original behavior). Never raises into the write path.
    """
    from lib.settings import settings
    cfg = settings.agent_messages
    days = getattr(cfg, "retention_days", None)
    if not days or days <= 0:
        return
    try:
        prune_messages(older_than_days=days,
                       keep_pinned=getattr(cfg, "retention_keep_pinned", True))
    except Exception:  # best-effort background prune — never break the write path
        log.error("retention_prune_failed", exc_info=True)


def message_stats() -> dict:
    """Counts that drive `regin messages stats` (total / unread / dismissed /
    pinned / test) plus the oldest row's timestamp."""
    with SessionLocal() as session:
        def _n(*conds) -> int:
            return len(session.exec(select(AgentMessage.id).where(*conds)).all())
        oldest = session.exec(
            select(AgentMessage.created_at)
            .order_by(AgentMessage.created_at.asc()).limit(1)).first()
        return {
            "total": _n(),
            "unread": _n(AgentMessage.read_at.is_(None),
                         AgentMessage.dismissed_at.is_(None)),
            "dismissed": _n(AgentMessage.dismissed_at.is_not(None)),
            "pinned": _n(AgentMessage.pinned == 1),
            "tests": _n(AgentMessage.is_test == 1),
            "oldest": oldest,
        }


__all__ = [
    "record_message", "list_session_messages", "list_inbox",
    "unread_count", "mark_read", "ack", "dismiss", "set_pinned",
    "live_keyed_message", "dismiss_keyed",
    "prune_messages", "message_stats",
]
