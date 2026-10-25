*Spec. Handed to* `/feature-pipeline`*, which owns the build. Designed by the wayfinder map [Feature ledger — which sessions built a feature, what they cost, what broke](<https://linear.app/caiuswang/issue/CAI-230>) across nine decision tickets; each is linked below rather than restated, and the ticket is the authority where this summary is thinner than the reasoning.*

## What this is for

regin records 2928 sessions costing $23,205 and a ledger of defects, and cannot answer *what did this feature cost, and what did it break*. The chain to answer it already exists end to end and is entirely unused — `AgentTaskRun` carries `trace_id` and `task_id`, `agent_task_links` maps `task_id → (provider, external_key)` — but nothing traverses it, and it only ever reaches task-launched sessions: **13 of 2928 (0.4%)**.

This feature makes a session's feature **declarable**, rolls the declarations up into cost and defect figures that state their own gaps, and surfaces both through one new command. It exists to serve exactly two statistics — **cost-per-feature** and **defects-per-feature**. Anything else is nice-to-have and must not shape the schema.

## Vocabulary

| Term | Meaning |
| -- | -- |
| **feature** | a Linear issue. `feature_key` is its identifier, stable from the first day of the spec. regin holds a read-through cache, never a copy. |
| **session** | a distinct trace. A run and the conversation it drove are two trace ids for one piece of work and collapse to one via `agent_run_sessions`. |
| **round** | `1 + completions the tracker recorded and later walked back`. Tracker-declared, always. |
| `Defect.round` | a *different* number: fix attempts on one defect. Never aggregated to a feature. |
| **escape** | a defect blamed on a feature that had already shipped. |
| **declared / recalled** | provenance of an attribution. `recalled` = the attributing trace is not the trace being attributed. |
| **gap** | a count of sessions we could not measure. Never a substituted zero. |
| **coverage** | declared sessions and dollars, over all sessions and dollars. |

## What it refuses to know

The refusals are the design. Read this section before the surface.

* **It never infers an attribution.** No `cwd` heuristic, no branch-name convention, no top-N-by-cost sweep. Unattributed is a first-class state and stays the *absence* of a row. The accepted cost is that the pipeline's own rounds-to-done experiment has **no historical baseline** — 2915 sessions stay unattributed forever unless a human recalls them one at a time ([CAI-239](<https://linear.app/caiuswang/issue/CAI-239>)).
* **It never claims a precise cost.** Attribution binds a *whole* session, so a feature's cost is labelled an **upper bound**, permanently ([CAI-234](<https://linear.app/caiuswang/issue/CAI-234>)).
* **It never compares two clocks.** The escape reading was built specifically to avoid comparing `store._now()` on regin's host against Linear's `stateHistory.startedAt`; both operands are declared at a single instant instead ([CAI-233](<https://linear.app/caiuswang/issue/CAI-233>)).
* **It never substitutes a zero for a gap.** `no-usage-captured`, `model-unpriced`, `zero-duration`, `traceless-runs` and `state-unknown` are counted and printed. A feature with nothing declared reads `none declared`, never `0` or `$0.00`.
* **It never lets the board flap.** Filing a defect never reopens the parent issue. Post-ship rework is read from the defects ledger.
* **It never syncs regin's defect status to a tracker item, in either direction.** `defects.status` asks *is this bug still real*; a tracker item asks *is this work still scheduled*. Divergence is surfaced, never repaired ([CAI-232](<https://linear.app/caiuswang/issue/CAI-232>)).
* **It never propagates a session's declared feature onto a defect.** `defects.feature_key` names the feature that **caused** the bug, not the one the reporting session was working on ([CAI-238](<https://linear.app/caiuswang/issue/CAI-238>)).
* **It never reports "in sync" or a stage it could not read.** `unknown` is distinct from "we never looked", and is never a stage name.

## Surface

**New**

* `regin feature attribute <KEY> --session $SID [--primary]` — writes the declaration. Runnable long after the session ended. Leaves a `feature.attributed` span.
* `regin feature ledger <KEY> [--json]` — the human report: stage line first, then cost, the session list, defects, escapes, coverage.
* `regin defects reopen <id>` — increments `Defect.round`, clears `missing_check`.
* `regin defects merge <dup> --into <keeper>` — deletes the duplicate, unions its suspect paths.
* `regin defects fix-item <id> --issue <KEY> | --create` — points a defect at the tracker item that will close it.

**Changed**

* `regin feature stage <KEY>` — unchanged shape, gains exactly one field, `escapes`. Still exits 2 with no tracker.
* `regin defects open` / `regin defects attribute <id>` — accept `--feature @session` / `@session`.
* `regin defects list --feature` — loses its trailing `rounds-to-done for <KEY>` line.

**Schema**

* `session_features` — `(trace_id, feature_key)`, `is_primary`, `provenance ∈ {declared, task-link, recalled}`, `round` (NULL on `recalled`). Mirrors `session_repos`.
* `defects` — `fix_provider`, `fix_external_key` (nullable, indexed for reverse lookup); `blamed_against_state` (write-once, raw `state_category`).
* partial unique index on `defects` `WHERE feature_key IS NULL`, keyed on `(session_id, symptom)`.

Schema work is **two edits plus the model** — an `alembic/versions/00NN_*.py` revision *and* the matching `db/schema.sql` change, or fresh and migrated installs diverge.

## Acceptance items

Names of tests that do not yet exist. The bar is these passing, not this prose.

### Attribution — [CAI-231](<https://linear.app/caiuswang/issue/CAI-231>), [CAI-235](<https://linear.app/caiuswang/issue/CAI-235>)

```
test_a_run_and_the_conversation_it_drove_count_as_one_session
test_every_run_that_produced_a_trace_counts_whatever_its_status
test_a_traceless_run_is_counted_separately_rather_than_dropped
test_a_task_linked_to_two_issues_is_refused
test_attribute_writes_a_session_features_row_and_leaves_a_span
test_attribute_works_long_after_the_session_ended
test_a_session_may_declare_two_features
test_is_primary_is_first_writer_wins_until_primary_overrides
test_an_unattributed_session_is_the_absence_of_a_row
test_feature_stage_is_not_a_declaration
test_the_round_is_snapshotted_on_a_declared_row
```

### Recalled attribution — [CAI-239](<https://linear.app/caiuswang/issue/CAI-239>)

```
test_attributing_a_trace_that_is_not_the_attributing_trace_is_recalled
test_provenance_is_decided_by_identity_never_by_elapsed_time
test_a_recalled_row_has_a_null_round
test_recalled_rows_are_counted_and_labelled_never_filtered
test_there_is_no_flag_to_exclude_recalled_rows
```

### Cost — [CAI-234](<https://linear.app/caiuswang/issue/CAI-234>)

```
test_a_features_cost_is_the_live_sum_of_its_declared_sessions
test_cost_is_read_live_and_moves_when_a_session_reprices
test_cost_is_labelled_an_upper_bound_wherever_it_is_printed
test_a_shared_session_charges_the_primary_in_full_and_the_secondary_zero
test_a_session_with_no_usage_captured_is_a_counted_gap_not_a_zero
test_a_model_with_no_price_is_a_counted_gap_not_a_zero
test_zero_duration_is_counted_separately_from_the_cost_gaps
test_tokens_are_a_breakdown_and_are_never_summed
test_a_feature_with_no_declared_sessions_reads_none_declared
test_coverage_is_reported_wherever_the_ledger_is_read
```

### Defect → fix item — [CAI-232](<https://linear.app/caiuswang/issue/CAI-232>)

```
test_the_fix_item_pointer_is_set_by_an_explicit_verb_not_by_defects_open
test_a_defect_has_at_most_one_fix_item
test_one_fix_item_may_serve_many_defects
test_closing_a_defect_never_touches_the_fix_item
test_closing_the_fix_item_never_closes_the_defect
test_with_fix_state_reads_unknown_when_no_tracker_is_reachable
test_with_fix_state_never_reports_in_sync
test_create_refuses_on_an_unattri