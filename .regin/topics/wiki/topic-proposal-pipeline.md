# Topic proposal pipeline (request → draft → review → apply/stop)

A reviewable funnel from a single text request to one approved topic in `.regin/topics/topic.json`. Every stage writes durable artefacts (ORM rows plus a handful of files under `.regin/topics/proposals/<id>/`) so each follow-up action — regenerate, restore, stop, accept, merge, downgrade — picks up exactly where the last one left off.

This page covers draft + review + apply + stop. The inline review-comment surface (feedback threads, anchored comments, the prompt-injection sweep) lives on the sibling topic [proposal-review-comments](./proposal-review-comments.md).

## The four stages

```
request ── draft (agent explores) ── review ── apply
```

The agent reads the repo with its own Read/Glob/Grep tools — there is no pre-built evidence pack. `lib/topics/proposal_providers.py` exposes a single provider (`external-agent`) whose configured agents come from `settings.topic_proposal_external_agents`.

1. **Request.** `POST /api/repos/<name>/topics/proposals` with `{agent?, topic_request, prompt_template_ids?}` (`web/blueprints/topics/proposals.py:54`). The route always kicks off `start_external_proposal_run` in a daemon thread — there is no synchronous branch. `default_template_slugs_for("external-agent")` selects the default prompt templates when the caller omits `prompt_template_ids`; `[]` is honoured as “opt out of all templates”.
2. **Draft.** A daemon `threading.Thread` runs `_draft_proposal` (`lib/topics/proposal_drafting.py:96`), which delegates to `run_external_agent_proposal` (`lib/topics/proposal_external.py:202`). The runner writes `instructions.md`, spawns the configured agent subprocess from `settings.topic_proposal_external_agents.<id>`, registers the live `Popen` with `run_control`, pipes the instructions on stdin, and waits with `config.timeout_seconds`. The instructions carry only: the user's `topic_request`, the prior draft (on regenerate), the active prompt templates, the rules, the output JSON shape, and the existing-approved-topics list returned by `_existing_topics_summary` so the agent avoids proposing duplicates.
3. **Review.** The validated draft lands at `state="completed"` with `review_status="pending_review"`. Reviewers can edit topics, open feedback threads, regenerate (carrying open threads forward as a leading prompt block), restore to an older revision, stop an in-flight run, or flip the run to `ready_to_apply`.
4. **Apply.** Only `POST /api/repos/<name>/topics/proposals/<id>/topics/<tid>/apply` (`web/blueprints/topics/apply.py:319`) mutates the approved graph. The route recomputes the diff server-side, gates it through pre/post `audit_graph` so a proposal cannot introduce new errors, and commits via `apply_diff`.

## The only provider: external-agent

`list_proposal_providers()` (`lib/topics/proposal_providers.py:16`) returns one entry whose `agents` list is the keys of `settings.topic_proposal_external_agents`. `default_external_agent_id()` prefers `claude`, then `codex`, then the first configured agent.

`run_external_agent_proposal` is split across small helpers so the cancel check has one clear home and each piece stays under the complexity budget:

- **`_resolve_agent_config`** (`proposal_external.py:166`) — pick the agent id and its launch spec, raising for unknown / empty configs.
- **`_handle_agent_output`** (`proposal_external.py:312`) — runs *after* `proc.communicate()`. The cancel check is the FIRST thing it does (see Stop section below) so a user-terminated subprocess is reported `cancelled`, never `failed`.
- **`_reject_bad_agent_result`** (`proposal_external.py:370`) — raises `_fail(...)` on a permission prompt, non-zero exit, or any mutation of the approved graph between `_read_topic_signature` snapshots.
- **`_persist_agent_payload`** (`proposal_external.py:396`) — load + validate the temp JSON, run `_validate_paths`, and `shutil.copyfile` it to the canonical `agent-output.json`.
- **`_cancelled`** (`proposal_external.py:412`) — stamp the terminal `cancelled` status and raise `TopicGraphError`. Raising means the job wrapper's success path (artifact write + `completed` stamp) never runs, and `_record_thread_failure` sees `cancelled` and leaves it alone.

Guards that protect the approved graph and the canonical output:

- **Stdout / stderr capture.** Written to `stdout.log` / `stderr.log` and tailed into trace spans (`proposal.agent.stdout` / `proposal.agent.stderr`, last 4000 chars each).
- **Two-step output write.** The agent writes JSON to `.tmp/agent-output.json`. Only after the subprocess exits 0, no permission prompt is detected, the topic signature is unchanged, and `_validate_paths` succeeds does regin `shutil.copyfile` it to `agent-output.json`. A partial write therefore never becomes canonical. If the file is missing, `_load_agent_payload` falls back to a fenced ```json``` block in stdout.
- **Three hard guards** (all inside `_reject_bad_agent_result`):
  * `_read_topic_signature` fingerprints `topic.json` (sorted, `updated_at` stripped) before and after the run — the payload is rejected if the agent mutated the approved graph.
  * `_validate_paths` is strict: **every** `ref.path` and `evidence_paths` entry must resolve inside the repo *and* exist on disk.
  * `_looks_like_permission_prompt` aborts with `state=waiting_for_permission` if stdout contains an interactive permission marker (“do you want to allow”, “write permission prompt”, etc.) — v1 runs non-interactively.
- **Trace spans.** Emitted under `topic-proposal-<id>` via `lib.trace.trace_service`: `session.start`, `session.title`, `proposal.agent.start` / `instructions` / `stdout` / `stderr` / `complete` / `failure` / `permission_request` / `cancelled`, `session.end`. `_find_agent_session_trace_id` best-effort back-links the wrapper run to the underlying tool-using agent session by matching the session title against `"Regin Topic Proposal Agent Task"` plus the proposal id.
- **Prompt template injection.** `_format_template_section` renders selected templates as a `## Custom Instructions` block between the user's topic request and the rules; the template slugs are stored in run status `prompt_template_ids` and in the proposal's `metadata.prompt_template_ids`.

## Artifact layout vs. ORM

For every run, `.regin/topics/proposals/<id>/` keeps the disk-only files; everything else lives in the ORM via `lib.topics.proposal_orm`.

| File | Written by | Purpose |
| --- | --- | --- |
| `wiki.md` | `_write_proposal_artifacts` (`proposal_drafting.py:60`) / restore endpoint | Draft narrative; promoted to `.regin/topics/wiki/<tid>.md` on accept/replace |
| `instructions.md` | `_instructions` (`proposal_external.py:573`) | Exact stdin the agent saw — reproducible by `regin doctor` |
| `stdout.log` / `stderr.log` | external runner | Raw streams; tailed into `error_detail` on failure |
| `.tmp/agent-output.json` | external agent | Untrusted draft — must pass `_validate_paths` |
| `agent-output.json` | external runner | Canonical copy of the validated temp output |
| `status.json` | `write_status` (`proposal_external.py:69`) | Mirror of the ORM run status (state, trace_id, agent, error, error_detail, prompt_template_ids, agent_trace_id, agent_trace_url) |
| `evidence.json` | `_downgrade_via_fresh_proposal` (`downgrade.py:329`) only | Small downgrade-only summary (file_count, top_directories). Live drafting runs do not write this. |

The proposal STATE (topics list, scope, provider, review markers) lives in the ORM: `save_proposal` (`core_io.py:121`) is ORM-only; `load_proposal` / `load_proposal_status` / `list_proposal_runs` read ORM first and fall back to disk only for proposals that have not been imported yet (the back-compat hatch the importer script relies on).

## Run-state lifecycle

A proposal run's `state` field flows through three terminal outcomes:

```
queued → running → completed     (success)
                  ↘ failed       (non-zero exit, validation error, etc.)
                  ↘ timed_out    (subprocess wall-clock exceeded)
                  ↘ waiting_for_permission  (interactive prompt detected)
                  ↘ cancelled    (user pressed Stop)
```

Write-time invariant in `_apply_status_invariants` (`proposal_orm/runs.py:429`): a non-empty `error` pins the state to `failed`; a non-null `completed_at` with state still `queued`/`running` is coerced to `completed`. A symmetric read-time guard lives in `proposal_orm/serializers.py:55`. Both rules deliberately operate only on `{queued, running, completed}` — `cancelled` (and `failed`/`timed_out`/`waiting_for_permission`) sit outside that set so they survive round-trips intact.

Distinct from the run state, the `review_status` field on each proposed topic (`VALID_PROPOSAL_REVIEW_STATES` in `lib/topics/proposals/_common.py:22`) walks:

```
draft → pending_review → changes_requested → ready_to_apply → partially_applied → applied
```

- Newly drafted (or regenerated) runs land on `pending_review` (set inside `create_proposal_run` / `_external_proposal_job` / `_external_regenerate_job`).
- `POST /proposals/<id>/review-state` (`proposals.py:141`) only accepts `pending_review`, `changes_requested`, `ready_to_apply`. The apply endpoint refuses anything except `ready_to_apply` or `partially_applied` (`apply.py:274`).
- `_recompute_proposal_status` (`_common.py:65`) mirrors what `/apply` does: once every topic has a non-empty `review_status` the run flips to `applied`; mixed state → `partially_applied`. Both the library accept/merge/replace path and the web `/apply` route call this rule so disk and ORM converge.

