# Feature #119 — Pipeline operation logic: audit and redesign

## Intent
Walk every Pipeline flow and fix three kinds of problem: flows that are **broken**, flows that take **too many steps**, and verbs that exist only in the CLI or API and have **no UI at all**. The goal is that an operator can take any task, feature or defect from creation to settled without touching a terminal, and that every surface offers the verb its row's state calls for.

This builds on feature **#110** (the Pipeline visual redesign: three tabs, quiet Overview, Board/List/Graph, triage rail). #119 keeps #110's layout and changes only **what each surface can do and where it leads**. Its slices branch from `f110/integration` once #110's S4 (integrate + verify) has landed.

Non-goals:
- colour, spacing and layout (owned by #110);
- writing state back to a remote tracker;
- linking a tracker ref to an existing native feature;
- CLI verbs for defect decline/snooze/unsnooze/restore (see Follow-ups);
- keyboard tab hotkeys (`g o` / `g t` / `g f`).

## Audit baseline
Audited 2026-09-28 on `f110/integration` by four read-only passes (Overview/shell, Tasks, Features, Defects), then re-checked by hand. Rows marked ✓ were confirmed in code by the spec author; the rest are cited from the audit with file:line and are re-proven by their acceptance test.

## Decisions (operator approves with the spec)

| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| D1 | The triage queue **ignores a numeric project scope** and says so ("N waiting · all projects"). An unattributed defect has no project, so it can never match one. `GET /api/defects?scope=triage` drops `project`; Overview `needs_decision` carries `all_projects`. The Overview loop's Defects stage stays project-scoped (it is the tab's population), so its Triage part reads 0 under a project. | Infer a project from the reporting session's repo | regin never infers a parent (`lib/defects` rule: a guessed parent is worse than a gap) |
| D2 | A **`/defects` list page** returns as a child of the shell, **not a tab**. It is reached from the loop's Defects facts, the rail's "All defects →" and a feature's "full list" link. This reverses #110's "route deleted, no redirect" for the list page only. | Keep no list, which leaves closed, declined and snoozed defects unreachable; or restore the fourth tab | #110's thesis was "defects stop being a tab". It still holds, but a defect must stay findable after it leaves the queue. |
| D3 | A new operator verb, **`reopen`**, on a feature. From `shipped`/`escaped` it appends `restarted` (+1 round). From `abandoned` it appends `specced` (0 rounds). **Ship is refused** from `shipped`, `escaped` and `abandoned` ("reopen first"). | Keep Ship at every stage | Today one Ship click on an escaped feature resets it to `shipped` with no round ✓ (`events.py::stage` counts only the latest claim), which falsifies rounds-to-done. |
| D4 | A **tracked** feature's spec is **read-only** in the UI, with "Edit in <tracker> →"; Refresh stays on the stage line (one control, not two). | Keep the editor and warn | CLAUDE.md: a tracked feature's spec lives in the issue. Refresh overwrites `features.body` ✓ (`sync.py`), so a UI edit is silently lost. |
| D5 | **Queued is retired from every Pipeline surface**: board column, status filter value, loop part and the "armed or queued" copy. The constant stays in the model. | Write `queued` for an armed task waiting on dependencies | Nothing writes `queued` ✓ (every reference is a reader). An armed task behind a dependency is already fired by `runs.fire_if_ready` and shown in the Blocked group, so a second word for it would add a state without adding a fact. |
| D6 | **Accept means the same on the rail and in the sheet**: attribute **and** create the fix task. "Accept only" is Shift+A on the rail and a secondary button in the sheet. | Keep #110's split, where the rail attributes only and the sheet also creates the fix task | The same label with two effects ✓ (`useDefectTriage.js` vs `TriageSheet.vue`). Most accepts need a fix, so the common path costs one key. |
| D7 | Arm parameters for a transcribed slice are **derived at read time** from the feature's slice-table row (`GET /api/tasks/<id>/arm-proposal`). They are never stored. | Store `transcribe`'s proposal on the task row | The slice table is the plan (`slice_table.py`). A stored copy drifts when the spec is edited and needs a schema change. |

## Shape

