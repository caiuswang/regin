# F171 无Feature的Task任务展示 — show tasks that have no feature

## Intent

> 有些Task比较简单，或者找不到关联的Feature，这个时候在Task的有些分组里，压根不会显示这些Task。在Overview界面也看不到这些Task。修复这些Task的展示。

A task filed under no feature ("loose task") must be reachable from the
Overview and from every Tasks reading the pipeline offers, under the scope it
belongs to. Today it is reachable only by already knowing to open the Tasks
tab under scope *All* (or *No project*).

Non-goals:
- No write-side change. How a task gets its `project_id` at creation
  (`lib/tasks/store.py::_created_project` — no feature + no explicit project
  ⇒ `NULL`) stays as is; inferring a project from the creating cwd/repo is a
  follow-up, not this feature.
- No "Group: Feature" grouping on the Tasks list. The Features tab is the
  feature-centric reading; this feature only makes loose tasks addressable.
- The Features tab keeps listing features only.

## Where loose tasks go missing today (measured on the live DB, 2026-10-22)

Population: 24 unarchived tasks with `feature_id IS NULL` — open: #519, #476
(idea, project 26), #80, #53 (idea, no project); 20 done (11 in project 26,
9 in none).

| Surface (scope) | Reads | Loose tasks shown | Defect |
|---|---|---|---|
| Tasks · list · Next step (All) | `next_step.groups` | yes (ideas preview 3: 53, 80, 519; 476 behind "more") | — |
| Tasks · board / graph (All) | `next_step.board`, `graph_lanes` (loose lane 0/4) | yes | — |
| Tasks · any reading (project 26) | `_by_project` | 519, 476 only; 53, 80 absent | expected — they are in *No project* |
| Overview (All) | `overview.overview` | **none** of the 4 open ones | **D1**: no panel lists idea tasks, and features-in-flight is the only route to open work — it needs a feature |
| Overview · Projects panel (All) | `projects.panel` pops the `None` bucket | no route to *No project* | **D2**: projectless loose tasks have no Overview entry under any scope |
| Tasks list filters | Filter menu checkbox "Feature steps" (unchecked → URL `unfeatured=1` → request `root_only=1`), chip "No feature only" | yes | — (already addressable; covered by `feature-steps.spec.js`) |

Run next / Running / Recently done already include loose tasks (every grain);
they only surface armed/running/done-this-week ones.

## Shape

```ts
// GET /api/pipeline/overview?project=<scope> — one new panel, one new row kind
type Overview = {
  // ...existing panels unchanged
  loose_tasks: {
    total: number              // open loose tasks under scope (idea|armed|queued|running|failed)
    rows: LooseTaskRow[]       // cap LOOSE_TASKS_CAP = 5
  }
  projects?: ProjectsPanel     // scope all / workspace only, as today
}
type LooseTaskRow = {
  id: number; title: string; status: TaskStatus; priority: Priority
  kind: string; project_id: number | null
}
type ProjectsPanel = {
  // ...existing fields
  no_project?: {               // present iff the None bucket has open tasks or open defects
    open_tasks: number; open_defects: number; scope: "none"
  }
}

// Tasks list query (useTaskQuery) — existing axis, URL key `unfeatured`,
// sent to the server as `root_only`
type TaskQueryState = { /* ... */ unfeatured: "" | "1" }
```

## Rules

Loose-tasks panel membership (`lib/pipeline/overview.py`):

| feature_id | status | in scope (`_task_scoped`) | in panel |
|---|---|---|---|
| NULL | idea / armed / queued / running / failed | yes | yes |
| NULL | done / archived | — | no |
| not NULL | any | — | no |
| NULL | open | no | no |

Order: status rank (running, failed, armed/queued, idea), then
`priority_rank` asc, then `id` asc. `total` counts the same WHERE as `rows`
without the cap.

Panel rendering (`frontend/src/views/OverviewView.vue`):