## Stop: cross-thread cancellation

Proposal runs execute in a daemon thread inside the Flask process, so the Popen handle for the agent subprocess lives on a worker thread's call stack — unreachable from the request thread that handles the Stop click. `lib/topics/proposals/run_control.py` bridges that gap with a process-global registry:

- **`reset(proposal_id)`** — clear any prior cancel flag and stale handle. Called from `start_external_proposal_run` and `start_external_regenerate_run` so a regenerate that reuses a previously-cancelled id isn't insta-cancelled by a stale flag.
- **`register(proposal_id, proc)`** — record the live `Popen` after `subprocess.Popen(...)` returns and before the worker blocks on `communicate()`.
- **`request_cancel(proposal_id)`** — set the cancel flag and, if a live handle is registered (`poll() is None`), call `proc.terminate()` directly. Calling by-handle avoids the PID-reuse hazard of signalling by persisted PID. Returns `True` iff a running subprocess was actually signalled; `False` is normal when the run is still queued (no process yet) or has already exited — the flag is set regardless.
- **`is_cancelled(proposal_id)`** — read the flag. Checked by `_handle_agent_output` BEFORE the non-zero-exit → `failed` branch, so a terminated subprocess always finalises as `cancelled`.
- **`release(proposal_id)`** — drop the live handle from the worker's job-wrapper `finally`. Releases only the handle, not the cancel flag (the flag is needed for any future terminal-state writes and is cleared by the next `reset`).

Single-process assumption: regin serves from one threaded `regin serve` process, so a module-level dict is shared across the worker and request threads. A multi-worker WSGI deployment would not share it — acceptable for the local dashboard this targets.

The public entry point is `stop_proposal_run(repo_path, proposal_id)` in `proposals/core_io.py:320`:

```
ACTIVE_RUN_STATES = {"queued", "running", "waiting_for_permission"}
```

1. Load the current status (`load_proposal_status` raises `TopicGraphError` if the run is unknown).
2. If state is outside `ACTIVE_RUN_STATES`, return `{"stopped": False, "already_terminal": True, "state": <existing>}` — a double-click on a finished run is a no-op, never a clobber.
3. Call `run_control.request_cancel(proposal_id)` (terminates the live process if one is registered, sets the cancel flag otherwise).
4. `write_status(out_dir, {..., state: "cancelled", error: None, completed_at: now})` for immediate UI feedback. The worker thread also stamps `cancelled` when it notices the kill — both writes are idempotent because the row already reads `cancelled`.
5. Activity-log a `topic_proposal_stopped` record carrying `prior_state` and `signalled` so the trail shows whether a live process was actually killed.

Worker-side guards keep the cancel decision sticky:

- `_handle_agent_output` runs the `run_control.is_cancelled` check FIRST, hands off to `_cancelled(ctx, status, ...)`, which stamps `state="cancelled"`, emits a `proposal.agent.cancelled` trace span, ends the session with `reason="cancelled"`, and raises `TopicGraphError`. Raising short-circuits the rest of the runner — `_reject_bad_agent_result`, `_persist_agent_payload`, the `completed` stamp — none of them run.
- `_record_thread_failure` in `proposals/external_jobs.py:184` returns early when it sees `state == "cancelled"`: the killed subprocess surfaces as an exception in the worker (`proc.terminate()` produces a non-zero returncode), but the user's cancel must not be downgraded to `failed`.
- Both `_external_proposal_job` and `_external_regenerate_job` call `run_control.release(proposal_id)` in a `finally`, so a successful or failed run always drops its handle.
- `_cancelled` writes `error=None` — a stop isn't a failure, so the row stays free of an `error` string that would otherwise trigger the `_apply_status_invariants` pin to `failed`.

Boundary honesty: if the agent finishes successfully in the narrow window between `stop_proposal_run`'s active-state check and the worker's terminal write, the run may land `completed` rather than `cancelled`. That's the honest outcome — the work did finish.

## Regenerate, restore

`regenerate_proposal_run` (`proposals/external_jobs.py:350`) is a thin alias — it always delegates to `start_external_regenerate_run` (`proposals/external_jobs.py:261`), which:

1. Calls `_guard_regenerate_not_in_flight` (`_common.py:39`) so two background regenerates never race on the same `proposal_id` (the race would otherwise leave the run row with `state=completed` *and* a failure `error`).
2. Builds a `_RegenerateInputs` snapshot: prior proposal + prior wiki + open feedback threads + previous revision id + agent + prompt_template_ids. When the proposal row has no topics (queued/failed runs) it falls back to the status row.
3. Calls `run_control.reset(proposal_id)` so a regenerate that reuses a previously-stopped id starts clean.
4. Spawns a daemon thread that runs `_draft_proposal`, calls `_reset_review_markers_for_regenerate` (`_common.py:49`) to strip stale `review_status` / `accepted_topic` / `merged_topic` markers off the new draft (a regenerate replaces topic content, so any prior accept marker on a same-id topic is stale and would hide Apply in the UI), then `_write_proposal_artifacts(append_revision=True, revision_kind="regenerated")` and `orm_mark_feedback_threads_addressed` for threads whose anchored content visibly changed between revisions.

`restore_proposal_to_revision` (`core_io.py:153`) is the other history operation — `POST /proposals/<id>/restore` with `{revision_id}` appends a new `kind="restored"` revision whose body is copied from an older revision. It also rewrites `proposal_dir/wiki.md` so a subsequent Apply publishes the restored wiki rather than the most recent draft (the per-topic wiki promotion reads from that file).

## Apply: server-side diff + atomic snapshot

Two callers funnel through one underlying write path:

- **HTTP `/apply`** (`web/blueprints/topics/apply.py:319`, `api_repo_topic_proposal_apply`). Recomputes the diff server-side via `_build_resolved_diff` from `(strategy, target_topic_id, options)` — clients never send the diff itself, so a stale `/diff` snapshot cannot be committed by a later `/apply`. Companion `/diff` route (`apply.py:159`) returns the same diff plus the items that would be silently dropped under the supplied `ApplyOptions`.
- **Library helpers** (`lib/topics/proposals/topic_actions.py`): `accept_proposed_topic`, `replace_approved_topic`, `merge_proposed_topic` all funnel through `_apply_topic_change` (`topic_actions.py:64`), which composes a one-topic `GraphDiff` and hands it to `apply_diff`. Intended for CLI and direct programmatic callers.

Safety checks (HTTP path):

1. Review state must be `ready_to_apply` or `partially_applied`.
2. Pre/post `audit_graph` runs; `diff_issues` returns only the issues *introduced* by the change. Pre-existing rot in unrelated topics becomes a non-blocking `graph_warnings` field rather than a hard fail.
3. Idempotency short-circuit: `_existing_apply_snapshot` (`apply.py:104`) queries `TopicAudit` provenance rows by `triggering_run_id=proposal_id` and topic id. If a prior snapshot exists *and* the target topic still lives in the graph, the endpoint returns `already_applied=true` instead of double-writing. The “still in the graph” guard covers the case where the topic was downgraded after the original apply — then the prior snapshot is stale and a fresh apply must run.
4. For `create` / `replace`, the proposal's `wiki.md` is loaded and passed through `apply_diff(..., wiki_pages={topic_id: body})` so the per-topic file under `.regin/topics/wiki/<tid>.md` lands in the same transaction as the snapshot. `merge` keeps the target's existing wiki narrative and omits `wiki_pages`.

Three strategies:

- **`create`** — promote a new topic. Library equivalent: `accept_proposed_topic`.
- **`replace`** — swap a draft for an existing approved topic of the same id; the original lives on as the snapshot's `before_topic`.
- **`merge`** — fold a draft's refs / aliases / include_globs / exclude_globs / commands into an existing approved topic; the target's wiki narrative is intentionally kept (see `_merge_into_target`). Duplicate aliases inside a single topic are collapsed before the merge to honour regin's alias-normalisation rule.

After the snapshot, `_restore_pruned_inbound_edges_after_apply` (`apply.py:210`) reads `proposal.metadata.pruned_inbound_edges` (written by the downgrade path) and re-attaches any edges that had to be pruned to make a previous downgrade legal — round-tripping downgrade → re-apply without losing sibling edges.

The library helpers (`accept_proposed_topic` etc.) also persist the per-topic wiki by calling `_persist_per_topic_wiki` (`_common.py:103`), which copies the proposal's full `wiki.md` to `.regin/topics/wiki/<slugify(topic_id)>.md`. For multi-topic proposals every accepted topic ends up with the same body — redundant but never lossy; the user can hand-edit later.

## Downgrade: two paths

`downgrade_topic_to_proposal` (`proposals/downgrade.py:390`) lifts an approved topic back into a proposal draft. It prefers a **merge-into-origin** strategy:

1. **`_try_downgrade_into_origin`** (`downgrade.py:188`) calls `orm_find_origin_proposal_run_for_topic` to locate the proposal run whose Apply originally brought this topic into the graph. If found, the topic + its inbound edges are dropped from the live graph (with rollback on failure), `orm_unaccept_topic_across_proposals` clears stale `review_status="accepted"` markers, and `orm_append_downgrade_revision` adds a new `kind="downgraded"` revision onto the origin run. The whole topic lifecycle stays in one run.
2. **`_downgrade_via_fresh_proposal`** (`downgrade.py:329`) is the fallback used when the origin lookup returns None (snapshots from before `triggering_run_id` existed) or the origin run was deleted. It mints a new timestamp-derived proposal id, writes a small `evidence.json` (file_count + top_directories), a stub or copied `wiki.md`, builds a `provider="approved-topic-downgrade"` proposal, and persists via `save_proposal`.

Both paths capture the pruned inbound edges in `proposal.metadata.pruned_inbound_edges` so a later Apply can restore them via `_restore_pruned_inbound_edges_after_apply`, and both schedule `_reindex_wiki_after_graph_change` (`_common.py:146`) on a daemon thread so the wiki dense index drops the downgraded topic's `wiki/<repo>/<topic_id>` row.

## Frontend surface

`frontend/src/components/topics/ProposalRunDetail.vue` is the per-run page. Around the run header it exposes:

- **Stop button** — rendered only when `selectedRun.state ∈ {queued, running, waiting_for_permission}` (`isActiveRun`). Clicking calls `stopProposal()` → `POST /repos/<n>/topics/proposals/<id>/stop`, after confirming. While the request is in flight the label flips to `Stopping…` and the button disables. On `ok`, the parent reloads; on error, the error banner surfaces the server message.
- **Review-state buttons** — toggle `pending_review` ↔ `changes_requested` ↔ `ready_to_apply`.
- **Regenerate / Restore / Delete / Apply / Ignore** — wired to the matching POST routes.

State colour mapping in both `ProposalRunDetail.vue::proposalStateColor` and `ProposalRunsList.vue::proposalStateColor`: `completed` → green, `failed`/`timed_out` → red, `waiting_for_permission` → yellow, `running`/`queued` → blue, `cancelled` → gray. The runs-list filter dropdown (`ProposalRunsList.vue::STATE_OPTIONS`) exposes `cancelled` alongside the other terminal options.

## HTTP surface, at a glance

All routes are blueprint-mounted under `topics_bp`; the file column is the source.

| Method + Path | Source | Effect |
| --- | --- | --- |
| `POST /api/repos/<n>/topics/proposals` | `proposals.py:54` | Start a new external-agent run in the background |
| `GET  /api/repos/<n>/topics/proposals/<id>` | `proposals.py:89` | Read draft + wiki + status + feedback threads |
| `GET  /api/repos/<n>/topics/proposals/<id>/status` | `proposals.py:111` | Poll the status row only |
| `POST /api/repos/<n>/topics/proposals/<id>/regenerate` | `proposals.py:122` | Background re-draft, reusing prior draft + open feedback threads |
| `POST /api/repos/<n>/topics/proposals/<id>/review-state` | `proposals.py:141` | Transition between `pending_review` / `changes_requested` / `ready_to_apply` |
| `POST /api/repos/<n>/topics/proposals/<id>/restore` | `proposals.py:165` | Append a `kind="restored"` revision copied from an older revision; rewrites `wiki.md` |
| `POST /api/repos/<n>/topics/proposals/<id>/stop` | `proposals.py:186` | Terminate the agent subprocess and stamp the run `cancelled` (idempotent; no-op on terminal state) |
| `POST /api/repos/<n>/topics/proposals/<id>/delete` | `proposals.py:199` | Drop ORM rows + on-disk run dir |
| `POST /api/repos/<n>/topics/proposals/<id>/topics/<tid>` | `proposals.py:211` | Edit a single proposed topic in place |
| `POST /api/repos/<n>/topics/proposals/<id>/ignore` | `proposals.py:230` | Mark one proposed topic ignored |
| `POST /api/repos/<n>/topics/proposals/<id>/topics/<tid>/diff` | `apply.py:159` | Side-effect-free server-side diff + dropped-items preview |
| `POST /api/repos/<n>/topics/proposals/<id>/topics/<tid>/apply` | `apply.py:319` | Promote (`create` / `replace` / `merge`) into the approved graph via `apply_diff` |
| `GET  /api/repos/<n>/topics/audit` | `apply.py:428` | Live graph audit issues (by code) for the workspace tab |
| `POST /api/repos/<n>/topics/audit/fix` | `apply.py:458` | Auto-fix `graph.dead_ref` and `graph.orphan_edge_target` issues |
| `GET  /api/repos/<n>/topics/snapshots` | `apply.py:565` | List `GraphSnapshot` rows newest-first |
| `GET  /…/snapshots/<id>/restore-preview` | `apply.py:582` | Show what would change on restore |
| `POST /…/snapshots/<id>/restore` | `apply.py:602` | Restore a snapshot (re-exports to disk + reindexes wiki) |
| `POST /…/snapshots/<id>/pin` and `/unpin` | `apply.py:646` / `apply.py:651` | Pin / unpin a snapshot |
| `POST /api/repos/<n>/topics/wiki/reindex` | `apply.py:684` | Synchronous wiki dense-index refresh |
| `POST /api/repos/<n>/topics/<topic_id>/downgrade` | `maintenance.py:47` | Lift an approved topic back into a proposal draft (origin-merge or fresh) |

