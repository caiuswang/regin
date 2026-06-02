# Session Spans — Design & Debugging

How regin records, stores, projects, and displays the timeline of a
Claude Code session. If a span shows the wrong duration, the wrong
parent, or is missing entirely, start here.

Related:

- [`TURN_USAGE.md`](./TURN_USAGE.md) — per-API-call token usage (a
  separate table, `turn_usage`, fed from the same transcript)
- `hook_manager/README.md` — the hook dispatch architecture that
  produces spans (see `registry.py` for the live handler list)
- `tests/trace/integration/README.md` — tmux-driven integration tests that
  drive a real `claude` CLI end-to-end

---

## 1. The data model

All spans live in one table:

```sql
CREATE TABLE session_spans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,     -- = Claude Code session_id
    span_id         TEXT NOT NULL,     -- 16-hex uuid4 half
    parent_id       TEXT,              -- nullable; filled by projection
    name            TEXT NOT NULL,     -- 'prompt', 'tool.Read', ...
    kind            TEXT DEFAULT 'internal',
    start_time      TEXT NOT NULL,     -- ISO-8601
    end_time        TEXT,              -- ISO-8601 or NULL
    duration_ms     INTEGER,
    attributes      TEXT NOT NULL DEFAULT '{}',  -- JSON
    status_code     TEXT DEFAULT 'UNSET',
    status_message  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Spans are **append-only** in the ingest path. Parent assignment and
envelope widening are computed on read (`fetch_session_projection`)
and only persisted when an operator explicitly calls
`POST /api/sessions/<id>/materialize`.

### Standard span names

| Name                | Emitter                                     | Event              | Shape |
|---------------------|---------------------------------------------|--------------------|-------|
| `session.start`     | `handlers/session_lifecycle.py`             | `SessionStart`     | First-class (under `conversation` if one exists) |
| `session.end`       | `handlers/session_lifecycle.py`             | `SessionEnd`       | First-class (never under a prompt) |
| `conversation`      | ingest path for a trace_id's first span     | n/a                | Root container |
| `prompt`            | `handlers/prompt_trace.py`                  | `UserPromptSubmit` | First-class; parents tool calls for this turn |
| `turn`              | `handlers/turn_trace/`                      | `UserPromptSubmit`, `Stop`, `SessionEnd` | Metadata marker: `attributes.model` |
| `tool.<Name>`       | `handlers/post_tool_trace.py`               | `PostToolUse`      | Orphan on emit; grafted under current prompt |
| `skill.read`        | `handlers/skill_read.py`                    | `PostToolUse(Read)` | Orphan on emit; grafted under current prompt |
| `plan.session`, `plan.draft`, `plan.review`, `plan.exit`, `plan.decision` | `handlers/plan_trace.py` | `PostToolUse`, `UserPromptSubmit` | Orphan on emit |
| `rule.check`        | `handlers/rule_check.py`                    | `PostToolUse`      | Orphan on emit |
| `file.edit`         | `handlers/file_changed.py`                  | `FileChanged`      | Orphan on emit |
| `pre_tool.<Name>`   | (reserved; not currently registered)        | `PreToolUse`       | Orphan on emit |
| `subagent.start`    | `handlers/subagent_lifecycle.py`            | `SubagentStart`    | Orphan on emit; grafted under current prompt; hosts its subagent's tool spans |
| `subagent.stop`     | `handlers/subagent_lifecycle.py`            | `SubagentStop`     | Orphan on emit; reparented under its matching `subagent.start` |

`turn.usage` rows are **not** stored here anymore — see
[`TURN_USAGE.md`](./TURN_USAGE.md) for the dedicated `turn_usage`
table.

---

## 2. Lifecycle of a span

```
Claude Code fires an event
    │
    ▼
python -m hook_manager <Event>      # settings.json entry point
    │
    ▼
hook_manager/runner.py
    │  sorts matching handlers by (priority, name) — LOWER priority runs EARLIER
    │
    ▼
handler.fn(payload)
    │
    ▼
lib/hook_plugin.post_span(...)       # builds OTel-ish span dict
    │
    ▼
POST /api/session-spans              # web/blueprints/trace/spans_ingest.py
    │
    ▼
