"""The process-entity kinds: features, tasks and defects.

Each is one SQL LIKE over the entity's own text — a feature's title and spec,
a task's title and body, a defect's symptom and missing check — scored by the
shared `score_doc` rules. Closed items sort after every open one whatever
their score: the palette is a navigator for work in flight, and a shipped
feature that mentions the query more often is still the less likely target.

Unlike the span-backed kinds there is no scan bound: these tables hold
hundreds of rows, not millions, and a bound would make `total` the bound
and leave every older match unreachable from Drill.
"""

from __future__ import annotations

from lib.features import events as feature_events
from lib.fulltext.docs import (
    KIND_DEFECT, KIND_FEATURE, KIND_TASK, LIKE_ESCAPE, Scored, SearchDoc,
    score_doc,
)

FEATURE_CLOSED_STAGES = (feature_events.STAGE_SHIPPED,
                         feature_events.STAGE_ESCAPED,
                         feature_events.STAGE_ABANDONED)
TASK_CLOSED_STATUSES = ("done", "failed", "archived")


def escape_like(text: str) -> str:
    """`text` as a literal inside a `LIKE ? ESCAPE '~'` pattern."""
    for ch in (LIKE_ESCAPE, "%", "_"):
        text = text.replace(ch, LIKE_ESCAPE + ch)
    return text


def like_text(query: str) -> str:
    return f"%{escape_like(query)}%"


def _likes(columns: tuple[str, ...]) -> str:
    return " OR ".join(f"{c} LIKE ? ESCAPE '{LIKE_ESCAPE}'" for c in columns)


def rank_open_first(scored: list[Scored]) -> list[Scored]:
    return sorted(scored, key=lambda s: (s.extra.get("closed", False),
                                         -s.score, s.order))


def _scored(docs: list[tuple[SearchDoc, bool]], query: str) -> list[Scored]:
    out = []
    for order, (doc, closed) in enumerate(docs):
        hit = score_doc(doc, query, order)
        if hit:
            hit.extra["closed"] = closed
            out.append(hit)
    return rank_open_first(out)


_FEATURE_SQL = f"""
WITH last_event AS (
    SELECT e.feature_id, e.kind,
           (SELECT COUNT(*) FROM defects d WHERE d.blamed_event_id = e.id) AS escapes
    FROM feature_events e
    WHERE e.id = (SELECT MAX(x.id) FROM feature_events x WHERE x.feature_id = e.feature_id)
)
SELECT f.id, f.title, f.body,
       {feature_events.stage_sql('le.kind', 'le.escapes')} AS stage
FROM features f LEFT JOIN last_event le ON le.feature_id = f.id
WHERE {_likes(('f.title', 'f.body'))}
ORDER BY f.id DESC"""


def search_features(conn, query: str) -> list[Scored]:
    pattern = like_text(query)
    docs = [
        (SearchDoc(kind=KIND_FEATURE, id=str(fid), title=title or f"F{fid}",
                   tag=stage, crumb=f"feature · F{fid}", body=body or "",
                   href=f"/features/{fid}"),
         stage in FEATURE_CLOSED_STAGES)
        for fid, title, body, stage in conn.execute(
            _FEATURE_SQL, (pattern, pattern))
    ]
    return _scored(docs, query)


def search_tasks(conn, query: str) -> list[Scored]:
    pattern = like_text(query)
    sql = ("SELECT id, title, body, status FROM agent_tasks "
           f"WHERE {_likes(('title', 'body'))} ORDER BY id DESC")
    docs = [
        (SearchDoc(kind=KIND_TASK, id=str(tid), title=title or f"T{tid}",
                   tag=status, crumb=f"task · T{tid}", body=body or "",
                   href=f"/tasks/{tid}"),
         status in TASK_CLOSED_STATUSES)
        for tid, title, body, status in conn.execute(
            sql, (pattern, pattern))
    ]
    return _scored(docs, query)


def search_defects(conn, query: str) -> list[Scored]:
    """A defect has no title column: R3 titles it by its symptom's first line,
    and the rest of the symptom plus the missing check is its body."""
    pattern = like_text(query)
    sql = ("SELECT id, symptom, missing_check, status, triage_state FROM defects "
           f"WHERE {_likes(('symptom', 'missing_check'))} ORDER BY id DESC")
    docs = []
    for did, symptom, missing, status, triage in conn.execute(
            sql, (pattern, pattern)):
        lines = [ln.strip() for ln in str(symptom or "").splitlines() if ln.strip()]
        body = "\n".join([*lines[1:], *([missing] if missing else [])])
        docs.append((
            SearchDoc(kind=KIND_DEFECT, id=str(did),
                      title=lines[0] if lines else f"D{did}",
                      tag=status, crumb=f"defect · D{did}", body=body,
                      href=f"/defects/{did}"),
            status == "closed" or triage == "declined"))
    return _scored(docs, query)


ENTITY_SEARCHES = {
    KIND_FEATURE: search_features,
    KIND_TASK: search_tasks,
    KIND_DEFECT: search_defects,
}