```ts
// ---- Feature stage, operator verbs (D3). Run-start writers are unchanged (events.on_run_start).
type Stage = 'spec' | 'building' | 'shipped' | 'escaped' | 'abandoned'
type FeatureVerb = 'ship' | 'reopen' | 'abandon'
interface FeatureNextStep {                        // NEW on GET /api/features/<id> and on list rows
  kind: 'write_spec' | 'slice' | 'arm_slices' | 'run' | 'watch' | 'resolve_defects' | 'ship' | 'reopen' | 'none'
  label: string                                    // "Arm 4 slices", "2 open defects block a clean ship"
  target: string | null                            // route or in-page anchor
}

// ---- Slicing from the UI (S3). Each endpoint wraps the same lib/tasks function the CLI calls.
interface SlicesReport {                           // GET /api/features/<id>/slices
  sliceable: boolean
  refusal: { message: string; missing: string[] } | null         // lib/tasks/slicing.SlicingRefusal verbatim
  tail_missing: boolean                            // integrate → migrate → verify rows absent
  rows: Array<{ n: string; title: string; depends_on: string[]; base: string; skill: string;
                verify: string | null; task_id: number | null; task_status: TaskStatus | null }>
}
// POST /api/features/<id>/slices/tail   → appends tail.py's rows to features.body once, in the table's own column order;
//                                         409 when present or the feature is tracked (D4); 400 when a command holds '|'
// POST /api/features/<id>/transcribe    → { created: number[], updated: number[], refused: {n,reason}[], slices }  — same lib call as the CLI
// GET  /api/tasks/<id>/arm-proposal     → { branch, base, skill, verify_cmd } | 404 when not a slice task
// POST /api/features/<id>/arm-slices    → body { items: [{ task_id, ...ArmParams }] }
//                                         → { armed: number[], refused: {task_id, reason}[] }  — per item; only this feature's
//                                           idea slices; never calls run (fire_if_ready releases a slice whose deps are done)

// ---- Task verbs (S4). Status set shown by the Pipeline: idea | armed | running | done | failed (D5)
type TaskVerb = 'arm' | 'run' | 'retry' | 'watch' | 'stop' | 'interrupt' | 'disarm' | 'mark_done' | 'reopen'
              | 'edit' | 'deps' | 'archive' | 'restore' | 'delete'
// lib/tasks/verbs.py holds the R4a table; every task row read (next-step, flat list, board, detail) carries
//   action: 'arm'|'run'|'retry'|'watch'|null  and  menu: TaskVerb[];  graph nodes carry action + waits_on
// POST /api/tasks/<id>/disarm → armed → idea; deletes the pending (not started) run row in the same transaction;
//                               409 while running or while a launch has opened an attempt, 409 when not armed
// next_step list groups: ready · running · blocked · failed (NEW) · ideas;  "N done hidden" counts done only

// ---- Defect verbs (S6). Status and triage are orthogonal axes (lib/defects/store.py).
type DefectVerb = 'accept' | 'accept_only' | 'attribute' | 'merge' | 'decline' | 'snooze' | 'unsnooze'
                | 'restore' | 'close' | 'reopen' | 'fix_create' | 'fix_link' | 'fix_clear'
                | 'edit_symptom' | 'edit_paths' | 'attach_image'
// POST   /api/defects/<id>/unsnooze → triage_state NULL; 409 unless snoozed
// POST   /api/defects/<id>/restore  → declined → open, triage NULL, round unchanged, missing_check NULL;
//                                     409 (remedy reopen) on a closed-as-fixed defect
// DELETE /api/defects/<id>/fix-item → fix_clear (the task itself is kept)
// PATCH  /api/defects/<id> takes symptom and/or suspect_paths: string[] (replaces the list)
// Every row carries queue_position: 'queue' | 'snoozed' | 'declined' | null — store.queue_position,
// the Python twin of in_queue(); a snoozed row a later write has woken reads 'queue'.
interface Refusal {                                  // a 409's body; store.DefectError.remedy
  ok: false; msg: string
  remedy?: { verb: 'merge' | 'reopen' | 'attribute' | 'fix_create'; args: { defect_id: number } & Record<string, unknown> }
}

// ---- Defects list page (D2) — GET /api/defects already serves the list_page envelope
// query: status=open|closed · triage=queue|snoozed|declined · feature_id · project · q · sort · page
// route /defects (shell child, no tab strip entry)

// ---- Live refresh (S8)
interface PipelineEvent { entity: 'task' | 'feature' | 'defect'; id: number; project_id: number | null }
// hub.broadcast_event('pipeline', PipelineEvent) on every write listed in Rules · Live refresh
```

### Feature stage (operator axis)
```
spec ──run start──▶ building ──ship──▶ shipped ──defect blamed──▶ escaped
  │                    │                  │                          │
  │                    │                  └──reopen (+1 round)──▶ building ◀──reopen (+1 round)──┘
  └──abandon──▶ abandoned ◀──abandon── (any)          abandoned ──reopen──▶ spec (0 rounds)
ship refused from: shipped ("already shipped") · escaped · abandoned ("reopen first")
```

### Defect triage axis (orthogonal to open/closed)
```
NULL ──snooze──▶ snoozed ──unsnooze──▶ NULL
NULL ──decline──▶ declined (status closed) ──restore──▶ NULL (status open, round unchanged)
```

## Rules

### R1 Broken flows (fixed as found; no redesign)

