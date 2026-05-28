# Session trace design

regin captures every Claude Code session as an OpenTelemetry-style trace: one `trace_id`, many spans, and a denormalized per-session aggregate row that keeps the dashboard list cheap.

## Data model (`lib/orm/models/trace.py`)

Three tables form the core:

- **`session_spans`** — single source of truth. Each row is one operation (`prompt`, `tool.Edit`, `rule.check`, `skill.read`, `session.start`, `cwd.changed`, …). Carries `trace_id`/`span_id`/`parent_id`, timing, `attributes` (JSON blob), status, and per-tool token attribution (`input_tokens`/`output_tokens`/`image_tokens`/`cost_usd`, plus `tool_use_id` and `turn_uuid` linking the span back to the Anthropic turn that billed it).
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

## Read path (dashboard)

The Vue SPA hits the read endpoints in `web/blueprints/trace/`:

- `sessions.py` — list view, session detail, and the `materialize` action. Backed by `fetch_session_paginated` and `fetch_session_projection` in `lib/trace/trace_service/queries.py`, which lean on SQLite-specific `json_extract` plus CTEs with `ROW_NUMBER()` (the reason the read side stayed raw SQL instead of moving to SQLModel).
- `turn_usage.py` — per-turn token table for one trace, plus `fetch_tool_token_rollup` for the per-tool breakdown.
- `mcp_calls.py`, `skill_reads.py`, `prompt_images.py` — narrower projections of the same trace, paginated by `list_mcp_calls_page` / `list_skill_reads_page` and the image lookup.

`lib/trace/projection.py` is the shared shaping layer that turns raw span rows into the tree the frontend expects (parent linking, duration coercion, attribute decoding).

The frontend mounts two views:

- `frontend/src/views/SessionsView.vue` — the list. Headline ctx % is computed from `peak_main_context_tokens` so server-side sub-calls (advisor today, sub-agents tomorrow) don't inflate the main conversation's apparent context size; `peak_context_tokens` is shown alongside when they diverge.
- `frontend/src/views/SessionTraceView.vue` — the per-trace tree. Loads `session_trace_map` (skeleton only), then hydrates each span's `attributes` on demand from the spans endpoint. Plays well with the auth bypass note in memory ([[feedback_verify_webui_in_browser]]) for Playwright runs.

## Why the shape is what it is

Four design choices are worth remembering:

1. **`session_spans` is the source of truth; `sessions` is a cache.** Aggregates exist so the list view is O(1) per row; `materialize_session` exists so that cache can always be rebuilt from spans. Never edit aggregates by hand — call `materialize_session`.
2. **`session_trace_map` is the skeleton.** Splitting the JSON-heavy `attributes` blob off the tree skeleton is what makes long sessions navigable in the UI.
3. **Turns aren't spans.** Turn usage has no causal parent and no duration, so it lives in `turn_usage` keyed by `(trace_id, turn_uuid)` and gets rolled up onto `sessions` separately.
4. **Repo membership uses high-signal events only.** `session_repos` is populated from cwd + file-mutating tools, not reads or Bash, so an incidental cross-repo read never mistags a session ([[feedback_repo_tag_detection]]).

Adjacent topics: rule lint events ride the same span pipeline ([[rule-engine-design]]); plan-mode draft/review timestamps are captured into `plan_sessions` and surfaced in their own view.