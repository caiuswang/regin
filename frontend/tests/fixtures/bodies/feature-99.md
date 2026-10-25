# Let project auto-carry when from a known endpoint

## Intent

When a create action starts from something that already has a project, the new row gets that
project without the operator re-picking it. The operator can see which project was carried and
can change it. The known endpoints are:

- a feature, for its tasks, its defects' fix tasks, and transcribed slices
- the Pipeline shell's project scope, for New feature and New task
- a task's project, for the working directory its run arms into

When nothing is known, the operator picks the project and nothing is guessed.

## Non-goals

- **Defects get no `project_id` column.** A defect's project is `feature.project_id`, derived
  through the join (`lib/defects/store.py` already filters this way). Carrying a copy would add a
  second writer of the same fact.
- **One run, one repo.** No `--add-dir` and no multi-directory runs. A feature that spans repos
  is modelled as tasks that each carry their own project.
- **No later re-derivation.** Carrying copies the project once, at creation. Changing a feature's
  project afterwards does not retarget tasks that already exist, because
  `features.project_id` is "declared, never inferred".
- **No server-side "current project".** The scope stays URL plus localStorage
  (`usePipelineScope.js`).
- **No CLI changes.** A `regin feature new --project` flag and a `regin task add --feature` flag
  are listed as a follow-up only.

## Types

```ts
type ProjectId = number            // repos.id; there is no FK
type Scope = 'all' | 'none' | `${ProjectId}`   // ?project= in the Pipeline shell

// POST /api/features: gains project_id, validated exactly as set_project validates it
//   (an int or null, and the repo must be registered; otherwise 400 with field="project_id")
type FeatureCreate =
  | { title: string; body?: string; project_id?: ProjectId | null }
  | { ref: string; provider?: string; project_id?: ProjectId | null }

// POST /api/tasks, capture_task(): the rule is whether the key is PRESENT
type TaskCreate = { title: string; feature_id?: number; project_id?: ProjectId | null; /*…*/ }
```

## Rule: a task's project at creation (server, `lib/tasks/store.py::capture_task`)