| # | Flow | Today | Required |
|---|---|---|---|
| B1 ✓ | Trace view → Declare feature | The picker reads `r.key`, but index rows have no `key`, so the typed search text is posted as the feature id (`DeclareFeatureDialog.vue:83-92`, `index.py`) | The row click chooses `r.id`, and the dialog posts that id |
| B2 ✓ | Triage under `?project=<id>` | The queue is always empty (`overview.py::_defects_scoped` ∧ `in_queue`); "older" mixes a global count with a scoped one (`TriageRail.vue:80,86`) | D1: the queue and the "older" count use one unscoped predicate; the header says "all projects" when a project is picked |
| B3 ✓ | Merge on `/defects/:id` | `onChanged` re-reads the deleted id and shows "no defect N" (`DefectDetailView.vue:52-55`) | Navigate to `/defects/<keeper>` and flash "#a folded into #b" |
| B4 ✓ | Task detail → Arm | The sheet gets `task` without `armed` (`TaskDetailView.vue:307-311`), so it prefills from localStorage and can re-aim the pending attempt | Pass `{...task, armed}`. The prefill order is R4b. |
| B5 ✓ | Feature ledger → Unattribute refused | The refusal is written to `loadError`, which replaces the page (`FeatureLedgerView.vue:175`) | Show it inline in the Sessions card, beside the control (I6) |
| B6 ✓ | Tracked feature → Edit spec → Refresh | The edit is silently overwritten (`sync.py` writes `body`) | D4: the editor is read-only for a tracked feature |
| B7 | Arm sheet copy | Says verify is "recorded, not yet run", but settling runs it as the gate (`TaskArmSheet.vue:196,203` vs `runs.py:1087-1106`) | Label: "Gate — exit 0 marks the task done" |
| B8 | Dead code | `DefectTriageCard.vue` is imported nowhere | Delete it |

### R2 Feature verbs by stage (ledger stage line; list row shows `next_step` only)

| Stage | next_step (first match) | Ship | Reopen | Abandon |
|---|---|---|---|---|
| spec | body empty → `write_spec` · no slice tasks transcribed → `slice` (shows the refusal, or "Transcribe N slices" when the table parses) · slice tasks exist and some are `idea` → `arm_slices` "Arm N slices" · else `run` | shown; the confirm says "no build run recorded" | — | shown, confirm |
| building | a task running → `watch` · a runnable armed task → `run` · open defects → `resolve_defects` · else `ship` | shown; the confirm lists open tasks and open blamed defects by id | — | shown, confirm |
| shipped | `none` | refused | shown, confirm "+1 round" | shown, confirm |
| escaped | `reopen` "N blamed defects — reopen to fix" | refused ("reopen first") | shown, confirm "+1 round" | shown, confirm |
| abandoned | `reopen` | refused | shown, confirm "back to spec, no round" | — |

