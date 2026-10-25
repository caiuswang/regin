## Intent

The feature detail page (`/features/:id`) ends in three stacked cards — Tasks,
Defects, Sessions — each a different list idiom bolted on as its own feature
landed. Read top to bottom they still tell the right story (steps → what went
wrong → the evidence), but the Defects card in particular reads as a wall of
identical always-on buttons, and the three cards agree on no shared row
grammar. This spec is the redesign of that bottom stretch — visual and
interaction only; no field, endpoint, or lifecycle changes.

## Current shape (the problem, stated as facts)

| Card | Row idiom today | Concrete complaint |
|---|---|---|
| Tasks | compact one-line row: status icon, kind chip, id, title | fine as-is — the baseline the other two should move toward |
| Defects | tall card per defect: metadata line, symptom, fact list, then **4–5 always-visible secondary buttons** (Re-attribute, Close/Reopen, Create fix task, Link existing task, Merge) | two closed defects with the same missing-check and same blame render as near-duplicate walls of buttons; nothing distinguishes the common case (already fixed, nothing to do) from the one needing action |
| Sessions | dense single-line mono row: date, trace id, title, tag, cost | correct information, but the only row of the three with a hover-reveal pattern for its destructive action (undeclare) — a precedent the Defects row does not follow |

The three cards also disagree on padding, border weight, and how a tag reads,
so the page looks like three different features skinned it rather than one.

## Options

**Chosen: option 2** (which includes option 1's collapse). Options 1 and 3 are kept below
for the record; option 3 was not built.

| # | Option | Change | Fixes | Leaves unfixed | Risk |
|---|---|---|---|---|---|
| 1 | **Action overflow on Defects** (recommended) | Collapse the Defects row's secondary buttons to one primary action (Close *or* Reopen, whichever applies) plus a single "⋯" menu (`components/ui/DropdownMenu.vue` already exists) holding Re-attribute, Create fix task, Link existing task, Merge — mirroring the hover-reveal precedent Sessions already set for its delete button | the clutter complaint, the biggest single visual offender | cross-card inconsistency (Tasks/Sessions/Defects still three different row grammars) | low — one component (`DefectActions.vue`), no new primitives |
| 2 | **Unify the row grammar** | Normalize row height/padding/border and tag rendering across all three cards; apply the hover-reveal-for-secondary-actions pattern everywhere a row has one; includes option 1's collapse as part of the same pass | the "looks like three different features" complaint holistically | page length — three full-width stacked cards, long feature still means heavy scroll | medium — touches `FeatureTasks.vue`, `FeatureEvidence.vue`, `DefectRow.vue`/`DefectActions.vue` |
| 3 | **Tabbed Activity panel** | Replace the three stacked cards with one card and `components/ui/Tabs.vue` (Tasks / Defects / Sessions), each tab label carrying its count | page length, gives each list room to grow without dominating | the page's own designed-in order — Spec → Tasks → Defects → Evidence is a deliberate "how is this going" narrative (see `FeatureLedgerView.vue`'s header comment); tabs hide two of three at any time, so a reader can no longer see defects next to the evidence that grounds them | high — also has to move the `#feature-sessions` hash-reveal (used by the index page's sessions-count link) from "scroll to card" to "switch tab and scroll" |

Options are not exclusive across time: 1 → 2 → 3 is a sensible order if more
than one is ever wanted, since each is a superset of the last's visual intent.

## Rules (hold regardless of option chosen)

- No action available today may become unreachable — collapsing into a menu
  changes *how many clicks*, never *what is possible*.
- The primary action on a Defects row must stay a single un-nested click:
  Close on an open defect, Reopen on a closed one. Everything else may move
  behind a disclosure.
- Sessions' existing gap/ghost-row rendering and the `#feature-sessions`
  anchor must keep resolving to the same visual location after any option.
- No change to what a row's data means or how it's fetched — this is
  presentation only.

## As built

| Piece | Where |
|---|---|
| Shared row grammar: `.ledger-list`, `.ledger-row` (+ `.ledger-row-static` for ghost rows), `.ledger-id`, `.ledger-tag` (+ `-amber`/`-green`), `.ledger-reveal` | global, `frontend/src/assets/style.css` (global because the rows live in three child components) |
| Defect row: primary Close/Reopen + `⋯` `DropdownMenu` (items carry `testid`/`title`) | `DefectActions.vue`; trigger `defect-more-<id>` |
| `DefectRow` `flat` (ledger-row instead of card) and `hideFeature` (no repeat of the page's own feature title) | set by `FeatureDefects.vue`; DefectsView/DefectDetailView keep card mode |
| Hover-reveal only inside a `.ledger-row`; full opacity on `hover: none`, focus-within, and open menu | `.ledger-row .ledger-reveal` |
| Defects card header link reads "open in the defects ledger" (was the feature title again) | `FeatureDefects.vue` |

## Acceptance items (as tests)

The tests live in `frontend/tests/feature-workbench.spec.js`. The names below are the intent; the spec titles are prose.

- `test_defects_row_shows_one_primary_action_not_five` — an open defect's row
  exposes exactly one un-nested primary button (Close); a closed one exposes
  exactly one (Reopen).
- `test_defects_row_secondary_actions_still_reachable` — Re-attribute, Create
  fix task, Link existing task, Merge are each still triggerable from the row
  (via the overflow menu under option 1/2), and each still performs the same
  POST it does today.
- `test_defects_row_overflow_hidden_when_no_secondary_action_applies` — a
  defect that already has a fix task linked does not offer "Create fix task"
  in the menu (parity with today's `v-if`).
- `test_sessions_anchor_still_resolves` — navigating to
  `/features/:id#feature-sessions` still scrolls the Sessions list into view.
- `test_tasks_defects_sessions_share_row_metrics` — row
  padding, border-radius and tag styling assert equal (or from one shared
  CSS class) across the three cards.
- `test_activity_tabs_show_counts` *(option 3 only)* — each tab label renders
  the count of its list (tasks / open+closed defects / sessions).

## Non-goals

Not touched by this spec: the write forms (report-defect, declare-session,
new-task), the figures card above these three, the spec/images cards, any
server route or response shape.