For the comment / feedback-thread routes (`/feedback-threads`, `/feedback-threads/<id>/comments`, `/resolution`, `/comments/<id>/update`, `/comments/<id>/delete`), see the [proposal-review-comments](./proposal-review-comments.md) topic.

## Tests worth opening

- `tests/topics/test_proposal_stop.py` covers the entire cancel surface: the `run_control` registry (terminate-live-process, no-live-process-flags-only, reset-clears-stale-flag), the `stop_proposal_run` state transitions (marks `cancelled`, no-op on terminal state, raises on missing run), and both worker-side guards (`_record_thread_failure` preserves `cancelled`, `_handle_agent_output` runs the cancel branch BEFORE the failure branch).
- `tests/topics/test_blueprint_topics.py` walks every other HTTP route — proposal creation, regenerate, review-state transitions, apply / diff, downgrade, snapshots — and is the canonical smoke test for the blueprint.

## Reading order for new contributors

1. `lib/topics/proposal_external.py` — the agent subprocess runner, the helper trio (`_handle_agent_output` / `_reject_bad_agent_result` / `_persist_agent_payload`), the three guards, the trace-span emissions. The heart of the draft stage.
2. `lib/topics/proposals/run_control.py` — tiny module, but the key to understanding how Stop reaches a Popen on another thread.
3. `lib/topics/proposal_drafting.py` — `_draft_proposal` dispatch, `_write_proposal_artifacts`, and the v1 schema validator.
4. `lib/topics/proposals/external_jobs.py` — background-thread proposal + regenerate orchestration with the in-flight guard and the `run_control.reset` / `register` / `release` calls.
5. `lib/topics/proposals/core_io.py` — ORM-first load/save with the disk fallback hatch, plus `restore_proposal_to_revision` and `stop_proposal_run`.
6. `lib/topics/proposals/topic_actions.py` — library-level accept / replace / merge + the `_apply_topic_change` shim that pre/post audits each one-topic diff.
7. `lib/topics/proposals/downgrade.py` — merge-into-origin first, fresh-proposal fallback; the source of `provider="approved-topic-downgrade"` proposals.
8. `web/blueprints/topics/proposals.py` and `web/blueprints/topics/apply.py` — the HTTP surface tying everything together; `/apply` is the canonical promotion path, `/stop` the cancel entry point.

# Proposal review comments (feedback threads anchored to topics & wiki)

Reviewers leave comments without editing the draft. Threads are first-class rows attached to a proposal run; each thread has an anchor (`general` / `topic_field` / `proposal_summary` / `wiki_range`), one or more comments, and a resolution state. On regenerate, the open threads are rendered into the agent's instructions as a “Review feedback to address in this revision” block, and threads whose anchored content actually changed are auto-snapped to `addressed`.

## Data model

`lib/orm/models/proposals.py` declares two tables (migration: `alembic/versions/0007_proposal_feedback_threads.py`).

- **`proposal_feedback_threads`** (`ProposalFeedbackThread`). Keyed by surrogate `id`. Linked to a run via `run_id`, to the revision it was opened against via `revision_id` (nullable for legacy rows), and optionally to a draft topic via `proposal_topic_id`. `anchor_kind` selects the anchor flavour; `anchor_json` carries the structured anchor blob (`{field}` for `topic_field`, `{section}` for `proposal_summary`/`general`, `{topic_id, section}` for `wiki_range`). `quoted_text` is a verbatim snippet shown above the thread. Resolution lifecycle: `resolution_state` is one of `open`, `resolved`, `dismissed`, `addressed` (the auto-state); `addressed_in_revision_id` records which revision closed an `addressed` thread.
- **`proposal_feedback_comments`** (`ProposalFeedbackComment`). Append-only message log per thread; `author_kind` distinguishes `user`/`agent` comments, `body` is plain markdown, `metadata_json` is reserved for future attachments.

Both tables carry `created_at` / `updated_at` strings and a `metadata_json` blob.

## CRUD (Python wrappers → ORM helpers)

