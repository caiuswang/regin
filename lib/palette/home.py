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


def _queued_defects() -> list[int]:
    """The triage queue, oldest report first — through the store's own
    predicate, so this list and the queue badge cannot disagree."""
    from lib.defects.store import in_queue  # noqa: PLC0415

    with SessionLocal() as session:
        return list(session.exec(select(Defect.id).where(in_queue())
                                 .order_by(Defect.opened_at, Defect.id)).all())


def _page(conn, label: str, entries: list, offset: int, limit: int,
          phases: dict | None = None) -> dict:
    """One page of a group's ordered `(kind, key)` entries as PaletteRows,
    one builder call per kind; `total` counts every entry."""
    window = entries[offset:offset + limit]
    keys: dict = {}
    for kind, key in window:
        keys.setdefault(kind, []).append(key)
    built = {kind: (R.session_rows(conn, ids, phases=phases) if kind == "session"
                    else _BUILDERS[kind](conn, ids))
             for kind, ids in keys.items()}
    items = [built[kind][key] for kind, key in window if key in built[kind]]
    return {"label": label, "total": len(entries), "offset": offset, "items": items}


_BUILDERS = {"task": R.task_rows, "feature": R.feature_rows, "defect": R.defect_rows}


def _needs_me(conn, waiting: list[str]) -> list:
    """Parked sessions oldest wait first, then the triage queue oldest first.
    The two never interleave: a session waits on the user right now, a defect
    waits on a decision."""
    blockers = R.parked_blockers(conn, waiting)
    seen = _last_seen(conn, waiting)
    waiting = sorted(waiting, key=lambda t: _local(
        R.blocker_since(blockers[t]) if t in blockers else seen.get(t), utc=False))
    return ([("session", t) for t in waiting]
            + [("defect", d) for d in _queued_defects()])


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


def _active_now(conn, working: list[str], shown: set) -> list:
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
    return [(kind, key) for _, kind, key in entries]


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


def _active_features(conn) -> list:
    from lib.features import events as feature_events  # noqa: PLC0415

    return [("feature", r[0]) for r in conn.execute(_BUILDING_SQL.format(
        stage=feature_events.stage_sql("le.kind", "le.escapes")))]


def _session_ids(conn, where: str, params: list, skip=()) -> list:
    skip = list(skip)
    clause = f"{where} AND trace_id NOT IN ({_marks(skip)})" if skip else where
    return [("session", r[0]) for r in conn.execute(
        f"SELECT trace_id FROM sessions WHERE {clause} ORDER BY last_seen DESC",
        [*params, *skip])]


def _desk(conn):
    """The phased candidates every desk group is cut from."""
    where, params = _eligible()
    candidates = _candidates(conn, where, params)
    phases = R.session_phases(conn, candidates)
    buckets = {t: R.session_status(p)["bucket"] for t, p in phases.items()}
    waiting = [t for t in candidates if buckets[t] == R.NEEDS_YOU]
    working = [t for t in candidates if buckets[t] == R.ACTIVE]
    return where, params, phases, waiting, working


# Every kind the palette lists, newest first; closed work sinks to the end.
_KIND_SQL = {
    "feature": ("SELECT f.id FROM features f ORDER BY "
                "(SELECT MAX(e.id) FROM feature_events e WHERE e.feature_id = f.id) DESC, "
                "f.id DESC"),
    "task": ("SELECT id FROM agent_tasks WHERE status <> 'archived' "
             "ORDER BY status = 'done', updated_at DESC, id DESC"),
    "defect": ("SELECT id FROM defects "
               "ORDER BY status <> 'open', opened_at DESC, id DESC"),
}
KIND_LABEL = {"feature": "Features", "task": "Tasks", "defect": "Defects",
              "session": "Sessions"}
DESK_LABEL = {"needs_me": "Needs me", "active_now": "Active now",
              "active_features": "Active features"}
LIST_KEYS = (*KIND_LABEL, *DESK_LABEL)


def _kind_entries(conn, kind: str) -> list:
    if kind == "session":
        where, params = _eligible()
        return _session_ids(conn, where, params)
    return [(kind, r[0]) for r in conn.execute(_KIND_SQL[kind])]


def _desk_entries(conn, key: str) -> tuple[list, dict]:
    _where, _params, phases, waiting, working = _desk(conn)
    if key == "needs_me":
        return _needs_me(conn, waiting), phases
    if key == "active_now":
        return _active_now(conn, working, set(waiting)), phases
    return _active_features(conn), phases


def listing(conn, key: str, offset: int, limit: int) -> dict | None:
    """One page of every entry behind a Home group or a kind — the paged
    source a "Show all" or an empty-query kind pick reads. None for an
    unknown key."""
    if key in KIND_LABEL:
        return {"key": key, **_page(conn, KIND_LABEL[key], _kind_entries(conn, key),
                                    offset, limit)}
    if key in DESK_LABEL:
        entries, phases = _desk_entries(conn, key)
        return {"key": key, **_page(conn, DESK_LABEL[key], entries, offset, limit, phases)}
    return None


def kind_totals(conn) -> dict:
    """`{kind: how many entries its list holds}` — the empty query's rail counts."""
    where, params = _eligible()
    totals = {k: conn.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
              for k, sql in _KIND_SQL.items()}
    totals["session"] = conn.execute(
        f"SELECT COUNT(*) FROM sessions WHERE {where}", params).fetchone()[0]
    return totals


def home(conn) -> dict:
    where, params, phases, waiting, working = _desk(conn)
    shown = set(waiting) | set(working)
    sessions = _page(conn, "Sessions", _session_ids(conn, where, params, shown),
                     0, SESSIONS_CAP)
    return {
        "needs_me": _page(conn, DESK_LABEL["needs_me"], _needs_me(conn, waiting),
                          0, NEEDS_ME_CAP, phases),
        "active_now": _page(conn, DESK_LABEL["active_now"],
                            _active_now(conn, working, set(waiting)), 0, ACTIVE_CAP, phases),
        "active_features": _page(conn, DESK_LABEL["active_features"],
                                 _active_features(conn), 0, FEATURES_CAP),
        "sessions": sessions,
        "totals": kind_totals(conn),
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
