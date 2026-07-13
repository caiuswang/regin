# Turn Usage Tracking

This document describes how per-assistant-turn token usage is captured,
stored, and surfaced in regin. If you're wondering "where does the ctx%
badge come from" or "why are there N turn_usage rows for my session",
start here.

## What we track

For each Claude API call in a session, we record:

| Field | Source | Meaning |
|---|---|---|
| `input_tokens` | `message.usage.input_tokens` | Fresh (uncached) input tokens sent this turn |
| `output_tokens` | `message.usage.output_tokens` | Tokens Claude generated in its response |
| `cache_read_tokens` | `message.usage.cache_read_input_tokens` | Tokens replayed from the prompt cache (~10% price) |
| `cache_creation_tokens` | `message.usage.cache_creation_input_tokens` | New tokens written into the cache this turn |
| `context_used_tokens` | sum of the three input buckets above | Size of the prompt actually sent to the model |
| `model` | `message.model` from transcript (base id only) | `claude-opus-4-7`, `claude-haiku-4-5-20251001`, … |
| `turn_index` | position in the transcript | 0, 1, 2, … |
| `timestamp` | transcript `.timestamp` | ISO string with Z suffix |
| `turn_uuid` | transcript `.uuid` | Claude Code's per-message globally-unique id |
| `request_id` | transcript `.requestId` | Anthropic `req_…` id |

At the session level, we maintain aggregates on `sessions.*`:
`input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens` are **sums** across turns;
`peak_context_tokens` is the **max** `context_used_tokens` ever seen
on a single turn; `context_window_tokens` is the inferred window size
for the model. `context_pct = peak / window` drives the header badge.

## Data source: the transcript JSONL

Claude Code writes a JSONL file per session at
`~/.claude/projects/<cwd-munged>/<session_id>.jsonl`. Each line is one
"entry" of type `user`, `assistant`, `system`, `tool_use_result`, etc.

Every assistant API response produces **1–2 adjacent `assistant` lines**
that share the same `message.id` — one per content block (text,
tool_use, etc.). Their `message.usage` blocks are identical copies.

Synthetic entries (session init, `/compact` markers) have
`message.model = "<synthetic>"` and no real usage data.

## The per-turn model

> One `turn_usage` row = one Anthropic API call.

A single "turn" can contain:
- Reasoning text
- Zero or more `tool_use` blocks (parallel tool calls)

So one turn can trigger multiple tool calls — each becomes its own
`tool.*` span in `session_spans`, but there's still just **one**
`turn_usage` row for the turn.

Tool results themselves don't have usage data. When Claude processes
them and responds, **that's a new assistant turn** → a new API call →
a new `message.id` → a new `turn_usage` row.

## Pipeline

```
Claude Code session
       │
       ▼  writes
  transcript.jsonl          <~/.claude/projects/…>
       │
       │  (read on every hook fire)
       ▼
 lib/trace/transcript_usage.read_usage(path)
       │     parse JSONL
       │     skip type != 'assistant'
       │     skip model == '<synthetic>'
       │     dedup by message.id (fallback requestId)
       ▼
 list[TurnUsage]            (38 deduped turns from ~72 raw lines)
       │
       │  (hook_manager.handlers.turn_trace)
       ▼
 POST /api/turn-usage       [{trace_id, turn_uuid, …}, …]
       │
       │  (web.blueprints.trace.api_ingest_turn_usage)
       ▼
 lib/trace/trace_service.ingest_turn_usage
       │     UPSERT turn_usage ON CONFLICT(trace_id, turn_uuid)
       │     recompute sessions.{input,output,cache_*,peak} from
       │       SUM/MAX over turn_usage for each touched trace_id
       │     update sessions.context_window_tokens =
       │       infer_window(sessions.model, peak)
       ▼
 DB: turn_usage (PK trace_id, turn_uuid) + sessions (one row)
       │
       │  (web.blueprints.trace.api_session_turn_usage /
       │   _session_summary / _row_to_dict)
       ▼
 GET /api/sessions/<id>        ← carries aggregate fields + context_pct
 GET /api/sessions/<id>/turn-usage  ← lazy: per-turn rows +
                                     span_refs / span_count / tool_summary
                                     (spans whose start_time falls in the
                                     turn's interval, for the sidebar's
                                     span-linkage drill-down)
       │
       ▼
 SessionTraceView.vue
   • Header badge:        ctx: 7.2% (72.4k/1M) · claude-opus-4-7[1m]
   • Timeline overview:   per-session-tree colored bars (not turns)
   • Sticky sidebar:
       - Span Details (selected tree node)
       - Turns panel:     click "load" → GET /…/turn-usage
                          table: # ctx in cR cW out
```

