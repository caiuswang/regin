# Session trace design

regin captures every Claude Code session as an OpenTelemetry-style trace: one `trace_id`, many spans, and a denormalized per-session aggregate row that keeps the dashboard list cheap.

## Data model (`lib/orm/models/trace.py`)

Three tables form the core:

- **`session_spans`** — single source of truth. Each row is one operation (`prompt`, `tool.Edit`, `rule.check`, `memory.recall`, `skill.read`, `session.start`, `cwd.changed`, …). Carries `trace_id`/`span_id`/`parent_id`, timing, `attributes` (JSON blob), status, and per-tool token attribution (`input_tokens`/`output_tokens`/`image_tokens`/`cost_usd`, plus `tool_use_id` and `turn_uuid` linking the span back to the Anthropic turn that billed it).
- **`session_trace_map`** — skeleton mirror of `session_spans` without the heavy `attributes` JSON. The frontend loads the whole tree shape from here and fetches `attributes` lazily per span.
- **`sessions`** — one row per `trace_id`, incrementally maintained at ingest time. Holds the title (with `title_source`), status, `started_at`/`last_seen`/`ended_at`, and rollup counters (`span_count`, `skill_reads`, `file_edits`, `rule_checks`, `plan_enters`, `prompts`, `tool_calls`). Token aggregates (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `peak_context_tokens`, `peak_main_context_tokens`, `context_window_tokens`, `cost_usd`) and `active_work_ms` (union of root-span intervals) are layered on by the turn trace handler.

Narrower side tables hang off the trace:

- **`turn_usage`** — one row per assistant turn keyed by `(trace_id, turn_uuid)`. Records the Anthropic `usage` block plus `effort_level` and `reasoning_tokens`. Kept off `session_spans` because turns aren't timeline ops.
- **`skill_reads`** — emitted by the `skill_read` PostToolUse handler when a skill file is read.
- **`plan_sessions`** — plan-mode draft/review timestamps, written by `plan_trace`.
- **`session_repos`** — multi-repo join. A session links to a registered repo only via high-signal spans (the `session.start` cwd, any `cwd.changed`, or a file-mutating tool — `Edit`/`Write`/`apply_patch`). Reads and Bash are deliberately excluded so an incidental read into another repo never tags the session.
- **`prompt_images`** — decoded image bytes attached to a `prompt` span, indexed by the `[Image #N]` marker in the prompt text.

## Capture path (hook handlers)

Claude Code emits hook events; `hook_manager.handlers.*_trace` translate them into span batches:

- `session_lifecycle.py` opens/closes `session.start` and writes the session's starting `cwd`.
- `prompt_trace.py` records a `prompt` span on UserPromptSubmit and persists image parts to `prompt_images`.
- `post_tool_trace.py` emits `tool.<Name>` spans on PostToolUse (with `tool_use_id` so token attribution can later be matched against the parent turn).
- `trace_payload.py` validates and normalizes hook payloads before any of the above run (see `lib/trace/payload_validation.py`).
- `turn_trace.py` pulls the Anthropic `usage` block from the transcript JSONL and writes `turn_usage`, then rolls per-turn totals up onto `sessions` (peak context, cost, cache hits, `active_work_ms`).
- Domain-specific spans (`rule.check`, `skill.read`, `cwd.changed`, `plan.*`, compact/subagent/permission events) come from their respective handlers under `hook_manager/handlers/`.

All of those routes terminate at the trace service's ingest API: handlers POST batches to `/api/spans` (`web/blueprints/trace/spans_ingest.py`), which delegates to `ingest_session_spans` in `lib/trace/trace_service/ingest.py`. That module owns the ~60-line `ON CONFLICT DO UPDATE` upsert for spans, the parallel `_SESSIONS_UPSERT_SQL` that increments the `sessions` aggregates by counter bucket (`_span_counter_buckets` decides which counters a given span name advances), and the secondary writes for status (`ingest_session_status`), per-tool token attribution (`ingest_tool_attribution`), and turn usage (`ingest_turn_usage`).

