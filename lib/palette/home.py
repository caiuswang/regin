"""The palette's empty query (R1 Home): a desk of what needs the user, what is
moving, and where they were — or, opened from a session, that session's chain.

Every group answers `{label, total, items: PaletteRow[]}`; `total` counts what
the group would hold uncapped, so the client's "Show all N" never guesses.
Nothing here writes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.defects import Defect
from lib.palette import rows as R
from lib.trace.alias import aliased_run_ids, trace_group
from lib.trace.pending_spans import HUMAN_BLOCKER_NAMES, PERM_PENDING_PREFIX, parse_naive_ts
from lib.trace.session_tags import system_origins

NEEDS_ME_CAP = 5
ACTIVE_CAP = 5
FEATURES_CAP = 5
SESSIONS_CAP = 4
# Sessions phased to find the working ones. A parked session joins regardless
# of age: a question nobody answered for a week is still waiting.
RECENT_CANDIDATES = 40

# Literal, not bound: SQLite only uses `idx_session_spans_pending` when the
# query's predicate textually implies the partial index's own.
_PARKED_SQL = (
    "SELECT DISTINCT trace_id FROM session_spans WHERE status_code = 'PENDING' "
    f"AND (name IN ({','.join(repr(n) for n in HUMAN_BLOCKER_NAMES)}) "
    f"OR span_id LIKE '{PERM_PENDING_PREFIX}%')")


def _group(label: str, items: list, total: int) -> dict:
    return {"label": label, "total": total, "items": items}


def _marks(values) -> str:
    return ",".join("?" * len(values))


def _local(ts, *, utc: bool):
    """A stamp on the host-local basis span and session stamps use. Pipeline
    tables write naive UTC (`datetime('now')`), so theirs are shifted first."""
    if utc and isinstance(ts, str) and ts and not ts.endswith("Z") and "+" not in ts[10:]:
        ts = ts + "+00:00"
    return parse_naive_ts(ts) or datetime.min


def _eligible() -> tuple[str, list]:
    """The sessions a human attaches to — the roster's own filter: no test
    fixtures, no system runs, no hand-posted rows without a cwd, and a
    regin-launched run once, under its child's id."""
    origins = list(system_origins())
    with SessionLocal() as session:
        hidden = aliased_run_ids(session)
    where = ("is_test = 0 AND cwd IS NOT NULL "
             f"AND (origin IS NULL OR origin NOT IN ({_marks(origins)}))")
    if hidden:
        where += f" AND trace_id NOT IN ({_marks(hidden)})"
    return where, [*origins, *hidden]


def _candidates(conn, where: str, params: list) -> list[str]:
    live = f"{where} AND COALESCE(status, '') <> 'ended'"
    recent = [r[0] for r in conn.execute(
        f"SELECT trace_id FROM sessions WHERE {live} "
        "ORDER BY last_seen DESC LIMIT ?", [*params, RECENT_CANDIDATES])]
    parked = [r[0] for r in conn.execute(
        f"SELECT trace_id FROM sessions WHERE {live} "
        f"AND trace_id IN ({_PARKED_SQL})", params)]
    return list(dict.fromkeys(recent + parked))


def _last_seen(conn, trace_ids) -> dict:
    if not trace_ids:
        return {}
    return dict(conn.execute(
        f"SELECT trace_id, last_seen FROM sessions WHERE trace_id IN ({_marks(trace_ids)})",
        list(trace_ids)).fetchall())


def _queued_defects(limit: int) -> tuple[list[int], int]:
    """The triage queue, oldest report first — through the store's own
    predicate, so this list and the queue badge cannot disagree."""
    from sqlalchemy import func  # noqa: PLC0415

    from lib.defects.store import in_queue  # noqa: PLC0415

    with SessionLocal() as session:
        total = session.exec(select(func.count()).select_from(Defect)
                             .where(in_queue())).one()
        ids = list(session.exec(select(Defect.id).where(in_queue())
                                .order_by(Defect.opened_at, Defect.id)
                                .limit(limit)).all()) if limit > 0 else []
    return ids, total


def _needs_me(conn, waiting: list[str], phases: dict) -> dict:
    """Parked sessions oldest wait first, then the triage queue oldest first.
    The two never interleave: a session waits on the user right now, a defect
    waits on a decision."""
    blockers = R.parked_blockers(conn, waiting)
    seen = _last_seen(conn, waiting)
    waiting = sorted(waiting, key=lambda t: _local(
        R.blocker_since(blockers[t]) if t in blockers else seen.get(t), utc=False))
    shown = waiting[:NEEDS_ME_CAP]
    defect_ids, queued = _queued_defects(NEEDS_ME_CAP - len(shown))
    session_rows = R.session_rows(conn, shown, phases=phases)
    defect_rows = R.defect_rows(conn, defect_ids)
    items = ([session_rows[t] for t in shown if t in session_rows]
             + [defect_rows[d] for d in defect_ids if d in defect_rows])
    return _group("Needs me", items, len(waiting) + queued)


