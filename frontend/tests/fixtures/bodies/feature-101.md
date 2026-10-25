# Better defect fix operation

## Intent
Today, reporting a defect and then fixing it takes two manual steps: create a task by hand, then use "Fix task" on the defect and type the task id to link them. Instead there should be **one button on the defect** that creates the fix task and links it. The words that task gives the agent must be an **editable prompt surface**, managed like every other prompt in regin, not a string hard-coded in Python.

## Where things stand (the starting point)
| Piece | Today | Gap |
|---|---|---|
| `lib/defects/fix_items.create_for` | Creates a task (title = symptom, body = `back_pointer()`) and sets `fix_task_id` | Body is hard-coded Python, so it can't be edited on /prompts |
| `POST /api/defects/<id>/fix-item {create:true}` + `regin defects fix-item --create` | Exist | — |
| `DefectActions.vue` "Create fix task" | Offered **only** right after a Close on this page | An open defect only gets "Fix task", which asks you to type an existing task id |
| `capture_task(... external_id=None)` | Every call adds a new row | A double-click or a second tab makes two tasks for one defect |
| defect screenshots (`defect_images`) | Stay on the defect | The fix task's agent never sees them |
| task body = `back_pointer()` | Launch prompt's `{{body}}` | The agent was never given the symptom itself |

## Shape
```
PromptSurface "defect-fix-task"   area="defects"   applies_to=("external-agent",)
  variables:
    defect_id        int      always
    symptom          str      always (verbatim)
    feature_named    str      always ("#<id> (<title>)")
    reported_session str      "" when reported_by_trace is NULL
    suspect_paths    str      "" when none
    missing_check    str      "" when not yet named (an open defect)
    round            int      always
    details          str      pre-rendered lines, only those that apply (session / paths / check)
Task created from a defect:
  title       = symptom[:200]          (data, not a prompt: stays in code)
  body        = render_surface("defect-fix-task", ctx)
  source      = "defect"
  external_id = "defect:<defect_id>"   (the stable id that makes create idempotent)
  feature_id  = defect.feature_id
  status      = idea                   (created, not launched)
```

## Button rules (DefectActions, rendered by DefectRow → the Defects list, defect detail and feature Defects tab)
| defect.feature_id | defect.fix_task_id | canWrite | Shown |
|---|---|---|---|
| any | any | false | nothing (as today) |
| NULL | NULL | true | "Create fix task" **disabled**, plus the note "Attribute it to a feature to create its fix task." |
| set | NULL | true | **"Create fix task"**: one click, no form |
| set | set | true | link `task #N` + "Re-point fix task"; no create button |

When the defect has no fix task, the pointer button reads "Link existing task". The after-Close offer stays, calls the same endpoint, and is disabled on an unattributed defect.


## Where the fix task shows up, and editing / merging defects
The feature page's Tasks card lists the feature's tasks in **creation order, oldest first** (`GET /api/tasks?feature_id=F&sort=created:asc&size=200`), with no regrouping by kind — so a newly created fix task is always the last row.

A defect's symptom stays editable after it is filed, open or closed: `store.edit` ← `PATCH /api/defects/<id> {symptom}` ← `regin defects edit <id> --symptom` ← "Edit symptom" in the row menu (prefilled with the current wording).

Merging is N duplicates → one keeper, all or none: `store.merge_many(dups, into=K)` ← `POST /api/defects/<K>/absorb {duplicates:[…]}` ← `regin defects merge A B … --into K` ← "Absorb duplicates…" on the keeper's row. "Merge into…" (`POST /api/defects/<id>/merge {into}`, triage "Dup") is the one-duplicate case of the same verb.

| Merge condition | Outcome |
|---|---|
| a duplicate is the keeper, the list is empty, or an id is missing | refused, nothing written |
| keeper + duplicates point at >1 distinct fix task | refused, nothing written |
| keeper closed, any duplicate open | refused (reopen the keeper first), nothing written |
| otherwise | duplicates deleted; keeper keeps its symptom, round and status; takes the union of suspect paths, the one fix task if it had none, and every duplicate screenshot renumbered after its own |

