# Topic proposal pipeline (request → draft → review → apply/stop)

The proposal pipeline is the funnel that turns a user's topic request into
reviewable draft topics and — on human approval — into rows of the approved
topic graph (`.regin/topics/topic.json` + its overlay). A draft is authored by
an external tool-using agent, validated and persisted through the proposal ORM,
iterated by a reviewer (regenerate / restore / feedback / stop), and finally
promoted one topic at a time through a single server-side `/diff` + `/apply`
path. Everything in this topic deliberately writes *proposal* artifacts, never
approved topics directly (`lib/topics/proposals/__init__.py`).

## Provider model

There is one proposal provider: the external tool-using agent. `lib/topics/proposal_providers.py:list_proposal_providers`
reports a single `external-agent` entry whose configured agents come from
`settings.topic_proposal_external_agents` (set in `settings.local.json`). The
runner lives in `lib/topics/proposal_external.py`. `external_agent_configured()`
and `default_external_agent_id()` (`proposal_external.py:43`, `:47`) gate and
pick the agent — `claude` is preferred, then `codex`, then the first configured
key. The agent explores the repo with its own Read/Glob/Grep tools; regin
pre-derives **no** evidence pack — the only pre-built context is the existing
approved-topic list and the bucket list (`_existing_topics_summary`,
`_bucket_summary` in `proposal_external.py:154`/`:171`).

## Starting a run (async)

The web worker must return immediately, so a proposal kicks off on a daemon
thread. `lib/topics/proposals/external_jobs.py:start_external_proposal_run`:

1. Allocates a `proposal_id` (a UTC timestamp stamp like `20260523T160359Z`)
   and the out-dir `<repo>/.regin/topics/proposals/<id>`.
2. Writes a `queued` status row (resetting `agent_signaled`/`signaled_by` so a
   reused id can't inherit a prior run's finish marker).
3. Clears any stale cancel flag via `run_control.reset(proposal_id)`.
4. Spawns `_external_proposal_job` on a daemon thread.

The job runs `_draft_proposal → _write_proposal_artifacts` and, unless the agent
already ingested via the finish signal, persists the draft and stamps the run
`completed`. Any exception inside the thread is captured into the run's status
(`_record_thread_failure`) so the failure surfaces in the UI instead of tearing
down the worker; `run_control.release(proposal_id)` always runs in `finally`.
The web entrypoint is `POST /api/repos/<name>/topics/proposals`
(`web/blueprints/topics/proposals.py:54`), which resolves prompt-template slugs
(provider defaults when the body omits them) and returns the new run's id +
status. A synchronous variant, `core_io.create_proposal_run`, exists for
direct/programmatic callers.

## The runner and the drafting prompt

`run_external_agent_proposal` (`proposal_external.py:247`) is the heart of the
draft step:

- Resolves the agent + its launch spec (`_resolve_agent_config`), stamps a
  `running` status, and emits a `session.start` / `session.title` pair so the
  run shows up as its own trace under id `topic-proposal-<id>`.
- Builds the instruction prompt with `_instructions` (`:681`) and writes it to
  `instructions.md`. The prompt carries: the user's topic request; an optional
  `## Custom Instructions` block from injected prompt templates
  (`_format_template_section`); a "Prior draft reference" block for regenerate
  runs (the previous proposal JSON + wiki + formatted review feedback, with the
  agent told to re-derive from the repo and **not** write changelog/diff prose);
  a "Sibling topics being refreshed" block for content-drift refresh batches
  (`_sibling_refresh_section`); the Rules; the exact finish command; the output
  JSON shape; and the existing-topics + bucket lists.
- Exports a set of `REGIN_TOPIC_PROPOSAL_*` env vars (out-dir, temp + canonical
  output paths, trace id, proposal id, and the finish command) so the agent
  writes its JSON to `.tmp/agent-output.json` and knows how to signal
  completion.
- `subprocess.Popen`s the agent, registers the live handle in `run_control` so
  the Stop endpoint can reach it, records the pid, then blocks on
  `proc.communicate(instructions, timeout=...)`.