`lib/topics/proposals/feedback.py` is a thin set of validators that delegate to the ORM helpers in `lib/topics/proposal_orm/feedback.py` and write an activity-log record per call. Every function returns the rendered thread dict shape (or `{"deleted_thread": True, ...}` on the comment-delete corner case).

| Library call | ORM helper | Effect |
| --- | --- | --- |
| `list_proposal_feedback_threads(repo_path, proposal_id, *, revision_id=None)` | `orm_list_feedback_threads` | List threads sorted by `updated_at DESC`. With `revision_id` set, hides threads opened in a later revision than the one being viewed. |
| `create_proposal_feedback_thread(...)` | `orm_create_feedback_thread` | Insert a thread + its first comment in one transaction. Validates that `proposal_topic_id` (if given) resolves to a draft topic via `_find_proposed_topic`. |
| `add_proposal_feedback_comment(...)` | `orm_add_feedback_comment` | Append a comment; bumps the thread's `updated_at`. |
| `set_proposal_feedback_thread_resolution(..., resolution_state)` | `orm_set_feedback_thread_resolution` | Move between `open`/`resolved`/`dismissed`. `addressed` is *not* a user-settable state — it's reserved for the auto sweep. Reopening (`open`) clears `addressed_in_revision_id`; closing deliberately leaves `updated_at` untouched so the thread doesn't jump to the top of the list. |
| `update_proposal_feedback_comment(..., body)` | `orm_update_feedback_comment` | Edit a comment in place. |
| `delete_proposal_feedback_comment(...)` | `orm_delete_feedback_comment` | Delete a comment. If it was the thread's last comment, the now-empty thread is deleted too and the call returns the `deleted_thread` marker. |

`MANUAL_RESOLUTION_STATES = {"open", "resolved", "dismissed"}` is exported from `lib.topics.proposal_orm` so the route handler can validate the body before touching the DB.

## Anchors

`ProposalCommentsSidebar.vue::anchorOptions` is the source-of-truth for what a UI reviewer can pick. The five anchor flavours are:

| `value` (UI) | `anchor_kind` | `anchor` payload | Use case |
| --- | --- | --- | --- |
| `general` | `general` | `{section: "revision-overview"}` | Notes that aren't tied to one topic (e.g. “drop diff prose from the wiki”). |
| `topic-summary` | `proposal_summary` | `{topic_id, section: "summary"}` | “The whole topic summary is off”. Snaps to `addressed` if any of the topic's snapshot fields differ between revisions. |
| `topic-intent`, `topic-aliases`, etc. | `topic_field` | `{topic_id, field}` | Per-field comments (intent, refs, aliases, include_globs, evidence_paths, edges). Snaps to `addressed` if that single field changes. |
| `wiki-preview` | `wiki_range` | `{topic_id, section: "wiki-preview"}` | Comments tied to the rendered wiki preview. Snaps to `addressed` whenever the proposal's `wiki` string differs. |

## Prompt injection on regenerate

The regenerate snapshot (`_RegenerateInputs` in `proposals/external_jobs.py`) carries the run's open feedback threads. `format_review_feedback_for_prompt` (`lib/topics/proposal_drafting.py:27`) renders them as a leading block in the agent instructions:

```
Review feedback to address in this revision:
1. topic `topic-proposal-pipeline`, field `intent`
   Quoted text: "…"
   - user: <comment body>
   - user: <comment body>
2. general review
   - user: <comment body>
```

The block is injected by `_instructions` (`lib/topics/proposal_external.py:573`) immediately before `Prior draft reference:` so the agent reads the feedback first. Closed threads (`resolved`, `dismissed`, `addressed`) are filtered out by the caller (`_RegenerateInputs` keeps only `resolution_state == "open"`).

## Auto-addressed sweep

After a regenerate finishes, `orm_mark_feedback_threads_addressed` walks every open thread, compares the previous and next proposal payloads, and flips the thread to `resolution_state="addressed"` if the anchored content changed. The diff rule lives in `_thread_addressed_by_revision`:

- `topic_field` → compare the named field on the matching topic id (lists/dicts are deep-compared via `_topic_snapshot_value`).
- `proposal_summary` → compare the full topic snapshot (`label` + `intent` + `status` + the seven list fields).
- `wiki_range` → compare the proposal `wiki` strings.
- `general` (and any unknown kind) → never auto-addressed; the user must close it manually.

`_is_thread_visible_at_revision` skips threads opened in revisions *newer* than the one being addressed, so reopening an earlier revision to retry doesn't accidentally close threads that were opened after.

## Read-time effective state

