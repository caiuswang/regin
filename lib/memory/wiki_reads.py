"""Derive the ``'read'`` wiki-recall signal from the session trace.

The exposure signal (``bump_wiki_recall`` in ``index_fetch``) records only that
a wiki *path* was surfaced. This module reconstructs the stronger ``'read'``
signal — the agent actually opened the file — from ``tool.Read`` spans whose
``file_path`` points into ``.regin/topics/wiki/<id>.md``. Recompute, not
increment: the count is derived fresh from the append-only span log each run,
so the sync is idempotent (see ``store.replace_wiki_read_counts``).
"""

from __future__ import annotations

import json

_WIKI_SEG = "/.regin/topics/wiki/"


def _topic_id_from_path(file_path: str) -> str | None:
    """`.../.regin/topics/wiki/<id>.md` -> `<id>`, else None. Rejects nested
    paths and the generated `index.md`, which is not a per-topic wiki."""
    idx = file_path.rfind(_WIKI_SEG)
    if idx < 0:
        return None
    rest = file_path[idx + len(_WIKI_SEG):]
    if "/" in rest or not rest.endswith(".md"):
        return None
    topic_id = rest[:-len(".md")]
    return topic_id if topic_id and topic_id != "index" else None


def compute_wiki_reads() -> dict[str, dict]:
    """Aggregate wiki `tool.Read` spans into
    ``{topic_id: {'count': int, 'last_read': str|None}}``.

    The `attributes` LIKE prefilter narrows the (large) Read-span set to the
    handful touching the wiki dir before any JSON is parsed."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import SessionSpan

    stmt = (select(SessionSpan.attributes, SessionSpan.start_time)
            .where(SessionSpan.name == "tool.Read")
            .where(SessionSpan.attributes.like(f"%{_WIKI_SEG}%")))
    agg: dict[str, dict] = {}
    with SessionLocal() as session:
        for attributes, start_time in session.exec(stmt).all():
            try:
                file_path = (json.loads(attributes or "{}") or {}).get("file_path")
            except (ValueError, TypeError):
                continue
            topic_id = _topic_id_from_path(file_path) if file_path else None
            if topic_id is None:
                continue
            row = agg.setdefault(topic_id, {"count": 0, "last_read": None})
            row["count"] += 1
            if start_time and (row["last_read"] is None
                               or start_time > row["last_read"]):
                row["last_read"] = start_time
    return agg


def sync_wiki_reads() -> list[dict]:
    """Recompute all ``signal='read'`` wiki counters from the trace and persist
    them. Returns the derived rows, most-read first. Idempotent."""
    import lib.memory as memory

    agg = compute_wiki_reads()
    memory.get_store().replace_wiki_read_counts(agg)
    return [{"topic_id": topic_id, **data} for topic_id, data
            in sorted(agg.items(), key=lambda kv: kv[1]["count"], reverse=True)]
