"""Every feature key regin knows, in one page (CAI-248, CAI-255).

No distinct-feature-key query existed anywhere before this: every read was
keyed by a key the caller already held. Enumerating them is a **four-arm union**
— `session_features` ∪ `defects.feature_key` ∪ `agent_task_links.external_key` ∪
the tasks something names as parent — and the union rather than any one table is
a measured decision: a quarter of the population exists *only* because a defect
was blamed on it, and exactly one key reaches the list through a declaration.

Every arm is a row that exists because somebody declared something. The fourth
reads `agent_tasks.parent_id` rather than sweeping in every root task, for the
same reason: admitting a task because it has merely *run* would be a filter
deciding which keys are really features.

It lives here and not in a blueprint because `cost.coverage()`, its precedent,
lives in lib: the union is ledger reasoning, not transport.

**A row adds no query.** `ledger.build()` measured 726-1679 ms per key, so a
loop over the page would be 15-35 s; instead the whole page — figures, defect
counts, cached stage and title, the population total, the unattributed-defect
counter — comes back from one statement, and `coverage()` is computed once for
the page rather than once for a row.

The list is unfiltered. Any filter is an *inference* about which keys are
really features (constraint 1), and hiding a key whose sessions `coverage()`
counts as declared would make the index disagree with the coverage line
rendered beneath it. Search is the one narrowing, and it narrows the view
without changing what the counters describe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from sqlmodel import text

from lib.activity_log import get_activity_logger
from lib.defects import stage as stage_module
from lib.defects import store as defect_store
from lib.features import cost as cost_module
from lib.features import lane as lane_module
from lib.orm import SessionLocal
from lib.tasks.query import Q_MAX, like_pattern

log = get_activity_logger("defects")


@dataclass(frozen=True)
class FeatureRow:
    """One line of the index.

    `cost_usd` is None — never `0.0` — when nothing is declared or nothing was
    captured, because the two readings are different and a zero would answer
    a question nobody can ask of this row.
    """

    key: str
    title: Optional[str]
    stage: Optional[str]
    stage_synced_at: Optional[str]
    sessions: int
    cost_usd: Optional[float]
    no_usage_captured: int
    total_defects: int
    open_defects: int
    escapes: int
    last_activity: Optional[str]

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "stage": self.stage,
                "stage_synced_at": self.stage_synced_at,
                "sessions": self.sessions, "cost_usd": self.cost_usd,
                "gaps": {"no_usage_captured": self.no_usage_captured},
                "defects": {"total": self.total_defects,
                            "open": self.open_defects,
                            "escapes": self.escapes},
                "last_activity": self.last_activity}


@dataclass(frozen=True)
class Page:
    """The whole index read.

    A keyed object rather than a bare list: the header counters are part of
    the page's meaning, not decoration, and `total` describes the *population*
    even when `items` holds a search's matches — so the surface can say
    "3 of 25 features" instead of implying it has shown everything.
    """

    items: list
    total: int
    coverage: cost_module.Coverage
    unattributed_defects: int

    def to_dict(self) -> dict:
        return {"items": [row.to_dict() for row in self.items],
                "total": self.total,
                "coverage": self.coverage.to_dict(),
                "unattributed_defects": self.unattributed_defects}


# Both read from `lib/features/lane.py`. The union and the lane used to be
# spelled here as well as in three other modules, and the copies had already
# drifted — the lane fanned a run out into two sessions where `attribution`
# collapsed it, so this page counted 51 where the ledger counted 50.
_KEYS = lane_module.KEYS
_LANE = lane_module.LANE

# `is_primary` decides whether a shared session's money lands on this key or
# another, exactly as `sessions_for_feature` decides it: a declaration marking
# the session primary elsewhere wins, and everything else is this key's.
_ATTRIBUTED = """
attributed AS (
    SELECT u.key, u.trace_id,
           CASE WHEN EXISTS (
               SELECT 1 FROM session_features sf
               WHERE sf.trace_id = u.trace_id AND sf.is_primary = 1
                 AND sf.feature_key <> u.key) THEN 0 ELSE 1 END AS is_primary
    FROM (SELECT feature_key AS key, trace_id FROM session_features
          UNION SELECT key, trace_id FROM lane) u
)"""

_SESSION_FACTS = """
session_facts AS (
    SELECT a.key,
           COUNT(DISTINCT a.trace_id) AS sessions,
           SUM(CASE WHEN a.is_primary = 1 THEN s.cost_usd END) AS cost_usd,
           SUM(CASE WHEN s.cost_usd IS NULL THEN 1 ELSE 0 END) AS no_usage,
           MAX(s.started_at) AS last_session
    FROM attributed a
    LEFT JOIN sessions s ON s.trace_id = a.trace_id
    GROUP BY a.key
)"""

_DEFECT_FACTS = """
defect_facts AS (
    SELECT feature_key AS key,
           COUNT(*) AS total,
           SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
           SUM(CASE WHEN blamed_against_state = 'completed' THEN 1 ELSE 0 END)
               AS escapes,
           MAX(opened_at) AS last_defect
    FROM defects WHERE feature_key IS NOT NULL GROUP BY feature_key
)"""

_SELECT = """
SELECT k.key,
       c.snapshot_json,
       c.synced_at,
       COALESCE(sf.sessions, 0),
       sf.cost_usd,
       COALESCE(sf.no_usage, 0),
       COALESCE(df.total, 0),
       COALESCE(df.open_count, 0),
       COALESCE(df.escapes, 0),
       NULLIF(MAX(REPLACE(COALESCE(sf.last_session, ''), 'T', ' '),
                  REPLACE(COALESCE(df.last_defect, ''), 'T', ' ')), '')
           AS activity,
       (SELECT COUNT(*) FROM keys) AS population
