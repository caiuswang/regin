# Session trace design

regin captures every Claude Code session as an OpenTelemetry-style trace: one `trace_id`, many spans, and a denormalized per-session aggregate row that keeps the dashboard list cheap.

## Data model (`lib/orm/models/trace.py`)

Three tables form the core:

- **`session_spans`** — single source of truth. Each row is one operation (`prompt`, `tool.Edit`, `rule.check`, `skill.read`, `session.start`, `cwd.changed`, …). Carries `trace_id`/`span_id`/`parent_id`, timing, `attributes` (JSON blob), status, and per-tool token attribution (`input_tokens`/`output_tokens`/`image_tokens`/`cost_usd`, plus `tool_use_id` and `turn_uuid` linking the span back to the Anthropic turn that billed it).
- **`session_trace_map`** — skeleton mirror of `session_spans` without the heavy `attributes` JSON. The frontend loads the whole tree shape from here and fetches `attributes` lazily per span.
- **`sessions`** — one row per `trace_id`, incrementally maintained at ingest time. Holds the title (with `title_source`), status, `started_at`/`last_seen`/`ended_at`, and rollup counters (`span_count`, `skill_reads`, `file_edits`, `rule_checks`, `plan_enters`, `prompts`, `tool_calls`). Token aggregates (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `peak_context_tokens`, `peak_main_context_tokens`, `context_window_tokens`, `cost_usd`) and `active_work_ms` (union of root-span intervals) are layered on by the turn trace handler. `origin` (`session` vs `workflow`) and `agent_kind` (`claude`/`codex`/`kimi`/…) tag the row for the list-view facets.

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
- `turn_trace/` pulls the Anthropic `usage` block from the transcript JSONL and writes `turn_usage`, then rolls per-turn totals up onto `sessions` (peak context, cost, cache hits, `active_work_ms`).
- Domain-specific spans (`rule.check`, `skill.read`, `cwd.changed`, `plan.*`, compact/subagent/permission events) come from their respective handlers under `hook_manager/handlers/`.

All of those routes terminate at the trace service's ingest API: handlers POST batches to `/api/spans` (`web/blueprints/trace/spans_ingest.py`), which delegates to `ingest_session_spans` in `lib/trace/trace_service/ingest.py`. That module owns the `ON CONFLICT DO UPDATE` upsert for spans, the parallel `_SESSIONS_UPSERT_SQL` that increments the `sessions` aggregates by counter bucket (`_span_counter_buckets` decides which counters a given span name advances), and the secondary writes for status (`ingest_session_status`), per-tool token attribution (`ingest_tool_attribution`), and turn usage (`ingest_turn_usage`).

Two helper modules sit alongside ingest:

- `lib/trace/transcript_parsers.py` + `lib/trace/transcript_usage.py` walk the Claude Code session JSONL to extract turns and usage blocks (the same source that drives the turn-trace handlers).
- `lib/trace/repair.py` retro-fits sessions whose spans landed before their parent (out-of-order ingest), and `materialize_session` rebuilds the `session_trace_map` skeleton + `sessions` aggregates from scratch for one `trace_id` — invoked from the trace view's rematerialize action.

## Serve-time merge: append-only store, read-time reconcile

`session_spans` is append-only — rows are never destructively rewritten to fix structure. Two writers disagree about the same turn: live hook events (`source='hook'`: tool timing, permissions, the in-flight `promptlive-` prompt placeholder) and the transcript scan (`source='transcript'`: the real `prompt-<uuid>` anchor, assistant_response / assistant.thinking, local commands). A placeholder and its resolved counterpart therefore coexist on disk by design.

`merge_spans` in `lib/trace/merge.py` is the single place that reconciles them. It runs at read time as a pure function over one window — no DB writes, no mutation — so projection rules can change without a data migration; they self-heal on the next read. It drops superseded placeholders (deterministic ids minted by `lib/trace/pending_spans.py`), retires resolved permission requests by tool name, and sweeps stale blockers older than the newest prompt, then hands off to `_graft_orphans` (`lib/trace/projection.py`) — the deterministic reparent ladder that nests NULL-parent spans under their `resp-`/`think-`/prompt anchor by `turn_uuid`, re-attributes `turn` spans, and nests subagent-owned spans under their `subagent.start`. The full mechanics live under the [[trace-merge-reconcile]] topic; the read path here only needs to know that every raw reader must go through the merge (or exclude PENDING rows itself).