Two helper modules sit alongside ingest:

- `lib/trace/transcript_parsers.py` + `lib/trace/transcript_usage.py` walk the Claude Code session JSONL to extract turns and usage blocks (the same source that drives `turn_trace`).
- `lib/trace/repair.py` retro-fits sessions whose spans landed before their parent (out-of-order ingest), and `materialize_session` rebuilds the `session_trace_map` skeleton + `sessions` aggregates from scratch for one `trace_id` — invoked from the SessionTraceView when a user clicks "rematerialize".

## Serve-time merge: append-only store, read-time reconcile

`session_spans` is append-only — rows are never destructively rewritten to fix structure. Two writers disagree about the same turn: live hook events (`source='hook'`: tool timing, permissions, the in-flight `promptlive-` prompt placeholder) and the transcript scan (`source='transcript'`: the real `prompt-<uuid>` anchor, assistant_response / assistant.thinking, local commands). A placeholder and its resolved counterpart therefore coexist on disk by design.

`merge_spans` in `lib/trace/merge.py` is the single place that reconciles them. It runs at read time as a pure function over one window — no DB writes, no mutation — so projection rules can change without a data migration; they self-heal on the next read. In order:

1. **Dedup / supersession** (`_drop_superseded_placeholders`). Placeholder ids are deterministic, minted by `lib/trace/pending_spans.py` under reserved prefixes: `promptlive-<sha1(session + text prefix)>` for prompts, `pending-<tool_use_id>` for blocking tools, `permreq-<tool_use_id>` for permission gates. A resolved span names the placeholders it supersedes via `pending_id_for_resolved`; those drop from the window, while a placeholder with no resolved counterpart survives — that's exactly how the live view shows in-flight work. Before a tool placeholder drops, `_inherit_turn_linkage` hands its `turn_uuid` + `resp-`/`think-` parent to the resolved span when the survivor never got its own: a slow tool resolves after the turn's attribution pass already ran (and the turn is cached, never re-attributed), so without the hand-off the resolved span would strand on the prompt-root graft fallback. The transfer is gated on the survivor's NULL `turn_uuid`, which also overrides any prompt-root parent a prior materialize baked in (`_persist_projection` writes `parent_id` but never `turn_uuid`).
2. **Permission retire by tool name** (`_drop_resolved_permission_requests`). Claude Code's PermissionRequest payload carries no `tool_use_id`, so pending `permission.request` rows are correlated by `tool_name` instead: a non-pending `tool.<X>` (granted) or a `permission.denied` retires every pending request for that tool in the trace. Safe because permissions block the session one at a time.
3. **Stale-blocker sweep** (`_drop_stale_blockers`). Any PENDING row older than the newest prompt was implicitly abandoned by the user submitting again — an interrupted `AskUserQuestion` / permission gate, or a stray `promptlive-` for a client-only command that never produced a model turn. Keyed on the monotonic row `id`, not `start_time` (anchors are tz-aware, placeholders naive). Windowed readers thread the per-trace global max prompt id in as `prompt_id_ceiling` so a stray still drops when it happens to be the newest anchor inside an older scroll-up window.