INSERT INTO session_spans (..., parent_id=NULL, ...)
```

Most span emitters call **module-level** `post_span` (no
`HookContext`), so `parent_id` is `NULL` at ingest. Parent assignment
happens at projection time.

### Handler priorities — why they matter

Handler priority controls emission order *within a single event*.
Emission order determines **timestamp order** (handlers run
sequentially; each handler's `post_span` uses `datetime.now()`), and
timestamp order is the only signal the projection uses to decide
parent/child relationships for orphan spans.

A handler that emits a span with priority *lower than* `prompt_trace`
(priority `100`) on `UserPromptSubmit` will produce a span with a
timestamp a few **milliseconds before** the new `prompt` span. In the
projection's sort-by-`start_time`, that span lands **ahead** of the
new prompt and gets grafted under the **previous** prompt — see §6
for the concrete bug this caused.

**Rule**: any trace handler that fires on `UserPromptSubmit` and
should belong to the *new* prompt must have priority > `100`. The
current registry pins `turn_trace` at `150` for exactly this reason.

---

## 3. Projection pipeline

The raw table is messy: most spans are parent-less, and timestamps
don't form a clean tree. The projection turns it into the tree the UI
consumes. It runs on every `GET /api/sessions/<id>` and never mutates
the DB unless `/materialize` is hit.

```
_fetch_spans(conn, trace_id)               # read rows for this trace_id
        │
        ▼
merge_spans(spans)             # reconcile the append-only table (read-time)
        │  1. dedup: drop superseded live placeholders (promptlive-/pending-/
        │     permreq-), resolved permission.requests, stale cross-turn blockers
        │  2. reparent: delegates to _graft_orphans (the 4 passes below)
        │       - pass 1: prompts + session.* grafted under `conversation`
        │       - pass 2: orphan tool/skill/etc. grafted under current prompt
        │       - pass 3: turn spans landing <1 s before a LATER prompt are
        │                 re-attached to that later prompt  (§6)
        │       - pass 4: spans carrying attributes.agent_id re-attached
        │                 to their matching `subagent.start` span  (§7)
        ▼
_widen_envelopes(spans)                    # parent span times cover children
        │   - walks direct children only
        │   - (transitive widening happens again in _build_span_tree)
        ▼
