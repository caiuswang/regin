*Spec. The first feature specced as a `regin:<id>` task rather than a Linear issue — the proof CAI-274 said the feature after it would be. Handed to `/feature-pipeline`, which owns the build.*

## What this is for

CAI-274 made regin's own tasks a tracker, so an install with no Linear credential can spec, build, ship and read a feature's ledger. It is reachable from the CLI and from the API. **It is not reachable from the browser**, and that makes the claim "fully functional rather than degraded" false for anyone who works in the dashboard.

The missing act is exactly one: **naming a task as a run of another**. Nothing in the SPA writes `parent_id`. Every other half of the grain already renders — a feature lists its runs, a run links back to its parent, the done-claim is a control — but the relationship those three surfaces display can only be created somewhere else.

The consequence is not cosmetic. A task enters the feature population *when something names it as parent*. So a feature created in the browser is a feature nobody can finish creating: it never appears in the index, its ledger counts zero sessions forever, and the operator has no way to tell that from a feature that genuinely cost nothing.

## What it refuses to know

* **It never infers a parent.** Not from a title prefix, not from a tag, not from two tasks being created close together. The grain is a declared relationship exactly like an attribution, and a picker is the declaration.
* **It never offers an act the server will refuse.** The three grain guards already exist and are tested; this feature makes the UI agree with them *before* the request, rather than rendering a control that always errors. A refusal that can only be discovered by trying is a control that lies.
* **It never lets the picker create a cycle or a third level.** The candidate list is the set the server would accept, computed by the same rule — top-level tasks, minus this task, minus any task holding runs.
* **It does not add a second way to ship.** The done-claim stays where CAI-274 put it.
* **It does not change the default feed.** `root_only` remains an opt-in narrowing; a run is a task like any other in the backlog.

## Surface

**A parent control on the edit sheet.** A picker listing the tasks that may legally be a parent, plus a clear-to-detach option. Writes `parent_id` through the PATCH that already accepts it — no new endpoint, no new validation, and the server's refusal remains the authority.

**A parent control on the composer,** so a run can be captured already attached rather than created and then moved. `POST /api/tasks` already accepts `parent_id`.

**An attach affordance on the detail page's Runs card,** because that is where an operator is standing when they realise a task belongs under this one. It opens the same picker against the other side of the relationship.

**The candidate list comes from the server.** The rule that decides who may be a parent is the same rule that refuses, and a copy of it in the client is a copy that drifts. Whether that is a query parameter on the feed or its own small read is the build's call; what it may not be is a filter the browser invents.

**Controls the grain forbids stop being offered.** `Arm` on a task that has runs of its own is the live instance: the server refuses it correctly today and the button is rendered anyway.

## Acceptance items

Names of tests that do not yet exist. The bar is these passing, not this prose.

```
test_the_edit_sheet_writes_parent_id_through_the_existing_patch
test_the_composer_can_capture_a_task_already_attached
test_the_runs_card_offers_an_attach_control
test_clearing_the_parent_detaches_the_run
test_the_candidate_list_excludes_the_task_itself
test_the_candidate_list_excludes_a_task_that_holds_runs
test_the_candidate_list_excludes_a_task_that_already_has_a_parent
test_the_candidate_list_is_computed_by_the_server_not_the_client
test_a_refused_parentage_renders_the_servers_own_sentence
test_arm_is_not_offered_on_a_task_that_has_runs
test_the_default_task_feed_is_still_unchanged
test_attaching_a_run_in_the_browser_puts_the_feature_in_the_index
test_a_feature_built_entirely_in_the_browser_reads_its_own_ledger
```

The last two are the point: they are the browser's version of the end-to-end claim CAI-274 proved on the CLI.

## Known limits

* **A picker is a list, and a backlog is long.** Whatever narrowing it gets — search, recency — is a way in, not a filter on what is legal, and the two must not be confused in the code.
* **Detaching the last run makes a feature vanish from the index** while its key still resolves. That is CAI-274's stated behaviour, inherited unchanged; this feature makes it reachable by mouse, which will make somebody meet it for the first time.
* **No bulk attach.** Selecting forty tasks and parenting them at once is a different act with a different failure mode, and nothing yet asks for it.

## How we will know it worked

An operator who has never opened a terminal can create a feature, attach a run to it, watch it appear in the feature index, ship it, and read its ledger — with no Linear credential anywhere. That is CAI-274's binary test, moved to the surface most people actually use.
