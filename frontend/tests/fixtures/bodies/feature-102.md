# Task / Feature / Defect: one model, the feature page as home

## Intent
There are three concepts, and they overlap. Today's working loop is: a feature holds the raw request → a task to "enrich the feature" → a second task to "implement it" → a defect for each bug → a fix task for each defect. Every step is a hand-typed task title that states a **phase**, not the work ("Enrich the feature", "Implement feature", "Enrich the spec and fix it."). As a result the task list is long and says nothing, and the one fact that tells tasks apart (which phase this is) exists only as prose.

The fix is to give each concept one job. A **feature** is the unit of intent and the page the operator works from. A **task** is one step of a feature, with a *typed* kind, so the system writes its title. A **defect** is a finding against a feature, and it owns at most one fix task. Repetition is carried by **runs of the same task**, never by new tasks.

## Where things stand
| Piece | Today | Gap |
|---|---|---|
| `agent_tasks` | `title`, `body`, `feature_id`. No column says what the step is | Phase lives in free text. Of 106 web tasks under a feature: 14 titled spec-only, 10 spec+build, 33 fix, 5 build, 44 other. 47 of them have no body |
| `events.on_run_start` (`lib/features/events.py`, called from `lib/tasks/runs.py`) | Any run of a task under the feature appends `build_started` (or `restarted` after a ship) | A spec-only run marks the feature *building*. Feature #102's own log is `specced` → `build_started` from run 174, an "Enrich the feature" run |
| `FeatureTasks.vue` quick-add | One free-text title field | The operator has to type the phase every time, and each click makes a new row |
| `fix_items.create_for` (feature #101) | Idempotent fix task per defect: `source="defect"`, `external_id="defect:<id>"`, title = symptom | Already the target shape. Spec and build lack the same shape |
| `/tasks` "Show runs" toggle (`useTaskQuery.js` `runs` → `root_only`) | Hides tasks under a feature by default. The label calls them "runs" | "Run" already names `agent_task_runs`. The toggle shows *feature steps*, not runs |
| `TaskRow.vue` | Renders only the task's own title | A feature step shows "Enrich the feature" with no feature name |
| launch prompt (`lib/prompts/surfaces/tasks.py`) | Same `<feature>` block for every task | "Write the spec and wait for approval, do not build" has to be typed into titles (tasks 193, 198, 200) |

## Shape
```
TaskKind = "spec" | "build" | "fix" | "task"        -- extensible category, not a boolean
agent_tasks.kind  TEXT NOT NULL DEFAULT 'task'      -- alembic revision + db/schema.sql + SQLModel field

spec   deliverable = features.body; changes no code; ends at an operator approval
build  deliverable = code that makes the spec's acceptance items green
fix    deliverable = one defect closed; created only through fix_items.create_for
task   free-form: an unfeatured task, or any legacy row

Step created from a feature verb (idempotent, like a defect's fix task):
  spec:  source="feature" external_id="feature:<fid>:spec"   title="Spec · [F<fid> <feature.title>](feature:<fid>)"
  build: source="feature" external_id="feature:<fid>:build"  title="Build · [F<fid> <feature.title>](feature:<fid>)"
  fix:   (feature #101 path, unchanged apart from kind="fix")
  body = the operator's optional instruction text, or NULL
Slice-table tasks from `task transcribe` get kind="build" and keep their (feature_id,"S<n>") keys.
```

Two orthogonal axes, never folded into one column:
```
kind   (what the step is):     spec | build | fix | task        -- set at create, editable
status (where its runs are):   idea → running → done | failed   -- unchanged, owned by runs.py
```

Feature lifecycle on a run starting (replaces the run-start row of the *Feature pipeline* table in ARCHITECTURE.md):
| task.kind | stage=specced | building | shipped / escaped | abandoned |
|---|---|---|---|---|
| spec | nothing | nothing | nothing | nothing |
| build, fix, task | build_started | nothing | restarted | build_started |

Per-feature cardinality, reached by re-running rather than re-creating:
| Step | Rows per feature | How a second attempt happens |
|---|---|---|
| spec | ≤ 1 (verb path) | Run the same task again |
| build | ≤ 1 (verb path) + N slice tasks | Run the same task again; `restarted` counts the round |
| fix | ≤ 1 per defect | Run the same task again |

## UI rules
| Surface | Rule |
|---|---|
| Feature page, `FeatureTasks` | Two verbs replace the title field: **Write spec** and **Build**. Each opens the existing step, or creates it on first use, with an optional instruction box that becomes `body`. Rows list in the order spec → build → fix, each with a kind chip and status. A free-form "+ task" stays behind a secondary control, with a **Stage** select (Task / Spec / Build, default Task) that becomes its `kind`. It creates a new row with no verb key, so it is never taken for the verb's step |
| `/tasks` board | The toggle reads **Show feature steps** / **Hide feature steps** (same `runs` URL param, so bookmarks keep working). A feature-linked row renders `#<fid> <feature.title>` as its primary line, a kind chip, and the task title as a secondary line |
| Task detail | Header shows the kind chip and the feature's title, linked. When `body` is NULL the page says the step's deliverable (per kind) instead of an empty body |
| Launch prompt | New surface `task-run-spec`, rendered into the envelope only for `kind=spec`. It says the deliverable is the spec on the row via `feature spec <fid> --file -`, that no code changes, and that the run ends by finishing so the operator can approve. `build` and `fix` keep today's blocks |

## Invariants
- `kind` is only ever declared (by a verb, the "+ task" Stage select, `task add --kind`, `fix_items`, or `transcribe`). It is never inferred from a title. Legacy rows stay `task` with no backfill.
- A spec run never appends a `feature_events` row. Stage and rounds read exactly as if the run had not happened.
- The verb path creates at most one spec step and one build step per feature: a second create returns the same row (found by `external_id`).
- A defect still owns at most one fix task (feature #101 invariant, unchanged).
- The feature title and spec have one home, `features`. A step's title cites the feature as an `@` reference (`Spec · [F<fid> …](feature:<fid>)`), and every surface that draws a task title reads it as words — `taskTitle()` in the browser, `title_words()` for the ledger and inbox cards: the feature's current title when the row carries `feature_title`, else the stamped label. Readers that name the feature (the board's primary line, the task page header) resolve `features.title` live as `feature_title`. The spec is never copied into a task body.
- A task regin generates names a feature or defect with the `@` reference grammar (`[F<id> …](feature:<id>)`, `[D<id> …](defect:<id>)`), never by copying its text. A fix task's body is the two references plus its image lines; the run reads the live symptom, reported session, suspect paths and missing check from the defect's line in its `<references>` block, so a defect reworded or closed, or a feature renamed, after the task was cut never leaves the task stale.
- `kind` is refused on the machine-key capture surface (`/api/tasks/capture`). The editor route `POST /api/tasks` accepts `task | spec | build` (never `fix`), and it is editable through `PATCH /api/tasks/<id>`.
- A second verb POST returns the existing step unchanged: an instruction sent with it is not applied (the UI only POSTs when the step is not already listed).

## Acceptance
```gherkin
Scenario: a spec run does not start the build
  Given feature #F whose last event is specced
  And its spec step S (kind=spec)
  When a run of S starts
  Then feature #F's last event is still specced and rounds(F) = 1

Scenario: the build after a spec starts the lifecycle
  Given feature #F at specced whose spec step has finished
  When a run of its build step starts
  Then feature #F's last event is build_started, declared_by "run:<id>"

Scenario: the verb is idempotent
  Given feature #F with no steps
  When "Write spec" is POSTed twice
  Then exactly one agent_tasks row has external_id "feature:F:spec"
  And its title is "Spec · [F<F> <F.title>](feature:<F>)" and its kind is spec

Scenario: re-running rather than re-creating
  Given feature #F shipped, with build step B
  When the operator clicks Build on #F
  Then no new task is created, B is opened, and running B appends restarted

Scenario: the board names the feature
  Given a task titled "Enrich the feature" under feature #F
  When /tasks is shown with feature steps on
  Then its row shows "#F <F.title>" as the primary line and the task title as secondary

Scenario: the toggle names what it shows
  Then the /tasks toggle reads "Show feature steps", and ?runs=1 still turns it on

Scenario: the spec prompt block is kind-scoped
  Given a spec step and a build step under the same feature
  Then only the spec step's launch prompt contains the rendered task-run-spec block

Scenario: legacy rows are not guessed
  Given rows created before the migration
  Then every one of them has kind = task, whatever its title says

Scenario: fix tasks carry their kind
  When "Create fix task" is clicked on an attributed defect
  Then the created task has kind = fix
```

## Non-goals
- Merging tasks into features, or removing the task entity. Runs, dependencies, comments and arm parameters all hang off a task row.
- Auto-creating steps when a feature is declared, or auto-arming a skill per kind.
- Backfilling `kind` on existing rows from their titles.
- Changing defect lifecycle, escape blame or `Defect.round`.
- Hiding the task board: unfeatured tasks stay there as they are.

