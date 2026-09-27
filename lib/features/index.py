"""Every feature regin knows, in one page (regin:82).

**The population is `features`.** `FROM features`, no union and no filter —
which is the whole of what this module used to spend a four-arm union deciding.
Any filter would be an *inference* about which rows are really features, and
hiding one whose sessions `coverage()` counts as declared would make the index
disagree with the coverage line rendered beneath it. Search is the one
narrowing, and it narrows the view without changing what the counters describe.

**A row adds no query.** `ledger.build()` measured 726-1679 ms per feature, so
a loop over the page would be 15-35 s; instead the whole page — figures, defect
counts, stage and title, the population total, the unattributed-defect counter
— comes back from one statement, and `coverage()` is computed once for the page
rather than once for a row.

The stage arrives with it. It used to require a cached tracker snapshot, so a
native feature had no stage at all until something fetched it and 31 keys read
`null`; now it is the last event's kind plus a count of escapes, both of them
rows in this database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlmodel import text

from lib.activity_log import get_activity_logger
from lib.defects import store as defect_store
from lib.features import attribution as attribution_module
from lib.features import cost as cost_module
from lib.features import events as events_module
from lib.orm import SessionLocal
from lib.orm.models import tasks as task_model
from lib.tasks.query import (
    FilterRejected, Q_MAX, like_pattern, parse_int,
)
from lib.utils import pagination as offset

log = get_activity_logger("defects")


@dataclass(frozen=True)
class FeatureRow:
    """One line of the index.

    `cost_usd` is None — never `0.0` — when nothing is declared or nothing was
    captured, because the two readings are different and a zero would answer
    a question nobody can ask of this row.
    """

    id: int
    title: str
    origin: str
    external_ref: Optional[str]
    stage: str
    rounds: int
    sessions: int
    tasks: int
    cost_usd: Optional[float]
    no_usage_captured: int
    total_defects: int
    open_defects: int
    escapes: int
    last_activity: Optional[str]
    tasks_done: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "origin": self.origin,
                "external_ref": self.external_ref, "stage": self.stage,
                "blurb": events_module.blurb(self.stage), "rounds": self.rounds,
                "sessions": self.sessions, "tasks": self.tasks,
                "tasks_done": self.tasks_done,
                "cost_usd": self.cost_usd,
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
    #: The population under the page's project scope — never the match.
    total: int
    coverage: cost_module.Coverage
    unattributed_defects: int
    #: How many rows the current filters match. Distinct from `total`, which
    #: stays the population: "3 of 25 features" and "page 1 of 4 matches" are
    #: two questions, and a single number answering both answers neither.
    matched: int = 0
    page: int = 0
    size: int = offset.DEFAULT_PAGE_SIZE

    sort: str = ""
    filters: Optional[dict] = None
    stage_counts: Optional[dict] = None

    def to_dict(self) -> dict:
        body = offset.Page(
            items=[row.to_dict() for row in self.items], total=self.matched,
            page=self.page, size=self.size,
            has_next=(self.page + 1) * self.size < self.matched,
            has_prev=self.page > 0).to_envelope()
        return offset.list_page(
            body, population=self.total, sort=self.sort,
            filters=self.filters,
            extras={"coverage": self.coverage.to_dict(),
                    "attribution": attribution_header(self.coverage),
                    "stage_counts": self.stage_counts,
                    "unattributed_defects": self.unattributed_defects})


def attribution_header(coverage: cost_module.Coverage) -> dict:
    """The Features header's one line: what share of sessions, and of money,
    any feature has claimed. `sessions_pct` is None over no sessions — 0% of
    nothing is not a figure."""
    body = coverage.to_dict()
    body["sessions_pct"] = (
        round(100 * coverage.declared_sessions / coverage.all_sessions)
        if coverage.all_sessions else None)
    return body


#: The table the page's rows come from. Named because "the population is the
#: table" is a claim about the statement rather than about today's result: a
#: union that returned the same rows now would reintroduce the disagreement
#: the moment one of its arms drifted.
ROW_SOURCE = "features"

_LANE = attribution_module.LANE

# `is_primary` decides whether a shared session's money lands on this feature
# or another, exactly as `sessions_for_feature` decides it: a declaration
# marking the session primary elsewhere wins, and everything else is this
# feature's.
_ATTRIBUTED = """
attributed AS (
    SELECT u.feature_id, u.trace_id,
           CASE WHEN EXISTS (
               SELECT 1 FROM session_features sf
               WHERE sf.trace_id = u.trace_id AND sf.is_primary = 1
                 AND sf.feature_id <> u.feature_id) THEN 0 ELSE 1 END
               AS is_primary
    FROM (SELECT feature_id, trace_id FROM session_features
          UNION SELECT feature_id, trace_id FROM lane) u
)"""

_SESSION_FACTS = """
session_facts AS (
    SELECT a.feature_id,
           COUNT(DISTINCT a.trace_id) AS sessions,
           SUM(CASE WHEN a.is_primary = 1 THEN s.cost_usd END) AS cost_usd,
           SUM(CASE WHEN s.cost_usd IS NULL THEN 1 ELSE 0 END) AS no_usage,
           MAX(s.started_at) AS last_session
    FROM attributed a
    LEFT JOIN sessions s ON s.trace_id = a.trace_id
    GROUP BY a.feature_id
)"""

# An escape is a join against an immutable row, not a string comparison: the
# defect points at the event blame was assigned against, and `shipped` is the
# only kind that makes it one.
_DEFECT_FACTS = """
defect_facts AS (
    SELECT d.feature_id,
           COUNT(*) AS total,
           SUM(CASE WHEN d.status = 'open' THEN 1 ELSE 0 END) AS open_count,
           SUM(CASE WHEN e.kind = 'shipped' THEN 1 ELSE 0 END) AS escapes,
           MAX(d.opened_at) AS last_defect
    FROM defects d
    LEFT JOIN feature_events e ON e.id = d.blamed_event_id
    WHERE d.feature_id IS NOT NULL GROUP BY d.feature_id
)"""

# The tasks a feature declares as its steps. `agent_tasks.feature_id` is the
# edge the Tasks tab already filters on, so this counts exactly the population
# `/tasks?feature_id=` lists — a count naming a different set than the link
# beside it opens is the dead link this slice exists to close. Archived rows
# are out for the same reason: the feed hides them unless asked, so counting
# them here would promise rows the link does not show. Runs stay in, because
# `feature_id` wins over `root_only` in `lib/tasks/query.py::_by_grain`.
_TASK_FACTS = f"""
task_facts AS (
    SELECT t.feature_id, COUNT(*) AS tasks,
           SUM(CASE WHEN t.status = '{task_model.TASK_STATUS_DONE}'
               THEN 1 ELSE 0 END) AS tasks_done
    FROM agent_tasks t
    WHERE t.feature_id IS NOT NULL
      AND t.status <> '{task_model.TASK_STATUS_ARCHIVED}'
    GROUP BY t.feature_id
)"""

# The last event decides the stage, and the order is `id` — never `at`: two
# events written in one transaction share a timestamp, so a ship-then-restart
# read by clock depends on the timestamp's precision.
_LAST_EVENT = """
last_event AS (
    SELECT e.feature_id, e.kind
    FROM feature_events e
    WHERE e.id = (SELECT MAX(x.id) FROM feature_events x
                  WHERE x.feature_id = e.feature_id)
)"""

# The activity operands are written in two formats — `sessions.started_at` is
# ISO with a `T`, `defects.opened_at` separates with a space — and `MAX` over
# strings compares them byte for byte, where `T` (0x54) beats ` ` (0x20). A
# defect opened at 23:00 therefore lost to a session started at 08:00 the same
# day. Normalized rather than parsed: both are already UTC and lexically
# ordered once the separator agrees, so no clock is read and no zone assumed.
#
# `created_at` is the FALLBACK, not a third operand. A feature is a row with a
# birthday now, so a freshly specced one sorts by when it was written instead
# of falling into the `activity IS NULL` tail with no signal at all — but it
# must not outrank real work, and as a `MAX` operand it would outrank every
# session and defect a migrated feature has, because the row is younger than
# its own history.
_ROWS = f"""
rows AS (
    SELECT f.id AS id,
           f.title AS title,
           f.body AS body,
           f.origin AS origin,
           f.external_ref AS external_ref,
           f.project_id AS project_id,
           f.created_at AS created_at,
           1 + (SELECT COUNT(*) FROM feature_events e
                WHERE e.feature_id = f.id AND e.kind = 'restarted') AS rounds,
           COALESCE(sf.sessions, 0) AS sessions,
           COALESCE(tf.tasks, 0) AS tasks,
           COALESCE(tf.tasks_done, 0) AS tasks_done,
           sf.cost_usd AS cost_usd,
           COALESCE(sf.no_usage, 0) AS no_usage,
           COALESCE(df.total, 0) AS total_defects,
           COALESCE(df.open_count, 0) AS open_defects,
           COALESCE(df.escapes, 0) AS escapes,
           {events_module.stage_sql('le.kind', 'df.escapes')} AS stage,
           COALESCE(
               NULLIF(MAX(REPLACE(COALESCE(sf.last_session, ''), 'T', ' '),
                          REPLACE(COALESCE(df.last_defect, ''), 'T', ' ')), ''),
               f.created_at) AS activity
    FROM {ROW_SOURCE} f
    LEFT JOIN session_facts sf ON sf.feature_id = f.id
    LEFT JOIN defect_facts df ON df.feature_id = f.id
    LEFT JOIN task_facts tf ON tf.feature_id = f.id
    LEFT JOIN last_event le ON le.feature_id = f.id
)"""

# `matched` rides the page's own statement as a window function — evaluated
# over the filtered set before LIMIT — so the filtered total costs no second
# read while there are rows to carry it.
_OUTER = """
SELECT id, title, origin, external_ref, stage, rounds, sessions, tasks,
       tasks_done, cost_usd, no_usage, total_defects, open_defects, escapes, activity,
       COUNT(*) OVER () AS matched