Merge then hands off to `_graft_orphans` (`lib/trace/projection.py`), the deterministic reparent ladder: orphan prompts and boundary spans graft to the conversation span, dangling parents self-heal, the turn-linkage preference pass nests NULL-parent spans under their `resp-` / `think-` / prompt anchor by `turn_uuid`, a chronological under-the-current-prompt fallback catches genuinely turn-less events (rule checks, attachments, local commands), `turn` and `memory.recall` spans re-attribute by lookahead (both fire on UserPromptSubmit a few ms before the new prompt's anchor, so a plain chronological graft would wrongly nest them under the *previous* prompt — see `_SUBMIT_LOOKAHEAD_NAMES`), and subagent-owned spans nest under their `subagent.start` by `agent_id`.

Consequences of the design:

- A span that "disappears" or moves in the UI was superseded or misparented, never lost — the row is still on disk.
- Anything aggregating raw `session_spans` must exclude PENDING placeholder rows (ingest's counter buckets already do, via `is_pending_span_id`).
- Over an already-reconciled historical window every drop rule is a no-op, so `merge_spans(raw) == _graft_orphans(raw)` — the idempotency property the read path relies on.
- `materialize_session` runs bare `_graft_orphans` (not `merge_spans`) and never persists `turn_uuid`, so a NULL `turn_uuid` stays a reliable marker of an un-attributed resolved span across rematerializes.

## Read path (dashboard)

The Vue SPA hits the read endpoints in `web/blueprints/trace/`:

- `sessions.py` — list view, session detail, and the `materialize` action. Backed by `fetch_session_paginated` and `fetch_session_projection` in `lib/trace/trace_service/queries.py`, which lean on SQLite-specific `json_extract` plus CTEs with `ROW_NUMBER()` (the reason the read side stayed raw SQL instead of moving to SQLModel). Both run their window through the serve-time merge before projection; the paginated reader also returns `retired_ids` (raw − merged) so the frontend's append-only conversation cards can prune exactly the rows the merge dropped — without it the cards would show placeholder + resolved duplicates.
- `turn_usage.py` — per-turn token table for one trace, plus `fetch_tool_token_rollup` for the per-tool breakdown.
- `mcp_calls.py`, `skill_reads.py`, `prompt_images.py` — narrower projections of the same trace, paginated by `list_mcp_calls_page` / `list_skill_reads_page` and the image lookup.

`lib/trace/projection.py` is the shared shaping layer that turns raw span rows into the tree the frontend expects (parent linking, duration coercion, attribute decoding).

The frontend mounts two views:

- `frontend/src/views/SessionsView.vue` — the list. Headline ctx % is computed from `peak_main_context_tokens` so server-side sub-calls (advisor today, sub-agents tomorrow) don't inflate the main conversation's apparent context size; `peak_context_tokens` is shown alongside when they diverge.
- `frontend/src/views/SessionTraceView.vue` — the per-trace tree. Loads `session_trace_map` (skeleton only), then hydrates each span's `attributes` on demand from the spans endpoint. Plays well with the auth bypass note in memory ([[feedback_verify_webui_in_browser]]) for Playwright runs.

## Why the shape is what it is

Five design choices are worth remembering:

1. **`session_spans` is the source of truth; `sessions` is a cache.** Aggregates exist so the list view is O(1) per row; `materialize_session` exists so that cache can always be rebuilt from spans. Never edit aggregates by hand — call `materialize_session`.
2. **`session_trace_map` is the skeleton.** Splitting the JSON-heavy `attributes` blob off the tree skeleton is what makes long sessions navigable in the UI.
3. **Turns aren't spans.** Turn usage has no causal parent and no duration, so it lives in `turn_usage` keyed by `(trace_id, turn_uuid)` and gets rolled up onto `sessions` separately.
4. **Repo membership uses high-signal events only.** `session_repos` is populated from cwd + file-mutating tools, not reads or Bash, so an incidental cross-repo read never mistags a session ([[feedback_repo_tag_detection]]).
5. **Reconcile at read time, not write time.** The store is append-only; placeholder/resolved disagreements are resolved per read window by `merge_spans` (`lib/trace/merge.py`), so projection-rule changes ship as code, self-heal on the next read, and never require a data migration. The price is that every raw reader must go through the merge (or exclude PENDING rows itself).

Adjacent topics: rule lint events ride the same span pipeline ([[rule-engine-design]]); plan-mode draft/review timestamps are captured into `plan_sessions` and surfaced in their own view.