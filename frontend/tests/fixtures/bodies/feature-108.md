# Feature 108 — One list chassis for the Pipeline tabs

## Intent
The four Pipeline tabs (Tasks, Features, Defects, Projects) each grew their own toolbar, count, pager, empty state and row shape. Moving between tabs means learning four UIs, and one axis (project) has two controls on Tasks. This feature makes the four lists share one chassis and one list endpoint contract. It keeps what differs for a good reason: the row content, the facets, and the tab-specific actions (task bulk ops, defect triage, feature coverage counters).

Non-goals: redesigning detail pages (`/tasks/:id`, `/features/:id`, `/defects/:id`, `/projects/:id`), the task Graph view, or the triage queue's keyboard flow. No generic column-configurable data grid.

## Options (B was chosen and is built; the design below is B)
| Option | What | Cost | Verdict |
|---|---|---|---|
| A. Cosmetic alignment | Keep 3 toolbars; align order, labels and CSS | S | Drift comes back: three components keep diverging |
| **B. Shared chassis + one envelope** | One `PipelineListToolbar`, one `ListStatus` (count/empty/loading/error/past-end), one `useListQuery` URL composable, one pager; the 4 endpoints return one envelope | M | **Recommended**: fixes the cause (4 implementations), and row renderers stay per-entity |
| C. Generic data-grid | Server-described columns, one generic table for all four | L | Overkill: task/defect rows are action-heavy text rows, not a grid |

## Current state → defect table (each row is what B fixes)
| # | Axis | Tasks | Features | Defects | Projects | Target |
|---|---|---|---|---|---|---|
| D1 | Toolbar location | in body | teleported into shell header | in body | none | in body, directly under the tab strip, on all four |
| D2 | Toolbar component | `TaskFilterBar` | `ListToolbar` | `DefectsToolbar` | — | one `PipelineListToolbar` |
| D3 | Search commit | Enter; `type=search` native clear | Enter | Search button + Esc reverts | — | Enter commits; Esc reverts to URL value; `type=text`; no Search button |
| D4 | Reset control | "Clear N", hidden at 0 | "Reset", disabled at 0 | "Reset", hidden at 0 | — | "Clear N", rendered and disabled at 0 |
| D5 | Sort | one select, dir baked into value (`created_desc`) | key select + arrow toggle | key select + "Newest/Oldest first" button | fixed order | key select + arrow toggle; URL `sort=key:dir` |
| D6 | Project control | shell select **and** `task-filter-project` (duplicate) | shell only | shell only | unscoped | shell only; per-tab project select removed |
| D7 | Result count | "N tasks" in toolbar + subtitle | "N of M match" line under the counters | only on the unattributed scope | in subtitle | one count slot in the toolbar (R4) |
| D8 | Pagination | keyset "Load more" | offset `PageControls` | offset `PageControls` | none | offset `PageControls` on all four; shown only when `total > size` |
| D9 | Loading / error / empty | `.empty-state` text | "Loading…", `.empty` dashed | "Loading…", `.empty` centered | `.empty-state` | one `ListStatus` component, 5 states (R6) |
| D10 | Second-level view | List/Graph underline `Tabs`, **not in URL** | — | scope `Tabs` (All/Unattributed/Triage), in URL | — | one segmented view switcher at the toolbar's left, always in URL (`view=`, `scope=`) |
| D11 | Row shape | 1-line rows | table | multi-line cards with inline action forms | table in Card | tables stay tables; list rows ≤ 2 lines at rest; defect actions behind a per-row disclosure |
| D12 | Keyboard | `c` new, `/` search, `x` select | none | triage only | none | `/` search on every tab; `c` on every tab with a create action |

## Shape

