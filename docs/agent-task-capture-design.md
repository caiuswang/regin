# Design: agent tasks — capture, execution, and dependencies

**Status:** slice 1 (capture, API keys, CLI) and its WebUI are built. Slice 3
(arm / run / stop / DAG) is specified below, not yet built. Slice 2 (Telegram
inbound) is spec only.

## Problem

Ideas arrive when you are not at the machine that can act on them — mid-session,
on a phone, from a third-party tool. regin has no place to put one. It has a
rich *execution* layer (`lib/agent_sdk/supervisor.launch_run`, which already
takes the full parameter set: prompt, cwd, env, permission mode, model, effort,
one-shot, resume) and no *intent* layer in front of it.

## Decision

A task is an **idea first**. Capture is cheap, unauthenticated-by-human, and
reachable from third parties. Everything privileged happens later, on one
authenticated surface.

### The safety invariant

> Captured tasks always land with status `idea`. A capture request can never set
> execution parameters, and an API key can never satisfy a user-session check.

Without this, a capture endpoint that accepted `cwd` + `permission_mode` would be
remote code execution. With it, the worst a leaked capture key can do is add rows
to a backlog. Every design choice below follows from it.

## Data model

Two new tables. Per the schema-drift rule in `CLAUDE.md`, each is **three** edits:
an Alembic revision (`alembic/versions/0017_*.py`, on top of
`0016_agent_run_sessions.py`), the matching `db/schema.sql` change, and the
SQLModel definition.

### `api_keys` → `lib/orm/models/api_keys.py`

| column | notes |
|---|---|
| `id` | pk |
| `name` | human label, shown in the UI |
| `key_hash` | SHA-256 of the full key, **unique + indexed** — the only lookup path |
| `key_prefix` | first 8 chars, for display only ("rgn_a1b2…") |
| `scopes` | JSON list; `task:capture` is the only scope defined |
| `created_by` | `users.id` of the admin who minted it |
| `created_at`, `last_used_at`, `revoked_at` | `revoked_at` non-null ⇒ rejected |

Key format `rgn_<32 hex>`, generated with `secrets.token_hex`, **displayed once**
at creation and never again. Lookup is a direct index hit on the hash of a
high-entropy secret, so there is no timing-comparison concern to design around.

### `agent_tasks` → `lib/orm/models/tasks.py`

| column | notes |
|---|---|
| `id` | pk |
| `title` | ≤200 chars, required |
| `body` | ≤8000 chars, nullable |
| `status` | `idea` \| `archived` in these slices |
| `source` | `web`, `cli`, `telegram`, … — who captured it |
| `external_id` | the source's own stable id; nullable |
| `tags_json`, `links_json` | TEXT, same convention as `lib/orm/models/proposals.py` |
| `created_by` | `users.id`, NULL for machine-originated |
| `created_at`, `updated_at` | |

`UNIQUE(source, external_id)`. NULLs do not collide in a unique index on either
SQLite or MySQL, so hand-typed captures with no external id coexist freely.

The `status` column is deliberately a string, not a boolean or an enum table: the
lifecycle grows to `armed`/`queued`/`running`/`done`/`failed`, and an extensible
category column beats a flag that has to be replaced later.

## Slice 1 — API keys and the capture endpoint

### Scope enforcement is a separate seam from user auth

`lib/auth.py` gains `require_scope(scope)`. It reads `Authorization: Bearer rgn_…`,
resolves the key, and **does not populate `g._current_user`**. That omission is
the control: `require_auth` and `require_editor` both go through
`get_current_user()`, so an API key cannot pass either of them no matter which
route it reaches. A key is not a weak user; it is a different principal.

Key CRUD is `require_role("admin")` and lives on `/api/api-keys`.

### `POST /api/tasks/capture`

```
Authorization: Bearer rgn_<key>          scope: task:capture
{ "title": str,            # required, ≤200
  "body": str,             # optional, ≤8000
  "source": str,           # required, ≤40, [a-z0-9_-]
  "external_id": str,      # optional, ≤200
  "tags":  [str],          # optional, ≤10 items
  "links": [{"label","href"}] }   # optional, ≤10 items
```

| outcome | status |
|---|---|
| new task created | `201 {"id":…, "created": true}` |
| `(source, external_id)` already present | `200 {"id":…, "created": false}` |
| unknown field present in body | `400` naming the field |
| malformed / over-length | `400` |
| missing, unknown, or revoked key | `401` |
| valid key without `task:capture` | `403` |
| over the per-key rate limit | `429` |