_build_span_tree(spans)                    # convert to PrimeVue TreeTable
        │   - recursive; re-widens start/end from children
        │   - isGroup flag on nodes with children
        ▼
{'widened': [...], 'tree': [...]}
```

The append-only store means a live placeholder and the real span coexist
in the table; `merge_spans` (`lib/trace/merge.py`) is the single point that
retires the superseded rows before parenting. With no placeholders present
it reduces to `_graft_orphans` — that equivalence is its no-regression
invariant.

**Source**: `lib/trace/merge.py::merge_spans` (dedup + reparent) wrapping the
pure transforms in `lib/trace/projection.py` (`_graft_orphans`,
`_widen_envelopes`, `_build_span_tree`); called via
`lib/trace/trace_service/queries.py::fetch_session_projection`.

### `_graft_orphans` in detail

Four passes. Each is idempotent and never mutates the input list.

1. **Prompts & session lifecycle → conversation.** If a `conversation`
   span exists, every orphan `prompt`, `session.start`, `session.end`
   gets `parent_id = conversation.span_id`.
2. **Everything else → current prompt.** Iterate spans sorted by
   `start_time`. Track `current_prompt`. When the timeline crosses a
   `prompt` span, update `current_prompt`. Any other orphan adopts the
   current prompt as parent. `session.*` is explicitly excluded from
   this pass so a trailing `session.end` never nests inside the last
   prompt.
3. **Turn re-attribution.** Scan every `turn` span; if its
   `start_time` falls within `_TURN_LOOKAHEAD_SECONDS` (1 s) *before*
   a later prompt's start, re-attach to that later prompt. This
   repairs historical data written before the priority fix in §6, and
   protects against any future handler emitting close-to-new-prompt
   spans on `UserPromptSubmit`.
4. **Subagent reparenting.** Build a map
   `agent_id → subagent.start.span_id`. Any non-`subagent.start` span
   whose `attributes.agent_id` matches an entry in that map has its
   `parent_id` overwritten to point at the subagent. This nests the
   subagent's internal tool calls (and the `subagent.stop` marker)
   under the subagent span instead of scattering them as siblings of
   the parent's own tool calls. Runs *after* pass 2 on purpose —
   pass 2 assigns the current prompt, pass 4 redirects subagent
   children off the prompt and onto their subagent. See §7.

### `_widen_envelopes` in detail

For each parent `P` with direct children `C₁…Cₙ`:

```
new_start = min(P.start, min(Cᵢ.start))
new_end   = max(P.end  , max(Cᵢ.end  ))
```

Order of iteration over `children_by_parent` is **not** topological.
A two-level tree (prompt → tool → sub_tool) can be visited
prompt-first, so prompt's widen doesn't yet see `sub_tool`. That's
fine because `_build_span_tree._to_nodes` does the recursive
re-widening when it builds the tree.

---

## 4. Display

The Vue trace view (`frontend/src/views/SessionTraceView.vue`)
consumes two payloads:

- **`spans`** — flat list of first-class (root) spans. Used for the
  Grafana-style overview strip and total `traceDuration`.
- **`tree`** — nested PrimeVue `TreeTable` nodes. Roots are rendered;
  children are fetched lazily via
  `/api/sessions/<id>/spans/<span_id>/children`.

The initial request passes `?shallow=1` so the server returns only
root nodes with a `child_count`, keeping the first paint small.

### What's first-class

A span is **first-class** iff it has no parent after projection.
Today that means:

- `conversation` (always)
- Orphans that couldn't be parented (e.g. span emitted before any
  `prompt` exists in the trace)

Everything else gets grafted into `conversation` or the most recent
`prompt`.

### Colors & labels

- `barColor(name)` map in the Vue view covers `skill.read`,
  `file.edit`, `plan.*`, `prompt`, `conversation`, and a
  `tool.*`/`pre_tool.*` prefix catch-all.
- The overview strip uses a separate palette cycled by index so each
  first-class span gets a distinct color regardless of name.

### Duration rendering

- **Leaf nodes**: `node.data.duration_ms` directly (from
  `_widen_envelopes`).
- **Group nodes** (have children): `groupDuration(node.data)` =
  `end_time − start_time` computed in the browser from the widened
  times in `_to_nodes`.

If `duration_ms` and `(end − start)` disagree, it's because
`_to_nodes` re-widened recursively past what `_widen_envelopes`
caught. The group-duration wins visually.

---

## 5. Invariants & timing rules

| Invariant | Why | Violated by |
|---|---|---|
| A prompt span's `end_time` ≤ next prompt's `start_time` | A user can't be in two prompts at once — the "active prompt" interval is `[prompt.start, next_prompt.start)` | Orphan spans emitted on `UserPromptSubmit` with priority < `prompt_trace` (see §6) |
| `session.end` has no prompt ancestor | Session end fires *after* the last turn; nesting it in the trailing prompt hides it in the UI | `_graft_orphans` pass 2 if `_SESSION_LIFECYCLE_NAMES` guard is removed |
| Every span has a resolvable `trace_id` | Cross-session joins must be impossible | n/a — enforced at ingest |
| A `turn` span's `attributes.model` reflects the **previous** completed turn, not the one about to start | `turn_trace` reads the transcript's *last* assistant entry | A `/model` switch arriving before the next prompt gets attributed to the prior turn |

---

## 6. Worked example — the "prompt duration includes user-idle gap" bug

**Symptom.** In the session timeline, the first `prompt` span's
duration equals the time from user submit #1 to user submit #2,
instead of "user submit #1 to AI response end #1".

**Reproduction.** Any session with a visible pause between the AI
finishing and the user typing the next prompt.

**Diagnosis.**

1. `turn_trace` fired on `UserPromptSubmit` with priority `50`.
2. `prompt_trace` fires on the same event with priority `100`.
3. Handlers run in ascending priority order
   (`hook_manager/runner.py:run`), so `turn_trace` ran first and
   posted a `turn` span with `start_time = T₂ − ε` (milliseconds
   before the new prompt span at `T₂`).
4. In projection:

   ```
   sorted by start_time:
     prompt #1   (T₀)          current_prompt = #1
     tool.*      (T₀.x)        grafted under #1
     ...
     turn        (T₂ − ε)      current_prompt is still #1 → grafted under #1
     prompt #2   (T₂)          current_prompt = #2
   ```

5. `_widen_envelopes` widened prompt #1's `end_time` to `T₂ − ε`,
   sweeping the entire user-idle gap into #1.

Real-data proof from `trace_id=c976ffba-…`:

```
turn      2026-04-24T12:50:25.759233  (parent=p1)
prompt    2026-04-24T12:50:25.768136  (prompt #2 — 9 ms later)
```

9 ms gap, but ~66 s of user-idle time between the last real tool call
(12:49:19) and the new prompt (12:50:25) got attributed to prompt #1.

**Fix.** Two changes, layered:

1. **`hook_manager/registry.py`** — bump `turn_trace.priority` from
   `50` to `150` so on `UserPromptSubmit` it runs *after*
   `prompt_trace`. Now the `turn` span's timestamp is
   strictly **after** the new prompt's; sorted order puts the new
   prompt first; the iterator advances `current_prompt` to #2 before
   processing the turn.
2. **`lib/trace/projection.py`** — new pass in `_graft_orphans` that
   re-attaches any `turn` span landing within `_TURN_LOOKAHEAD_SECONDS`
   (1 s) *before* a later prompt. Repairs sessions already stored
   with the old ordering, no backfill required.

**Regression tests.**

- `hook_manager/tests/test_registry.py::test_prompt_trace_runs_before_turn_trace_on_user_prompt_submit`
- `tests/trace/test_trace_api.py::test_graft_orphans_reattaches_turn_span_near_next_prompt`

---

## 7. Worked example — 3-level tree for subagents

**Goal.** When the parent session spawns a subagent (the `Agent` /
`Task` tool), the subagent's internal tool calls should nest under
*the subagent*, not under the parent's prompt as flat siblings of the
parent's own tool calls. The desired shape:

```
conversation
└── prompt
    ├── tool.Read                (parent's own tool call)
    ├── subagent.start           ──┐
    │   ├── tool.Bash             │ all tagged with the subagent's
    │   ├── tool.Read             │ agent_id, reparented in pass 4
    │   └── subagent.stop        ──┘
    └── tool.Agent                (parent's PostToolUse for the Agent call)
```

### Why it's tricky — subagents share the parent's `session_id`

Claude Code does **not** issue a separate session id for a subagent.
Every hook fired inside the subagent (`PostToolUse` for each tool it
runs, `SubagentStart`/`SubagentStop`) carries the parent's
`session_id`, so all those spans land in the parent's trace under the
same `trace_id`. Verified from `~/.claude/hook-payloads.jsonl`:

```
hook_event_name="PostToolUse"   tool_name=Bash
session_id=<parent>
agent_id=adde22dbf9050defa      agent_type=Explore
```

What *does* differ:

- **Transcript file**: the subagent writes its own API turns to
  `.../subagents/agent-<id>.jsonl`, while `transcript_path` stays on
  the parent's `.../<session_id>.jsonl`. `turn_trace` reads only
  `transcript_path`, so subagent tokens never reach `turn_usage` —
  i.e. the parent's `peak_context_tokens` / per-turn ctx numbers are
  **not** polluted by subagent work. Only the span tree is.
- **`agent_id` + `agent_type` on the payload**: present on every
  subagent-internal hook, absent on parent-owned hooks. This is the
  sole discriminator.

### How the fix works

1. **`handlers/post_tool_trace.py`** — copies `payload.raw['agent_id']`
   (and `agent_type` when present) into the tool span's `attributes`.
   No behavior change for parent-owned tool spans — they simply lack
   the field.
2. **`handlers/subagent_lifecycle.py`** — already stamps `agent_id`
   on both `subagent.start` and `subagent.stop` spans.
3. **`_graft_orphans` pass 4** — builds `agent_id → subagent.start`,
   then reparents any span whose `attributes.agent_id` matches.
   `subagent.stop` is included; `subagent.start` itself is skipped.
4. **`_widen_envelopes`** needs no change — the subagent.start's
   envelope naturally expands to cover every reparented child,
   giving the subagent span the correct `[start, stop]` duration.

### Why pass 4 runs *after* pass 2

Pass 2 assigns every orphan to the current prompt. Pass 4 then
overrides that assignment for subagent children only, redirecting
them off the prompt and onto the subagent. This ordering means:

- Sessions predating the `agent_id` tagging (no attribute set on
  old tool spans) still work — pass 4 simply has nothing to do and
  the spans stay flat under the prompt, as before.
- The subagent span itself (`subagent.start`) stays under the prompt
  — exactly where it was grafted in pass 2 — because pass 4 skips
  spans named `subagent.start`.

### What about `tool.Agent`?

`tool.Agent` is the parent's `PostToolUse` span for the `Task` tool
call (Claude Code names that tool internally as `Agent`, hence the
span name). The full lifecycle of one Task call is:

1. Parent calls `Task(subagent_type=…, prompt=…)`. From the parent's
   POV, this is a tool call exactly like Read or Bash.
2. Claude Code spawns the subagent → `SubagentStart` fires →
   `subagent.start` span.
3. Subagent runs its own tools → each `PostToolUse` fires with
   `agent_id` set → nested `tool.*` spans.
4. Subagent finishes → `SubagentStop` fires → `subagent.stop` span.
5. Control returns to the parent → the parent's `PostToolUse` for the
   `Agent` tool fires → `tool.Agent` span. **No `agent_id`** because
   this is a parent-context hook.

`tool.Agent` is the marker "the Task tool returned" from the
**parent's** perspective. It's conceptually redundant with
`subagent.stop` — both mean "subagent done" — but they come from
different hooks: `subagent.stop` is the subagent reporting it
stopped, `tool.Agent` is the parent's tool call completing. Both are
kept because they represent two different observation points and have
different attribute shapes (`subagent.stop` carries
`result_preview`; `tool.Agent` carries the standard tool fields).

Because `tool.Agent` has no `agent_id`, pass 4 ignores it and pass 2
leaves it under the prompt as a normal sibling of the parent's other
tool spans. The result is `tool.Agent` rendered next to (not inside)
the subagent's work — visible at the parent level as the closing
marker of the Task call.

### Backfill & old sessions

- Spans persisted before `post_tool_trace.py` started stamping
  `agent_id` won't have the attribute, so they keep their old shape
  (flat under prompt). No migration is required — the projection is
  computed on read and each new session benefits immediately.
- If a session needs to be restaged with the new shape, the spans
  would have to be re-emitted (the attribute isn't recoverable from
  existing rows). In practice this is not worth the effort.

### Regression tests

- `tests/trace/test_trace_api.py::test_graft_orphans_nests_subagent_tool_spans_under_subagent_start` — pure projection logic
- `tests/trace/integration/test_subagent_spans.py::test_subagent_tool_spans_nest_under_subagent_start` — slow end-to-end via tmux + real `claude` Task call, asserts the nested shape on a freshly emitted session

---

## 8. Debugging cookbook

### Symptom: a first-class span has the wrong duration

1. Open the session in the Trace view; copy the `trace_id`.
2. Dump raw rows ordered by `start_time`:

   ```bash
   sqlite3 db/regin.db <<'SQL'
   SELECT span_id, parent_id, name, start_time, end_time, duration_ms
   FROM session_spans WHERE trace_id = '<id>'
   ORDER BY start_time;
   SQL
   ```

3. Identify the span whose timestamp inflates the envelope:
   - If it has a `name` that *should* belong to a later prompt
     (`turn`, `session.start`, anything handler-emitted at a known
     boundary event), §6 applies.
   - If it's a legitimate `tool.*` span with `end_time` after the next
     prompt's `start_time`, the tool genuinely ran long — not a bug.
4. Cross-check against the projected output:

   ```python
   from lib.trace.projection import _fetch_spans, _graft_orphans, _widen_envelopes
   import sqlite3
   c = sqlite3.connect('db/regin.db'); c.row_factory = sqlite3.Row
   raw = _fetch_spans(c, '<id>')
   widened = _widen_envelopes(_graft_orphans(raw))
   ```

### Symptom: a span is missing from the UI

1. Is it in `session_spans`? If not, the hook never ran or `post_span`
   failed — check `~/.claude/traces/ingest-errors.jsonl` and
   `~/.claude/traces/hook-errors.jsonl`.
2. Is the handler in the registry? `describe_handlers()` or
   `/api/hook-handlers`. Check `enabled`.
3. Did the handler throw? Handler exceptions are swallowed and logged
   to `hook-errors.jsonl` — grep for the handler name.
4. Is the span orphaned at the root? `?shallow=1` only returns
   root-level spans; if it's grafted under a prompt, expand that
   prompt in the tree.

### Symptom: parent is wrong

`_graft_orphans` attributes by timestamp only. If a span was emitted
long after its conceptual parent (e.g. via `post_event` with a stale
`start_time`), it can end up under the wrong prompt. Check the span's
`start_time` against the prompt boundaries; it belongs to the prompt
with `prompt.start_time ≤ span.start_time < next_prompt.start_time`.

### Symptom: span appears twice

Ingest is **not** keyed by `span_id`; re-POSTing the same
`span_id` creates a duplicate row. `turn_usage` dedups via
`(trace_id, turn_uuid)` primary key — **spans do not.** If a handler
is firing twice, look at the registry for duplicate entries or at the
event matcher for overlap.

### Symptom: `session.end` nested inside the last prompt

Regression — `_SESSION_LIFECYCLE_NAMES` guard in
`_graft_orphans` prevents this. If you see it, the guard was deleted
or a new lifecycle name was added without being registered in that
set. Covered by
`tests/trace/test_trace_api.py::test_graft_orphans_keeps_session_end_out_of_prompts`.

---

## 9. Optional: statusline ingest for accurate model + context

Claude Code fires `statusLine.command` on every UI refresh with a
stdin JSON blob that carries:

- `session_id`
- `model.id` — **includes the variant suffix** (e.g. `claude-opus-4-7[1m]`)
- `context_window.used_tokens` / `total_tokens` / `used_percentage`
- `workspace.current_dir`, `cwd`, `git.branch`, etc.

This is the only runtime surface that reliably carries the variant
suffix and the real context window. Hook payloads omit `model`
entirely on `SessionStart` (at least in v2.1.119), and the
transcript JSONL strips the suffix from every `message.model` — so
without this feed, `sessions.model` stays as the bare base and
`infer_window` misreads a 1M session as 200k until peak crosses the
base cap.

`scripts/regin-statusline` is the regin side of that contract. It's
an opt-in, standalone command: users wire it up as
`statusLine.command`, it reads stdin, POSTs
`{trace_id, model, context_used_tokens, context_window_tokens}` to
`POST /api/session-status`, and — unless run with `--ingest-only` —
also prints a minimal default status line so it can double as the
statusline itself.

Zero coupling: regin never reaches into any existing statusline.
Users who already have a custom statusline keep it and chain our
script as a sink:

```bash
# In the existing statusline script:
input="$(cat)"
printf '%s' "$input" | /abs/path/to/regin/scripts/regin-statusline --ingest-only
# ... your own rendering below ...
```

Users without a custom statusline point `~/.claude/settings.json` at
regin directly:

```json
"statusLine": {
  "type": "command",
  "command": "/abs/path/to/regin/scripts/regin-statusline"
}
```

The endpoint (`web/blueprints/trace/turn_usage.py::api_ingest_session_status`)
and the service logic (`trace_service.ingest_session_status`) are
tested independently of the script — they accept any caller, so a
future UI "mark this session as 1M" button can reuse the same path.

Outages are swallowed: if regin's Flask server is down the script
prints the default statusline and exits 0. A statusline that fails
is strictly worse than one that serves stale data.

---

## 10. Files you'll touch

| File | Role |
|---|---|
| `lib/hook_plugin.py` | `post_span`, `build_span`, `post_event`; shared emission helpers |
| `hook_manager/registry.py` | Single source of truth for handler order (priority) |
| `hook_manager/handlers/*.py` | Per-event span emitters |
| `hook_manager/runner.py` | Dispatcher: sort by priority, run, merge |
| `lib/trace/merge.py` | Read-time reconcile entry point: dedup placeholders/pending/permissions, then delegate to `_graft_orphans` |
| `lib/trace/projection.py` | Pure projection: graft + widen + tree |
| `web/blueprints/trace/` | HTTP surface (`sessions.py`, `spans_ingest.py`, `turn_usage.py`, … — `/api/sessions`, `/api/session-spans`, `/api/sessions/<id>/spans/<span_id>/children`, `/materialize`) |
| `lib/trace/trace_service/` | DB-facing package: `queries.py` (`fetch_session_projection`), `ingest.py` (`materialize_session`, span + turn_usage ingest) |
| `frontend/src/views/SessionTraceView.vue` | Timeline UI (overview strip + TreeTable + sidebar) |