`_finish_command` (`:59`) builds the completion command from the server's own
interpreter + the regin CLI path + an explicit `--repo`, so it works regardless
of the target repo or whether `regin` is on the agent's PATH.

## Notify-on-finish

Rather than the server racing a fixed timeout (which would kill a long draft
mid-flight), the agent signals completion itself by running
`regin topics proposal-finish <id>` as its final step. `_proposal_wait_timeout`
(`:369`) returns `None` by default (no ceiling); a configured
`topic_evolution.proposal_run_timeout_seconds > 0` is only a backstop — a
process that already signalled is still treated as success at the ceiling.

`finish_proposal_run` (`lib/topics/proposals/finish.py`) runs **in the agent's
own process** and is the authoritative ingest: it loads the agent's output JSON
(temp first, then canonical), re-runs the same contract the runner's exit path
uses (`_load_agent_payload → _normalise_agent_payload → _validate_paths`),
persists the proposal + wiki via `_write_proposal_artifacts`, and stamps the run
`completed` with `agent_signaled=True`. It is idempotent: a call after a
terminal state, or a second call, is a no-op. It appends a `regenerated`
revision when the run already has revisions, else writes a fresh `generated`
one.

Back on the server side, the runner and the background job both check
`_load_signaled_result` / `_already_ingested_by_agent`: if the agent already
ingested, they return its persisted result and **skip** a redundant re-ingest so
the proposal is never double-persisted. The legacy stdout/exit-code parse path
in `_handle_agent_output` (`proposal_external.py:391`) remains the fallback for
agents that exit without signalling.

## Output validation and integrity guards

`_handle_agent_output` checks, in order:

1. **Cancellation first** — `run_control.is_cancelled` so a terminated
   subprocess (non-zero exit) is reported `cancelled`, never `failed`.
2. **Signal result** — short-circuit on notify-on-finish.
3. `_reject_bad_agent_result` (`:458`) raises via `_fail` on: a permission
   prompt detected in the output (`_looks_like_permission_prompt` — v1 runs
   non-interactively, status `waiting_for_permission`), a non-zero exit code, or
   any mutation of the approved graph (a `_read_topic_signature` mismatch — a
   structural fingerprint that ignores `updated_at`/whitespace).