**Unknown fields are a loud 400, not a silent drop.** A caller trying to pass
`cwd` or `permission_mode` must find out immediately; silently ignoring them is
how a misconfigured integration becomes a false sense of capability.

**Idempotency is identifier-based.** Replay is resolved by the
`(source, external_id)` unique constraint, never by a time window — every webhook
provider redelivers, and a dedup window would be a clock heuristic. A replay
updates `updated_at` and returns the existing id.

Rate limiting is per key, reusing the limiter shape already in
`lib/agent_bridge/delivery.py`.

### CLI

`regin task add "<title>" [--body … --tag …]`, `regin task list`,
`regin task show <id>` in `cli/commands/tasks.py`. Local CLI writes go straight
through `lib/tasks/store.py`, not over HTTP.

### Logging

`lib/activity_log.py` with logger name `tasks`: `log.write("task_captured", …)`,
`log.write("api_key_created", …)`, `log.error("capture_rejected", …)`. Key
material never reaches a log call — the wrapper's redaction is defense in depth,
not the control.

## Slice 2 — Telegram inbound

The bot token and chat id already exist under `settings.agent_messages` for
**outbound** push (`lib/agent_messages/push/telegram.py`). The same bot receives.
No new credential, no new app, no inbound port.

### Transport

`lib/tasks/ingest/telegram.py` long-polls the Bot API `getUpdates` with
`timeout=25`, in a daemon thread started next to `_start_pending_reaper` in
`cli/commands/server.py`, gated by `agent_messages.telegram_ingest_enabled`
(default `false`).

The long poll is a **blocking call the server holds open**, not a scheduled
wake-up — the Bot API returns as soon as an update exists. There is no polling
interval to tune and no skew window anywhere in this design.

### Two-layer replay defense

1. **Cursor.** `getUpdates` is acknowledged by passing `offset = last_update_id + 1`.
   The cursor is persisted in a new `integration_cursors(source PK, cursor,
   updated_at)` row so a server restart does not re-read the backlog.
2. **Identity.** Every captured update also carries
   `external_id = "tg:<update_id>"`, so even a lost or rolled-back cursor cannot
   create a duplicate — the unique constraint catches it.

The cursor is an optimization; the identifier is the correctness guarantee.

### Behaviour

- Only updates whose `chat.id` equals `agent_messages.telegram_chat_id` are
  captured. Anything else is dropped and counted via the activity log — the bot
  is discoverable by username, so an allowlist is mandatory, not optional.
- Message text becomes `title` (first line, truncated) + `body` (remainder).
- The bot replies `✓ captured #<id>` via `base.http_post_json`, reusing the
  transport helper the push channel already uses.
- Non-text updates (photos, stickers, edits) are ignored in these slices.

## Settings

Added to `AgentMessagesConfig` in `lib/settings.py` — the block is already
`scope: local`, which is what keeps the bot token out of git-tracked settings:

- `telegram_ingest_enabled: bool = False`

New `TasksConfig` block:

- `capture_enabled: bool = False`
- `capture_rate_limit_per_minute: int = 30`

Both must be reachable from the Settings UI. That means the existing hand-
maintained lists: `_AGENT_MESSAGES_FIELDS` and a new `_TASKS_FIELDS` +
`_settings_blocks()` entry in `web/blueprints/settings.py`, and `SECTIONS` +
`BLOCK_META` + the sidebar button in `frontend/src/views/SettingsView.vue`. A
settings field with no UI is invisible.

## Acceptance checklist

1. A `POST /api/tasks/capture` with a valid `task:capture` key creates a row with
   `status = 'idea'` and returns `201`.
2. The same request replayed verbatim returns `200 {"created": false}` and the
   table still holds exactly one row.
3. A capture body containing `cwd`, `model`, or `permission_mode` returns `400`
   naming the field, and creates nothing.
4. An API key presented to `require_auth`/`require_editor` routes (e.g.
   `POST /api/agent-runs`) returns `401` — proving `g._current_user` is never set
   by the key path.
5. A revoked key returns `401`; a valid key lacking the scope returns `403`.
6. Exceeding `capture_rate_limit_per_minute` on one key returns `429` and the
   next minute succeeds.
