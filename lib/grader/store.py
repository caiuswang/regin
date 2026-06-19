"""Persistence for session grades (`session_grades` in the primary DB).

Append-only by convention: every grading run inserts new rows; readers
take the latest row per (trace_id, axis). `ensure_schema()` keeps CLI
paths working against older local DBs that predate the table — the same
CREATE TABLE the web app runs at startup (`web/startup.py`) and `regin
init` bakes from `db/schema.sql`.
"""

from __future__ import annotations

import json

from sqlmodel import func, select

from lib.activity_log import get_activity_logger
from lib.grader.models import AxisGrade
from lib.orm import SessionLocal
from lib.orm.models.grades import SessionGrade
from lib.orm.models.trace import Session as SessionRow

log = get_activity_logger("grader")

_schema_ready = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_grades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    axis            TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'screen',
    scoreboard      TEXT NOT NULL DEFAULT '{}',
    report          TEXT NOT NULL DEFAULT '',
    detail          TEXT NOT NULL DEFAULT '{}',
    rubric_version  TEXT,
    judge           TEXT,
    is_test         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def ensure_schema() -> None:
    """Create `session_grades` if this DB predates the grader."""
    global _schema_ready
    if _schema_ready:
        return
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute(_SCHEMA_SQL)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session_grades_trace "
                     "ON session_grades(trace_id, axis)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session_grades_created "
                     "ON session_grades(created_at DESC)")
        conn.commit()
    finally:
        conn.close()
    _schema_ready = True


def save_grade(trace_id: str, grade: AxisGrade, *, is_test: int = 0) -> int:
    ensure_schema()
    row = SessionGrade(
        trace_id=trace_id,
        axis=grade.axis,
        verdict=grade.verdict,
        tier=grade.tier,
        scoreboard=json.dumps(grade.scoreboard),
        report=grade.report,
        detail=json.dumps(grade.detail),
        rubric_version=grade.rubric_version,
        judge=grade.judge,
        is_test=is_test,
    )
    with SessionLocal() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
    log.write("grade_saved", trace_id=trace_id, axis=grade.axis,
              verdict=grade.verdict, tier=grade.tier, grade_id=row.id)
    return int(row.id)


def _serialize(row: SessionGrade, *, with_detail: bool = False) -> dict:
    out = {
        "id": row.id,
        "trace_id": row.trace_id,
        "axis": row.axis,
        "verdict": row.verdict,
        "tier": row.tier,
        "scoreboard": json.loads(row.scoreboard or "{}"),
        "report": row.report,
        "rubric_version": row.rubric_version,
        "judge": row.judge,
        "is_test": row.is_test,
        "created_at": row.created_at,
    }
    if with_detail:
        out["detail"] = json.loads(row.detail or "{}")
    return out


def latest_grades(trace_id: str, *, with_detail: bool = True) -> dict:
    """Latest grade per axis for one session."""
    ensure_schema()
    with SessionLocal() as db:
        rows = db.exec(
            select(SessionGrade)
            .where(SessionGrade.trace_id == trace_id)
            .order_by(SessionGrade.id.desc())
        ).all()
    out: dict[str, dict] = {}
    for row in rows:
        if row.axis not in out:
            out[row.axis] = _serialize(row, with_detail=with_detail)
    return out


def _session_meta(db, trace_ids: list[str]) -> dict[str, dict]:
    if not trace_ids:
        return {}
    rows = db.exec(select(SessionRow)
                   .where(SessionRow.trace_id.in_(trace_ids))).all()
    return {r.trace_id: {"title": r.title, "cost_usd": r.cost_usd,
                         "prompts": r.prompts, "started_at": r.started_at}
            for r in rows}


def list_grades(*, limit: int = 100, axis: str | None = None,
                verdict: str | None = None,
                include_tests: bool = False) -> list[dict]:
    """Latest grades, newest first, with session metadata attached.

    Latest-per-(trace, axis) is resolved with a MAX(id) window subquery —
    no overfetch heuristic — and the verdict filter applies *after* that
    dedup, so a session whose newest grade superseded an older verdict
    never resurfaces under the old one.
    """
    ensure_schema()
    latest_ids = select(func.max(SessionGrade.id)).group_by(
        SessionGrade.trace_id, SessionGrade.axis)
    query = (select(SessionGrade)
             .where(SessionGrade.id.in_(latest_ids))
             .order_by(SessionGrade.id.desc()))
    if axis:
        query = query.where(SessionGrade.axis == axis)
    if verdict:
        query = query.where(SessionGrade.verdict == verdict)
    if not include_tests:
        query = query.where(SessionGrade.is_test == 0)
    with SessionLocal() as db:
        latest = db.exec(query.limit(max(limit, 1))).all()
        meta = _session_meta(db, [r.trace_id for r in latest])
    out = []
    for row in latest:
        entry = _serialize(row)
        entry["session"] = meta.get(row.trace_id, {})
        out.append(entry)
    return out


_PASS_VERDICTS = ("satisfied", "efficient")


def recent_failing_trace_ids(*, limit: int = 200,
                             include_tests: bool = False) -> list[str]:
    """Distinct trace ids whose *latest* grade on some axis missed its pass
    verdict, newest first — the candidate pool for failure-mode
    aggregation. Latest-per-(trace, axis) is resolved with the same
    MAX(id) window `list_grades` uses, so a session whose newest grade was
    upgraded to satisfied never resurfaces under an older failing row."""
    ensure_schema()
    latest_ids = select(func.max(SessionGrade.id)).group_by(
        SessionGrade.trace_id, SessionGrade.axis)
    query = (select(SessionGrade.trace_id, SessionGrade.id)
             .where(SessionGrade.id.in_(latest_ids))
             .where(SessionGrade.verdict.notin_(_PASS_VERDICTS))
             .order_by(SessionGrade.id.desc()))
    if not include_tests:
        query = query.where(SessionGrade.is_test == 0)
    seen: list[str] = []
    with SessionLocal() as db:
        for trace_id, _id in db.exec(query).all():
            if trace_id not in seen:
                seen.append(trace_id)
            if len(seen) >= limit:
                break
    return seen


__all__ = ["ensure_schema", "save_grade", "latest_grades", "list_grades",
           "recent_failing_trace_ids"]
