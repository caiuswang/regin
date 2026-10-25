*Spec. Handed to* `/feature-pipeline`*, which owns the build. Designed by the wayfinder map [Feature ledger without Linear — regin's own tasks as the tracker](<https://linear.app/caiuswang/issue/CAI-260>) across twelve decision tickets; each is linked below rather than restated, and the ticket is the authority wherever this summary is thinner than the reasoning.*

## What this is for

Linear's free tier caps issue count. When it is reached, regin stops being able to declare a feature — and the feature ledger, which exists to answer *what did this feature cost and what escaped*, stops working for everything new. This feature removes that dependency: **regin's own** `agent_tasks` **become a tracker behind the existing** `IssueTrackerProvider` **seam**, so an install with no Linear credential is fully functional rather than degraded.

Linear stays a registered, working provider and every existing `CAI-*` key keeps resolving — **29 distinct keys / 44 rows**, measured. What ends is the assumption that a feature *is* a Linear issue.

This revises the completed map [Feature ledger — which sessions built a feature, what they cost, what broke](<https://linear.app/caiuswang/issue/CAI-230>). Its constraints 3-8 are inherited unchanged. Two are revised:

* **Constraint 1** — "the Linear issue is the feature" → **the tracker issue is the feature, and the tracker may be Linear or regin itself.** `feature_key` remains the identifier and remains stable from the first day of the spec.
* **Constraint 2** — "regin tasks are N:1 against a feature; a task is a run" → **repealed as stated, preserved as a hierarchy: a parent task is a feature, its child tasks are runs.** The two grains stay distinct; they stop needing two entities to express it.

## Vocabulary

| Term | Meaning |
| -- | -- |
| **feature** | a tracker issue — a Linear issue, or a top-level regin task. `feature_key` is its identifier, stable from the first day of the spec, and **never rewritten**. |
| **native ref / key** | `regin:<task row id>` — e.g. `regin:42`. Slash-free, lowercase scheme, integer id. |
| **parent task** | a task with `parent_id IS NULL` that something names as parent. The feature grain. Holds no runs, ever. |
| **child task** | a task with a `parent_id`. The run grain. Holds runs; can never be a parent. |
| **round** | `1 + completions walked back`. Tracker-declared, always — natively a recorded column, never a derivation. |
| **completion walked back** | writing `running` over a claimed `done` on a parent, i.e. work restarting after a ship claim. The one incrementing edge. |
| **filing correction** | the three other exits from `done` (`→ idea`, `→ failed`, `→ archived`). Nothing was built, so nothing was walked back. |
| **done-claim / ship** | an explicit operator transition to `done` on a parent task. The native analogue of moving a Linear issue to Done. |
| **lane** | the SQL join from a feature key to the sessions that built it. One expression, five call sites. |
| **live** | a provider-declared capability (`stage_is_live`) — the cached stage cannot lag the fact, so there is nothing to refresh. |

## What it refuses to know

The refusals are the design. Read this section before the surface.

* **It never migrates a feature between trackers.** `feature_key` is identity and is never rewritten — no verb, no button, no alias, no second link, both directions. An operator who wants the other tracker declares a fresh feature; the old ledger stays intact. A rewrite would lie about provenance three unrepairable ways: write-once `defects.blamed_against_state` re-attributed to a task that never held that state, branch names rooted on the old key in unrewritable git history, and an orphaned `issue_cache` row ([CAI-271](<https://linear.app/caiuswang/issue/CAI-271>)).
* **It never lets one task hold two feature keys.** All three doors refuse **at the act, not at read** — attach on a task that already holds a link, attach of a native ref at all, and setting a child's `parent_id` onto a linked task.
* **It never branches on** `provider_id` **outside a provider module.** Every provider distinction is a declared capability or a registry lookup: `stage_is_live`, `is_configured()`, `KEY_SQL`/`key_for_task`. The one live violation — `stage_source.DEFAULT_PROVIDER = "linear"` — is deleted.
* **It never infers the done-claim from a child run settling.** A run settling `done` means *this session finished*, never *this feature shipped*. Deriving it would flip a feature to `shipped` when its first build session ended and turn every later defect into an escape, making `/feature-pipeline`'s "a defect before the done-claim costs no round" unreachable ([CAI-273](<https://linear.app/caiuswang/issue/CAI-273>)).
* **It never infers** `rounds` **from run timestamps.** The one live re-arm in the database is readable only from timestamps, and regin records no declared act behind it. `rounds` is a recorded event or it is `1` ([CAI-266](<https://linear.app/caiuswang/issue/CAI-266>)).
* **It never admits a task to the feature population by a filter.** The fourth union arm reads `agent_tasks.parent_id` — a row that exists only because an operator named a key — exactly like the three arms beside it ([CAI-267](<https://linear.app/caiuswang/issue/CAI-267>)).
* **It never puts a network call on a native path.** Every native read is `SessionLocal()` + `session.get(AgentTask, id)`. A fetch on a write path made the suite non-hermetic once; native paths are local by construction.
* **It never says "the tracker did not answer" about a local read.** `regin:999` raises *no such task*. A local read cannot be unreachable, so `retryable` is never `True` on this provider and `NotConfigured` is never raised defensively.
* **It never truncates a body silently.** Both body bounds **refuse**. The cache clamp stops truncating under 64,000.
* **It never gives the native provider a settings flag.** `is_configured()` is unconditionally `True`: a local provider holds no credential, and a switch able to turn it off would let an operator break constraint 1.
* **It never derives a parent's status at read time.** Child events write it, recomputed from the children, never bumped — with two status-first guards (`archived`, `done`).
* **It never reads** `1` **as "we could not look".** A native `rounds` of `1` means built once, never walked back. A local read cannot fail the way that fear describes.

## Surface

### The provider

A new module in `lib/tasks/integrations/`, registered with one `register_provider` line.

* `provider_id = "regin"`; `stage_is_live = True`; `is_configured()` → `True`, unconditionally, with no settings field.
* `parse_ref` claims `regin:<digits>` and any absolute `http(s)` URL whose path is `/tasks/<digits>`, **ignoring the host**. `Regin:42`, `regin:042` and `…/tasks/042` all canonicalize to `regin:42`. `regin:0`, a signed number and a bare `42` are **not claimed**.
* `ref_hint` — `"a regin task ref like regin:42, or the /tasks/42 URL"`.
* Exports `KEY_SQL` (a SQL expression) and `key_for_task(id)` so the ref format lives in one file in both languages; `lib/features` imports them.
* `fetch_issue` reads `AgentTask` directly, not through `lib/tasks/store`.
* Appears in `provider_rows()` as an always-on row contributing **no settings fields**.

`base.IssueTrackerProvider` gains `stage_is_live: bool = False`. Linear's `_KEY_RE` is **deliberately left untouched** — the claim sets are disjoint (a colon defeats its `fullmatch`), so `AmbiguousRef` between the two is unreachable by construction rather than merely unlikely.

**Status →** `state_category`**:**

| `agent_tasks.status` | `state_category` | stage |
| -- | -- | -- |
| `idea` | `backlog` | `spec` |
| `armed` | `unstarted` | `spec` |
| `queued` | `unstarted` | `spec` |
| `running` | `started` | `building` |
| `done` | `completed` | `shipped` / `escaped` |
| `failed` | `started` | `building` |
| `archived` | `canceled` | `spec` |

`idea` → `backlog` because arming is the commitment, and an idea is not committed. `failed` → `started` because a feature whose every child failed is being built, not specified-not-started. `archived` → `canceled`, not `unknown`: `unknown` is reserved for a vocabulary we cannot map, and `archived` is one we know exactly.

`ExternalIssue` **fields:** `title` ← `title`; `description` ← `body` through `clamp_description`; `priority` ← `priority` (pass-through); `labels` ← `tags_json`; `url` ← the relative `/tasks/<id>`; `assignee` ← `users.display_name` for `agent_tasks.assignee`, `None` if unresolvable; `extra` ← `{"Project": <repo name>}` only; `rounds` ← `rounds.from_task(task)`.

**Errors:** a missing id raises `IntegrationError(f"regin has no task {id}.", retryable=False)`.

### The grain

`agent_tasks` gains `parent_id` and `assignee`, both nullable and **without a foreign key** — the provenance-label discipline `created_by` and `project_id` already state. Parentage is *not* on `agent_task_deps`: "waits for" and "is a run of" are two axes.

**Depth is exactly two**, enforced at write time — a task may take a parent only if that parent's own `parent_id IS NULL`. This makes acyclicity free: a cycle needs a child that is also a parent, and the rule forbids one.

**Three symmetric guards keep the grains disjoint:** `arm_task` refuses on a task with children; child attachment refuses on a task that has runs; a dep edge between a parent and its own child is refused as `DepRejected`.

**Surfaces:** `parent_id` is settable at capture *and* edit (a structural claim, not an execution parameter). `assignee` is edit-only — claiming is an act by an authenticated human about themselves. `query.py` gains `parent_id` and `root_only`; **the default feed is unchanged**.

### The parent's status

Written by child events — run start, run settle, child attach, child detach — recomputed in the same transaction, **never bumped**. The recompute reads the parent's status **first**:

| Parent reads | Behaviour |
| -- | -- |
| `archived` | never written — terminal against all triggers |
| `done` | written by **exactly one edge — a child run starting** → `running`, incrementing `completions_walked_back`. Run settle, child attach and child detach all no-op. |
| anything else | recomputed from the table below |

| Children | Parent `status` |
| -- | -- |
| none has ever run (including no children) | `idea` |
| any child run open (`starting`/`running`) | `running` |
| all settled, ≥1 `done` | `running` — built, awaiting the claim |
| all settled, none `done` | `failed` |

**A child that has never run is not settled**, so a parent with unstarted work stays out of `running`-by-completion and never reads as shipped. Row 3 writes `running` rather than no-opping, because attaching an already-run child to a fresh parent is the common adoption path — 48 of 51 live tasks are run-bearing — and a no-op would strand such a parent at `idea` → stage `spec` with finished work hanging off it. `running` on a parent is not a claim about a live process: a parent is never armed and holds no runs.

### The done-claim

`TASK_TRANSITIONS` gains four entries: `idea → done`, `running → done`, `failed → done` (the claim) and `done → idea` (the un-claim, which costs no round). Not from `archived` — restore to `idea` first.

**Grain guard:** the claim is refused when the task has a `parent_id` or any `agent_task_runs` row, raising `TransitionRejected` naming the grain. **A childless task is claimable** — that is how the spec-only feature ships.

**Reach:** `regin task ship <id>`, writing the DB **directly** through `store.py` like `add`/`delete`, not over HTTP — a done-claim releases no dependents, so it must keep working with the server stopped. The un-claim is PATCH/SPA-only. It cannot live on `regin feature`: a `feature ship` that worked only for `regin:*` would be a caller branching on provider.

**A claim racing a concurrent child run-start** (left open by [CAI-273](<https://linear.app/caiuswang/issue/CAI-273>)): both writes touch the parent row, so the transaction boundary decides and **no additional rule is needed** — both orderings are already defined and neither loses a fact. Claim-then-run-start walks the claim back and counts a round, which is exactly what a restart after a ship claim is. Run-start-then-claim leaves the claim standing, and the later run settle no-ops under the `done` guard.

### `rounds`

`agent_tasks.completions_walked_back`, `INTEGER NOT NULL DEFAULT 0`, incremented by the recompute on its one edge. `rounds.from_task(task: AgentTask) -> int` returns `1 + task.completions_walked_back` — it takes the **row**, so it adds no query and `rounds.py` keeps importing nothing project-local. `rounds` **itself is never stored**, so the count and the reported number cannot drift.

**The counter is never decremented and never reset** — the precedent is `escapes`, which is all-time. Stated cost: this is the one number on a native feature that a hard `DELETE` of rows cannot bring back into agreement with them.

`status` recomputes while this bumps because the things differ: a status is a projection of current state, a walked-back completion is an event and is not recoverable from current state.

### The population and the lane

The index union gains a fourth arm:

```sql
UNION SELECT DISTINCT <KEY_SQL> FROM agent_tasks WHERE parent_id IS NOT NULL
```

A task enters the population **when something names it as parent** — not when it has merely run. Measured consequence: **+0 keys today** (population stays at 29); it grows only as the grain is adopted.

**The lane is copy-pasted in five sites and moves into a new** `lib/features/lane.py`**:** `index._LANE`, `cost.coverage`, and `attribution._task_link_traces` / `_lane_features_for` / `traceless_runs`. All five gain the parent hop — three of them are the ledger page, so without that a native feature would show sessions and cost in the index and **zero** on its own page. The native lane is `agent_task_runs → agent_tasks (child) → parent`, unioned with the link shape.

**Extraction forces one collapse rule:** `LIMIT 1` **everywhere.** Sites 1-2 `LEFT JOIN` and fan out; site 3 uses `canonical_trace()`. They already disagree by one session today (`sdk-30da717d448f` has two `agent_run_sessions` rows). A run driving two conversations is an ingestion artefact, not a fact worth preserving. **This changes a live figure.**

`issue_cache` for a native key is written by the child-event recompute through the generic `resolve` → `fetch_issue` path — **no new trigger, no provider branch** — plus a fifth trigger, **parent edit**, which lives in `lib/tasks/store.py` **on the write, not the route** (all eight existing `remember` call sites are fetch paths, and `regin task ship` bypasses HTTP entirely). The `remember` is **best-effort and cannot fail the status write**.

`synced_at` **leaves** the index `_ORDER` activity expression: a native key's cache row is rewritten on every child event while a Linear key's moves only when a human clicks Refresh, which would quietly make the index "native features, then everything else."

`AmbiguousTaskLink` is **extended** to fire on a task that is a parent *and* holds a link (0 such tasks today), and the guard moves into `lane.py` — `index._LANE` has none today, so a two-link task makes the index double-count the sessions the ledger refuses to count at all.

### Comments

New generic tables with a polymorphic subject; `proposal_feedback_*` is **left untouched**. Code lives in a new `lib/comments/`.

```sql
CREATE TABLE comment_threads (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind     TEXT NOT NULL,
    subject_id       TEXT NOT NULL,
    resolution_state TEXT NOT NULL DEFAULT 'open',
    created_by       INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX ix_comment_threads_subject ON comment_threads(subject_kind, subject_id);

CREATE TABLE comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL REFERENCES comment_threads(id) ON DELETE CASCADE,
    author_kind TEXT NOT NULL DEFAULT 'user',
    author_id   INTEGER,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX ix_comments_thread ON comments(thread_id);
```

`subject_id` is `TEXT` because a subject id is opaque to this table. **No FK on the subject** — a task's delete path deletes its threads; `comments.thread_id` cascades. `resolution_state` holds exactly `open` / `resolved`, meaning only *has this conversation been dealt with*; whether the ticket is closed is the task's own `status`. No anchor columns. Flat threads. A comment carries `author_kind` **and** `author_id`; `author_id` is null for an agent comment.

**API:** `GET`/`POST /api/tasks/<id>/comments` for threads and comments, `PATCH` on a thread for `resolution_state`. **No MCP surface and no second** `subject_kind` — the subject is widened exactly as far as tasks need.

### CLI

**New:** `regin task ship <id>` — the done-claim, direct DB write, works with the server stopped.

**Unchanged in shape, now resolving natively:** `regin feature stage <KEY>`, `regin feature ledger <KEY>`, `regin defects fix-item --issue regin:42`. `feature stage`'s exit-code-2 policy needs no native branch.

**Not added:** `regin feature ship` (constraint 2), and no migration verb of any kind.

### API and SPA

* `GET /api/features/<key>` returns `stage_live` beside `stage_synced_at`, resolved by registry lookup.
* `POST /api/features/<key>/stage/refresh` stays **uniform** — not gated, not 409'd, not branched. Only the *control* disappears. `features.py`'s editor gate survives on its second reason (it writes a shared cache); the "spends someone else's API quota" half becomes false for a native key.
* `FeatureStageLine`: `live` (with a small green dot) replaces the `as of …` slot; `canRefresh` becomes `canWrite && !stage_live`.
* `links.serialize()` gains `stage_live`. `TaskLinkCard` renders `live` instead of `synced …`, drops Refresh, and renders a **relative** url as a `router-link` with no external-link glyph — keyed on the **url's shape**, provider-agnostic, needing no field.
* **The index is untouched** and `stage_live` stays out of its payload: `FeaturesView.vue` renders no freshness and no Refresh at all, and the key shape already tells the two apart.
* `TaskDetailView`: the status `Badge` becomes an editable control on a top-level task (Ship / Un-claim) and stays read-only otherwise; a parent lists its children as runs, a child links back to its parent; a threads panel renders the task's comments.
* **Deleted as unreachable:** the two "No issue tracker is configured" branches in `TaskLinkField.vue:119` and `TaskExternalLinks.vue:70`.

### Schema

Three edits each — a new `alembic/versions/00NN_*.py` revision, the matching `db/schema.sql` change, and the SQLModel field — or fresh and migrated installs diverge.

* `agent_tasks.parent_id` — `INTEGER` NULL, indexed, no FK.
* `agent_tasks.assignee` — `INTEGER` NULL, no FK.
* `agent_tasks.completions_walked_back` — `INTEGER NOT NULL DEFAULT 0`. NOT NULL is safe **because** of the default; nullable was rejected since NULL would mean "unknown".
* `comment_threads` + `comments` — new tables, so no NOT NULL-ALTER hazard and no data migration.
* `session_features` and `defects` need **no schema change** — verified: both `feature_key` columns are bare `TEXT`, and [CAI-240](https://linear.app/caiuswang/issue/CAI-240/deduplicating-unattributed-defects-within-a-session)'s partial unique index is `WHERE feature_key IS NULL`, which a native key never touches.

**No backfill anywhere.** Per the `rounds` decision there is deliberately nothing to backfill from.

### Constants (no schema change — all three are Python)

| Constant | Site | Was | Becomes |
| -- | -- | -- | -- |
| `CAPTURE_BODY_MAX` | capture — third-party intake | `BODY_MAX = 8000` | `8000`, still refusing |
| `BODY_MAX` | the authenticated edit path | `8000` | `64_000`, refusing |
| `DESCRIPTION_MAX` | `clamp_description` before `issue_cache` | `8000`, silently truncating | `64_000` |

**64,000, not 32,000:** the largest real artifact is 29,488 characters (a 92% fill of 32,000) and three of the five over-8000 issues are wayfinder maps, so the re-homing work inherits this number. This spec is itself 34,718 characters — over 32,000 before a word was written about it.

## Acceptance items

Names of tests that do not yet exist, grouped by the ticket that decided each. The bar is these passing, not this prose.

### The native ref — [CAI-261](<https://linear.app/caiuswang/issue/CAI-261>)

```
test_a_native_ref_is_regin_colon_task_id_and_is_slash_free
test_linear_does_not_claim_a_native_ref
test_native_does_not_claim_a_bare_number
test_a_pasted_tasks_url_is_claimed_whatever_the_host
test_regin_042_canonicalizes_to_regin_42
test_regin_0_and_a_signed_number_are_not_claimed
test_the_url_of_a_native_issue_is_the_relative_tasks_path
test_a_native_key_and_a_linear_key_can_never_collide
test_the_features_route_resolves_a_native_key_unencoded_and_encoded
```

### The grain — [CAI-263](<https://linear.app/caiuswang/issue/CAI-263>)

```
test_a_task_may_take_a_parent_only_if_that_parent_is_top_level
test_arming_a_task_that_has_children_is_refused
test_attaching_a_child_to_a_task_that_has_runs_is_refused
test_a_dep_edge_between_a_parent_and_its_own_child_is_refused
test_parent_id_is_settable_at_capture_and_at_edit
test_assignee_is_settable_at_edit_and_never_at_capture
test_an_unresolvable_assignee_serializes_as_null
test_root_only_and_parent_id_filter_the_feed
test_the_default_task_feed_is_unchanged
test_a_child_run_starting_writes_the_parent_running
test_the_parent_status_is_recomputed_from_its_children_never_bumped
test_child_attach_and_detach_recompute_the_parent
test_archived_is_terminal_against_every_child_event
test_a_child_that_has_never_run_is_not_settled
```

### The provider — [CAI-265](<https://linear.app/caiuswang/issue/CAI-265>)

```
test_the_native_provider_is_configured_without_any_setting
test_there_is_no_native_enabled_flag
test_each_task_status_maps_to_its_state_category
test_a_failed_task_reads_started_not_unstarted
test_an_archived_task_reads_canceled_not_unknown
test_an_idea_reads_backlog_and_an_armed_task_reads_unstarted
test_the_native_assignee_resolves_to_a_display_name
test_an_unresolvable_assignee_yields_none_not_a_placeholder
test_extra_carries_only_the_project_name
test_a_missing_task_id_is_a_non_retryable_integration_error
test_the_native_provider_never_reports_retryable
test_the_native_provider_reads_the_task_row_not_the_api_projection
test_stage_source_has_no_default_provider
test_the_no_tracker_configured_message_branch_is_gone
test_the_no_tracker_fixture_also_drops_the_native_builder
```

### The population and the lane — [CAI-267](<https://linear.app/caiuswang/issue/CAI-267>)

```
test_a_task_enters_the_population_when_something_names_it_as_parent
test_a_task_that_has_merely_run_is_not_in_the_population
test_every_root_task_is_not_swept_into_the_population
test_the_population_is_unchanged_on_todays_database
test_all_five_lane_sites_read_one_expression
test_the_index_and_the_feature_page_count_the_same_sessions_for_one_key
test_a_trace_with_two_conversations_counts_once_everywhere
test_a_native_features_sessions_are_its_childrens_runs
test_traceless_runs_are_reported_for_a_native_feature
test_coverage_reads_the_shared_lane
test_a_child_event_writes_the_native_keys_issue_cache_row
test_the_cache_write_goes_through_resolve_and_fetch_issue
test_synced_at_no_longer_feeds_the_index_ordering
test_a_parent_that_also_holds_a_link_is_ambiguous
test_detaching_the_last_child_removes_the_key_and_its_cache_row
test_a_feature_declared_before_its_first_child_is_invisible
test_the_index_page_stays_one_statement
```

### Comments — [CAI-264](<https://linear.app/caiuswang/issue/CAI-264>)

```
test_a_comment_thread_names_its_subject_by_kind_and_id
test_deleting_a_task_deletes_its_threads
test_deleting_a_thread_deletes_its_comments
test_resolution_state_holds_only_open_and_resolved
test_a_tasks_resolution_is_a_comment_not_a_resolution_state
test_a_comment_carries_an_author_kind_and_an_author_id
test_an_agent_comment_has_a_null_author_id
test_threads_are_flat
test_a_thread_has_no_anchor_columns
test_the_proposal_feedback_tables_are_untouched
```

### `rounds` — [CAI-266](<https://linear.app/caiuswang/issue/CAI-266>)

```
test_writing_running_over_done_increments_completions_walked_back
test_done_to_idea_counts_no_round
test_done_to_failed_counts_no_round
test_done_to_archived_counts_no_round
test_rounds_from_task_is_one_plus_the_column
test_rounds_is_never_stored
test_rounds_from_task_takes_the_row_and_issues_no_query
test_a_non_parent_task_reads_rounds_one
test_a_never_run_parent_reads_rounds_one
test_the_counter_is_never_decremented_or_reset
test_a_child_event_on_an_archived_parent_mints_no_round
test_a_missing_native_task_says_no_such_task_not_the_tracker_did_not_answer
test_feature_stage_still_exits_two_for_an_unclaimable_ref
```

### The live stage — [CAI-269](<https://linear.app/caiuswang/issue/CAI-269>)

```
test_the_native_provider_declares_stage_is_live
test_a_provider_that_does_not_declare_it_is_not_live
test_stage_live_is_a_registry_lookup_not_a_provider_branch
test_a_live_features_payload_carries_stage_live_true
test_a_live_features_header_renders_live_and_no_refresh_control
test_a_linear_features_header_is_unchanged
test_editing_a_parents_title_updates_the_header_without_a_child_event
test_the_parent_edit_cache_write_happens_in_the_store_not_the_route
test_the_refresh_endpoint_is_uniform_across_providers
test_a_relative_link_url_renders_as_a_router_link_with_no_external_glyph
test_stage_live_is_absent_from_the_index_payload
```

### No migration between trackers — [CAI-271](<https://linear.app/caiuswang/issue/CAI-271>)

```
test_there_is_no_verb_to_move_a_feature_between_trackers
test_attach_refuses_on_a_task_that_already_holds_a_link
test_attach_refuses_a_native_ref_outright
test_attach_refuses_on_a_task_that_already_has_children
test_setting_a_parent_id_refuses_when_that_parent_holds_a_link
test_preview_refuses_exactly_as_attach_does
test_a_task_grain_error_is_an_integration_error_and_maps_to_400
test_the_registry_still_claims_a_native_ref_for_features_and_fix_item
test_the_extracted_lane_carries_the_ambiguity_guard
```

### Where the spec lives — [CAI-272](<https://linear.app/caiuswang/issue/CAI-272>)

```
test_capture_refuses_a_body_over_the_capture_bound
test_the_edit_path_accepts_a_body_up_to_sixty_four_thousand
test_neither_body_bound_truncates
test_clamp_description_does_not_truncate_under_sixty_four_thousand
test_a_top_level_spec_task_mints_its_key_on_creation
test_a_spec_task_reads_stage_spec_before_any_session_exists
test_feature_stage_resolves_a_spec_task_that_is_in_no_population_arm
```

### The done-claim — [CAI-273](<https://linear.app/caiuswang/issue/CAI-273>)

```
test_a_child_run_settling_done_never_ships_the_feature
test_all_settled_with_one_done_reads_running
test_an_explicit_transition_to_done_is_the_ship_claim
test_a_childless_task_is_claimable
test_the_claim_is_refused_on_a_task_with_a_parent_id
test_the_claim_is_refused_on_a_task_that_holds_runs
test_the_claim_is_refused_from_archived
test_done_to_idea_un_claims_and_costs_no_round
test_a_child_run_starting_over_a_claimed_done_walks_the_claim_back
test_child_attach_and_detach_no_op_over_a_claimed_done
test_a_run_settling_over_a_claimed_done_no_ops
test_regin_task_ship_works_with_the_server_stopped
test_there_is_no_feature_ship_verb
test_a_claim_writes_the_issue_cache_from_the_store
test_a_failed_cache_write_never_fails_the_status_write
test_a_defect_filed_before_the_claim_is_not_an_escape
```

## The documentation set

From the inventory ([CAI-268](<https://linear.app/caiuswang/issue/CAI-268>)). 200 `Linear` hits across 48 files, but only **\~34 prose sites in production source and docs** are real: 118 are test fixture data, 33 the adapter itself, 15 the unrelated CSS/maths word. Four edits, in descending size:

1. `docs/agents/issue-tracker.md` **— rewrite.** Lines 3 and 11 (the binding, and "Linear is the only registered provider today") and the whole **Wayfinding operations** table. This is **the single file holding every process convention's tracker binding**, and it is where three of this map's decisions each put one line: the refusal to migrate, the spec-task convention, and the native done-claim.
2. **A vocabulary pass** replacing *third-party / foreign / external / remote* across 10 source files + `db/schema.sql:1028` — `base.py:1,7,80`, `integrations/__init__.py:1`, `settings.py:911,1303`, `orm/models/tasks.py:236`, `tasks/runs.py:274`, `web/blueprints/tasks.py:554,609`, `useTaskLinks.js:1,3`, `TaskLinkCard.vue:2`, `TaskExternalLinks.vue:83`.
3. **An error-copy pass** across the seven *"the tracker did not answer"* sites, splitting *unreachable* from *no such issue*: `features.py:138`, `ledger.py:202`, `FeatureLedgerView.vue:58`, `FeatureStageLine.vue:48,95`, `defects/tracker.py:5,24`, `cli/commands/feature.py:5-7`, `cli/commands/defects.py:159-161`.
4. **Deletions:** `router.js:61-62` (a what-comment asserting a feature *is* a Linear issue — deleted, not reworded) and the two "No issue tracker is configured" UI branches.

Also: `lib/features/stage_source.py:24-28` and `lib/features/index.py:4-8` ("three-table union" → four arms); `docs/feature-pipeline.md:250` ("reading the history needs tracker access") is that file's only entry; `docs/task-management-scale.md:76` needs one clause, not a rewrite.

**Immutable — must not be reworded:** `alembic/versions/0021_task_external_links.py`, `docs/agent-task-capture-design.md:224`, and the three test module docstrings that explain why *that* test mocks Linear.

**Outside the tree:** `~/.claude/skills/feature-pipeline/` is not in this repo and names no tracker; `/feature-pipeline` needs no rewording, since step 5 already says "moving the issue to Done". `CLAUDE.md` and `AGENTS.md` contain zero mentions of Linear, so the verbatim-mirror rule does not bite here.

## Corrections that land with this

1. **The index and the ledger page already disagree about one trace.** `sdk-30da717d448f` has two `agent_run_sessions` rows, so `index._LANE` counts 51 where `attribution` counts 50. `LIMIT 1` everywhere **changes a live figure** — that belongs in a named test, not in a silent correction.
2. **A live Linear defect in** `issue_cache`**.** Two of its 24 rows are clipped to exactly 8,000 — [CAI-242](https://linear.app/caiuswang/issue/CAI-242/feature-ledger-the-sessions-that-built-a-feature-what-they-cost-and) (14,046) and [CAI-257](https://linear.app/caiuswang/issue/CAI-257/feature-ledger-in-the-browser-the-index-the-ledger-page-and-declaring) (16,726). The clamp rise repairs it. **Prophylactic, not a visible fix**: verified that nothing renders `description` (absent from `FeatureLedgerView.vue`, `FeaturesView.vue`, `features.py`, `ledger.py`).
3. `stage_source.DEFAULT_PROVIDER = "linear"` — the map's one live constraint-2 violation. Deleting the fallback is behaviour-preserving: it fires only when `providers_for()` is empty, in which case the subsequent lookup also finds nothing.
4. **Two tests change with the dead branch:** `test_task_links.py:186` is repurposed to assert *Linear* is absent rather than that nothing is configured; `:212` is deleted with the branch it covers. The `no_tracker` fixture (38 usages, untouched call sites) additionally `delitem`s the native builder.
5. `synced_at` **leaves the index ordering.** Stated cost: 5 keys today have a cached title but no sessions and no defects, and lose their only ordering signal to the `activity IS NULL` tail.
6. [**CAI-263**](<https://linear.app/caiuswang/issue/CAI-263>)**'s recompute table was corrected twice** — a never-run child is not settled, and `done` left the vocabulary entirely. The corrected table above is the authority.
7. `stage_synced_at` **is dead payload on** `FeatureRow` — nothing in the SPA reads it. Noted, not removed.
8. `AmbiguousTaskLink` **is extended** to a parent that also holds a link, and the guard moves into `lane.py` where `index._LANE` never had one.

## Known limits

* **A task body has no revision history.** The spec lives in a `body` overwritten in place, so a spec edit is unrecoverable where Linear's description history makes it recoverable.
* **A task has no status history.** The map priced this absence **four** times: `archived` is terminal because there is nothing to restore from; a walked-back completion is counted into a column because it cannot be reconstructed; a spec edit is unrecoverable; and the un-claim `done → idea` makes a ship claim **erasable with no trace**. Every decision chose an answer that needs no history — but an effort that adds one should revisit all four.
* `archived` **→** `canceled` **renders as stage** `spec`, which reads oddly for a filed feature. There is no cancelled stage and `lib/defects/stage.py` is out of scope on this map.
* `completions_walked_back` **never decrements** — the one native number a hard `DELETE` of rows cannot bring back into agreement with them.
* **A feature declared before its first child is invisible** in the index, since nothing yet names it as parent. To be stated in the docstring as intent, or "I declared it and it didn't appear" reads as a bug.
* **Pasting another install's** `/tasks/42` **URL links the local row 42.** Host-matching was rejected because regin has no authoritative self-address. Same hazard as mistyping the number, and the fetched title exposes it at once.
* **The** `sdk-*` **disjoint set is flagged, not decided.** 50 raw run traces ($265.93) are disjoint from the 51 collapsed conversations ($386.81). Whether that is the same work double-counted or spend the ledger drops is not settled by this feature.
* **+0 live effect on landing.** The population stays at 29 keys, 0 tasks have children, every native key reads `rounds = 1`, and the column ships at `0`. The only live figures that move are the one-session collapse correction and the ordering of the 5 activity-less keys.

## How we will know it worked

The metric is unchanged from [CAI-242](<https://linear.app/caiuswang/issue/CAI-242>): rounds-to-done per feature, and the pipeline is worth keeping only if it falls on features that go through it.

What this feature adds is a second, binary test: **an install with no Linear credential can spec, build, ship and read the ledger of a feature end to end.** The proof is the next feature after this one — this spec is filed as a Linear issue because `BODY_MAX = 8000` binds today on both the capture and edit paths (`lib/orm/models/tasks.py:65`, `capture.py:160,184`, refusing at `_string`), and raising it to 64,000 is this spec's own work item. **The first feature specced as a** `regin:<id>` **task is the one that follows.**