```ts
// Every Pipeline list endpoint: GET /api/tasks, /api/features, /api/defects, /api/pipeline/projects
type ListPage<T> = {
  items: T[]
  pagination: { page: number; size: number; total: number; has_next: boolean; has_prev: boolean }
  population: number                 // unfiltered count under the shell's project scope only
  applied: { sort: `${string}:${'asc'|'desc'}`; filters: Record<string, string> }  // what the server applied
  extras?: Record<string, unknown>   // tab-specific: features.coverage, tasks.counts, defects.scope
}

// URL is the only list state (existing rule, now one implementation)
type ListQuery = {
  q?: string; sort?: string; page?: string; size?: string
  view?: string                      // tasks: list|graph; defects keep `scope`
  project?: string                   // shell-owned; a tab reads, never writes
  [facet: string]: string | undefined
}

type ListStatusState = 'loading' | 'error' | 'empty' | 'no-match' | 'past-end' | 'rows'
```

Clear N counts `q` + the tab's facets (Tasks also counts `archived`/`runs`); it keeps `project`, the view axis (`view`, defects `scope`) and arrival keys (`feature_id`, `assignee`, `fix_item`). Sort is not counted.

Components: `PipelineListToolbar` (props: facets, sortOptions, views, count, total, population, noun, dirtyCount; emits patch/reset); `ListStatus` (props: state, noun, population, total, page, error; slot for the create CTA); `useListQuery(path, defaults, keptKeys)` generalises `useDefectsQuery`; `usePage` becomes the one fetcher.

## Endpoint changes
| Endpoint | Change |
|---|---|
| `GET /api/tasks` | Adds offset `page`/`size`; returns `ListPage`; `sort` accepts `key:dir` (`created`, `updated`, `priority`); keyset `cursor` removed from the list read; `counts` moves to `extras.counts` on every page. `GET /api/tasks/counts` also carries `population`, so grouped mode fills the count slot from that one read |
| `GET /api/features` | `extras.total` → top-level `population`; adds `applied` |
| `GET /api/defects` | Adds `population`; `scope`/`sort` echo → `applied` |
| `GET /api/pipeline/projects` | Adds `q`, `status`, `sort` (`name`, `progress`, `open_defects`, `activity`; default `activity:desc`), offset paging, and the envelope. `activity` = newest of task `updated_at`, defect `updated_at`, feature `created_at`; a project with none sorts last both ways. "No project" is last under every sort; `population` = every project row incl. "No project" |
| `GET /api/pipeline/counts` | `tasks` is the Tasks tab population (below), no longer every row |

## Rules
| When | Then |
|---|---|
| Any facet, search or sort changes | `router.replace`, `page` dropped, one request |
| Unknown facet value | 400 naming the field; `ListStatus` error state names it |
| Unknown sort key or old tasks form (`created_desc`) | server falls back to the default sort and echoes it in `applied.sort`; the toolbar renders `applied.sort`, not the requested one |
| Grouped tasks (`group≠none`) | groups built from `extras.counts`; each group pages with the same offset scheme ("Load more" appends `page+1`) |
| Tab has a create action | the primary button sits in the shell header's action slot (the only thing teleported there) and `c` opens it |
| Write on a row | reload in place: rows stay, dimmed while busy; page and scroll kept; shell counts invalidated |
| A kept-alive tab is not on screen | its list does not react to another tab's query; shortcuts (`/`, `c`) are unbound while deactivated |
| Grouped tasks and `/tasks/counts` fails | counts are dropped and the list falls back to the flat feed with the counts-error line, never stale headers |

## Populations (what `population` and the tab strip count)
| Tab | Population under `?project=` |
|---|---|
| Tasks | not archived AND `feature_id IS NULL` — the tab's default view (the UI always sends `root_only=1` unless "Show feature steps"). One predicate, `lib/tasks/query.py::population_stmt`, shared by the list, `/api/pipeline/counts` and the project rollup + panel open-work list |
| Features | every feature row in the scope |
| Defects | every defect whose feature is in the scope (`none` includes unattributed) |
| Projects | unscoped: every project row |