7. With ingest enabled, a Telegram message from the allowlisted chat produces one
   `agent_tasks` row with `source='telegram'`, `external_id='tg:<update_id>'`; a
   message from any other chat produces none.
8. Restarting the server with the cursor row deleted re-reads delivered updates
   and creates **zero** new rows (the identity layer holds on its own).
9. `regin task add` / `list` / `show` round-trip a task without the server
   running.
10. Fresh `regin init` and `regin migrate` on an existing DB both produce the same
    schema — `db/schema.sql` and the `0017` revision agree.

## Gates

- `.venv/bin/python -m pytest -q -n auto --dist=loadfile` — whole suite, because
  the diff touches `lib/orm/` and `lib/settings.py`.
- radon grade ≥ C and grit clean on every touched `**/*.py`.
- `cd frontend && npx vite build` for the SettingsView change.
- Independent fresh-context review of the diff.

## Out of scope

Conditions/fuses (a task becoming runnable because the world changed rather than
because its dependencies finished), the Linear and GitHub capture adapters, and
the public tunnel with HMAC verification. Nothing in slices 1–2 can start an
agent; that is slice 3's job alone.

## Slice 3 — arm, run, stop, and the DAG

Capture gives regin an intent layer; this slice connects it to the execution
layer that already exists. `lib/agent_sdk/` needs no new engine — `supervisor.launch_run`
launches, `registry.stop_run` stops, `registry.interrupt_run` interrupts, and
`web/blueprints/agent_runs.py` already exposes all three. What is missing is the
join between a task row and a run.

### Schema (Alembic `0018`, `db/schema.sql`, SQLModel — three edits again)

`agent_tasks` gains `params_json`, `verify_cmd`, and `autorun`. The status set
extends to `idea | armed | queued | running | done | failed | archived`.

`agent_task_runs` — append-only, one row per attempt: `task_id`, `trace_id`
(joins `agent_runs.trace_id`, which brings cost, spans, and grade for free),
`attempt`, `status`, `started_at`, `ended_at`, `exit_reason`, `verify_exit_code`.

`agent_task_deps(task_id, depends_on_id)` — an edge table, not an `after: []`
column, because both directions are queried (what blocks X, what X blocks) and
acyclicity must be enforced on insert. A self-edge and any cycle are rejected at
write time, so no reader has to defend against one.

### Arming is the privileged step

`POST /api/tasks/<id>/arm` (`require_editor`) is the only route that may write
execution parameters. It validates against an allowlist mirroring `launch_run`'s
signature — `cwd`, `model`, `permission_mode`, `effort`, `one_shot`, `worktree`,
a bounded `env` — and rejects unknown fields by name. This is what preserves the
capture invariant: a captured task arrives inert, and only an authenticated
human makes it runnable.

`skill` is the one arm field that is not a `launch_run` parameter. It names a
slash command (`{name, args}`, `lib/tasks/arm.py::_skill`) and shapes the launch
prompt instead: `runs._invocation` puts `/<name> <args>` on the first line and
the task body after it, so the CLI resolves the skill exactly as a typed
command and the body arrives as the rest of its arguments — a task can be armed
to run *as* `/goal-verified` or `/tdd` rather than as a bare prompt. The arm
sheet suggests the ids `/api/skills` knows but accepts any name, because the
launched CLI resolves skills from the operator's own disk, not from regin.

### Run and stop delegate; they do not reimplement

`POST /api/tasks/<id>/run` refuses with `409` when a dependency is unmet (naming
the blocking ids) or when the task already has a live run. It writes the
`agent_task_runs` row before launching, so a crash between the two leaves a
visible `starting` row rather than an untracked child.

`POST /api/tasks/<id>/stop` calls `registry.stop_run(trace_id)` and reports what
it returned. It must **not** mark the row terminal itself: a stop that only
closes the prompt queue lands between turns and silently fails on a turn that
never ends, so the authority on "this run ended" is the run's own completion
callback, never the route that asked it to.

### A status row is a claim, not proof of liveness

`agent_task_runs.status` outlives the process that wrote it, exactly as
`AgentRun.status` does — a runner killed with the server leaves a `running` row
with nothing behind it. Three rules follow:

- The terminal write happens in a `finally` on the run's completion path, and
  teardown is idempotent.