### Hook firing

`turn_trace` registers for **three** Claude Code events:

| Event | Fires when | Why |
|---|---|---|
| `UserPromptSubmit` | user sends a prompt | Cheapest place to catch new turns between bursts |
| `Stop` | an assistant response completes | Real-time capture per turn |
| `SessionEnd` | the session closes | Final catch-all for the last turn |

Each fire does a full transcript rescan (O(file size), typically
<1 MB). Every deduped turn is POSTed. Dedup at the DB layer means the
rescan is cheap: only actually-new `(trace_id, turn_uuid)` pairs
insert; the rest are silently no-op on `ON CONFLICT`.

We deliberately **don't** fire on `PostToolUse` — too frequent, and
the transcript's assistant-message flush lags a tool call by enough
that we'd just see the same turns `Stop` or the next
`UserPromptSubmit` would give us.

### Idempotency

The primary key `(trace_id, turn_uuid)` guarantees that no matter how
many times the handler fires or how many times the transcript is
rescanned, a given API call contributes exactly one row to
`turn_usage`. This is the load-bearing invariant — if something starts
showing double-counted token totals, the first place to look is here.

## Model window inference

The transcript writes `message.model` as the **base** model id
(`claude-opus-4-7`), even when the user selected the 1M-token variant
(`claude-opus-4-7[1m]`) via `/model`. So we can't just trust the
transcript's model for window size.

`lib/tokens/model_windows.infer_window(model, peak_tokens)`:

1. Look up `model` in `_WINDOWS` — returns the base window size.
2. If `peak_tokens > base`, try `f"{model}[1m]"` — returns the
   extended window if it exists.