| total | renders |
|---|---|
| 0 | panel omitted (not an empty card) |
| 1..5 | rows; each row links `/tasks/<id>` |
| > 5 | 5 rows + "View all N" → Tasks tab `?unfeatured=1` carrying the current scope |

The panel is titled "No feature" and sits full-width below Run next. A URL
naming `unfeatured` suppresses the reader's stored narrowings (status,
priority, archived) on arrival, as `feature_id`/`assignee` do, so the list
opened by "View all N" holds N rows (`useTaskQuery.js::SUPPRESSES_STORED`);
Clear still clears it.

Projects panel `no_project` row:

| scope | None bucket open_tasks + open_defects | row |
|---|---|---|
| all | > 0 | shown last, label "No project", links scope `none` |
| all | 0 | absent |
| workspace / project / none | any | absent |

Tasks list "no feature" (`useTaskQuery.js`, `TaskFilterMenu.vue`, `utils/taskChips.js`):

| control | effect |
|---|---|
| filter menu checkbox "Feature steps" unchecked | sets URL `unfeatured=1`; requests carry `root_only=1` to `/api/tasks`, `/api/tasks?group=next_step`, board and graph |
| chip "No feature only" | shown while set; ✕ clears it |
| `feature_id` also set | `feature_id` wins (`_by_grain`, unchanged); the "No feature only" chip is hidden |

## Invariants

- I1: For every scope S, the set of open loose tasks under S equals
  `overview(S).loose_tasks.total` equals the `n` sum of the open next-step
  groups of `/api/tasks?group=next_step&root_only=1` under S.
- I2: Every open loose task under scope *All* is reachable from the Overview:
  in `loose_tasks.rows`, or behind its "View all" link.
- I3: No count is computed client-side; every number above comes from SQL
  (lesson 9eee2b0b).

## Acceptance

```gherkin
Feature: tasks with no feature are visible

  Scenario: Overview lists open loose tasks
    Given idea tasks #A (no feature, project P) and #B (no feature, no project)
    And idea task #C under feature F
    When GET /api/pipeline/overview?project=all
    Then loose_tasks.total = 2 and loose_tasks.rows ids = {A, B}
    And #C is not in loose_tasks.rows

  Scenario: Overview respects scope
    When GET /api/pipeline/overview?project=P
    Then loose_tasks.rows ids = {A}
    When GET /api/pipeline/overview?project=none
    Then loose_tasks.rows ids = {B}

  Scenario: settled loose tasks are not in the panel
    Given loose task #D is done and #E is archived
    Then neither appears in loose_tasks.rows nor in loose_tasks.total

  Scenario: panel cap and link
    Given 7 open loose tasks under scope all
    Then loose_tasks.total = 7 and len(rows) = 5
    And the Overview renders "View all 7" linking the Tasks tab with unfeatured=1
    And a stored status filter is not applied on that arrival

  Scenario: empty panel is omitted
    Given no open loose task under scope S
    Then the Overview under S renders no loose-tasks panel and no console error

  Scenario: No project row in the Projects panel
    Given #B is open with project NULL
    When GET /api/pipeline/overview?project=all
    Then projects.no_project.open_tasks >= 1
    And the Projects panel renders a "No project" row linking scope none
    Given no open task or defect has project NULL
    Then projects.no_project is absent

  Scenario: Tasks list filter "No feature"
    Given the Tasks tab with "Feature steps" unchecked
    Then the URL carries unfeatured=1 and the requests carry root_only=1
    And list, Next-step groups, board and graph show only tasks with no feature
    And the open next-step group counts sum to overview.loose_tasks.total under the same scope
    When the "No feature only" chip is cleared
    Then unfeatured is removed and the full population returns
```

Gates: `pytest -n auto --dist=loadfile tests/pipeline tests/tasks`, radon ≥ C
on touched `.py`, `vite build`, Playwright specs for the Overview and Tasks
routes plus `responsive.spec.js`.