- Readers merge the row's claim with liveness from `agent_runs`/the registry
  rather than trusting `status == 'running'`.
- A row is never condemned on pid inequality alone; the recorded pid is probed,
  or a foreign process reaps a live peer's runs.

### Firing is event-driven

When a run completes, its callback resolves which dependents just became
runnable — every dependency `done`. There is no scheduler and no interval: the
completion that could change the answer is the thing that recomputes it.

Completion is not the only such write. Arming a task whose dependencies already
finished, and editing a dependency set so nothing blocks, recompute the same
answer through `runs.fire_if_ready` — without them a task can become runnable
with no event left to notice.

A dependency edge is the run grant: arming a task behind another one is the
operator saying "run this when that lands", so the release launches it and asks
for no second opt-in. A launch refused at release time (a full
`max_concurrent_runs`, a worktree that cannot be made) emits a `task.ready`
event through `lib/agent_messages/events.py` naming the refusal, and the task
stays `armed`.

## Slice 4 — images on an idea

An idea is often a screenshot. This slice lets one be pasted into the capture
composer, re-edited after the task is saved, and delivered to the agent when
the task runs.

### Schema (Alembic `0019`, `db/schema.sql`, SQLModel)

`agent_task_images(id, task_id, idx, media_type, bytes, byte_size, sha256,
created_at)`, `UNIQUE(task_id, idx)`.

Keyed to the **task**, which is what separates it from `bridge_attachments`. An
attachment belongs to a trace and is claimed and spent by the first prompt that
carries its `[Image #N]` marker; a task has no trace until someone runs it —
maybe never, maybe three times — so its images are re-read on every attempt
instead. Nothing is claimed and nothing is swept, and `idx` carries no marker:
a task's prompt is its title and body, and the images ride the launch beside
it. A freed `idx` is never reissued, so a rendered thumbnail cannot quietly
become a different picture.

Validation is `attachments.sniff_media_type` — magic bytes, not the content
type a paste event claimed — under the same `prompt_image_max_bytes` /
`prompt_images_max_count` ceilings the bridge composer answers to, because the
bytes end up in the same place: a model's context.

### The composer uploads after the row exists

`POST /api/tasks/<id>/images` takes one base64 image against a task that
already exists. The capture composer therefore holds pasted files in the
browser and flushes them once `POST /api/tasks` returns an id, rather than
staging them server-side against a draft id. An idea abandoned mid-typing
leaves nothing behind, so there is no orphan sweep to write.

`GET`/`DELETE /api/tasks/<id>/images/<idx>` serve and detach. Writes are
`require_editor`; the bytes are `require_auth` like every other read. A task
payload carries `images: [{id, idx, media_type, byte_size}]` — metadata only,
fetched for a whole page in one query, because inlining base64 would turn a
list read into a download.

### Deleting is not archiving

`archived` is a filing decision that keeps the row, its attempts, its images
and its links, and the edit surface can move it back to `idea`. `DELETE
/api/tasks/<id>` (`require_editor`) keeps nothing: `store.delete_task` removes
the task plus every row keyed to it, in one transaction, and there is no
restore. `DELETE /api/tasks/bulk` does the same for a selection and reports
`{deleted, rejected}` per row, the way `POST /api/tasks/bulk` does.

Dep edges go in **both** directions. An edge left pointing at a deleted
blocker makes `deps.blocking()` return an id that no longer resolves, and the
dependent then waits on it forever with nothing reporting why. Dependents the
removal unblocks are deliberately *not* fired: launching an agent as a side
effect of a deletion is not what the operator asked for.

The refusal is the one `_archive_refusal` already makes — `runs.live_run` is a
probe, not a reading of `status`, so a `running` claim left behind by a dead
runner does not wedge the row, while an agent still reachable is a `409`.

`regin task delete <id>` is the local mirror; it echoes the title before the
prompt, because an operator confirming a bare id is confirming a number.

### Delivery threads through the existing prompt path

`supervisor.launch_run(images=…)` → `run_session` → `runner.enqueue`, where
`_query_payload` already turns `{media_type, data_b64}` entries into content
blocks. The announced prompt span stays the plain sentence: the span is the
trace's copy of what was *said*, and the bytes belong to the turn.

### Out of scope

The delivered images are not copied into `prompt_images`, so the trace view
renders the launch prompt without thumbnails. Binding them to the announced
span is the natural next step.