## Invariants
- I1: the toolbar count, the pager's `total`, and the tab-strip count under the same `?project=` with no narrowing are the same number.
- I2: every narrowing on screen is in the URL. Reloading the URL reproduces the same rows, controls and view (List/Graph included).
- I3: no list filters or sorts a loaded array client-side (Projects included).
- I4: exactly one control writes `?project=`, and it is the shell's.
- I5: an empty list always says which of {nothing exists, nothing matches, past the end} it is.

## R4 — count slot
| Narrowed? | Text |
|---|---|
| no | `M <noun>` |
| yes | `N of M <noun>` |
| counts unavailable | slot hidden; rows still render |
| widened past the population (`N > M`, e.g. Show archived) | `N <noun>` |

## R6 — ListStatus
| State | Condition | Shows |
|---|---|---|
| loading | first read, no rows yet | 3 skeleton rows (a reload keeps the rows, dimmed) |
| error | read failed | `role=alert` message + Retry button |
| empty | `population = 0` | noun-specific sentence + create CTA if the tab has one |
| no-match | `total = 0`, `population > 0` | "No <noun> match these N filters" + Clear |
| past-end | `total > 0`, `items = []` | "Page p is past the end of T" + "First page" link |

## Acceptance
```gherkin
Scenario: One toolbar, same place, on every tab
  Given each of /tasks, /features, /defects, /projects
  Then a [data-testid=pipeline-list-toolbar] is rendered below [data-testid=pipeline-tabs]
  And #pipeline-actions contains no search input

Scenario: Tasks has one project control
  Given /tasks
  Then [data-testid=task-filter-project] does not exist
  And changing [data-testid=pipeline-project] changes the project_id sent to GET /api/tasks

Scenario: Search commit and revert are the same on every tab
  Given a tab's search box holding the URL value "foo"
  When I type "bar" and press Escape
  Then the box reads "foo" and no request was made
  When I type "bar" and press Enter
  Then the URL has q=bar, no page param, and history length is unchanged

Scenario: Sort is key:dir everywhere
  Given /tasks?sort=created_desc
  Then the toolbar shows the server default sort and GET /api/tasks returned applied.sort = the default
  When I toggle direction
  Then the URL sort ends with ":asc" or ":desc"

Scenario Outline: Count agrees with the tab strip
  Given <tab> with no narrowing under ?project=<p>
  Then the toolbar count equals [data-testid=pipeline-count-<key>]
  Examples: tasks, features, defects × p ∈ {all, none, a repo id}

Scenario: Clear N is always present
  Given a tab with no narrowing
  Then [data-testid=list-clear] is visible and disabled
  Given two facets set
  Then it reads "Clear 2" and is enabled

Scenario: Empty states are distinguished
  Given a search that matches nothing on /features
  Then ListStatus shows no-match with a Clear button
  Given /defects?page=99
  Then ListStatus shows past-end with a First page link

Scenario: Task view survives reload
  Given /tasks, when I switch to Graph
  Then the URL has view=graph, and reloading shows Graph

Scenario: Tasks paginate by offset
  Given more than one page of tasks, flat
  Then PageControls is shown and ?page=1 renders rows 51–100

Scenario: Projects narrow server-side
  Given /projects?q=reg
  Then GET /api/pipeline/projects was called with q=reg and every row name contains "reg"

Scenario: Envelope contract
  Given each list endpoint
  Then the JSON has items, pagination.{page,size,total,has_next,has_prev}, population, applied.sort
```

Gates: pytest for `web/blueprints/{tasks,features,defects,pipeline}.py` and their `lib/` query modules; `vite build`; Playwright `pipeline-*.spec.js`, `feature-index`, `defects-ledger`, `defects-triage`, `tasks-linear`, `tasks-and-keys`, `responsive`. Existing specs that assert the old toolbar test ids or the Features teleport get updated to the new contract, not kept alongside it.