4. `_persist_agent_payload` parses + validates the JSON: schema (`validate_proposal`),
   non-empty wiki, and `_validate_paths` — every `refs[].path` and
   `evidence_paths[]` entry must exist inside the repo working tree (paths that
   escape the repo or don't exist are rejected).

The run is then linked to the real tool-using agent trace via
`_find_agent_session_trace_id` (matches recent `sessions` rows by title), and
stamped `completed`.

## Two status axes

A proposal carries two orthogonal status axes, and `write_status`
(`proposal_external.py:84`) is careful to keep them apart:

- **Run lifecycle state** — `queued → running → completed | failed | cancelled |
  timed_out | waiting_for_permission`. Owned by the runner / job / stop / reap
  paths.
- **Review state** (`proposal["status"]` / `metadata.proposal_status`) —
  `draft → pending_review → changes_requested → ready_to_apply →
  partially_applied → applied` (`VALID_PROPOSAL_REVIEW_STATES` in
  `lib/topics/proposals/_common.py:22`). Owned solely by the proposal-save /
  apply paths.

`write_status` explicitly excludes `proposal_status` from its metadata patch:
because `load_status` spreads the whole metadata bag back into the status dict,
carrying it would let a run-lifecycle write re-stamp a stale review state over a
fresh one (e.g. stranding a regenerated draft as un-appliable). The
`_recompute_proposal_status` helper (`_common.py:65`) advances the review state
from per-topic `review_status` counts only once topics have actually been
reviewed.

## Persistence: ORM-first, disk-for-artifacts

The proposal ORM (`lib/topics/proposal_orm/`) is the source of truth for
proposal state (topics list, scope, metadata, revisions). `_write_proposal_artifacts`
(`lib/topics/proposal_drafting.py:72`) writes `wiki.md` to disk and calls
`orm_save_proposal`; it does **not** write `topics.json`. The disk side of a
`proposals/<id>/` dir owns `wiki.md`, `instructions.md`, `agent-output.json`,
`evidence.json`, `stdout.log`, and `stderr.log`. Reads go ORM-first with a disk
fallback for repos not yet imported: `core_io.load_proposal`,
`load_proposal_status`, and `list_proposal_runs` all prefer the ORM and only
scan disk when no row exists. `backfill_disk_proposals_to_orm` upserts legacy
on-disk-only proposal dirs into the `proposal_runs` table (called by the
git-sync / import button in `web/blueprints/topics/maintenance.py:72`) so
feedback threads and status updates can find them.

Revisions come in four `kind`s, all in `lib/topics/proposal_orm/revisions.py`:
`generated` (initial), `regenerated` (re-draft), `restored`
(`orm_restore_proposal_to_revision` — copy a historical revision forward), and
`downgraded` (an approved topic lifted back into its origin run).

## Review iteration

- **Regenerate** — `start_external_regenerate_run` (`external_jobs.py:285`)
  guards against a concurrent in-flight regenerate, resolves the prior draft +
  its **open** feedback threads + the agent/templates/topic_request from the
  prior run, and re-runs the drafting thread with `prior_draft` populated. After
  the new revision lands, `_mark_addressed_feedback_after_regenerate`
  auto-resolves feedback threads whose anchored content changed. Web:
  `POST .../regenerate`.
- **Restore** — `core_io.restore_proposal_to_revision` appends a `restored`
  revision whose body is copied from a historical revision and rewrites
  `proposals/<id>/wiki.md` to match (the apply path reads the approved wiki from
  that file). Web: `POST .../restore`.
- **Review state** — `set_proposal_review_state` flips the review axis between
  `pending_review` / `changes_requested` / `ready_to_apply`. Apply refuses to
  run unless the proposal is marked ready (`_review_state_not_ready` in
  `apply.py`). Web: `POST .../review-state`.
- **Feedback threads** — anchored comment threads drive the regenerate prompt;
  the full CRUD surface lives in `proposals.py` (`.../feedback-threads...`).
  (Thread internals are out of scope here — see the feedback-threads topic.)
- **Review note** — `_maybe_review_note` runs a gated, best-effort LLM review
  after a run completes; `POST .../review-note` generates one on demand.

## Cancel (Stop) and reaping

An in-flight run can be cancelled from a *different* HTTP request than the one
that owns the subprocess, so `lib/topics/proposals/run_control.py` keeps the live
`Popen` handles reachable across threads in one process (the single-process
`regin serve` assumption) plus a set of cancelled ids:

- `stop_proposal_run` (`core_io.py:392`, web `POST .../stop`) no-ops on an
  already-terminal run, else calls `run_control.request_cancel` (SIGTERMs the
  live subprocess if any) and stamps the run `cancelled` for immediate UI
  feedback.
- The worker thread independently notices the kill: `_handle_agent_output`'s
  cancel-first check routes it to `_cancelled` (`proposal_external.py:500`),
  which stamps the terminal `cancelled` state (no `error`, since a stop isn't a
  failure) and raises so the job's success path never runs.
  `_record_thread_failure` sees `cancelled` and leaves it alone.

The in-process runner can't see one failure mode: `regin serve` restarted
mid-run, so the daemon thread and `Popen` handle are gone and the run is pinned
non-terminal forever. `reap_stranded_proposal_runs`
(`lib/topics/proposals/reap.py`, CLI `regin topics proposal-reap`) marks such
runs `failed` — a run is stranded when it is non-terminal, has no live local
subprocess (`run_control.is_live`), never signalled, and has gone quiet past
`topic_evolution.proposal_stranded_grace_seconds`. It is safe to call
opportunistically (e.g. while listing runs).

## Promoting a draft into the approved graph

Two layers exist side by side.

**Modern `/diff` + `/apply`** (`web/blueprints/topics/apply.py`). Both endpoints
recompute the diff server-side from the request's `(strategy, target_topic_id,
options)` — the client never sends the diff, so a `/diff` at T0 followed by
`/apply` at T1 can't commit a stale prospective graph. `/diff`
(`apply.py:160`) is side-effect-free and returns the resolved diff plus the
items that would be silently dropped under the chosen `ApplyOptions`
(`prune_orphan_edges`, `drop_dead_refs`, `dedupe_aliases`). `/apply`
(`apply.py:420`) checks idempotency (`_already_applied_noop_snapshot`), refuses
unless the proposal is review-ready, returns `400 unresolvable_errors` when the
resolved diff still has unresolved errors, and otherwise commits through
`apply_diff`. After the commit it round-trips pruned inbound edges
(`_restore_pruned_inbound_edges_after_apply`), stages forward sibling edges for
multi-topic proposals (`_stage_forward_sibling_edges`), advances the
content-drift baseline (`_advance_drift_baseline_after_apply`), persists the
per-topic wiki, and marks the proposed topic applied.

**Legacy accept / replace / merge / ignore** (`lib/topics/proposals/topic_actions.py`,
web routes in `proposals.py` and `maintenance.py`). Each composes a one-topic
`GraphDiff` and routes it through the same `apply_diff` write path via
`_apply_topic_change`, which runs a pre/post `audit_graph` diff so an operation
can never *introduce* new errors (pre-existing rot in unrelated topics becomes
advisory `graph_warnings` rather than a hard block). `accept` creates a new
topic, `replace` atomically swaps an existing id (used when a regenerated draft
re-uses an approved id), `merge` folds a draft's refs/aliases/globs/commands
into an existing topic while keeping that topic's wiki, and `ignore` marks a
proposed topic ignored without touching the graph. `_approved_topic_from_proposal`
+ `_approved_refs_from_proposal` + `_approved_edges_from_proposal` are the
proposal→approved-graph converters (drop unknown ref roles, validate edge types,
filter edges whose target isn't a real topic).

**Downgrade** (`lib/topics/proposals/downgrade.py`, web
`POST .../topics/<id>/downgrade`) is the reverse: it lifts an approved topic back
into a proposal draft, preferring to append a `downgraded` revision onto the
run that originally brought the topic in (falling back to a fresh
`approved-topic-downgrade` proposal when there's no provenance pointer). It drops
the topic + its inbound edges from the approved graph (atomically), records the
pruned inbound edges in run metadata so a later apply can restore them, and
persists the topic's wiki into the proposal dir for re-apply.

## Trace emission

Every run emits OTel-style spans through `_emit` (`proposal_external.py:588`)
under trace id `topic-proposal-<id>`, tagged `agent_type=topic-proposal-agent`:
`proposal.agent.start`, `.instructions`, `.stdout`/`.stderr`,
`.permission_request`, `.complete` / `.failure` / `.cancelled`, bracketed by
`session.start` / `session.title` / `session.end`. The wrapper run also links to
the *real* tool-using agent's session trace via `agent_trace_id` /
`agent_trace_url` so the dashboard can jump from the pipeline run to the agent
that actually authored the draft.

## Reading order for new contributors

1. `lib/topics/proposals/__init__.py` — the package map + public API re-exports.
2. `lib/topics/proposal_providers.py` — the single external-agent provider.
3. `lib/topics/proposals/external_jobs.py` — `start_external_proposal_run` /
   `start_external_regenerate_run` and their daemon-thread jobs.
4. `lib/topics/proposal_external.py` — the subprocess runner, the drafting
   prompt (`_instructions`), the two-axis `write_status`, and the validation
   guards.
5. `lib/topics/proposals/finish.py` — notify-on-finish ingest.
6. `lib/topics/proposals/run_control.py` + `core_io.py:stop_proposal_run` +
   `reap.py` — cancel + strand-reaping.
7. `lib/topics/proposals/core_io.py` — load/save/list + review-state + restore.
8. `lib/topics/proposals/topic_actions.py` + `web/blueprints/topics/apply.py` —
   the promote-to-graph layers (legacy actions + modern `/diff` + `/apply`).
9. `lib/topics/proposals/downgrade.py` — the reverse path.