3. Otherwise return the base (even if we've only observed small
   prompts, we can't confirm 1M usage from the transcript alone).

Because of step 3, a 1M-context session that never gets over 200k of
context looks like a 200k session to this code. That's the default
limitation — see the opt-in statusline ingest below for how to fix it.

`_session_summary` and `_row_to_dict` in `web/blueprints/trace/sessions.py`
call `infer_window(sessions.model, peak)` at API **read** time, so
the UI derives its window from the live `sessions.model` column —
whatever the freshest writer pushed there. This means fixing
`sessions.model` for a running session (e.g. via the statusline
endpoint described below) instantly corrects every downstream view
without a backfill.

### Where `sessions.model` gets its variant suffix

Current Claude Code versions (observed through v2.1.119) do **not**
include `model` in the `SessionStart` hook payload, and the
transcript strips the variant from every `message.model`. That
leaves three possible sources, in increasing order of reliability:

1. **The `infer_window` step-2 fallback** — kicks in only once peak
   actually exceeds the base window. Passive, and misreads 1M sessions
   as 200k until the threshold is crossed.
2. **An opt-in POST to `POST /api/session-status`** — the variant
   and the true context-window total land immediately. Implemented
   in `scripts/regin-statusline`, which reads the same stdin JSON
   Claude Code feeds to any statusline and pulls out `model.id` +
   `context_window.{used_tokens,total_tokens}`. The script is
   independent of any existing statusline; opt in either by pointing
   `statusLine.command` at it directly, or by chaining it from your
   own statusline via `--ingest-only`. See
   `docs/trace/SPAN_DESIGN.md` for wiring details.
3. **Manual SQL override** — patch `sessions.model` by hand if a
   past session needs retroactive correction.

## Schema

```sql
CREATE TABLE turn_usage (
    trace_id               TEXT NOT NULL,
    turn_uuid              TEXT NOT NULL,
    turn_index             INTEGER NOT NULL,
    timestamp              TEXT NOT NULL,
    model                  TEXT,
    input_tokens           INTEGER NOT NULL DEFAULT 0,
    output_tokens          INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens  INTEGER NOT NULL DEFAULT 0,
    context_used_tokens    INTEGER NOT NULL DEFAULT 0,
    request_id             TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (trace_id, turn_uuid)
);
CREATE INDEX idx_turn_usage_trace_ts ON turn_usage(trace_id, timestamp);
```

The table is part of the baseline `db/schema.sql` (built by `regin init`);
an older DB is brought to the current shape by `regin migrate`
(`alembic upgrade head`). Legacy `session_spans` rows where
`name='turn.usage'` are no longer migrated automatically — use
`regin trace backfill-tokens` to repopulate.

## Key files

| File | Role |
|---|---|
| `lib/trace/transcript_usage.py` | Pure-function JSONL parser + dedup by `message.id` |
| `lib/tokens/model_windows.py` | Model → context-window map; `infer_window(model, peak)` |
| `hook_manager/handlers/turn_trace/` | Reads transcript, POSTs turn rows |
| `hook_manager/registry.py` | Registers turn_trace on UserPromptSubmit, Stop, SessionEnd |
| `lib/hook_plugin.py` | `post_event('turn_usage', rows)` → `/api/turn-usage` |
| `lib/trace/trace_service/` | `ingest.py::ingest_turn_usage` + `queries.py::fetch_turn_usage`; also rebuilds `sessions.*` aggregates |
| `web/blueprints/trace/turn_usage.py` | `POST /api/turn-usage`, `GET /api/sessions/<id>/turn-usage`, derives `context_pct` at read time |
| `db/schema.sql` | Canonical table DDL (used by `regin init`) |
| `alembic/versions/` | Post-baseline schema changes; applied by `regin migrate` |
| `cli/commands/trace.py` | `regin trace backfill-tokens` fills `turn_usage` from on-disk transcripts |
| `frontend/src/views/SessionTraceView.vue` | Header ctx% badge + lazy Turns panel |

## Why not "one span per turn"?

That was the first iteration. Storing turns as `session_spans` rows
with `name='turn.usage'` bloated `/api/sessions/<id>` (each response
inlined every turn's attributes alongside the actual timeline spans)
and made the tree UI rendering fight with frontend filters to hide
them. Moving to a dedicated table with a narrow read endpoint means:

- `/api/sessions/<id>` shrinks (no turn noise in the tree).
- `/api/sessions/<id>/turn-usage` is only called when the user opens
  the Turns panel.
- The frontend doesn't have to filter anything out — the data simply
  isn't there unless asked for.
- The schema can have real typed columns instead of JSON-blob
  attributes.

## Common confusions

- **"input_tokens is always 6"** — expected. Claude Code
  aggressively prompt-caches, so nearly all input shows up under
  `cache_read_tokens`. The "6" is the tiny uncached framing overhead.
  Use `context_used_tokens` (all three input buckets summed) for
  "how big was the prompt".
- **"A commit prompt produces 4 new turns"** — normal. Commit flow
  is: gather-status (parallel Bash) → decide msg → commit (parallel
  Bash) → verify. Four API calls, each one row.
- **"Turn count doesn't match transcript line count"** — expected.
  Raw lines include non-assistant entries, synthetic ones, and 1–2
  lines per real assistant response. The parser dedups by
  `message.id` down to the real count.
- **"Cache tokens drop to 0 and ctx jumps"** — a `/compact` run
  invalidates the cache. The next turn has high
  `cache_creation_tokens` (rebuilding) and low
  `cache_read_tokens`. Normal.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Row count far exceeds expected turns | Stale rows from a prior ingest | `DELETE FROM turn_usage WHERE trace_id=?` then `regin trace backfill-tokens --all` |
| ctx% shows wrong window size | `sessions.model` is the bare base not `[1m]` | Check `session.start` span attrs; re-ingest to let API's read-time `infer_window` correct it |
| `peak_context_tokens` is NULL | No `turn_usage` rows yet | Wait for next `Stop`/`UserPromptSubmit`, or run `regin trace backfill-tokens --only-missing` |
| Numbers off for one specific session | Transcript truncation / `/compact` | Run the backfill for that session; the parser does full-file rescan |
