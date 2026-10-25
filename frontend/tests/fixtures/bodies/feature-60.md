*Spec. Handed to `/feature-pipeline`, which owns the build.*

## What this is for

regin:63 made the feature grain reachable from the browser, but only through a detour: to build a feature you capture it, then capture a second task, name the second a run of the first, and run the second. Nobody does that. An operator captures an idea, arms it, presses Run — and that path leaves the task out of the feature population, unattributed, and permanently unshippable (the task holds runs, so it is a run and not a feature).

The missing act is one: **pressing Run on a top-level task is the declaration that it is a feature and this attempt builds it.** The click already says "build this"; counting the attempt as the feature's session is the same declaration read back, not an inference.

*Revised during the build.* The first cut minted a child task per click. That broke 47 tests and the dependency/finish flow, because every task became two rows. The shipped rule is smaller: a top-level task's **own** runs are its sessions, under its own key. Nothing is minted.

## What it refuses to know

* **It never infers which tasks are features.** A top-level task enters the population when it holds a run (`lane.py` self arm) — the Run click is the declaration. An idea nobody has run is not a feature yet.
* **It never hands a task two keys.** A top-level task linking a tracker issue counts under the issue, never under `regin:<id>` as well.
* **It never adds a second way to ship.** The done-claim stays on `claimed_at`; a top-level task holding runs is claimable, and its own run starting over the claim walks it back.
* **The API's default feed is unchanged.** The browser asks for roots; the server's default is still every task.

## Surface

**Run on a top-level task.** `POST /api/tasks/<id>/run` works as before; the attempt is now a session of `regin:<id>`. A settled task (`done`/`failed`) that still carries its params runs again without re-arming. Running a shipped task withdraws the claim and opens round 2.

**The prompt names its feature.** A `<feature>` block tells the agent which key its session counts under — its own for a top-level task, the parent's for a run of one — and how to declare a defect against that key. A linked task keeps its `<tracker>` block instead.

**Shipping.** A top-level task holding runs can be shipped; the ship control reads `claimed_at`, now serialized, rather than `status`.

**The list shows features, and their runs on demand.** The browser's default feed is `root_only=1`; every root row carries a child count (one query per page, never one per row) and expands to its runs. A "Show runs" toggle lifts the narrowing. Group counts follow the same `root_only`.

**The detail page can run.** Run/Stop/Interrupt controls appear beside Arm. A run's grain card links to the feature ledger; a feature's grain card links to its own ledger.

## Acceptance items

```
tests/tasks/test_feature_run.py — 16 items, incl.
test_running_a_root_task_puts_it_in_the_feature_population
test_a_second_attempt_is_a_second_session_of_the_same_feature
test_a_linked_root_task_counts_under_the_issue_not_its_own_key
test_a_run_of_a_feature_counts_under_the_parent_not_itself
test_the_prompt_names_the_tasks_own_feature_key
test_a_run_by_hand_under_a_feature_names_the_parents_key
test_a_root_task_that_holds_runs_can_be_shipped
test_running_a_shipped_task_walks_the_claim_back
test_a_settled_root_task_runs_again_without_re_arming
test_a_page_carries_run_and_child_counts_without_a_query_per_row
test_the_default_task_feed_is_still_unchanged

frontend/tests/feature-runs.spec.js — 5 items (e2e)
test_the_list_asks_for_roots_by_default_and_show_runs_lifts_it
test_a_feature_row_expands_to_its_runs
test_a_task_with_attempts_names_them_and_opens_its_history
test_the_detail_page_offers_run_on_a_feature
test_a_run_and_a_feature_both_link_to_the_ledger
```

## Known limits

* Every top-level task that was ever run is now in the feature index — including old one-shot chores. That is the rule applied to history, not a migration; archive what should not be listed.
* A top-level task holding runs still cannot take children: its sessions are its own attempts, and admitting both would count a feature two ways.
* Defects are still declared from the CLI (`regin defects open … --feature regin:<id>`); the prompt names the command, the browser does not offer it.
