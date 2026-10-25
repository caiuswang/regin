# Feature #125 — Pipeline pages and operation logic, round 2

## Intent
Feature **#119** gave every Pipeline surface the verbs its rows need. This round fixes what an operator still trips over once those verbs exist. The problems fall into four groups:

- **times that lie**: "just now" on features nobody touched, and UTC calendar days shown as local ones;
- **a feature page you cannot find your way around**: Project takes a whole row, and there is no jump to Tasks, Spec, Slices or Images;
- **counts that disagree with the list they link to**;
- **controls offered to people who cannot use them, or that do nothing**.

The two problems the operator reported are **P1** (Last activity) and **P2** (feature page navigation). Everything else came from an audit of the code and is listed with file:line.

Non-goals:
- new verbs or lifecycle changes (owned by #119);
- colour and visual restyling;
- rewriting the session-ingest writer to store UTC (see Follow-ups);
- scroll-position restore across navigations.

## Audit baseline
Audited 2026-09-29 on `f119/integration` at `133d6a5a`, with #119's S1–S14 all done. The spec author reproduced P1 against the live DB. Two read-only passes (Tasks; Overview, shell and defects) found the rest, and the spec author re-read the rows marked ✓ in code. The acceptance test re-proves every row.

### P1 root cause ✓
The two operands of `lib/features/index.py::_ROWS.activity` are written on **two clocks**. Neither carries a zone:

| Column | Writer | Clock | Sample (UTC+4 host, 08:28Z) |
|---|---|---|---|
| `sessions.started_at` / `last_seen` | trace ingest (`datetime.now().isoformat()`) | **host local**. Older rows carry `+00:00` or `Z`; there are 5 shapes (lengths 19/20/23/26/32) | `2026-09-29T12:27:42.580852` |
| `defects.opened_at` / `updated_at`, `features.created_at`, `feature_events.at`, `agent_tasks.updated_at` | `datetime('now')` / `_now()` | **UTC** | `2026-09-29 08:18:28` |

`MAX()` compares the two clocks as if they were one. `pipelineFormat.js::parse()` then reads the zone-less result as UTC, so on a UTC+N host any feature with a session in the last N hours is "in the future", and `ago()` prints `just now` (`min < 1`). The `activity:desc` sort (the Features tab's default) floats those same rows to the top. The code comment at `index.py:225` ("both are already UTC") is false.

A second fault sits behind the first. `activity` reads only session **start** and defect **opened**. It never reads a ship, a reopen, a task settling or a defect being closed, so a feature can change and still look idle.

## Decisions (operator approves with the spec)

| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| D1 | `last_activity` = the latest of: an attributed session's `last_seen`, the feature's `feature_events.at`, its defects' `updated_at`, and its non-archived tasks' `updated_at`. It falls back to `features.created_at`. | Keep "session started ∨ defect opened" | The column is labelled "Active". A ship, reopen, triage or task settle is activity the old reading ignored. A fresh feature already has a `specced` event, so it sorts by birth without a special case (lesson ced8cbc3's concern). |
| D2 | Normalize **at read time, in SQL**. A session stamp with a zone goes through `datetime(x)`; one without goes through `datetime(x, 'utc')`, which reads it as host local. Every UTC-naive column goes through `datetime(x)`. The API emits `YYYY-MM-DDTHH:MM:SSZ`. | Rewrite the ingest writer to UTC and backfill 3.4k rows | Five historical shapes survive any writer fix, and a backfill is a migration this round doesn't need. The one assumption: the server's zone is the zone the hooks wrote in. Both run on the operator's host today. |
| D3 | The feature page gets a **section index**: one sticky strip under the stage line that lists every drawn section with its count, and every entry is a link. It is the page's single jump surface. The figure tiles keep their two existing links. | Add a Tasks tile to the figures | A tile is a figure, not a table of contents: Spec, Slices and Images have no figure to hang a link on. |
| D4 | **Project moves into the stage line** as an inline picker (`regin ▾`), and the Project card is deleted. | Keep the card and make it shorter | Project is one field of metadata, the same kind of thing as the id, ref and stage badge on that line. |
| D5 | Every calendar day and clock time on a Pipeline surface is shown in the **viewer's local zone**, through one helper module. The absolute UTC stamp goes in the `title`. | Keep UTC days | An operator reads "Sep 29" as their own Sep 29. Two list modes currently show two different days for the same task (T3). |
| D6 | A read route is viewer-readable. `GET /api/tasks/<id>/runs` goes from `@require_editor` to `@require_login`. | Hide the Runs button from viewers | Attempt history is a read like every other task read, and the row's "N attempts" chip already tells a viewer it exists. |
| D7 | **Regin does not judge whether a closed defect's check exists.** `missing_check` is the closer's note, stored as typed and never parsed; the ledger draws no check figure and no check gap. `lib/defects/checks.py` (the regex oracle over test sources and its four readings) is deleted. Whether a fix is covered is proved where the fix is made, by the fix task's verify going red then green, not at close. | (a) Keep the regex reading and teach it more spellings. (b) Confirm the named id at close with each suite's own collector (`pytest --collect-only`, `playwright test --list`), storing `check_suite`/`check_id` | (a) guesses which suite a free-text string means: it hard-codes `frontend/`, `def test_` and `test('…')`, so another repo reads almost every row as a gap, and here it misrouted `x.spec.js::test_y` (#219). (b) was specced and dropped before landing: a collector proves only that a test *by that name exists*, not that it catches the bug, so naming any existing test passes it — the same rubber stamp one rung up — while costing a schema change, one adapter per suite, a settings block and 4–8 s per close. Removing the guess removes #219 without adding a new judgment. |

## Shape

```ts
// ---- P1 (D1, D2) — GET /api/features rows, /api/features/<id>, project panel rows
interface FeatureRow { /* … unchanged … */ last_activity: string /* 'YYYY-MM-DDTHH:MM:SSZ', never null, never zone-less */ }

// ---- P2 (D3, D4) — frontend only
type LedgerSection = 'spec' | 'slices' | 'images' | 'tasks' | 'defects' | 'sessions'
interface SectionEntry { key: LedgerSection; anchor: string; label: string; count: string | null; drawn: boolean }
// anchors: feature-spec · slices · feature-images (NEW) · feature-tasks · feature-defects · feature-sessions
// LEDGER_ANCHORS gains feature-images; the index reads counts off the ledger payload already served
//   (slice_tasks, images, tasks d/t from FeatureTasks' load, defects open/total, sessions.rows.length)

// ---- Counts ↔ links (C1)
// GET /api/defects gains queue=out  → status open ∧ store.queue_position(row) <> 'queue'
//   (the same predicate overview._defects' `open_rest` counts); unknown value → 400 naming `queue`

// ---- Time helpers (D5) — frontend/src/utils/time.js (NEW; pipelineFormat/ledgerFormat/taskMeta import it)
localDay(iso): string      // "Sep 29", viewer zone
localStamp(iso): string    // "Sep 29, 14:05", viewer zone
parseUtc(iso): Date | null // zone-less = UTC (every server stamp is UTC after D2)
```

### Ledger page order (after)
```
back link
stage line:  #125  <title>  <ref>  [stage]  blurb  N rounds · N open(→defects) · N escaped(→defects)  Project: regin ▾  live
section index (sticky):  Spec · Slices 4 · Images 2 · Tasks 3/7 · Defects 1/5 · Sessions 9
refusal line (when a write failed)
figures (4 tiles, unchanged)
Spec → Slices → Images → Tasks → Defects → Sessions
```

## Rules

### R1 Time (P1 + audit)

| # | Where | Today | Required |
|---|---|---|---|
| T1 ✓ | `lib/features/index.py::_ROWS.activity`, `_SESSION_FACTS`, `_DEFECT_FACTS` | Local session start and UTC defect open are MAXed as strings, which renders "just now" | D1 operands, normalized per D2, emitted with `Z`. The false comment at :225 is rewritten. |
| T2 ✓ | `TaskDetailView.vue:261,265` Created/Updated · `TaskRunHistory.vue:62-63` · `TaskComments.vue:103` | The raw UTC string is printed ("01:00:00" for a 09:00 local capture) | `localStamp`, with the UTC stamp in `title` |
| T3 ✓ | `ledgerFormat.js:70 shortDate` (slices the UTC string) vs `taskMeta.js:139 taskDay` (local); `pipelineFormat.js:60 monthDay` (`timeZone:'UTC'`) | The same task shows two different days in two list modes; defect rows and triage cards show the UTC day | `shortDate`, `taskDay` and `monthDay` are all `localDay` |

### R2 Feature page navigation (P2)

| # | Rule |
|---|---|
| N1 | `FeatureSectionNav` sits directly under the stage line. It is `position: sticky; top: 0` inside `.content-scroll` and holds one entry per **drawn** section, in page order. A section that renders nothing (e.g. Slices on a tracked feature) has no entry. |
| N2 | Entry labels and counts: Spec (no count; "empty" when the body is blank) · Slices `n` slice tasks · Images `n` · Tasks `done/total` · Defects `open/total` · Sessions `n`. Every count is the one the section itself draws, from the same payload. |
| N3 | Clicking an entry reveals the section (`featureLinks.reveal`) and sets the hash with `router.replace`, so back-navigation never replays in-page jumps (#119 R6 back-link rule). |
| N4 | The entry whose section is in view carries `aria-current="location"`. It is driven by `IntersectionObserver` callbacks, not a scroll poll (I7). |
| N5 | Phone (<md): the strip is a single horizontally scrolling row. It causes no page overflow (`dom-measure --overflow` clean). |
| N6 | The Project card (`FeatureProject.vue`) is removed. Its picker, unknown-project note and error render inline in the stage line (D4). A save shows a transient "Saved" state on the control itself. It is not a toast, and the refusal stays beside the control (#119 I6). A viewer sees the project name as text. |
| N7 | The stage line's `N open` and `N escaped` are links to `#feature-defects` when nonzero. |
| N8 | `FeatureImages` carries `id="feature-images"` and is added to `LEDGER_ANCHORS`, so `/features/<id>#feature-images` arrives on it. |

### R3 Counts that must equal their link

| # | Surface | Today | Required |
|---|---|---|---|
| C1 ✓ | Overview loop, Defects → "Open" (`overview.py:112-121`, `OverviewLoop.vue:31`) | Counts open minus the triage queue, but links to `/defects?status=open`, which includes the queue | Link `/defects?status=open&queue=out`; the list lists exactly the counted rows |
| C2 ✓ | Run next header (`RunNextPanel.vue:43-44`) | Shows `runNext.total` (armed, blocked included) beside "All in Tasks →" (`group=ready`) | Header count = `ready`. When there are blocked tasks, a "· N blocked" link goes to `group=blocked` |
| C3 | Merge picker "N more" (`DefectMergePicker.vue:48,107-108`) | The server total includes this defect and the same-feature rows already shown, so "No other open defect matches" and "1 more" appear together | `more = total − shown − (self matched ? 1 : 0)`; nothing is printed when `more ≤ 0` |
| C4 | Triage rail "older" block (`TriageRail.vue:102-110`) | Labelled "older defects still unattributed" but means snoozed. It subtracts one loaded page from another, so past 50 queued rows it double-lists | Label "N snoozed", with the count from the server (`/api/defects?triage=snoozed` total), linking to `/defects?triage=snoozed`. No client-side subtraction (I3) |

### R4 Controls a user cannot use, or that do nothing

| # | Surface | Today | Required |
|---|---|---|---|
| V1 ✓ | Flat and grouped task list (`TaskRow.vue:145-152`, `TaskRows.vue:25`) | `canWrite` is never passed, so a viewer gets Arm/Run/Stop, ⋯ (Delete) and checkboxes, and the server answers 403 | `canWrite` is threaded through, as on the board card; a viewer gets no checkbox and no bulk bar |
| V2 ✓ | Runs history (`tasks.py:651`) | `@require_editor` makes the viewer dialog read "Editor role required" | D6 |
| V3 | `TaskPrimaryAction.vue:33-34` while a run is starting (`live.trace_id` null) | Watch links to the page it is on, and Stop answers 409 | Primary reads "Starting…" (not a link) and Stop is disabled, until the next `pipeline` event brings a trace id |
| V4 | Arm button (`TaskPrimaryAction.vue:84-90`) | No `:disabled="busy"` | Disabled while busy, as Run/Retry/Stop are |
| V5 | "Attribute it to a feature…" note (`DefectActions.vue:248-249`) | Shown on declined rows whose menu has no Attribute | Shown only when the row's verb set (`defectVerbs.js`) contains `attribute` |
| V6 | "blocked by #N" in the flat list (`TaskListPanel.vue:174-183`) | Resets the filters and scrolls after one `nextTick`, without awaiting the reload; off-page blockers land nowhere | `router.push('/tasks/N')`, as the detail page does |

### R5 Task list friction

| # | Surface | Today | Required |
|---|---|---|---|
| L1 ✓ | Mobile Tasks toolbar (`TasksView.vue:131-137`) | The "Sort" button lists groupings, and the real sort and the count sit in the `#meta` slot, which is hidden on phones | Two menus, **Group** and **Sort**, and the count line shown on phones |
| L2 | Flat row date (`TaskRow.vue:48`) | Always `created_at`, even under `sort=updated` | The date column shows the field the sort keys on (created or updated), and the column header names it |
| L3 | Next-step selection (`NextStepList.vue:49-54`) | Keeps ids that a live refresh moved out of view, so Archive acts on hidden rows | After each reload the selection is intersected with the visible ids; Esc clears it; a select-all box heads each group |
| L4 | Detail page Archive (`TaskDetailView.vue:193-203`) | Appears in both the header and ⋯ | The header owns it, and the detail ⋯ omits `archive`/`restore` |
| L5 | Board failed cards (`TaskBoard.vue:132-148`, `TaskBoardCard.vue`) | Look like done cards | Failed cards form a "Failed" sub-group at the top of the Done column, each with a status icon |
| L6 | Flat-list bulk result (`TaskListPanel.vue:153-158`) | "N updated, M could not be", with no ids | `BulkRejected` names each refused row, as in the next-step list (#119 R4c) |

### R6 Shell and defect pages

| # | Surface | Today | Required |
|---|---|---|---|
| S1 ✓ | Scope chip on `/defects` (`PipelineView.vue:66-70`) | Off a tab, `carried` reads `lastScoped`/`storedScope()`, never `?project=`. Arriving from a Projects row shows "All projects" over a filtered list, and picking a scope on `/defects` never relabels the chip | On `/defects` the chip reads `route.query.project`. `storedScope` is reactive, so a pick relabels the chip at once and New ▾ prefills the new scope |
| S2 ✓ | `/defects`, `/defects/:id` header (`DefectsView.vue:86`, `DefectDetailView.vue:48`) | They publish `subtitle`, but the shell reads `subline`, so no heading is drawn | They publish `subline` |
| S3 | Create on the defect pages | No `PipelineCreateButton` is teleported | New ▾ is offered, with **Defect** as the default (on the detail page, prefilled with that defect's feature) |
| S4 | Overview after a failed scope switch (`OverviewView.vue:99,108`) | Keeps the previous scope's panels under the new chip | A 404 on the scope replaces the panels with the not-found state and "Show all projects" |
| S5 | Snoozed tag on defect rows (`DefectRow.vue:55-56`) | Renders the raw `triage_state`, so a woken row reads "snoozed" but offers queue verbs | Renders `queue_position` |

### R7 Defect checks (D7)

| # | Surface | Today | Required |
|---|---|---|---|
| K1 | Ledger payload (`ledger.py`) | `defects.checks` carries the regex readings and `defects.gaps` the three check gaps | `defects.checks` is gone and `defects.gaps` is `{}` |
| K2 | `feature ledger` render (`_render_checks`) | Prints `checks  N written of M closed defects` plus one line per unredeemed row | No `checks` line |
| K3 | `GAP_BLURB` (`ledgerFormat.js`) | `check_not_yet_written`, `check_name_unverified`, `no_check_named` | The three keys are deleted; the defects tile draws no check gap |
| K4 | `missing_check` | Read by the oracle to decide whether a check exists | Unchanged on write (`defects close --check`, `POST /api/defects/<id>/close`), shown as the human note, read by nothing that decides anything |

## Invariants
- I1 Every timestamp a Pipeline endpoint emits is UTC. A session-derived one carries `Z`; no pipeline row emits a session stamp without a zone.
- I2 Every printed count whose element is a link equals the number of rows that link lists on arrival (#110 I1, #119 I3).
- I3 No client-side subtraction or filtering produces a printed count (lesson 9eee2b0b).
- I4 No write control renders for a viewer on any task or defect surface.
- I5 No slice makes a schema change: every rule is a read, a route guard, a deletion or a frontend change.
- I8 No code path reads `missing_check` to decide whether a check exists; `lib/defects/checks.py` does not exist after S7.
- I6 A refusal renders beside its control, never replacing the page (#119 I6).
- I7 Nothing polls. Freshness comes from `pipeline` events and IntersectionObserver callbacks only.

## Acceptance

```gherkin
Feature: Last activity is true
  Scenario: a UTC+N host does not see the future
    Given the server runs with TZ=Asia/Dubai and feature F's only session started 3 hours ago (naive local stamp)
    Then GET /api/features returns F.last_activity ending in "Z", equal to that start in UTC
    And the Features table shows "3h" for F, not "just now"
  Scenario: activity sort interleaves both clocks correctly
    Given feature A had a session 2h ago (local stamp) and feature B had a defect opened 1h ago (UTC stamp)
    When I sort by Last activity ↓ Then B is above A
  Scenario: a ship is activity
    Given feature F last touched 5 days ago When I ship F Then F.last_activity is within a minute of now

Feature: Feature page navigation
  Scenario: every section is one click away
    Given feature F with a spec, 3 slice tasks, 2 images, 7 tasks (3 done), 5 defects (1 open) and 9 sessions
    Then the section index reads "Spec · Slices 3 · Images 2 · Tasks 3/7 · Defects 1/5 · Sessions 9"
    When I click "Tasks" Then #feature-tasks is at the top of the scroll pane and the URL hash is #feature-tasks
    And browser Back leaves the feature page instead of returning to the previous hash
  Scenario: project is inline
    Then no element [data-testid=feature-project] card exists and the stage line holds the project picker

Feature: Counts equal their links
  Scenario: loop Open defects
    Given 2 open attributed defects and 3 in the triage queue
    Then the loop's "Open" reads 2 and its link lists exactly 2 rows
  Scenario: run next
    Given 3 ready and 2 blocked armed tasks Then Run next reads 3, shows "· 2 blocked", and each link lists that many

Feature: Viewer gating
  Scenario: viewer on the flat list
    Given I am a viewer on /tasks?group=none Then no row shows Arm, Run, ⋯ or a checkbox
  Scenario: viewer reads run history
    When a viewer opens Runs on a task with 2 attempts Then both attempts are listed

Feature: The ledger does not judge a defect's check
  Scenario: a note naming a test nobody wrote
    Given D is closed with --check "tests/defects/test_x.py::test_nobody_wrote"
    Then the feature ledger (JSON and text) carries no checks figure and defects.gaps is {}
  Scenario: the oracle is gone
    Then lib/defects/checks.py does not exist and nothing in lib/, web/ or cli/ imports it

Feature: Scope on /defects
  Scenario: arriving from a project row
    When I click a Projects row's "3 open defects" on /overview Then the chip reads that project's name
```

### Acceptance tests

| Test | Scenario |
|---|---|
| test_feature_activity_normalizes_local_session_stamp | P1 · TZ host (API, TZ patched) |
| test_feature_activity_sort_across_clocks | P1 · sort (API) |
| test_feature_activity_counts_events_defects_tasks | D1 operands (API: ship, defect update, task settle each move it) |
| test_feature_activity_emits_zulu | I1 (API: every row ends in Z) |
| test_local_day_helpers_agree | T2/T3 (Playwright, `timezoneId: 'America/Los_Angeles'`: TaskRow, NextStepRow, defect row show one local day) |
| test_ledger_section_index_counts_and_jump | N1–N3 (Playwright) |
| test_ledger_section_index_current_on_scroll | N4 (Playwright) |
| test_ledger_section_index_phone_no_overflow | N5 (Playwright at 390px) |
| test_ledger_project_inline | N6, D4 (Playwright) |
| test_ledger_images_anchor_arrival | N8 (Playwright) |
| test_defects_queue_out_filter | C1 (API: `queue=out` equals overview `open` part) |
| test_overview_loop_open_link_matches_count | C1 (Playwright) |
| test_run_next_count_is_ready_and_blocked_link | C2 (Playwright) |
| test_merge_picker_more_excludes_self_and_shown | C3 (Playwright) |
| test_triage_rail_snoozed_count_from_server | C4 (Playwright) |
| test_task_list_viewer_sees_no_write_controls | V1, I4 (Playwright) |
| test_task_runs_readable_by_viewer | V2, D6 (API) |
| test_primary_action_starting_state | V3 (Playwright) |
| test_attribute_note_follows_verb_set | V5 (Playwright) |
| test_blocked_by_link_opens_task | V6 (Playwright) |
| test_tasks_mobile_group_and_sort_menus | L1 (Playwright at 390px) |
| test_flat_row_date_follows_sort | L2 (Playwright) |
| test_next_step_selection_trimmed_on_refresh | L3 (Playwright) |
| test_task_detail_archive_once | L4 (Playwright) |
| test_board_failed_subgroup | L5 (Playwright) |
| test_flat_bulk_names_rejected | L6 (Playwright) |
| test_defects_scope_chip_follows_query | S1 (Playwright) |
| test_defect_pages_publish_subline_and_create | S2, S3 (Playwright) |
| test_overview_bad_scope_shows_not_found | S4 (Playwright) |
| test_defect_row_tag_uses_queue_position | S5 (Playwright) |
| test_ledger_reads_no_check_text | K1, K2 (`tests/features/test_ledger.py`: API + `feature ledger` render) |
| test_no_check_oracle_remains | I8 (`tests/defects/test_no_check_oracle.py`) |
| test_every_slice_merged_back | Tail |
| test_full_suite_passes | Gates |
| test_live_check_on_8321 | S6 · live check after the server restart (activity ages, section index, counts) |

## Gates (build step)
- `cd frontend && npx vite build`
- the Playwright specs for the routes a slice touches, plus `responsive.spec.js`
- `.venv/bin/python -m pytest -q -n auto --dist=loadfile tests/features tests/pipeline tests/tasks tests/defects`
- radon ≥ C; BundleEngine clean on every touched `.vue`
- Every slice is armed with `launch=tmux`. A tmux run lives on regin's own tmux server, not inside `regin serve`, so it survives a server restart, including one it triggers itself (#120, `lib/tasks/tmux_runs.py`).
- Only **S6** restarts :8321, because the server runs from the main checkout and serves a slice's code only once that slice is merged into `f125/integration`. S1–S5 verify through pytest and the Playwright `webServer`, which own their own servers and ports.
- S6 restarts the server only when no open run was launched through the SDK (an SDK run is a child of `regin serve` and would die with it). If one is open, S6 says so and leaves the restart to the operator, rather than killing it.

## Work slices

**Base: `f125/integration`**, cut from `f119/integration` at `133d6a5a` or later when the spec is approved.

No slice needs a migrate step, because I5 means none makes a schema change. S7 was added after S6 landed and is a deletion. Integrate, verify and the live check on :8321 are one slice, which needs no MANUAL row because it is a tmux run that restarts the server itself (Gates). S4 depends on S1 because both edit `TaskRow.vue` and `TaskDetailView.vue`.

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | True time | D1/D2 activity SQL + `Z` emission; the false comment rewritten; `utils/time.js` with `localDay`/`localStamp`/`parseUtc`; shortDate/taskDay/monthDay routed through it; task detail, run history and comment stamps localized | P1, D1, D2, D5, R1, I1 | test_feature_activity_normalizes_local_session_stamp, test_feature_activity_sort_across_clocks, test_feature_activity_counts_events_defects_tasks, test_feature_activity_emits_zulu, test_local_day_helpers_agree | — | — | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/features tests/pipeline && cd frontend && npx vite build |
| S2 | Feature page navigation | `FeatureSectionNav` (sticky, counts, replace-hash, IntersectionObserver current, phone strip); Project inline in the stage line and `FeatureProject` card removed; open/escaped links; `feature-images` anchor | P2, D3, D4, R2 | test_ledger_section_index_counts_and_jump, test_ledger_section_index_current_on_scroll, test_ledger_section_index_phone_no_overflow, test_ledger_project_inline, test_ledger_images_anchor_arrival | — | — | f125/integration | goal-verified-treenav | cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/feature-ledger.spec.js tests/feature-workbench.spec.js tests/responsive.spec.js |
| S3 | Counts equal their links | `queue=out` defects filter; loop Open link; Run next ready count + blocked link; merge picker "more"; triage rail snoozed block from server | R3, I2, I3 | test_defects_queue_out_filter, test_overview_loop_open_link_matches_count, test_run_next_count_is_ready_and_blocked_link, test_merge_picker_more_excludes_self_and_shown, test_triage_rail_snoozed_count_from_server | — | — | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/defects tests/pipeline && cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/pipeline-redesign-shell.spec.js tests/defect-verbs.spec.js |
| S4 | Task surfaces: gating and friction | V1–V4, V6 and L1–L6: canWrite threaded to the flat/grouped list; runs route viewer-readable; Starting… state; Arm busy; blocked-by → detail; mobile Group/Sort; date follows sort; selection trim + Esc + select-all; single Archive; board Failed sub-group; bulk rejected named | R4 (V1–V4, V6), R5, D6, I4 | test_task_list_viewer_sees_no_write_controls, test_task_runs_readable_by_viewer, test_primary_action_starting_state, test_blocked_by_link_opens_task, test_tasks_mobile_group_and_sort_menus, test_flat_row_date_follows_sort, test_next_step_selection_trimmed_on_refresh, test_task_detail_archive_once, test_board_failed_subgroup, test_flat_bulk_names_rejected | — | S1 | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/tasks && cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/tasks-views.spec.js tests/responsive.spec.js |
| S5 | Shell and defect pages | S1–S5 of R6 plus V5: scope chip reads `?project=` on /defects with a reactive stored scope; `subline` published; New ▾ on defect pages; Overview bad scope → not-found; row tag from `queue_position`; attribute note follows the verb set | R6, R4 (V5) | test_defects_scope_chip_follows_query, test_defect_pages_publish_subline_and_create, test_overview_bad_scope_shows_not_found, test_defect_row_tag_uses_queue_position, test_attribute_note_follows_verb_set | — | — | f125/integration | goal-verified-treenav | cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/pipeline-project-scope.spec.js tests/defect-verbs.spec.js tests/pipeline-shell.spec.js |
| S7 | Stop guessing defect checks | K1–K4: `lib/defects/checks.py` and `tests/defects/test_missing_check_reading.py` deleted; `ledger.py` drops `checks` and `_render_checks`, `defects.gaps` is `{}`; the three old `GAP_BLURB` keys deleted | D7, R7, I5, I8 | test_ledger_reads_no_check_text, test_no_check_oracle_remains | — | — | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/defects tests/features && cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/defect-verbs.spec.js tests/feature-ledger.spec.js |
| S6 | integrate + verify + live check | Check that S1–S5 are all merged back into `f125/integration` and none landed zero commits; run the full `.venv/bin/python -m pytest -q -n auto --dist=loadfile` and `cd frontend && ./node_modules/.bin/playwright test` on the integration branch; then, in `/Users/taowang/regin` on `f125/integration`, `cd frontend && npx vite build`, stop the process listening on :8321 and relaunch `.venv/bin/regin serve` detached (`nohup … >> /tmp/regin-serve.log 2>&1 &`), wait for `GET /api/auth/me` to answer 200, and live-check on :8321: /features Active shows real ages (no row reads "just now" unless a session is live now), the feature page's section index jumps, and Run next / loop Open match their lists | Work slices, Acceptance, Gates, P1, P2, R3 | test_every_slice_merged_back, test_full_suite_passes, test_live_check_on_8321 | — | S1, S2, S3, S4, S5 | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile && cd frontend && ./node_modules/.bin/playwright test |

## Follow-ups (not built)
- Make trace ingest write `sessions.started_at`/`last_seen` as UTC with a zone, and backfill, which would remove D2's same-host assumption.
- Projects-row "open tasks" may exclude failed tasks while its link lists them (`projects.py:52`); this is unverified.
- Running-now turns and cost don't refresh live, because session writes raise no `pipeline` frame.
- Defect → fix-task link on `/defects` rows is shown only after "Details".