FROM rows"""

_CTES = (f"WITH {_LANE},{_ATTRIBUTED},{_SESSION_FACTS},{_DEFECT_FACTS},"
         f"{_TASK_FACTS},{_LAST_EVENT},{_ROWS}")

#: Sort key → (the expression it orders by, the direction it means by default).
#: Every key is offered with both directions; none of them is the total order
#: on its own, which is what `_TIEBREAK` is for.
SORT_KEYS: dict[str, tuple[str, str]] = {
    "activity": ("activity", "DESC"),
    "rounds": ("rounds", "DESC"),
    "open_defects": ("open_defects", "DESC"),
    "escapes": ("escapes", "DESC"),
    "cost": ("cost_usd", "DESC"),
    "stage": (events_module.stage_rank_sql("stage"), "ASC"),
    "created": ("created_at", "DESC"),
}

DEFAULT_SORT_KEY = "created"

#: Keys whose column has a gap reading, sorted last in BOTH directions rather
#: than as a value. A NULL cost is `none declared`, not the cheapest — an
#: `ORDER BY` that ranks it as $0.00 sorts a question nobody asked — and a
#: feature nothing has touched is not the least recently touched.
_GAPPED = ("activity", "cost")

#: Appended to every order, so the ordering is total: without it two rows
#: sharing a key can land on two pages or on neither as the offset moves. It
#: runs in the key's direction because `created_at` has second resolution: a
#: fixed `id ASC` put the older of two same-second features first under
#: `created:desc`, the default.
_TIEBREAK = "id"


def parse_sort(raw: str) -> tuple[str, str]:
    """`key:dir` → the key and direction this page will use.

    An unknown key falls back to the default and an unknown direction to that
    key's own, where an unknown facet *value* is refused: a sort is a view of
    the same rows, so a link naming a column this tab dropped should still
    show the list, while a dropped filter shows a list that disagrees with the
    controls above it.
    """
    key, _, direction = (raw or "").strip().partition(":")
    key = key.strip().lower()
    if key not in SORT_KEYS:
        key = DEFAULT_SORT_KEY
    chosen = direction.strip().upper()
    return key, chosen if chosen in ("ASC", "DESC") else SORT_KEYS[key][1]


def _order_by(key: str, direction: str) -> str:
    column = SORT_KEYS[key][0]
    gaps_last = f"{column} IS NULL, " if key in _GAPPED else ""
    return f" ORDER BY {gaps_last}{column} {direction}, {_TIEBREAK} {direction}"


#: What `page()` reads when a caller names no filters.
NO_FILTERS: dict = {"q": "", "stage": "", "project_id": None,
                    "unassigned": False, "sort": ""}


def parse_filters(args) -> dict:
    """The index's query parameters off a request `args` mapping.

    A bad *value* is refused rather than dropped, exactly as the task feed
    refuses one: a silently ignored filter shows a list that does not match
    the controls above it. An absent one is simply "no constraint".
    """
    # `project_id=none` is a real scope, not an absent filter: a feature
    # declares its project, so "declared in no project" is a population you
    # have to be able to ask for.
    raw_project = (args.get("project_id") or "").strip()
    unassigned = raw_project == "none"
    stage = (args.get("stage") or "").strip()
    if stage and stage not in events_module.STAGES:
        raise FilterRejected(f"unknown stage: {stage}", "stage")
    return {
        "q": (args.get("q") or "").strip()[:Q_MAX],
        "stage": stage,
        "project_id": None if unassigned else parse_int(raw_project or None,
                                                        "project_id"),
        "unassigned": unassigned,
        "sort": (args.get("sort") or "").strip(),
    }


#: A feature whose last event is `abandoned` was taken down, and the Features
#: tab never lists one unless asked for that stage by name. One predicate over
#: the bare table, so the tab strip, the population and the stage counts all
#: leave out the same rows. `features.id` is spelled out because it is also
#: embedded in `lib.pipeline.counts`' ORM statement.
LIVE_SQL = (
    "COALESCE((SELECT e.kind FROM feature_events e"
    " WHERE e.feature_id = features.id ORDER BY e.id DESC LIMIT 1), '')"
    f" <> '{events_module.ABANDONED}'")

#: The stages a stage tab is drawn for, in lifecycle order. `abandoned` is
#: reachable by `stage=abandoned` and never counted into `all`.
LIVE_STAGES = (events_module.STAGE_SPEC, events_module.STAGE_BUILDING,
               events_module.STAGE_SHIPPED, events_module.STAGE_ESCAPED)


def _where(spec: dict) -> tuple[str, dict]:
    """The narrowing, as a clause over the `rows` CTE and its binds."""
    clauses: list[str] = []
    binds: dict = {}
    if spec["q"]:
        clauses.append("(title LIKE :q ESCAPE '\\'"
                       " OR COALESCE(external_ref, '') LIKE :q"
                       " ESCAPE '\\'"
                       " OR COALESCE(body, '') LIKE :q ESCAPE"
                       " '\\')")
        binds["q"] = like_pattern(spec["q"])
    if spec["stage"]:
        clauses.append("stage = :stage")
        binds["stage"] = spec["stage"]
    elif not spec.get("any_stage"):
        clauses.append(f"stage <> '{events_module.STAGE_ABANDONED}'")
    if spec["unassigned"]:
        clauses.append("project_id IS NULL")
    elif spec["project_id"] is not None:
        clauses.append("project_id = :project_id")
        binds["project_id"] = spec["project_id"]
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", binds


def query_sql(spec: dict) -> str:
    """The one statement this page runs."""
    where, _ = _where(spec)
    order = _order_by(*parse_sort(spec.get("sort", "")))
    return f"{_CTES}{_OUTER}{where}{order} LIMIT :limit OFFSET :offset"


def _row_from(record) -> FeatureRow:
    """One database record as the index renders it."""
    (feature_id, title, origin, external_ref, stage, rounds, sessions, tasks,
     tasks_done, cost_usd, no_usage, total, open_count, escapes, activity,
     _matched) = record
    return FeatureRow(
        id=feature_id, title=title, origin=origin, external_ref=external_ref,
        stage=stage,
        rounds=int(rounds or 1),
        sessions=int(sessions or 0),
        tasks=int(tasks or 0),
        tasks_done=int(tasks_done or 0),
        cost_usd=None if cost_usd is None else float(cost_usd),
        no_usage_captured=int(no_usage or 0),
        total_defects=int(total or 0), open_defects=int(open_count or 0),
        escapes=int(escapes or 0), last_activity=activity)


def _spec_from(q: str, filters) -> dict:
    """One narrowing, whatever the caller named it with."""
    spec = dict(NO_FILTERS)
    spec.update(filters or {})
    if q:
        spec["q"] = (q or "").strip()[:Q_MAX]
    return spec


def page(q: str = "", *, filters: Optional[dict] = None,
         page_index: int = 0, size: int = offset.DEFAULT_PAGE_SIZE) -> Page:
    """One offset page of the index, under `filters` and its sort.

    Offset rather than keyset, which is the split `lib/utils/pagination.py`
    already argues: this population grows by an operator act — link, declare,
    blame — not by ingest, so a page number stays meaningful between two
    reads, and "page 7" and a sortable column are both things a cursor cannot
    express.
    """
    spec = _spec_from(q, filters)
    page_index, size = offset.clamp_page(page_index), offset.clamp_size(size)
    _, binds = _where(spec)
    binds.update({"limit": size, "offset": page_index * size})
    with SessionLocal() as session:
        rows = session.exec(text(query_sql(spec)).bindparams(**binds)).all()

    items = [_row_from(record) for record in rows]
    matched = int(rows[0][-1]) if rows else _matched_total(spec)
    population = _population_total(spec)
    sort_key, sort_dir = parse_sort(spec.get("sort", ""))

    log.read("feature_index_read", count=len(items), q=spec["q"],
             page=page_index, size=size)
    return Page(items=items, total=int(population or 0),
                coverage=cost_module.coverage(),
                # The same function `/defects` lists from, not a second
                # `feature_id IS NULL` count in this module's SQL: two readers
                # of one predicate is how a header and the page it links to
                # come to report different numbers.
                unattributed_defects=len(defect_store.unattributed()),
                matched=matched, page=page_index, size=size,
                stage_counts=stage_counts(spec),
                sort=f"{sort_key}:{sort_dir.lower()}",
                filters={"q": spec["q"], "stage": spec["stage"],
                         "project_id": "none" if spec["unassigned"]
                         else spec["project_id"]})


def rows(filters: dict, *, size: int,
         page_index: int = 0) -> tuple[list[FeatureRow], int]:
    """One page of rows under `filters` and how many matched — the page's own
    statement without the header counters a panel does not draw."""
    spec = _spec_from("", filters)
    _, binds = _where(spec)
    size = offset.clamp_size(size)
    binds.update({"limit": size, "offset": page_index * size})
    with SessionLocal() as session:
        records = session.exec(text(query_sql(spec)).bindparams(**binds)).all()
    matched = int(records[0][-1]) if records else 0
    return [_row_from(record) for record in records], matched


def _matched_total(spec: dict) -> int:
    """The filtered count, when the page itself carried no row to hold it.

    `COUNT(*) OVER ()` rides the page's own statement while there are rows.
    An offset past the end has none, and still has to report the true total
    rather than 0 — a pager that reads "page 9 of 1" is how a user gets
    stranded with no way back.
    """
    where, binds = _where(spec)
    with SessionLocal() as session:
        matched = session.exec(text(
            f"{_CTES} SELECT COUNT(*) FROM rows{where}"
        ).bindparams(**binds)).first()
    return int(matched[0]) if matched else 0


def _population_total(spec: dict) -> int:
    """The unnarrowed count under the page's project scope only — the same
    predicate `lib.pipeline.counts` counts the Features tab with, so the
    toolbar and the tab strip cannot disagree."""
    scope = {**NO_FILTERS, "project_id": spec["project_id"],
             "unassigned": spec["unassigned"], "any_stage": True}
    where, binds = _where(scope)
    where = f"{where} AND {LIVE_SQL}" if where else f" WHERE {LIVE_SQL}"
    with SessionLocal() as session:
        population = session.exec(text(
            f"SELECT COUNT(*) FROM {ROW_SOURCE}{where}"
        ).bindparams(**binds)).first()
    return int(population[0]) if population else 0


def stage_counts(spec: dict) -> dict[str, int]:
    """One count per stage tab under every filter but the stage itself.

    Stage-blind for the reason the task status chips are: a tab reports how
    many rows it WOULD show. `all` is the sum of the four live stages, never
    a separate count, so the tabs cannot disagree with their own total.
    """
    where, binds = _where({**spec, "stage": "", "any_stage": True})
    with SessionLocal() as session:
        records = session.exec(text(
            f"{_CTES} SELECT stage, COUNT(*) FROM rows{where} GROUP BY stage"
        ).bindparams(**binds)).all()
    found = {stage_name: int(n) for stage_name, n in records}
    out = {stage_name: found.get(stage_name, 0) for stage_name in LIVE_STAGES}
    return {"all": sum(out.values()), **out}


def spent(project_id: Optional[int], unassigned: bool = False) -> Optional[float]:
    """What the features under one scope cost, summed the way each row's
    `cost_usd` is — `None` when no feature there has a declared cost."""
    spec = {**NO_FILTERS, "project_id": project_id, "unassigned": unassigned,
            "any_stage": True}
    where, binds = _where(spec)
    with SessionLocal() as session:
        total = session.exec(text(
            f"{_CTES} SELECT SUM(cost_usd) FROM rows{where}"
        ).bindparams(**binds)).first()
    return None if not total or total[0] is None else float(total[0])


def stage_counts_by_project() -> dict[Optional[int], dict[str, int]]:
    """`{project_id: {stage: n}}` over every feature, from the page's own CTEs.

    The same `rows` the index lists from, grouped rather than paged: a project
    rollup that computed stage a second way would disagree with the stage
    chips the Features tab renders under the same scope.
    """
    with SessionLocal() as session:
        records = session.exec(text(
            f"{_CTES} SELECT project_id, stage, COUNT(*) FROM rows"
            " GROUP BY project_id, stage")).all()
    out: dict[Optional[int], dict[str, int]] = {}
    for project_id, stage_name, n in records:
        out.setdefault(project_id, {})[stage_name] = int(n)
    return out