- Server-side refusals live in `lib/features/store.py` (`VerbRefused` → API 409, CLI exit 2; one `VERBS` table per stage served as the ledger's `verbs`), so the CLI `feature ship` refuses identically. A new CLI verb, `regin feature reopen <id>`, mirrors the API. The ledger also serves `ship_check` (open tasks/defects by id, warnings) and `slice_tasks`; `next_step` comes from `lib/features/next_step.py` for the ledger and every index row.
- Ship on a **tracked** feature adds to the confirm: "<tracker> still says <state>; the next Refresh will count a walk-back unless you move it there."
- **Rename**: the ledger title is editable inline (`PATCH {title}`); disabled for tracked features (D4).
- **Unattribute** asks for confirmation (it clears the primary flag), and a refusal stays inline (B5).
- The **Build** step button is hidden once the feature has transcribed slice tasks, so a feature has one way to build.

### R3 Spec → slices → tasks from the UI

| Surface | Shows | Verb |
|---|---|---|
| Ledger "Slices" panel (under the spec) | `SlicesReport`: the refusal verbatim, or rows with task link and status | "Add tail rows" when `tail_missing` · "Transcribe" when `sliceable` · "Arm N" when any slice task is `idea` |
| Arm-all sheet | one editable row per `idea` slice task, prefilled from `arm-proposal` | "Arm N": one `POST arm-slices`; refusals named per row; the sheet stays open on refusal |
| Expanded Features row (`FeatureActiveTasks`) | adds an **Ideas** part: "K not armed · Arm →" (→ ledger `#slices`) | — |

Arming never runs. A slice whose dependencies are already done is fired by the existing `fire_if_ready`, because the edge is the grant. The operator starts the chain with Run on the first slice.

### R4a Task verbs by state × surface (primary = the one visible action; ⋯ = the row menu)

| State | Primary (list row · board card · graph node · detail · Overview) | ⋯ menu (list row · board card · detail) |
|---|---|---|
| idea | Arm | Edit · Deps · Mark done · Archive · Delete |
| armed, deps satisfied | ▶ Run | Disarm · Re-arm · Edit · Deps · Mark done · Archive · Delete |
| armed, blocked | "waits on #N" — a link to #N, no button | Disarm · Re-arm · Deps · Edit · Archive · Delete |
| running | Watch **and** Stop (confirm) | Interrupt |
| failed | Retry (= Run, reuses the last attempt's params) | Re-arm · Mark done · Archive · Delete |
| done | — | Reopen (→ idea) · Archive |
| archived | — | Restore · Delete |

- The graph node carries the primary only; clicking the node body opens the detail page.
- **Failed group** in the next-step list (after Blocked, before Ideas), ordered by latest attempt ended desc, with Retry. "N done hidden" counts done only.
- **Mark done** on a task that is the fix item of an open defect: the confirm names those defects and links each one (rows carry `fixes_open_defects`). It never closes them, because closing needs a `missing_check` (lesson 9c0a962b).
- **Mark done releases dependents**: the done write (PATCH or bulk) calls `runs.fire_ready_dependents`, exactly as a settling run does, so an armed task waiting on a manually completed one (e.g. a MANUAL restart row) starts without a second click. Mark done is allowed from `armed` and drops the pending attempt, as Disarm does.
- Every list grouping (next-step, none, status, priority, project) draws R4a's primary + ⋯ through `TaskPrimaryAction` + `TaskVerbMenu`, the components the board card and graph node use, so a task offers the same controls in every view (the graph node carries the primary only). `TaskRow.vue` keeps its primary in a fixed-width slot so the date column aligns; run history opens from its attempts chip. A legacy `queued` row offers no verb.
- **Run refusals** render through `TaskRefusal` on every surface, so blocking tasks are links. **Run all N** lists every refused task with its reason, not just a count.
- **Stop/Run feedback** comes from the live refresh (R7), not a manual reload.

### R4b Arm sheet prefill (first non-empty source wins, per field)

| Order | Source |
|---|---|
| 1 | the task's pending `armed` params (re-arm) |
| 2 | `GET /api/tasks/<id>/arm-proposal` (slice tasks, D7) |
| 3 | the last attempt's params |
| 4 | remembered prefs (`useArmPrefs`): only `skill` / `model` once row 2 or 3 supplied anything; a task with neither keeps the full remembered set. Never `branch` / `base` |

### R4c Task editing

| Where | Adds |
|---|---|
| Edit sheet | `kind` (spec/build/task; a fix task shows `fix` locked — only `fix_items.create_for` makes or unmakes one), `assignee`, `feature` (the shared `FeaturePicker`, opening on the current feature) |
| Detail header | Archive/Restore; every write control hidden when `!canWrite` |
| Next-step (default) list | row checkboxes and the bulk bar: priority · project · feature · archive · delete. Rejected rows are named, not counted. |

### R5 Defect verbs by state × surface

| State | Rail / sheet | ⋯ menu (`/defects/:id`, `/defects` rows) |
|---|---|---|
| open · unattributed · untriaged | **A** Accept (attribute + fix task, D6) · **⇧A** Accept only · **D** Dup · **X** Decline · **S** Snooze | **Attribute** · Merge · Decline · Snooze · Edit symptom · Edit paths · Attach image |
| open · unattributed · snoozed | not in the rail | **Unsnooze** · Attribute · Merge · Decline · Edit symptom · Edit paths |
| open · attributed | — | **Close** · Re-attribute · Fix task (create / link / clear) · Merge · Absorb · Edit symptom · Edit paths · Attach image |
| closed · declined | — | **Restore** · Edit symptom |
| closed | — | **Reopen** (+1 round) · Edit symptom · Edit paths · Absorb |

The bold verb is also the row's one primary button. A closed defect keeps Edit and Absorb: its wording can be corrected after the fact (defect #194), and absorbing an open duplicate into a closed keeper is refused by the store with a `reopen` remedy. The matrix is one table, `frontend/src/utils/defectVerbs.js`.

- **Merge / Dup** opens a picker of open defects (same feature first, then a text search). It shows the keeper's symptom, paths and images and confirms "delete #a, keep #b". Afterwards the page moves to the keeper (B3).
- **Re-attribute** and the **New defect** feature field use `FeaturePicker`: a server-side `q` search, abandoned features excluded, no 200-row cap.
- Triage cards (rail and sheet) show the defect's images. The detail page can attach images and edit suspect paths.
- A refusal that names another verb (merge, reopen, attribute) carries `remedy`, and the UI renders it as a button instead of CLI text. A `reopen` remedy is confirmed first (it adds a round).
- An Accept whose fix task is refused has already moved the defect out of the queue, so its refusal is shown above the cards (rail and sheet), never on the card the cursor lands on next.
- **Arriving at `/features?triage=<id>`** focuses that card so A/D/X/S work at once. An id beyond the loaded page is fetched directly.

### R6 Shell: creation and navigation

| Flow | Rule |
|---|---|
| Create | The header create control is a split button: the tab's default ("New task" / "New feature", what `c` opens) plus a **▾** offering **Task · Feature · Defect**. The shell owns all three dialogs, so every tab creates every kind. Task and feature prefill the scoped `project_id`. The **New defect** dialog takes symptom (required), feature (optional; none sends it to triage), suspect paths and images. |
| Quick capture | Carries the scoped `project_id`; tab counts re-read after a capture |
| Feedback | Create, Run, Stop, Arm and Merge each flash a toast that links the object ("Attempt 2 started · Watch") — on the phone too, where Running now is not drawn |
| Loop facts | **Every** printed fact links. Tasks: running / ready / failed → `/tasks?view=list&group=<g>` (the list scrolls to and expands the group; a next-step key in `group=` is an address that reads as the next-step grouping, stays in the URL across filter changes, and is never stored as the reader's grouping). Features: building / in spec / escaped → `/features?stage=<s>`. Defects: open → `/defects?status=open`; to triage → `/features`. |
| Projects panel & 1d head | features / open tasks / open defects counts link to the same tabs under `?project=<id>` |
| Run next | "All in Tasks →" → `/tasks?view=list&group=ready`; the empty state links `group=ideas`; the feature name and "waits on #N" are links |
| Back link on detail pages | Returns to the in-shell route (with its query) the user came from — the router's `history.state.back`, left by `router.back()`, so no forward entry is pushed; a same-page entry (an in-page `#anchor`) does not count. Otherwise the parent tab |
| Project change on a detail page | Goes to the parent list under the new scope, not to `/overview`: `/features/:id` → Features, `/defects/:id` → the `/defects` list |
| Project picker | ↑/↓ move, Enter picks the highlighted row; the search is server-side (`q`), no 100-row cap |
| New feature dialog | Keeps its draft until the feature is declared or the operator discards it (as the task composer does) |

### R7 Live refresh (event-driven, no polling)

| Write | Broadcast |
|---|---|
| task status change, run start, run settle (`runs.complete_run`), task create/delete | `pipeline {entity:'task'}` |
| `feature_events` append, feature PATCH | `pipeline {entity:'feature'}` |
| any `lib/defects/store.py` write | `pipeline {entity:'defect'}` |

The shell subscribes to the `pipeline` topic on the existing notifications stream (`/api/notifications/stream/topics`). Each mounted Pipeline view re-reads its own data when an event arrives. While a read is in flight, further events set a dirty flag that triggers one more read after it. "Updated …" shows the time of the last successful read. No interval, timeout or `setTimeout` retry is used anywhere (CLAUDE.md root-cause rule).

## Invariants
- I1 Every verb reachable in the UI goes through the same `lib/` function as its CLI or API twin; no business rule is re-implemented in Vue.
- I2 No action is reachable only on hover, and every row's primary action follows R4a / R5 for its state on every surface that draws the row.
- I3 Every count is the server's (#110 I1 unchanged); lists and pickers search server-side (lesson 9eee2b0b).
- I4 Rounds move only through `restarted`. Ship, merge, restore and unsnooze never change a feature's or a defect's round. Reopen on a feature (+1) and reopen on a defect (+1) are the only operator verbs that do.
- I5 No new table or column: every new verb uses existing statuses, event kinds and columns (`suspect_paths_json`, `triage_state`, `feature_events.kind` is free text).
- I6 A refusal never replaces a page. It renders beside the control that caused it.
- I7 Nothing polls: freshness comes only from R7 events and from the operator's own writes.

## Acceptance

```gherkin
Feature: Broken flows
  Scenario: declare a session to a feature (B1)
    Given the trace view's Declare-feature dialog lists feature #12
    When I click #12's row and Declare
    Then POST /api/features/12/attribute is sent and the session appears on /features/12
  Scenario: triage under a project (B2)
    Given one open unattributed untriaged defect and ?project=<id>
    Then the rail and the Overview "Needs a decision" list it, headed "all projects"
  Scenario: merge lands on the keeper (B3)
    When I merge #a into #b on /defects/a Then I am on /defects/b and see "#a folded into #b"
  Scenario: re-arm keeps pending params (B4)
    Given task T armed with branch x When I open Arm on /tasks/T Then the branch field reads x

Feature: Feature lifecycle
  Scenario: reopen an escaped feature
    Given feature F escaped with rounds 1 When I reopen F Then F is building with rounds 2
  Scenario: ship is refused where it would falsify rounds
    Given F escaped When I POST ship Then it is refused "reopen first" and no event is appended
  Scenario: ship confirm lists open work
    Given F building with an armed task and an open blamed defect
    When I click Ship Then the confirm names both by id

Feature: Slicing from the UI
  Scenario: transcribe and arm all
    Given F's body holds a sliceable table S1..S3
    When I click Transcribe Then 3 idea tasks appear in the Slices panel
    When I click "Arm 3" and confirm Then the 3 tasks are armed with each row's base/skill/verify and none is running

Feature: Task verbs
  Scenario: failed tasks are reachable and retryable
    Given a failed task T Then the default list shows it in "Failed" with Retry
  Scenario: every surface shows the state's primary action
    Then an armed deps-clear task shows ▶ Run on the list row, the board card and the graph node
  Scenario: stop from the list
    Given a running task Then its list row shows Watch and Stop

Feature: Defect verbs
  Scenario: stranded defects are reachable
    Given a declined defect D When I open /defects?triage=declined Then D is listed with Restore
    When I Restore D Then D is open, untriaged, and its round is unchanged
  Scenario: new unattributed defect lands in triage
    When I create a defect from "New ▾ → Defect" without a feature Then it appears in the triage rail

Feature: Shell
  Scenario: every loop fact is a link
    Then each fact printed on /overview is an anchor whose target lists exactly that fact's rows
  Scenario: live refresh without polling
    Given /overview is open When a task run starts from the CLI Then Running now shows it without a reload
    And no request to /api/pipeline/overview is issued while nothing changes
```

### Acceptance tests

| Test | Scenario |
|---|---|
| test_declare_feature_dialog_attributes_chosen_row | Broken · B1 (Playwright) |
| test_triage_queue_ignores_project_scope | Broken · B2 (API: overview + defects list agree) |
| test_defect_merge_lands_on_keeper | Broken · B3 (Playwright) |
| test_task_detail_rearm_prefills_pending_params | Broken · B4 (Playwright) |
| test_unattribute_refusal_stays_inline | Broken · B5 (Playwright) |
| test_tracked_feature_spec_is_read_only | Broken · B6 / D4 (Playwright) |
| test_feature_reopen_appends_restarted_and_counts_round | Lifecycle · reopen (API + CLI) |
| test_feature_reopen_from_abandoned_returns_to_spec | Lifecycle · reopen abandoned |
| test_feature_ship_refused_when_shipped_escaped_or_abandoned | Lifecycle · ship refused (store, so CLI too) |
| test_feature_next_step_per_stage | R2 next_step table (API) |
| test_feature_ship_confirm_lists_open_work | Lifecycle · ship confirm (Playwright) |
| test_feature_slices_endpoint_reports_refusal_and_rows | Slicing · report (API) |
| test_feature_slices_tail_appends_once | Slicing · tail (API) |
| test_feature_transcribe_endpoint_matches_cli | Slicing · transcribe (API) |
| test_task_arm_proposal_from_slice_row | Slicing · D7 proposal (API) |
| test_arm_slices_bulk_names_refusals_and_never_runs | Slicing · arm all (API) |
| test_feature_slice_panel_transcribe_and_arm_all | Slicing · transcribe and arm all (Playwright) |
| test_next_step_failed_group_and_retry | Task verbs · failed group (API) |
| test_task_disarm_returns_to_idea_and_drops_pending_run | Task verbs · disarm (API) |
| test_task_mark_done_fix_task_names_open_defects | Task verbs · mark done (API + confirm) |
| test_task_mark_done_releases_armed_dependents | Task verbs · mark done fires dependents (API) |
| test_task_primary_action_on_every_surface | Task verbs · R4a matrix (Playwright: list, board, graph, detail) |
| test_task_stop_on_running_rows | Task verbs · stop (Playwright) |
| test_run_all_names_each_refusal | Task verbs · run all (Playwright) |
| test_queued_not_offered | D5 · no Queued column, filter value or loop part |
| test_task_edit_sheet_sets_kind_assignee_feature | R4c · edit sheet (Playwright) |
| test_task_detail_archive_and_viewer_gating | R4c · detail header (Playwright) |
| test_tasks_bulk_bar_on_grouped_list | R4c · bulk on the default list (Playwright) |
| test_defects_list_page_filters_and_pages | D2 · /defects list (Playwright) |
| test_defects_api_triage_filter | D2 · `triage=` filter (API) |
| test_defect_unsnooze_and_restore_keep_round | Defect verbs · unsnooze, restore (API) |
| test_defect_detail_menu_offers_verbs_by_state | R5 matrix (Playwright) |
| test_defect_merge_picker_previews_and_confirms | R5 · merge picker (Playwright) |
| test_feature_picker_server_search_excludes_abandoned | R5 · FeaturePicker (API) |
| test_defect_edit_suspect_paths | R5 · paths (API) |
| test_defect_attach_image_on_detail | R5 · images (Playwright) |
| test_triage_accept_same_on_rail_and_sheet | D6 (Playwright) |
| test_defect_refusal_carries_remedy | R5 · remedy (API) |
| test_triage_arrival_focuses_card | R5 · arrival (Playwright) |
| test_new_defect_dialog_unattributed_lands_in_triage | R6 · New defect (Playwright) |
| test_pipeline_create_menu_prefills_scope | R6 · create menu (Playwright) |
| test_quick_capture_carries_scope_and_refreshes_counts | R6 · quick capture (Playwright) |
| test_overview_every_fact_links_to_filtered_tab | R6 · loop facts (Playwright) |
| test_detail_back_link_returns_to_origin | R6 · back link (Playwright) |
| test_project_change_on_detail_goes_to_parent_tab | R6 · project change (Playwright) |
| test_project_picker_keyboard_and_server_search | R6 · picker (Playwright) |
| test_new_feature_dialog_keeps_draft | R6 · draft (Playwright) |
| test_action_toasts_link_the_object | R6 · feedback (Playwright) |
| test_pipeline_events_broadcast_on_writes | R7 (API: one event per write kind) |
| test_pipeline_views_refresh_on_event | R7 (Playwright: CLI write → view updates, no idle requests) |
| test_every_slice_merged_back | Tail · every slice merged back to `f119/integration` (S9) |
| test_full_suite_passes | Gates · whole pytest + Playwright suites green on the integration branch |

## Gates (build step)
- `cd frontend && npx vite build`
- The Playwright specs for the routes a slice touches, plus `responsive.spec.js`
- `.venv/bin/python -m pytest -q -n auto --dist=loadfile` over `tests/tasks tests/features tests/defects tests/pipeline`
- radon ≥ C; BundleEngine clean on every touched `.vue`
- No slice restarts, kills or relaunches the regin server on :8321: a task run lives inside it, so a restart ends the run. pytest and the Playwright `webServer` own their own servers and ports, so no gate needs it. A live check of a slice that changes server Python is a **MANUAL** row (S10–S14): the operator restarts the server on `f119/integration`, checks, and marks the task done, which releases the slices built on top. S7 changes only the frontend and gets none.

## Work slices

**Base: `f119/integration`** (cut from `f110/integration` after #110 S4 landed)

The chain tail is one slice, not three: no slice changes the schema (I5, every Schema cell is —), so a migrate slice would have nothing to prove, and integrate and verify both run on the merged branch.

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | Broken flows | B1–B8: declare picker id, unscoped triage queue with "all projects" header, merge → keeper, re-arm prefill, inline unattribute refusal, tracked spec read-only, gate copy, dead card deleted; never restarts :8321 (live check is S10) | R1, D1, D4, I6 | test_declare_feature_dialog_attributes_chosen_row, test_triage_queue_ignores_project_scope, test_defect_merge_lands_on_keeper, test_task_detail_rearm_prefills_pending_params, test_unattribute_refusal_stays_inline, test_tracked_feature_spec_is_read_only | — | — | f119/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/pipeline tests/defects && cd frontend && npx vite build |
| S2 | Feature lifecycle verbs | `reopen` in store/API/CLI; ship refusals in store; ship confirm listing open work; tracked-ship warning; `next_step` on detail and list rows; inline rename; unattribute confirm; Build hidden once slices exist; never restarts :8321 (live check is S11) | D3, R2, Shape (FeatureNextStep, Feature stage), I1, I4 | test_feature_reopen_appends_restarted_and_counts_round, test_feature_reopen_from_abandoned_returns_to_spec, test_feature_ship_refused_when_shipped_escaped_or_abandoned, test_feature_next_step_per_stage, test_feature_ship_confirm_lists_open_work | — | — | f119/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/features && cd frontend && npx vite build |
| S3 | Slicing from the UI | slices / tail / transcribe / arm-proposal / arm-slices endpoints over lib/tasks; ledger Slices panel; arm-all sheet; `FeatureActiveTasks` "not armed" part; arm-sheet prefill order; never restarts :8321 (live check is S13) | D7, R3, R4b, Shape (SlicesReport), I1 | test_feature_slices_endpoint_reports_refusal_and_rows, test_feature_slices_tail_appends_once, test_feature_transcribe_endpoint_matches_cli, test_task_arm_proposal_from_slice_row, test_arm_slices_bulk_names_refusals_and_never_runs, test_feature_slice_panel_transcribe_and_arm_all | — | S11 | f119/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/tasks tests/features && cd frontend && npx vite build |
| S4 | Task verbs by state | R4a matrix on list/board/graph/detail/Overview; Failed group + Retry; Stop on running rows; disarm endpoint; mark done with fix-task check; mark done releases armed dependents; `TaskRefusal` everywhere; run-all names refusals; Queued retired from surfaces; never restarts :8321 (live check is S12) | D5, R4a, Shape (TaskVerb), I2 | test_next_step_failed_group_and_retry, test_task_disarm_returns_to_idea_and_drops_pending_run, test_task_mark_done_fix_task_names_open_defects, test_task_mark_done_releases_armed_dependents, test_task_primary_action_on_every_surface, test_task_stop_on_running_rows, test_run_all_names_each_refusal, test_queued_not_offered | — | S10 | f119/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/tasks tests/pipeline && cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/tasks-views.spec.js |
| S5 | Task editing & bulk | edit sheet kind/assignee/feature (`FeaturePicker`); detail Archive/Restore + `canWrite` gating; bulk bar on the grouped list incl. set feature, rejected rows named; never restarts :8321 (live check is S13) | R4c, I3 | test_task_edit_sheet_sets_kind_assignee_feature, test_task_detail_archive_and_viewer_gating, test_tasks_bulk_bar_on_grouped_list | — | S12 | f119/integration | goal-verified-treenav | cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/tasks-views.spec.js |
| S6 | Defect verbs & list | `/defects` list page + `triage=` filter; unsnooze / restore endpoints; R5 ⋯ matrix; merge picker with preview; `FeaturePicker` (server `q`, no abandoned); suspect-paths edit; image attach + images on triage cards; Accept unified (D6); `remedy` on refusals; triage arrival focus; never restarts :8321 (live check is S12) | D2, D6, R5, Shape (DefectVerb, Refusal), I3, I4, I6 | test_defects_list_page_filters_and_pages, test_defects_api_triage_filter, test_defect_unsnooze_and_restore_keep_round, test_defect_detail_menu_offers_verbs_by_state, test_defect_merge_picker_previews_and_confirms, test_feature_picker_server_search_excludes_abandoned, test_defect_edit_suspect_paths, test_defect_attach_image_on_detail, test_triage_accept_same_on_rail_and_sheet, test_defect_refusal_carries_remedy, test_triage_arrival_focuses_card | — | S10 | f119/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/defects tests/pipeline && cd frontend && npx vite build |
| S7 | Shell creation & navigation | "New ▾" menu with New defect dialog; scope prefill; quick capture scope + count refresh; action toasts; every loop fact and project count linked; `group=` list anchor; back-to-origin; project change → parent tab; picker keys + server search; NewFeatureDialog draft | R6, I2 | test_new_defect_dialog_unattributed_lands_in_triage, test_pipeline_create_menu_prefills_scope, test_quick_capture_carries_scope_and_refreshes_counts, test_overview_every_fact_links_to_filtered_tab, test_detail_back_link_returns_to_origin, test_project_change_on_detail_goes_to_parent_tab, test_project_picker_keyboard_and_server_search, test_new_feature_dialog_keeps_draft, test_action_toasts_link_the_object | — | S12 | f119/integration | goal-verified-treenav | cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/pipeline-redesign-shell.spec.js |
| S8 | Live refresh | `pipeline` broadcasts on task/feature/defect writes; shell topic subscription; views re-read on event with an in-flight dirty flag; no polling; never restarts :8321 (live check is S14) | R7, I7 | test_pipeline_events_broadcast_on_writes, test_pipeline_views_refresh_on_event | — | S13, S7 | f119/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/pipeline tests/tasks tests/defects tests/features && cd frontend && npx vite build |
| S9 | integrate + verify | 检查 S1–S8 都已合回 `f119/integration`，无人落零 commit；然后在集成分支上跑全量 `.venv/bin/python -m pytest -q -n auto --dist=loadfile`、`cd frontend && ./node_modules/.bin/playwright test` | Work slices, Acceptance, Gates | test_every_slice_merged_back, test_full_suite_passes | — | S14 | f119/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile && cd frontend && ./node_modules/.bin/playwright test |
| S10 | MANUAL — restart server after S1 | operator only, never armed. In `/Users/taowang/regin` (already on `f119/integration`): `cd frontend && npx vite build`, restart `regin serve`, then check B1–B8 on :8321 and Mark done | Gates, R1 | live check: B1–B8 on :8321 | — | S1 | f119/integration | — | — |
| S11 | MANUAL — restart server after S2 | operator only, never armed. In `/Users/taowang/regin` (already on `f119/integration`): `cd frontend && npx vite build`, restart `regin serve`, then check reopen, refused ship and next_step on a feature ledger, then Mark done | Gates, R2 | live check: feature reopen and ship refusal on :8321 | — | S2 | f119/integration | — | — |
| S12 | MANUAL — restart server after S4 and S6 | operator only, never armed. In `/Users/taowang/regin` (already on `f119/integration`): `cd frontend && npx vite build`, restart `regin serve`, then check the task verb matrix (disarm, Failed group, Stop) and /defects (list, unsnooze, restore), then Mark done | Gates, R4a, R5 | live check: task verbs and /defects on :8321 | — | S4, S6 | f119/integration | — | — |
| S13 | MANUAL — restart server after S3 and S5 | operator only, never armed. In `/Users/taowang/regin` (already on `f119/integration`): `cd frontend && npx vite build`, restart `regin serve`, then check Slices panel transcribe + arm-all and bulk set-feature, then Mark done | Gates, R3, R4c | live check: slicing and bulk bar on :8321 | — | S3, S5 | f119/integration | — | — |
| S14 | MANUAL — restart server after S8 | operator only, never armed. In `/Users/taowang/regin` (already on `f119/integration`): `cd frontend && npx vite build`, restart `regin serve`, then write a task from the CLI and see /overview update without reload, then Mark done | Gates, R7 | live check: pipeline events on :8321 | — | S8 | f119/integration | — | — |

## Follow-ups (not built)
- CLI verbs `defects decline|snooze|unsnooze|restore` (today web-only, the reverse of the usual gap).
- Tracker write-back on ship/reopen, which would remove the false-round warning in R2.
- Refuse `PATCH {title}` on a tracked feature in `lib/features/store.rename`; today only the UI disables rename (Refresh overwrites the title).
- `autorun` is accepted by arm and stored, but nothing reads it. Remove it or give it a reader.
- `PATCH {status: done}` from `running` (API/bulk only — the UI never offers it) fires dependents while the upstream agent may still be live; refuse it while `runs.live_run` answers.
- Linking a tracker ref to an existing native feature.
- `arm-proposal` always proposes the default `f<ID>/s<n>` branch; a feature transcribed with a custom `--branch-prefix` is not remembered (D7 forbids storing it).