## Invariants
- There is one place that writes a fix task's body: `fix_items.create_for` → `render_surface("defect-fix-task")`. The CLI, HTTP and UI paths all go through it.
- Each defect gets at most one fix task, ever: creating again returns the same task (found by `external_id`) and never adds a second row. After `fix-item --clear`, create relinks that same task.
- Create refuses (409) when the defect points at a task it did not create, because re-pointing is the operator's correction.
- Screenshots are copied onto the task at the same `idx`, once, on first create.
- Creating never changes `defects.status`, `triage_state` or `round`.
- A user edit on /prompts reaches the next task created from a defect. A broken edit falls back to the default body; it never blocks creation (`render_surface` contract).
- The default body names the defect id, the feature, and the symptom verbatim, followed by the `details` lines.
- Finishing never closes a defect (close needs the missing check, which only the agent can name). Instead, a run's `<finish>` block names the literal `regin defects close <id> --check '…'` command for every **open** defect whose `fix_task_id` is the task (surface `task-run-finish-order-defects`), and `POST /api/tasks/runs/<id>/finish` with `status=done` is refused 409 (`open_defects: [ids]`, before any report is posted) while one is still open. `status=failed` is never refused.
- A defect edit re-derives `symptom_key`; an edit onto another row's key on the same dedup axis is refused and names `merge`.
- A merge never cascade-deletes a duplicate's screenshots: they are re-pointed to the keeper before the duplicate row is deleted.

## Acceptance
```gherkin
Scenario: one click on an open, attributed defect
  Given open defect #D attributed to feature #F with no fix task
  When the operator clicks "Create fix task" on its row
  Then a task exists with title = D.symptom[:200], feature_id = F, source = "defect", status = idea
  And D.fix_task_id is that task's id
  And the row shows "task #N" linking to /tasks/N without a page reload

Scenario: unattributed defect
  Given open defect #D with feature_id NULL
  Then "Create fix task" is disabled and names `attribute` as the remedy
  And POST /api/defects/D/fix-item {create:true} returns 4xx and creates no task

Scenario: double submit is idempotent
  Given attributed defect #D with no fix task
  When create is POSTed twice
  Then exactly one agent_tasks row has external_id "defect:D"
  And both responses carry the same fix_task_id

Scenario: the prompt is a managed surface
  Given surface "defect-fix-task" appears on /prompts under area "defects" with its variables listed
  When the operator edits its body to include "REPRO FIRST" and creates a fix task
  Then the new task's body contains "REPRO FIRST" and the rendered symptom

Scenario: default body parity
  Given no user edit
  Then the created body contains "regin defect #D", the symptom, the feature, the reporting session, every suspect path, and the missing check when one is set

Scenario: open defect without a missing check
  Given open defect #D with missing_check NULL
  When a fix task is created
  Then the body has no "missing check:" line and no leftover {{placeholder}}

Scenario: create does not undo a re-point
  Given defect #D whose fix task was re-pointed to task #T that was not created from it
  When create is POSTed
  Then the response is 409 naming `re-point`, and D.fix_task_id is still T

Scenario: screenshots ride the task
  Given defect #D with an image at idx 2
  When create is POSTed twice
  Then the task has exactly one image, at idx 2, with the same bytes

Scenario: a fix task cannot finish done over its open defect
  Given open defect #D whose fix_task_id is task #T, and a running attempt R of T
  Then R's launch prompt contains `defects close D --check` before its `task finish R` command
  When the agent runs `regin task finish R` with status done
  Then the server answers 409 naming the close command, T stays running, and no report is posted
  When the agent closes #D and finishes again
  Then T settles done

Scenario: a new fix task is the last row on the feature page
  Given feature #F with spec, build and free-form tasks
  When a fix task is created from one of its defects
  Then the feature page's Tasks card lists it last

Scenario: a closed defect can be re-edited
  Given closed defect #D
  When the operator saves new wording via "Edit symptom"
  Then D.symptom and D.symptom_key are the new wording and D stays closed

Scenario: several duplicates fold into one keeper
  Given defects #K, #A and #B, where #A has a screenshot
  When `regin defects merge A B --into K` runs
  Then #A and #B are gone, #K keeps its symptom, and #K holds #A's screenshot and both path sets
```

## Non-goals
- Launching the task automatically. Create leaves it at `idea`; running it is still the task page's action.
- Arming the task with a skill (e.g. `bug-fix`).
- A create action in the triage queue (`DefectTriageCard` / `useDefectTriage` VERBS).
- Bulk create across many defects (DefectActions says why there is no bulk control).


