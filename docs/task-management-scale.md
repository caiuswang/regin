# Scaling the task surface

`/tasks` reads a table that grows without bound. This is the order the layers
go in, and the trigger for each — so nothing is built ahead of the data, and
nothing is discovered at 5 000 rows that should have been decided at 50.

The rule the whole design rests on: **a filter is a WHERE clause, never an
`Array.filter`.** A control whose result is computed over the page that happens
to be loaded answers a different question than the one it appears to ask, and
any count rendered beside it describes the page rather than the backlog.

## Shipped

| Layer | What | Where |
|---|---|---|
| Keyset pagination | `size=50`, hard cap 200; cursor over `(created_at DESC, id DESC)` — O(log n) at any depth and immune to rows arriving mid-browse | `lib/utils/pagination.py`, `useCursor` |
| Server-side filters | `q`, `tag`, `source`, `priority`, `project_id` (incl. `none` = backlog), `status`, `sort`, `include_archived` | `lib/tasks/query.py` |
| Project = repo | A task's project is a **registered repo** (`repos.id`), reusing the meaning `project_id` already has in `pattern_deployments` and `web/blueprints/skills.py`. Managing them stays at `/repos` | `lib/tasks/store.py::_resolved_project` |
| Server-side counts | `GET /api/tasks/counts` — group totals on status / priority / project. Each axis is blind to **its own** filter so a header reports what it *would* show; it honours every other filter | `query.counts` |
| Per-group paging | Each group owns its own keyset feed. A collapsed group costs nothing; an open one pages independently | `TaskGroup.vue` |
| Composite indexes | `(status, created_at, id)`, `(created_at, id)`, `(priority_rank, created_at, id)`, `(project_id)` | `db/schema.sql`, `0020` |
| Archived is separate | Excluded from the unfiltered feed; reached by `status=archived` or `include_archived=1` | `query.apply_filters` |
| Graph is a neighbourhood | `?root=&depth=&status=`; unfocused and over the caller-supplied `cap` returns `truncated` + `total_nodes` instead of an unreadable SVG | `lib/tasks/deps.py` |

## Not built — and what has to be true first

| Layer | Build it when | Notes |
|---|---|---|
| Infinite scroll | The "Load more" click is the top complaint | IntersectionObserver on the last row. Cheap; deliberately not done while the row counts are small enough that the explicit click is *clearer*. |
| Row virtualization | One group routinely holds **>500 rows in the DOM** — measure before building | Windowed list. Until then the per-group keyset already bounds the DOM to one page per open group, which is the cheaper half of the same win. |
| FTS5 search | `LIKE '%q%'` measurably slows — in SQLite that is roughly **50k rows** | An `agent_tasks_fts` virtual table + triggers. `LIKE` cannot use an index for a leading wildcard, so this is a step change, not a tuning knob. |
| Saved views | More than ~3 filter combinations are re-typed daily | The filter state is already a flat object (`useTaskFilters().params`); a saved view is a named row holding it. |
| Cycles / iterations | Planning moves off "whatever is armed" | Another nullable label on `agent_tasks`, same shape as `project_id`. |
| Detach tasks when a repo is unregistered | Repos are unregistered often enough to matter | `DELETE /api/repos/<name>` leaves `agent_tasks.project_id` dangling, so the chip disappears but the id stays — and re-adding the repo mints a *new* id, which does not re-file those tasks. |
| Searchable dependency picker | The backlog passes a few hundred tasks | `TaskDepsEditor` reads `/api/tasks/graph` for its candidate list, so it pulls every task. It should become a search-as-you-type over `/api/tasks?q=`. Until then it is deliberately **exempt from the graph node cap** — see below. |

## Three traps already hit — don't re-lay them

**A computed sort column breaks pagination.** `priority` holds words, so the
obvious sort is `ORDER BY CASE priority WHEN 'urgent' THEN 0 …`. That is wrong
twice: the expression cannot use an index, and `keyset_page_stmt` builds the
next cursor by reading each order column's value **back off the serialized
row** — a CASE has no key there, so page one works and page two 500s. The fix
is the denormalized `priority_rank` integer, written beside `priority` by
`lib/tasks/store.py::_set_priority` and serialized so the cursor can find it.
Any future sort axis must be a real column for the same reason.

**A drawing limit is not a data limit.** The graph caps at
`GRAPH_NODE_CAP` (150) nodes because a layered SVG past that is unreadable —
but `TaskDepsEditor` reads the *same* endpoint for its dependency candidate
list. Capping by default would silently offer no dependencies at all on exactly
the backlog where they matter. So `cap` is a query parameter a caller that is
**drawing** opts into (`TaskGraphPanel` passes `DRAW_NODE_CAP`); the editor
passes none and is never truncated.

**A group header counted from loaded rows lies.** It reports the size of the
page, and a collapsed group reports zero. Group headers come from
`/api/tasks/counts`, never from `items.length`.

## Vocabulary

A task's **project** is a registered repo. regin already had this concept and
`project_id` already meant a repo id — in `pattern_deployments`, in
`web/blueprints/skills.py`, and in the skills UI, which renders
`Project = <repo>/…`. A second `projects` table would have been a third name
for the same thing, so the task surface reads `/api/repos` and creating or
retiring one stays at `/repos`.

There is no foreign key, exactly as `pattern_deployments.project_id` has none.
The writer checks instead (`lib/tasks/store.py::_resolved_project`), so an
unregistered id is refused rather than stored as a chip nothing can resolve.

## Identifiers

A task is addressed as `#<id>` — the integer primary key — built in exactly one
place, `frontend/src/utils/taskMeta.js::taskRef`. It is not a Linear-style
`ENG-42` because the integer is what `regin task add`, the CLI's
`task finish <id>`, the notification payloads and every existing deep link
already carry; a per-project sequence would have to renumber all of them.
Routing every call site through `taskRef` is what keeps that a one-function
change if the trade ever flips.

A top-level task additionally carries a *feature* key, `regin:<id>`, which is a
different address for a different question — `#7` is the task, `regin:7` is the
feature it is, and the second is what a ledger, a defect and a branch name are
read under.