def _running_tasks(conn) -> dict:
    """`{task id: (trace ids its runs touch, latest run start)}` for every
    running task — each run's own id plus its alias halves, since the session
    a human sees is the run's child."""
    runs: dict = {}
    for task_id, trace_id, started in conn.execute(
            "SELECT t.id, r.trace_id, r.started_at FROM agent_tasks t "
            "LEFT JOIN agent_task_runs r ON r.task_id = t.id "
            "WHERE t.status = 'running' ORDER BY r.id"):
        traces, latest = runs.get(task_id, (set(), None))
        if trace_id:
            traces.add(trace_id)
        runs[task_id] = (traces, started or latest)
    every = [t for traces, _ in runs.values() for t in traces]
    if every:
        for linked, run_ids in R.alias_map(conn, every).items():
            for traces, _ in runs.values():
                if traces & run_ids:
                    traces.add(linked)
    return runs


def _active_now(conn, working: list[str], shown: set, phases: dict) -> dict:
    """Working sessions and running tasks, newest first. A running task folds
    into its session — the session row already carries its `T` chip — and
    stands alone only when no session of its runs is on the desk."""
    seen = _last_seen(conn, working)
    entries = [(_local(seen.get(t), utc=False), "session", t) for t in working]
    on_desk = shown | set(working)
    for task_id, (traces, started) in _running_tasks(conn).items():
        if not traces & on_desk:
            entries.append((_local(started, utc=True), "task", task_id))
    entries.sort(key=lambda e: e[0], reverse=True)
    top = entries[:ACTIVE_CAP]
    session_rows = R.session_rows(conn, [k for _, kind, k in top if kind == "session"],
                                  phases=phases)
    task_rows = R.task_rows(conn, [k for _, kind, k in top if kind == "task"])
    built = {"session": session_rows, "task": task_rows}
    items = [built[kind][k] for _, kind, k in top if k in built[kind]]
    return _group("Active now", items, len(entries))


_BUILDING_SQL = """
WITH last_event AS (
    SELECT e.feature_id, e.kind, e.at,
           (SELECT COUNT(*) FROM defects d WHERE d.blamed_event_id = e.id) AS escapes
    FROM feature_events e
    WHERE e.id = (SELECT MAX(x.id) FROM feature_events x WHERE x.feature_id = e.feature_id)
)
SELECT le.feature_id FROM last_event le
WHERE {stage} = 'building'
ORDER BY le.at DESC, le.feature_id DESC"""


def _active_features(conn) -> dict:
    from lib.features import events as feature_events  # noqa: PLC0415

    ids = [r[0] for r in conn.execute(_BUILDING_SQL.format(
        stage=feature_events.stage_sql("le.kind", "le.escapes")))]
    rows = R.feature_rows(conn, ids[:FEATURES_CAP])
    return _group("Active features",
                  [rows[f] for f in ids[:FEATURES_CAP] if f in rows], len(ids))


def _sessions(conn, where: str, params: list, shown: set) -> dict:
    skip = list(shown)
    clause = f"{where} AND trace_id NOT IN ({_marks(skip)})" if skip else where
    args = [*params, *skip]
    total = conn.execute(f"SELECT COUNT(*) FROM sessions WHERE {clause}", args).fetchone()[0]
    ids = [r[0] for r in conn.execute(
        f"SELECT trace_id FROM sessions WHERE {clause} "
        "ORDER BY last_seen DESC LIMIT ?", [*args, SESSIONS_CAP])]
    rows = R.session_rows(conn, ids)
    return _group("Sessions", [rows[t] for t in ids if t in rows], total)


def home(conn) -> dict:
    where, params = _eligible()
    candidates = _candidates(conn, where, params)
    phases = R.session_phases(conn, candidates)
    buckets = {t: R.session_status(p)["bucket"] for t, p in phases.items()}
    waiting = [t for t in candidates if buckets[t] == R.NEEDS_YOU]
    working = [t for t in candidates if buckets[t] == R.ACTIVE]
    needs_me = _needs_me(conn, waiting, phases)
    active_now = _active_now(conn, working, set(waiting), phases)
    shown = set(waiting) | set(working)
    return {
        "needs_me": needs_me,
        "active_now": active_now,
        "active_features": _active_features(conn),
        "sessions": _sessions(conn, where, params, shown),
        "server_now": datetime.now().isoformat(),
        "server_now_utc": datetime.now(timezone.utc).isoformat(),
    }


def chain(conn, trace_id: str) -> list[dict]:
    """This session's chain: the session (marked `here`), its features, the
    task it runs, the defects reported from it. A session regin never
    recorded has no chain."""
    group = trace_group(conn, trace_id) or [trace_id]
    if not conn.execute(f"SELECT 1 FROM sessions WHERE trace_id IN ({_marks(group)})",
                        group).fetchone():
        return []
    features, tasks = R.session_links(conn, [trace_id])
    feature_ids = [c["id"] for c in features.get(trace_id, [])]
    task_ids = [tasks[trace_id]["id"]] if trace_id in tasks else []
    defect_ids = [r[0] for r in conn.execute(
        f"SELECT id FROM defects WHERE reported_by_trace IN ({_marks(group)}) "
        "ORDER BY opened_at, id", group)]
    links = (
        (R.session_rows(conn, [trace_id], here=trace_id), [trace_id]),
        (R.feature_rows(conn, feature_ids), feature_ids),
        (R.task_rows(conn, task_ids), task_ids),
        (R.defect_rows(conn, defect_ids), defect_ids),
    )
    return [rows[key] for rows, keys in links for key in keys if key in rows]