Consequences that matter to the read path:

- A span that "disappears" or moves in the UI was superseded or misparented, never lost — the row is still on disk.
- Anything aggregating raw `session_spans` must exclude PENDING placeholder rows (ingest's counter buckets already do, via `is_pending_span_id`).
- `materialize_session` runs bare `_graft_orphans` (not `merge_spans`) and never persists `turn_uuid`, so a NULL `turn_uuid` stays a reliable marker of an un-attributed resolved span across rematerializes.

## Read API (`web/blueprints/trace/`)

The Vue SPA hits the read endpoints in this blueprint:

- `sessions.py` — keyset-paginated list, session detail / `/map` skeleton, and the `materialize`, `close`, delete, and batch-delete actions. Backed by `fetch_session_paginated` and `fetch_session_projection` in `lib/trace/trace_service/queries.py`, which lean on SQLite-specific `json_extract` plus CTEs with `ROW_NUMBER()` (the reason the read side stayed raw SQL instead of moving to SQLModel). Both run their window through the serve-time merge before projection; the paginated reader also returns `retired_ids` (raw − merged) so the frontend's append-only conversation cards can prune exactly the rows the merge dropped — without it the cards would show placeholder + resolved duplicates.
- `turn_usage.py` — per-turn token table for one trace, plus `fetch_tool_token_rollup` for the per-tool breakdown that feeds `ToolTokenRollup`.
- `mcp_calls.py`, `skill_reads.py`, `prompt_images.py` — narrower projections of the same trace, paginated by `list_mcp_calls_page` / `list_skill_reads_page` and the image lookup.

`lib/trace/projection.py` is the shared shaping layer that turns raw span rows into the tree the frontend expects (parent linking, duration coercion, attribute decoding).

## Dashboard: the session list (`frontend/src/views/SessionsView.vue`)

The list view is a keyset-paginated table. `useCursor` drives it against `/sessions` (page size 50): load-more appends, any filter change resets. Its `buildQuery` assembles the request from a bank of faceted filters, each persisted to `localStorage` and each omitted from the URL when it equals the server default:

- **Range** — `last_seen` presets (today / yesterday / 7d / 30d / all). Boundaries are computed in the browser's local clock and serialized as naive local ISO so the server's lexicographic string compare matches the stored text format.
- **Kind** — `real` / `test` / `all` (real is the default; seeded once from the legacy `regin_sessions_show_tests` flag).
- **Workflow runs** — an orthogonal axis to Kind, keyed on the row's `origin` (`session` vs `workflow`): `hide` (default) / `show` / `only`. When runs are hidden the server reports `workflow_hidden_count`, and when only-runs hides some by date it reports `workflow_date_hidden_count`; both surface as one-click hint buttons (from `extras`).
- **Status** — any / active / inactive.
- **Repo** — options from `/api/repos`; a multi-repo session matches every repo it touched.
- **Trace ID** — case-insensitive prefix match, committed on Enter.
- **Search** with a **scope** selector (title / prompt / both).

Each row (`SessionRow` on desktop, a mobile card list under `sm:hidden`) renders an agent-kind icon (`workflow`/`claude`/`codex`/`kimi`/generic), status pills (active / ended / closed / test / workflow), activity counters, and a context badge. The headline **ctx %** is computed from `peak_main_context_tokens` so server-side sub-calls (advisor, sub-agents) don't inflate the main conversation's apparent context size; an all-inclusive `+sub` badge appears alongside only when it diverges from the main figure by more than 1%. Active-vs-idle detection is shared via `utils/sessionActivity.js` (one source for this view, `SessionRow`, and the `/live` poll cadence). Row actions cover single delete, multi-select batch delete, and **Close** — which settles a corrupt or interrupted session that never emitted a SessionEnd by flipping its status to ended while keeping the trace data.

## Dashboard: the per-trace view (`frontend/src/views/SessionTraceView.vue`)

The browser route is `/trace/sessions/<trace_id>` (data API `/api/sessions/<id>/map`, [[trace_view_correct_url_and_endpoints]]). The view is a thin orchestrator over a fleet of composables that each own one slice of state:

- **`useTraceData`** — the core. Owns `treeNodes` + pagination and every fetch/merge/reconcile primitive (`loadSession`, `reloadLiveTail`, `loadOlder`, the `ensure*Loaded` lazy loaders), mutating the SFC-owned `session` / `selectedSpan` refs threaded in. It loads the `session_trace_map` skeleton first; the tree shape renders before any heavy `attributes` are fetched.
- **`useSpanContentCache`** — on-demand `attributes` cache. `fetchSpanContent(span_id)` hydrates one span, and `allSpans` overlays the cache onto `session.spans` so every consumer reads one merged list. `dropRetiredSpans` (`utils/traceFormatters.js`) prunes the merge's `retired_ids` from the append-only card set.
- **`useViewMode`** — four view modes resolved `?view=` query > `localStorage` > default: **conversation** (the clean centered feed, `SessionConversationView`, with an opt-in span-detail rail), **timeline** (`SessionTimelineTree`, a PrimeVue TreeTable with lazily-loaded children), **terminal** (`SessionTerminalLog`, a flat log that loads every span rather than the shallow root set), and **messages** (the `send_to_user` feed + session goal, fetched from `/sessions/<id>/agent-messages`).
- **Live sync** — a visibility-gated poll (`LIVE_POLL_MS = 4000`) fires `reload()` → `reloadLiveTail()` so a user parked at the bottom keeps seeing new spans and any placeholder→anchor duplicate gets reconciled away. The poll is **self-terminating**: for a session that is already closed on open it runs one bounded catch-up (`syncClosedSessionTail`, also the crash-recovery path) instead of the recurring poll; for a live session that ends mid-view it stops once `ended_at` is set AND the tail stops advancing (`maybeStopOnConverge`, gated on `newestLoadedId`). When live sync retires it flips `liveSyncActive` false so the scroll/wheel pull-to-refresh (`useTraceScroll`) stops re-triggering a backend transcript rescan.
- **Header + overview** — a sticky page header (`useStickyHeader` measures its height into a CSS var that the sidebar and TreeTable `<thead>` pin under) frames `SessionTraceHeader`, the `ToolTokenRollup` (`useToolRollup`), and the `TraceOverviewStrip` mini-timeline (`useTraceTimeline` supplies bounds + `active_work_ms`).
- **Turns + pivots** — `useTurns` drives the turn-usage sidebar and the bidirectional turn⇄span cross-highlight; `useWorkflowMeta` supplies the plans this session authored, the workflow runs it launched, and (when the session IS a run) its stale-snapshot marker + launching-session backlink; `useCompactWatch` polls `compact.pre → compact.post`; `useRuleTriggers` + `SuppressButton` gate the rule-suppression UI on a selected `rule.check` span.
- **Queued prompts** — prompts typed while the agent is busy fire no hook, so they can't be spans; they're derived live from the transcript and rendered as a transient banner that clears the moment the agent dequeues them.

The view plays well with the auth-bypass note in memory ([[feedback_verify_webui_in_browser]]) for Playwright runs.

## Why the shape is what it is

Five design choices are worth remembering:

1. **`session_spans` is the source of truth; `sessions` is a cache.** Aggregates exist so the list view is O(1) per row; `materialize_session` exists so that cache can always be rebuilt from spans. Never edit aggregates by hand — call `materialize_session`.
2. **`session_trace_map` is the skeleton.** Splitting the JSON-heavy `attributes` blob off the tree skeleton is what makes long sessions navigable: the frontend loads the shape from `/map`, then hydrates attributes on demand through `useSpanContentCache`.
3. **Turns aren't spans.** Turn usage has no causal parent and no duration, so it lives in `turn_usage` keyed by `(trace_id, turn_uuid)` and gets rolled up onto `sessions` separately.
4. **Repo membership uses high-signal events only.** `session_repos` is populated from cwd + file-mutating tools, not reads or Bash, so an incidental cross-repo read never mistags a session ([[feedback_orthogonal_data_model_axes]]).
5. **Reconcile at read time, not write time.** The store is append-only; placeholder/resolved disagreements are resolved per read window by `merge_spans` ([[trace-merge-reconcile]]), so projection-rule changes ship as code, self-heal on the next read, and never require a data migration. The price is that every raw reader must go through the merge (or exclude PENDING rows itself) — which is why the paginated endpoint threads `retired_ids` back to the append-only conversation cards.

Adjacent topics: span builders and payload schemas live under [[trace-span-capture]]; token/cost accounting under [[trace-usage-billing]]; rule lint events ride the same span pipeline ([[rule-engine-design]]); and the full list of SPA pages is mapped in [[webui-surface]].