FROM keys k
LEFT JOIN issue_cache c ON c.external_key = k.key
LEFT JOIN session_facts sf ON sf.key = k.key
LEFT JOIN defect_facts df ON df.key = k.key
"""

# The two operands are written in two formats — `sessions.started_at` is ISO
# with a `T`, `defects.opened_at` separates with a space — and `MAX` over
# strings compares them byte for byte, where `T` (0x54) beats ` ` (0x20). A
# defect opened at 23:00 therefore lost to a session started at 08:00 the same
# day. Normalized rather than parsed: both are already UTC and lexically
# ordered once the separator agrees, so no clock is read and no zone is
# assumed.
#
# Recency is the only total order that ranks nothing: every alternative sorts
# a gap against a figure. `NULL` cost on a key with no session would sort as
# the cheapest — `none declared` rendered as `$0.00` by an ORDER BY — and
# open-defects-first sorts a column measured empty across the whole
# population. The surface is a way in, not a leaderboard.
#
# `synced_at` is deliberately NOT one of the activity terms any more. A native
# key's cache row is rewritten on every child event while a Linear key's moves
# only when a human presses Refresh, so leaving it in would quietly reorder
# the page into "native features, then everything else". Stated cost: 5 keys
# today have a cached title but no sessions and no defects, and lose their
# only ordering signal to the `activity IS NULL` tail.
_ORDER = " ORDER BY activity IS NULL, activity DESC, k.key"


def _title_of(snapshot: Optional[dict]) -> Optional[str]:
    return (snapshot or {}).get("title") or None


def _stage_of(snapshot: Optional[dict], escapes: int) -> Optional[str]:
    """The stage name a cached snapshot implies, without a per-row query.

    `stage_module.stage_from_snapshot` would re-read the defect ledger for
    every key; the counts are already in the union, so only the naming rule is
    borrowed. None stays None: `spec` is a real stage and must not stand in
    for "nobody has looked".
    """
    if not snapshot:
        return None
    return stage_module.name_for(snapshot.get("state_category") or "",
                                 escapes)


def _row_from(record) -> FeatureRow:
    """One database record as the index renders it."""
    (key, snapshot_json, synced_at, sessions, cost_usd, no_usage,
     total, open_count, escapes, activity, _population) = record
    try:
        snapshot = json.loads(snapshot_json) if snapshot_json else None
    except json.JSONDecodeError:
        snapshot = None
    return FeatureRow(
        key=key, title=_title_of(snapshot),
        stage=_stage_of(snapshot, escapes),
        stage_synced_at=synced_at if snapshot else None,
        sessions=int(sessions or 0),
        cost_usd=None if cost_usd is None else float(cost_usd),
        no_usage_captured=int(no_usage or 0),
        total_defects=int(total or 0), open_defects=int(open_count or 0),
        escapes=int(escapes or 0), last_activity=activity)


def _query(needle: str) -> tuple:
    """The one statement, and its binds."""
    sql = (f"WITH {_KEYS},{_LANE},{_ATTRIBUTED},{_SESSION_FACTS},"
           f"{_DEFECT_FACTS}{_SELECT}")
    binds = {}
    if needle:
        sql += (" WHERE (k.key LIKE :q ESCAPE '\\'"
                " OR COALESCE(json_extract(c.snapshot_json, '$.title'), '')"
                " LIKE :q ESCAPE '\\')")
        binds["q"] = f"%{like_pattern(needle)}%"
    return sql + _ORDER, binds


def page(q: str = "") -> Page:
    """The index, narrowed by `q` over key and cached title."""
    needle = (q or "").strip()[:Q_MAX]
    sql, binds = _query(needle)
    with SessionLocal() as session:
        rows = session.exec(text(sql).bindparams(**binds)).all()

    items = [_row_from(record) for record in rows]
    population = rows[0][-1] if rows else _population_total()

    log.read("feature_index_read", count=len(items), q=needle)
    return Page(items=items, total=int(population or 0),
                coverage=cost_module.coverage(),
                # The same function `/defects` lists from, not a second
                # `feature_key IS NULL` count in this module's SQL: two
                # readers of one predicate is how a header and the page it
                # links to come to report different numbers.
                unattributed_defects=len(defect_store.unattributed()))


def _population_total() -> int:
    """The scalar the row loop would have carried, when a search matched
    nothing — the counter describes the population, so it survives a query
    that returned no rows."""
    with SessionLocal() as session:
        population = session.exec(text(
            f"WITH {_KEYS} SELECT COUNT(*) FROM keys")).first()
    return population[0] if population else 0
