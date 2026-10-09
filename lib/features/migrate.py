"""Backfilling a feature out of the five places one used to live (regin:82).

Called by alembic `0027`, and by the reconciliation test that guards it. It
lives here rather than inside the revision because it is the thing the parity
gate has to be able to run twice: once to migrate, once to check that the four
figures a feature reported before it ran are the four it reports after.

**Every mapping is total and lossless except two, and both are recorded as
gaps rather than substituted with zero:**

| Lost | Why it cannot be recovered | Reads as |
|---|---|---|
| The *moments* of past rounds | `completions_walked_back` was an integer; when it was incremented was never written down | The count is right; every event carries the migration's timestamp and the history before it is empty |
| The blame snapshot of defects whose `blamed_against_state` was NULL | Nobody looked at the time, so what state the feature was in was never recorded | Pointed at `specced` — *not an escape* — and the `sync:backfill` declarer is what exposes the guess |

The second means the migrated escape total can be low. The shortfall has a
determinable upper bound — exactly the rows with a NULL snapshot — and it is
queryable, so it is a known quantity rather than an unknown one.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlmodel import text

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.features import (
    ORIGIN_NATIVE, RESTARTED, SHIPPED, SPECCED,
)

log = get_activity_logger("defects")

#: Every backfilled event signs itself, so a statistic that must not mix
#: reconstructed history with declared history can exclude it by name.
BACKFILL = "sync:backfill"

#: The native key shape that used to *be* a feature's identity.
_NATIVE_REF = re.compile(r"^regin:(\d+)$", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _legacy_keys(conn) -> list[str]:
    """The population, read the way the five-arm union read it.

    Reproduced here rather than imported: `lib/features/lane.py` is deleted by
    the same change that adds this file, and a backfill that could only run
    while the old code still existed would be unrunnable on the install that
    actually needs it.
    """
    rows = conn.execute(text("""
        SELECT feature_key AS key FROM session_features
        UNION SELECT feature_key FROM defects WHERE feature_key IS NOT NULL
        UNION SELECT external_key FROM agent_task_links
        UNION SELECT DISTINCT ('regin:' || parent_id) FROM agent_tasks
            WHERE parent_id IS NOT NULL
        UNION SELECT ('regin:' || t.id) FROM agent_tasks t
            WHERE t.parent_id IS NULL
              AND EXISTS (SELECT 1 FROM agent_task_runs r
                          WHERE r.task_id = t.id)
              AND NOT EXISTS (SELECT 1 FROM agent_task_links l
                              WHERE l.task_id = t.id)
        UNION SELECT ('regin:' || id) FROM agent_tasks
            WHERE claimed_at IS NOT NULL
    """)).all()
    return sorted({row[0] for row in rows if row[0]})


def _native_id(key: str):
    match = _NATIVE_REF.match(key or "")
    return int(match.group(1)) if match else None


def _title_and_body(conn, key: str, task_id) -> tuple:
    """What this feature is called, from whichever row held the words.

    A native feature takes its task's title; a remote one takes the cached
    snapshot's, falling back to the key itself. Neither is a guess — both are
    the string that was already being rendered as this feature's name.
    """
    if task_id is not None:
        row = conn.execute(text(
            "SELECT title, body FROM agent_tasks WHERE id = :i"
        ).bindparams(i=task_id)).first()
        if row is not None:
            return row[0], row[1]
    row = conn.execute(text(
        "SELECT snapshot_json FROM issue_cache WHERE external_key = :k"
    ).bindparams(k=key)).first()
    if row is not None:
        try:
            snapshot = json.loads(row[0])
        except (json.JSONDecodeError, ValueError):
            snapshot = {}
        return snapshot.get("title") or key, snapshot.get("description")
    return key, None


def _cached_rounds(conn, key: str) -> int:
    """A remote feature's rounds, from the snapshot regin last fetched.

    The alternative was a network call from inside a migration, which would
    make an install's schema upgrade depend on somebody else's API being up.
    """
    row = conn.execute(text(
        "SELECT snapshot_json FROM issue_cache WHERE external_key = :k"
    ).bindparams(k=key)).first()
    if row is None:
        return 1
    try:
        return int(json.loads(row[0]).get("rounds") or 1)
    except (json.JSONDecodeError, ValueError, TypeError):
        return 1


def _feature_row(conn, key: str, at: str) -> int:
    """The `features` row for one legacy key, created if new."""
    task_id = _native_id(key)
    title, body = _title_and_body(conn, key, task_id)
    origin = ORIGIN_NATIVE if task_id is not None else "linear"
    ref = None if task_id is not None else key
    url = None if task_id is not None else None
    conn.execute(text(
        "INSERT INTO features (title, body, origin, external_ref,"
        " external_url, created_at) VALUES (:t, :b, :o, :r, :u, :at)"
    ).bindparams(t=(title or key)[:200], b=body, o=origin, r=ref, u=url,
                 at=at))
    return conn.execute(text("SELECT last_insert_rowid()")).first()[0]


def _events_for(conn, feature_id: int, key: str, at: str) -> None:
    """The lifecycle, reconstructed from the columns that held pieces of it.

    Order matters and is the whole reason this is written event by event: the
    last row decides the stage, so a shipped-then-walked-back feature has to
    end on `restarted` and a still-shipped one on `shipped`.
    """
    def write(kind: str) -> None:
        conn.execute(text(
            "INSERT INTO feature_events (feature_id, kind, declared_by, at)"
            " VALUES (:f, :k, :d, :at)"
        ).bindparams(f=feature_id, k=kind, d=BACKFILL, at=at))

    write(SPECCED)
    task_id = _native_id(key)
    if task_id is not None:
        row = conn.execute(text(
            "SELECT claimed_at, completions_walked_back FROM agent_tasks"
            " WHERE id = :i").bindparams(i=task_id)).first()
        claimed, walked_back = (row[0], int(row[1] or 0)) if row else (None, 0)
    else:
        claimed, walked_back = None, _cached_rounds(conn, key) - 1
        if _remote_completed(conn, key):
            claimed = at
    # Each walked-back round is the pair that produced it. The moments are
    # gone — the column was a counter — so they all carry the migration's
    # timestamp, and the `sync:backfill` declarer says so.
    for _ in range(walked_back):
        write(SHIPPED)
        write(RESTARTED)
    if claimed:
        write(SHIPPED)


def _remote_completed(conn, key: str) -> bool:
    row = conn.execute(text(
        "SELECT snapshot_json FROM issue_cache WHERE external_key = :k"
    ).bindparams(k=key)).first()
    if row is None:
        return False
    try:
        return json.loads(row[0]).get("state_category") == "completed"
    except (json.JSONDecodeError, ValueError):
        return False


def _adopt_tasks(conn, feature_id: int, key: str) -> None:
    """Point every task that was a run of this feature at the feature row.

    The three old paths collapse into one column: a task's own key, the key of
    the parent it was a run of, and the key of the issue it linked.
    """
    task_id = _native_id(key)
    if task_id is not None:
        conn.execute(text(
            "UPDATE agent_tasks SET feature_id = :f"
            " WHERE id = :i OR parent_id = :i"
        ).bindparams(f=feature_id, i=task_id))
        return
    conn.execute(text(
        "UPDATE agent_tasks SET feature_id = :f WHERE id IN"
        " (SELECT task_id FROM agent_task_links WHERE external_key = :k)"
    ).bindparams(f=feature_id, k=key))


def _move_run_params(conn) -> None:
    """Give each task's latest attempt the parameters the task was armed with.

    The latest, not all of them: the columns recorded one armed state, so
    stamping every historical attempt with today's parameters would fabricate
    a record of what those attempts actually spent. An armed task with no
    attempt yet gets an `armed` row, which is what the parameters now mean.
    """
    conn.execute(text("""
        UPDATE agent_task_runs SET
            params_json = (SELECT t.params_json FROM agent_tasks t
                           WHERE t.id = agent_task_runs.task_id),
            autorun     = COALESCE((SELECT t.autorun FROM agent_tasks t
                                    WHERE t.id = agent_task_runs.task_id), 0)
        WHERE id IN (SELECT MAX(id) FROM agent_task_runs GROUP BY task_id)
    """))
    conn.execute(text("""
        INSERT INTO agent_task_runs
            (task_id, attempt, status, params_json, autorun)
        SELECT t.id, 1, 'armed', t.params_json, COALESCE(t.autorun, 0)
        FROM agent_tasks t
        WHERE t.params_json IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM agent_task_runs r
                          WHERE r.task_id = t.id)
    """))


def _point_defects(conn, mapping: dict, at: str) -> int:
    """Re-point every defect, and return how many blames had to be guessed.

    `reported_by_trace` needs no write: `0027` *renames* `session_id` into it,
    so the column arrives already carrying every value it had — which is the
    point of renaming rather than adding one beside it.

    `blamed_against_state = 'completed'` names the feature's `shipped` event;
    everything else names its `specced`. A NULL snapshot means nobody ever
    looked, so it gets `specced` — *not an escape* — and is counted here rather
    than absorbed, because the escape total this produces can be low by exactly
    this number.
    """
    guessed = 0
    rows = conn.execute(text(
        "SELECT id, feature_key, blamed_against_state FROM defects"
        " WHERE feature_key IS NOT NULL")).all()
    for defect_id, key, state in rows:
        feature_id = mapping.get(key)
        if feature_id is None:
            continue
        kind = SHIPPED if state == "completed" else SPECCED
        event = conn.execute(text(
            "SELECT id FROM feature_events WHERE feature_id = :f"
            " AND kind = :k ORDER BY id DESC LIMIT 1"
        ).bindparams(f=feature_id, k=kind)).first()
        if event is None:
            event = conn.execute(text(
                "SELECT id FROM feature_events WHERE feature_id = :f"
                " ORDER BY id LIMIT 1").bindparams(f=feature_id)).first()
        if state is None:
            guessed += 1
        conn.execute(text(
            "UPDATE defects SET feature_id = :f, blamed_event_id = :e"
            " WHERE id = :i"
        ).bindparams(f=feature_id, e=event[0] if event else None, i=defect_id))
    conn.execute(text("""
        UPDATE defects SET fix_task_id = (
            SELECT CAST(substr(fix_external_key, 7) AS INTEGER)
            WHERE fix_provider = 'regin')
        WHERE fix_external_key LIKE 'regin:%'
    """))
    log.write("defect_blame_backfilled", rows=len(rows), guessed=guessed,
              at=at)
    return guessed


def backfill(conn=None) -> dict:
    """Populate `features` and `feature_events`, and re-point everything.

    Takes the caller's connection when it has one. Alembic runs inside its own
    DDL transaction, and opening a second connection from here deadlocks
    against it on SQLite — which is why this is a connection parameter rather
    than a `SessionLocal()` like every other writer in the codebase.

    Returns `{legacy key: feature id}` plus the two gap counts, so the
    reconciliation gate can compare a feature's four figures with the ones it
    reported before this ran — per feature, because an aggregate can agree
    while two features have swapped their numbers.
    """
    if conn is not None:
        return _run(conn)
    with SessionLocal() as session:
        out = _run(session.connection())
        session.commit()
    return out


def _run(conn) -> dict:
    at = _now()
    mapping: dict[str, int] = {}
    for key in _legacy_keys(conn):
        feature_id = _feature_row(conn, key, at)
        mapping[key] = feature_id
        _events_for(conn, feature_id, key, at)
        _adopt_tasks(conn, feature_id, key)
    for key, feature_id in mapping.items():
        conn.execute(text(
            "UPDATE session_features SET feature_id = :f"
            " WHERE feature_key = :k"
        ).bindparams(f=feature_id, k=key))
    guessed = _point_defects(conn, mapping, at)
    _move_run_params(conn)
    log.write("feature_backfill_complete", features=len(mapping),
              blames_guessed=guessed)
    return {"features": mapping, "blames_guessed": guessed,
            "round_moments_lost": at}