`_feedback_thread_to_dict` in `proposal_orm/serializers.py` emits both the raw `stored_resolution_state` and a computed `resolution_state`. `_effective_resolution_state` downgrades an `addressed` thread back to `open` *when the UI is viewing a revision earlier than the one that addressed it* — from that vantage point the change isn't visible yet, so the thread shouldn't look closed. The frontend sidebar uses `resolution_state` for the badge tone (`open` → yellow, `resolved`/`addressed` → green, `dismissed` → grey).

## HTTP surface

All routes are blueprint-mounted under `topics_bp` in `web/blueprints/topics/proposals.py`.

| Method + Path | Source | Effect |
| --- | --- | --- |
| `POST /api/repos/<n>/topics/proposals/<id>/feedback-threads` | `proposals.py:248` | Create a thread with its first comment |
| `POST /…/feedback-threads/<thread_id>/comments` | `proposals.py:296` | Append a comment to a thread |
| `POST /…/feedback-threads/<thread_id>/resolution` | `proposals.py:273` | Move the thread between `open` / `resolved` / `dismissed` |
| `POST /…/feedback-threads/<thread_id>/comments/<comment_id>/update` | `proposals.py:320` | Edit a comment body |
| `POST /…/feedback-threads/<thread_id>/comments/<comment_id>/delete` | `proposals.py:345` | Delete a comment (deletes the thread if it was the last one) |

The per-thread list itself is returned inline by `GET /api/repos/<n>/topics/proposals/<id>` (`feedback_threads` key in the response), so there's no dedicated list route — reviewers always pull threads alongside the proposal payload.

## Frontend: ProposalCommentsSidebar

`frontend/src/components/topics/ProposalCommentsSidebar.vue` is the review-side UI. It is mounted from `ProposalRunDetail.vue` in two places so the sidebar appears both in the main proposal view and inside the diff drawer.

Key props/behaviour:

- `selectedTopic` — when a draft topic is focused, the anchor picker exposes the four topic-scoped anchors (`topic-summary`, `topic-intent`, `topic-aliases`, `wiki-preview`) in addition to `general`. The filter buttons toggle between “All threads” and “Selected topic” (which keeps the unanchored `general` threads visible).
- `readonly` — set when the user is viewing a historical revision. Comment composer + reply + edit/delete are hidden; the sidebar reverts to a read-only badge list.
- Composer state (`composerAnchor`, `composerBody`, `composerBusy`) drives `createThread()` → `POST /feedback-threads`. The composer auto-resets to `general` whenever `selectedTopic` changes.
- Per-thread state (`replyingThreadId`, `replyDrafts`, `editingCommentId`, `editDraft`) is local-only — the sidebar emits `updated` after every mutating request so the parent reloads the canonical thread list.
- Resolution dropdown calls `setResolution(threadId, state)` → `POST /resolution`; the UI never offers `addressed` (matches `MANUAL_RESOLUTION_STATES` on the backend).
- Tone mapping (`threadTone`): `addressed`/`resolved` → green, `dismissed` → grey, anything else → yellow.

## Activity log

Every wrapper writes a structured record via `_topics_log().write(...)`:

- `proposal_feedback_thread_created`
- `proposal_feedback_comment_added`
- `proposal_feedback_thread_resolution_set`
- `proposal_feedback_comment_updated`
- `proposal_feedback_comment_deleted` (carries `deleted_thread` so log readers can spot the cascade)
- `proposal_feedback_threads_addressed` (emitted from the auto sweep with `thread_count` and the two revision ids)

These flow through the standard activity log (`lib/activity_log.py`) tagged `feature=topics`; `regin logs grep --feature topics proposal_feedback` filters to just the comment trail.

## Reading order for new contributors

1. `lib/orm/models/proposals.py` — the two table classes and their columns.
2. `lib/topics/proposal_orm/feedback.py` — the ORM helpers, including the diff rules in `_thread_addressed_by_revision` and the `MANUAL_RESOLUTION_STATES` constant.
3. `lib/topics/proposal_orm/serializers.py::_feedback_thread_to_dict` — the read-time effective-state computation.
4. `lib/topics/proposals/feedback.py` — the validator/activity-log layer used by the route handlers.
5. `lib/topics/proposal_drafting.py::format_review_feedback_for_prompt` and `lib/topics/proposal_external.py::_instructions` — how open threads land in the agent's prompt.
6. `web/blueprints/topics/proposals.py` (routes ~248–353) — the HTTP surface.
7. `frontend/src/components/topics/ProposalCommentsSidebar.vue` — the review UI; pair with `ProposalRunDetail.vue` for the mount points.
8. `tests/topics/test_blueprint_topics.py` — worked examples of every thread / comment / resolution path through the routes.