| `project_id` key in request | `feature_id` | stored `project_id` |
|---|---|---|
| present, a registered id | any | that id (it may differ from the feature's project, because features can span repos) |
| present, `null` | any | `NULL` (an explicit "No project" is honoured) |
| present, unregistered id | any | 400 `UnknownProject` (unchanged) |
| **absent** | set, and the feature has a project | **`feature.project_id`** |
| absent | set, and the feature has no project | `NULL` |
| absent | unset | `NULL` |

This one server rule covers every creator that passes a `feature_id`:
`FeatureTasks.vue` New task, `lib/defects/fix_items.py::create_for` (Create fix task), and
`lib/tasks/transcribe.py`. None of them has to send the project itself.

## Rule: what a creation form pre-selects (frontend)

| Form | Known endpoint | Project control starts at | Editable |
|---|---|---|---|
| Feature page, Tasks card, New task (`FeatureTasks.vue`) | the feature | the feature's project, or "No project" | yes, via a Project select in the inline form |
| Feature page, Report defect (`FeatureDefects.vue`) | the feature | shows the feature's project as read-only text | no (derived, see Non-goals) |
| Defect, Create fix task (`DefectActions.vue`) | the defect's feature | nothing is shown; the server rule carries it | no (server-authored task) |
| Pipeline Features tab, New feature (`NewFeatureDialog.vue`) | scope = repo id | that repo | yes (a new Project select, in both Declare and Adopt modes) |
| same | scope = `none` or `all` | "No project" | yes |
| Pipeline Tasks tab, New task (`TaskComposerDialog.vue`) | Tasks toolbar project = repo id | that repo | yes (the existing select) |
| same | toolbar project = `none`, `all`, or unset | "No project" | yes |

A pre-selection is a starting value, and the same editable select always shows it. An unknown
id (a dropped repo, or a scope naming a repo that is no longer listed) falls back to
"No project" and is never sent.

## Rule: an armed run's working directory (`TaskArmSheet.vue` via `seededArmDraft`)

Today the sheet re-uses the last armed task's `cwd` from localStorage whatever this task's
project is. That lets a task filed under repo B run in repo A. The seeding order below replaces
it, and the first match wins:

| # | Condition | `cwd` seeds to |
|---|---|---|
| 1 | the task already has armed params (a re-arm) | its own `params.cwd` (unchanged) |
| 2 | the task has a project whose repo is in `/api/repos` | that repo's `path` |
| 3 | the task has no project | the recalled cwd (`regin.tasks.arm.last`, unchanged) |
| 4 | otherwise | `''`, meaning the server working directory (unchanged) |

- **Seeding from the project is not a recall.** It does not raise the "carried over from another
  task" banner.
- **A cwd that disagrees with the task's project repo shows a note.** When the chosen `cwd` is
  neither that repo's `path` nor inside it, the sheet shows the non-blocking note
  "filed under «project» but will run in «cwd»". Arming is never refused over it.
- **The worktree anchors on the seeded cwd, not the regin checkout.** The anchor is
  `cwd or settings.project_root`, so the worktree fields need no change of their own.

## Invariants

1. A creation request that states `project_id`, including `null`, is stored as stated. The
   carry applies only to an absent key.
2. After creation, a task's `project_id` is never written as a side effect of its feature's
   project changing.
3. `defects` has no project column. The defect list's project filter and the feature page's
   defect project text both read `features.project_id`.
4. Every project pre-selection is visible in an editable control before submit, except the
   server-authored fix task.
5. A run has exactly one working directory.

## Acceptance

```gherkin
Feature: project auto-carry

  Scenario: task created under a feature inherits its project
    Given feature F with project_id = R1
    When POST /api/tasks {title: "step", feature_id: F}
    Then the task's project_id = R1

  Scenario: explicit null is honoured
    Given feature F with project_id = R1
    When POST /api/tasks {title: "step", feature_id: F, project_id: null}
    Then the task's project_id is NULL

  Scenario: explicit other project is honoured (multi-repo feature)
    Given feature F with project_id = R1 and a registered repo R2
    When POST /api/tasks {title: "step", feature_id: F, project_id: R2}
    Then the task's project_id = R2

  Scenario: fix task created from a defect carries the feature's project
    Given feature F with project_id = R1 and closed defect D on F
    When POST /api/defects/D/fix-item {create: true}
    Then task D.fix_task_id has project_id = R1

  Scenario: projectless feature carries nothing
    Given feature F with project_id NULL
    When POST /api/tasks {title: "step", feature_id: F}
    Then the task's project_id is NULL

  Scenario: feature created with a project
    When POST /api/features {title: "x", project_id: R1}
    Then the feature's project_id = R1
    And POST /api/features {title: "y", project_id: 99999} returns 400 with field "project_id"

  Scenario: New feature pre-selects the scoped project
    Given /pipeline/features?project=R1
    When I click New feature
    Then the dialog's Project select shows R1's name
    And submitting creates a feature with project_id = R1

  Scenario: New feature under "all" asks
    Given /pipeline/features with no project scope
    When I click New feature
    Then the Project select shows "No project" and lists every registered repo

  Scenario: New task in the Tasks tab pre-selects the toolbar project
    Given the Tasks toolbar project is R1
    When I open the New task dialog
    Then the Project select shows R1's name

  Scenario: New task on a feature page shows the carried project
    Given feature F with project_id = R1
    When I click New task on F's Tasks card
    Then the inline form shows a Project select set to R1's name

  Scenario: arm sheet seeds cwd from the task's project
    Given task T with project_id = R2 and never armed
    And the last armed task used cwd = path(R1)
    When I open T's Arm sheet
    Then cwd = path(R2)
    And no "carried over" banner is shown

  Scenario: arm cwd mismatch is flagged, not refused
    Given task T with project_id = R2
    When I set cwd = path(R1) in the Arm sheet
    Then a note reads that T is filed under R2 but will run in path(R1)
    And Arm still succeeds
```
