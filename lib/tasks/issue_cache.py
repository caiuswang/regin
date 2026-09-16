"""The one place a fetched issue is remembered (CAI-245).

Keyed by the feature — `(provider, external_key)` — and not by the link row
that happened to trigger the fetch, because a feature key may carry no link at
all and may carry two. One fact, one row, so the task-link card and the
feature ledger can never report different stages for the same issue.

**Every successful fetch writes here; only the web reads.** The asymmetry is
the point: a CLI run warms the surface, but `blame_state` deciding an escape
from a stale snapshot would rewrite the ledger's own numbers, so the CLI keeps
going to the tracker and `regin feature ledger` stays behaviourally unchanged.

A failed fetch writes nothing, which is what makes freshness two states rather
than three — never fetched, or fetched at T. A row meaning "tried and failed"
is one no reader could act on, and the reason already rides on the operator's
link as `sync_error`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select, text

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.tasks import IssueCache

log = get_activity_logger("tasks")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def remember(provider: str, external_key: str, issue) -> None:
    """Record what the tracker just said about one issue.

    Takes the normalized `ExternalIssue`, never a provider's raw payload: the
    cache holds the shape every reader already binds, so nothing downstream
    has to know which vendor answered.
    """
    if not provider or not external_key or issue is None:
        return
    snapshot = json.dumps(issue.to_dict())
    with SessionLocal() as session:
        row = session.get(IssueCache, (provider, external_key))
        if row is None:
            row = IssueCache(provider=provider, external_key=external_key,
                             snapshot_json=snapshot, synced_at=_now())
        else:
            row.snapshot_json = snapshot
            row.synced_at = _now()
        session.add(row)
        session.commit()
    log.write("issue_cached", provider=provider, external_key=external_key)


def lookup(provider: str, external_key: str) -> tuple[Optional[dict],
                                                      Optional[str]]:
    """`(snapshot, synced_at)` for one key, both None when never fetched."""
    if not provider or not external_key:
        return None, None
    with SessionLocal() as session:
        row = session.get(IssueCache, (provider, external_key))
        if row is None:
            return None, None
        synced_at = row.synced_at
        raw = row.snapshot_json
    try:
        return json.loads(raw), synced_at
    except json.JSONDecodeError:
        log.error("issue_cache_unreadable", provider=provider,
                  external_key=external_key)
        return None, synced_at


def lookup_many(keys) -> dict:
    """`{external_key: (snapshot, synced_at)}` in one statement.

    The index renders a stage per row and must not fire a query per row, so
    the whole page's cache arrives together.
    """
    wanted = [k for k in dict.fromkeys(keys) if k]
    if not wanted:
        return {}
    placeholders = ", ".join(f":k{n}" for n in range(len(wanted)))
    binds = {f"k{n}": key for n, key in enumerate(wanted)}
    with SessionLocal() as session:
        rows = session.exec(text(
            "SELECT external_key, snapshot_json, synced_at FROM issue_cache"
            f" WHERE external_key IN ({placeholders})"
        ).bindparams(**binds)).all()
    out = {}
    for key, raw, synced_at in rows:
        try:
            out[key] = (json.loads(raw), synced_at)
        except json.JSONDecodeError:
            out[key] = (None, synced_at)
    return out


def providers_for(external_key: str) -> list[str]:
    """Every provider holding a cached issue under this key."""
    with SessionLocal() as session:
        rows = session.exec(
            select(IssueCache.provider)
            .where(IssueCache.external_key == external_key)).all()
    return [row if isinstance(row, str) else row[0] for row in rows]
